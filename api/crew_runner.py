# ============================================================
# SIGMA-Vial · Ejecución del Crew en segundo plano y captura de eventos
#
# El ciclo de supervisión tarda de segundos (stub) a minutos (Gemini/Ollama),
# así que no puede resolverse en una petición HTTP síncrona (Fase 4, §5.1).
# Aquí el Crew corre en un hilo trabajador y cada paso se publica como evento:
#
#   · bus de eventos de CrewAI  → inicio/fin de tarea, llamada y resultado de
#                                  cada herramienta, delegación, respuestas del LLM
#   · step_callback del Crew    → permite detener el ciclo entre pasos
#   · task_callback (sigma_crew)→ escritura de salidas a disco (Fase 3)
#
# La API reenvía esos eventos al navegador por Server-Sent Events.
# En producción el hilo se sustituye por un trabajador Celery y la lista en
# memoria por un canal de Redis (ver README, «Escalamiento»).
# ============================================================
import datetime
import hashlib
import importlib.util
import json
import os
import re
import sys
import threading
import time
import uuid

from . import db

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_SUP = os.path.join(RAIZ, "supervision")
for p in (DIR_SUP, os.path.join(RAIZ, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

import herramientas as H      # noqa: E402  (Python determinista; no carga ningún LLM)

# (título, rol literal del código, etiqueta corta para el log)
TAREAS = [
    ("Diagnóstico de desempeño", "Analista de Telemetría Vial", "ANALISTA"),
    ("Propuesta de ajuste v1", "Ingeniero de Planes de Señalización", "INGENIERO"),
    ("Auditoría normativa, ciclo 1", "Auditor Normativo y de Seguridad Vial", "AUDITOR"),
    ("Corrección v2", "Ingeniero de Planes de Señalización", "INGENIERO"),
    ("Auditoría y publicación, ciclo 2", "Auditor Normativo y de Seguridad Vial", "AUDITOR"),
    ("Síntesis para la coordinación", "Sintetizador de Movilidad", "SINTETIZADOR"),
]
ETIQUETA_ROL = {"analista": "ANALISTA", "ingeniero": "INGENIERO", "auditor": "AUDITOR",
                "sintetizador": "SINTETIZADOR"}
RE_VIOLACION = re.compile(r"^(?:(I\d):\s*)?(.*?)\s*\((R\d)\b")


def huella(config) -> str:
    """Identificador de versión de una configuración: lo que el operador vio en pantalla."""
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]


def etiqueta(rol) -> str:
    r = (rol or "").lower()
    return next((v for k, v in ETIQUETA_ROL.items() if k in r), "SISTEMA")


def ahora_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def parsear_violacion(v):
    m = RE_VIOLACION.match(v)
    if not m:
        return "—", v, "R?"
    return m.group(1) or "—", m.group(2), m.group(3)


class Ciclo:
    def __init__(self, escenario, motor):
        self.id = uuid.uuid4().hex[:12]
        self.numero = db.siguiente_numero()
        self.escenario, self.motor = escenario, motor
        self.estado = "en_ejecucion"
        self.t0, self.dur, self.error = time.time(), 0.0, ""
        self.eventos, self._seq, self._candado = [], 0, threading.Lock()
        self.tareas = [{"estado": "pendiente", "resumen": ""} for _ in TAREAS]
        self.tareas_obj = []
        self.detener = False
        self.ajuste = None              # configuración ajustada por el operador y revalidada
        self.decision = None
        db.ejecutar("INSERT INTO ciclos (id, numero, inicio, escenario, motor, estado) VALUES (?,?,?,?,?,?)",
                    (self.id, self.numero, ahora_iso(), escenario, motor, self.estado))

    # ---- eventos ----
    def anotar(self, tipo, agente="SISTEMA", texto="", datos=None, ts=None):
        with self._candado:
            self._seq += 1
            if ts is not None and ts.tzinfo is None:      # CrewAI marca sus eventos en UTC
                ts = ts.replace(tzinfo=datetime.timezone.utc)
            t = ts.timestamp() if ts else time.time()
            ev = {"seq": self._seq, "ts": datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S"),
                  "t": t, "tipo": tipo, "agente": agente,
                  "texto": texto, "datos": datos or {}}
            self.eventos.append(ev)
        try:
            db.guardar_evento(self.id, ev)
        except Exception:               # la bitácora nunca debe tumbar la corrida
            pass
        return ev

    def segundos(self):
        return round(time.time() - self.t0 if self.estado == "en_ejecucion" else self.dur, 1)

    def resumen(self):
        hechas = sum(t["estado"] in ("completada", "veto") for t in self.tareas)
        return {"id": self.id, "numero": self.numero, "escenario": self.escenario, "motor": self.motor,
                "estado": self.estado, "segundos": self.segundos(), "error": self.error,
                "tareas": [dict(t, n=i + 1, titulo=TAREAS[i][0], agente=TAREAS[i][1])
                           for i, t in enumerate(self.tareas)],
                "hechas": hechas, "decision": self.decision}


ACTUAL: Ciclo | None = None
CICLOS: dict[str, Ciclo] = {}


def _indice_tarea(ev):
    if ACTUAL is None:
        return None
    for i, t in enumerate(ACTUAL.tareas_obj):
        if t is getattr(ev, "task", None) or str(getattr(t, "id", "")) == str(getattr(ev, "task_id", "")):
            return i
    return None


# ---------------- escucha del bus de eventos de CrewAI ----------------
def _registrar_oyentes():
    from crewai.events import (LLMCallCompletedEvent, TaskCompletedEvent, TaskFailedEvent, TaskStartedEvent,
                               ToolUsageErrorEvent, ToolUsageFinishedEvent, ToolUsageStartedEvent,
                               crewai_event_bus)

    @crewai_event_bus.on(TaskStartedEvent)
    def _tarea_inicia(_, ev):
        c, i = ACTUAL, _indice_tarea(ev)
        if c is None or i is None:
            return
        c.tareas[i]["estado"] = "en_curso"
        c.anotar("tarea_inicio", TAREAS[i][2], f"Tarea {i + 1} · {TAREAS[i][0]}", {"n": i + 1}, ev.timestamp)

    @crewai_event_bus.on(TaskCompletedEvent)
    def _tarea_termina(_, ev):
        c, i = ACTUAL, _indice_tarea(ev)
        if c is None or i is None:
            return
        raw = str(getattr(ev.output, "raw", "") or "")
        resumen = _resumir_salida(i, raw)
        c.tareas[i]["resumen"] = resumen
        if c.tareas[i]["estado"] != "veto":
            c.tareas[i]["estado"] = "completada"
        c.anotar("tarea_fin", TAREAS[i][2], f"Tarea {i + 1} concluida · {resumen}", {"n": i + 1}, ev.timestamp)

    @crewai_event_bus.on(TaskFailedEvent)
    def _tarea_falla(_, ev):
        c, i = ACTUAL, _indice_tarea(ev)
        if c is not None and i is not None:
            c.tareas[i]["estado"] = "error"
            c.anotar("error", TAREAS[i][2], f"Tarea {i + 1} falló: {str(ev.error)[:200]}", {"n": i + 1}, ev.timestamp)

    @crewai_event_bus.on(ToolUsageStartedEvent)
    def _herramienta(_, ev):
        c = ACTUAL
        if c is None:
            return
        args = ev.tool_args if isinstance(ev.tool_args, dict) else str(ev.tool_args)
        tipo = "delegacion" if "delegate" in (ev.tool_name or "").lower() or "coworker" in (ev.tool_name or "").lower() \
            else "herramienta"
        c.anotar(tipo, etiqueta(ev.agent_role), f"{ev.tool_name}({_args_cortos(args)})",
                 {"herramienta": ev.tool_name, "args": args}, ev.timestamp)

    @crewai_event_bus.on(ToolUsageFinishedEvent)
    def _resultado(_, ev):
        c = ACTUAL
        if c is None:
            return
        _interpretar_resultado(c, ev)

    @crewai_event_bus.on(ToolUsageErrorEvent)
    def _error_herr(_, ev):
        c = ACTUAL
        if c is not None:
            c.anotar("error", etiqueta(ev.agent_role), f"{ev.tool_name} devolvió error: {str(ev.error)[:160]}",
                     {}, ev.timestamp)

    @crewai_event_bus.on(LLMCallCompletedEvent)
    def _razonamiento(_, ev):
        c = ACTUAL
        if c is None or c.motor == "stub":       # el guion fijo no razona: no se muestra como si lo hiciera
            return
        txt = ev.response if isinstance(ev.response, str) else ""
        m = re.search(r"Thought:\s*(.+?)(?:\n(?:Action|Final Answer)|$)", txt, re.S)
        txt = (m.group(1) if m else txt).strip()
        if txt:
            c.anotar("razonamiento", etiqueta(getattr(ev, "agent_role", "")), txt[:260], {}, ev.timestamp)


def _args_cortos(args):
    if isinstance(args, dict):
        return ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)[:40]}" for k, v in args.items())
    return str(args)[:80]


def _cargar_json(raw):
    for intento in (lambda: json.loads(raw), lambda: json.loads(H._extraer_json(raw))):
        try:
            d = intento()
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return None


def _resumir_salida(i, raw):
    """Resumen de una línea para la tarjeta de la tarea. Proviene de la salida del LLM."""
    d = _cargar_json(raw)
    if d:
        if "planes_por_interseccion" in d:
            ciclos = sorted({p.get("ciclo_s") for p in d["planes_por_interseccion"].values()})
            i3 = d["planes_por_interseccion"].get("I3", {}).get("verde_peatonal_s")
            return f"{d.get('version', 'propuesta')}: ciclos {ciclos} s · verde peatonal I3 {i3} s"
        if isinstance(d.get("hallazgos"), list):
            partes = []
            for h in d["hallazgos"]:
                if not isinstance(h, dict) or h.get("severidad") in ("nula", "baja"):
                    continue
                peat = h.get("espera_peatonal_vs_webster_pct")
                sp = h.get("spillback_vs_webster_pct")
                txt = f"{h.get('interseccion')}: severidad {h.get('severidad')}"
                if peat is not None and peat > 0:
                    txt += f", espera peatonal {peat:+.1f} % (p={h.get('p_wilcoxon_peatonal')})"
                elif sp is not None and sp > 0:
                    txt += f", spillback {sp:+.1f} %"
                partes.append(txt)
            if partes:
                return " · ".join(partes)
        dict_ = [(k, v) for k, v in d.items() if isinstance(v, dict) and "veredicto" in v]
        if "veredicto" in d:
            dict_.append(("propuesta", d))
        if dict_:
            return " · ".join(f"{k.replace('dictamen_', '').replace('_', ' ')}: {v['veredicto']}"
                              f" ({len(v.get('violaciones', []))} violaciones)" for k, v in dict_)
    linea = next((l.strip("#*-> ").strip() for l in raw.splitlines() if len(l.strip("#*-> ").strip()) > 25), raw)
    return linea[:180]


def _interpretar_resultado(c, ev):
    nombre, salida = ev.tool_name or "", str(ev.output or "")
    agente = etiqueta(ev.agent_role)
    try:
        d = json.loads(salida)
    except Exception:
        d = None
    args = ev.tool_args if isinstance(ev.tool_args, dict) else {}
    if nombre == "validador_reglas_duras" and isinstance(d, dict):
        archivo = args.get("archivo", "propuesta")
        viol = d.get("violaciones", [])
        if d.get("veredicto") == "APROBADO":
            c.anotar("aprobado", "VALIDADOR", f"{archivo}: APROBADO · R0–R6 cumplen", {"archivo": archivo}, ev.timestamp)
            return
        reglas = sorted({parsear_violacion(v)[2] for v in viol})
        if archivo == "vigente":
            c.anotar("hallazgo", "VALIDADOR",
                     f"La configuración vigente v1.0 incumple {', '.join(reglas)} ({len(viol)} violaciones)",
                     {"archivo": archivo, "violaciones": viol}, ev.timestamp)
        else:
            c.anotar("veto", "VALIDADOR",
                     f"VETO · {archivo} incumple {', '.join(reglas)} en {len(viol)} puntos. Propuesta rechazada.",
                     {"archivo": archivo, "violaciones": viol}, ev.timestamp)
            i = _indice_tarea(ev)
            if i is not None:
                c.tareas[i]["estado"] = "veto"
        n_tarea = (_indice_tarea(ev) or 0) + 1
        for v in viol:
            inter, param, regla = parsear_violacion(v)
            db.ejecutar("INSERT INTO vetos VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (c.id, c.numero, time.strftime("%H:%M:%S"), f"Tarea {n_tarea}", archivo, inter,
                         "Configuración vigente" if archivo == "vigente" else "Ingeniero", regla, param,
                         "Hallazgo" if archivo == "vigente" else "Veto automático"))
    elif nombre == "publicador_configuracion" and isinstance(d, dict):
        if d.get("publicado"):
            c.anotar("publicado", "PUBLICADOR", f"Revalidó y escribió {d.get('archivo')} (fuente: {d.get('fuente')}). "
                     "Queda pendiente la autorización del operador.", d, ev.timestamp)
        else:
            c.anotar("veto", "PUBLICADOR", f"Publicación bloqueada: {d.get('motivo')}", d, ev.timestamp)
    else:
        c.anotar("resultado", agente, f"↳ {nombre}: {_resumen_herramienta(nombre, d, salida)}", {}, ev.timestamp)


def _resumen_herramienta(nombre, d, salida):
    if isinstance(d, dict):
        if nombre == "comparador_lineas_base":
            partes = [f"{k} {v['plan_fijo_webster']['variacion_pct']:+.1f}% (p={v['plan_fijo_webster']['p_wilcoxon']})"
                      for k, v in d.get("por_interseccion", {}).items() if "plan_fijo_webster" in v]
            return f"{d.get('metrica')} vs plan fijo: " + " · ".join(partes)
        if nombre == "calculadora_webster" and "ciclo_comun_s" in d:
            return f"ciclo común factible {d['ciclo_comun_s']} s"
        if nombre == "lector_telemetria_sumo":
            return f"{d.get('replicas_por_celda', '?')} réplicas por celda · escenario {d.get('escenario')}"
        return ", ".join(list(d)[:5])
    return salida[:120]


# ---------------- corrida ----------------
def iniciar(escenario, motor):
    global ACTUAL
    if ACTUAL is not None and ACTUAL.estado == "en_ejecucion":
        raise RuntimeError("Ya hay un ciclo en ejecución")
    c = Ciclo(escenario, motor)
    CICLOS[c.id] = c
    ACTUAL = c
    threading.Thread(target=_hilo, args=(c,), daemon=True).start()
    return c


_oyentes = False


def _hilo(c: Ciclo):
    global _oyentes
    clave = os.getenv("GEMINI_API_KEY", "")
    try:
        os.environ["SIGMA_LLM"] = c.motor
        if c.motor == "stub":
            os.environ.setdefault("SIGMA_STUB_PAUSA", "0.9")
        if c.motor == "gemini" and not clave:
            raise RuntimeError("Falta GEMINI_API_KEY en el servidor (.env o secretos del Space)")
        c.anotar("sistema", "SISTEMA", f"Ciclo #{c.numero} · escenario {c.escenario} · motor {c.motor}")
        if not _oyentes:
            _registrar_oyentes()
            _oyentes = True
        # sigma_crew construye el LLM al importarse: se carga de nuevo en cada ciclo,
        # por ruta, para que tome el motor elegido.
        spec = importlib.util.spec_from_file_location("sigma_crew_supervision", os.path.join(DIR_SUP, "sigma_crew.py"))
        sc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sc)
        c.tareas_obj = list(sc.crew.tasks)

        def paso(_):
            if c.detener:
                raise RuntimeError("Ciclo detenido por el operador")
        sc.crew.step_callback = paso
        c.anotar("sistema", "SISTEMA", "Crew armado: 4 agentes · 6 tareas · Process.sequential")
        sc.ejecutar(c.escenario)
        aprobada = H.DIR_DATOS / "configuracion_aprobada.json"
        if aprobada.exists():
            cfg = json.loads(aprobada.read_text(encoding="utf-8"))
            db.ejecutar("UPDATE ciclos SET version_candidata=?, huella=? WHERE id=?",
                        (cfg.get("version", ""), huella(cfg), c.id))
            c.anotar("listo", "SISTEMA", f"Configuración {cfg.get('version', '')} lista. Espera autorización del operador.")
        else:
            c.anotar("veto", "SISTEMA", "El ciclo terminó sin una configuración publicable.")
        c.anotar("verificacion", "VERIFICACION", "verificacion.py contrastó las cifras del reporte con las salidas del Crew")
        c.estado = "terminado"
    except Exception as e:                       # noqa: BLE001
        msg = str(e)
        if clave:
            msg = msg.replace(clave, "***")
        c.error, c.estado = msg[:500], ("detenido" if c.detener else "error")
        c.anotar("error", "SISTEMA", f"La corrida terminó con error: {msg[:300]}")
    finally:
        c.dur = time.time() - c.t0
        db.ejecutar("UPDATE ciclos SET fin=?, estado=?, error=? WHERE id=?", (ahora_iso(), c.estado, c.error, c.id))

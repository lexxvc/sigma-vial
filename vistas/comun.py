# ============================================================
# SIGMA-Vial · Consola de supervisión — estado y utilidades compartidas
# ============================================================
import csv, datetime, importlib, io, json, os, sys, threading, time

import streamlit as st

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_SUP = os.path.join(RAIZ, "supervision")
for p in (os.path.join(RAIZ, "src"), os.path.join(RAIZ, "app"), DIR_SUP):
    if p not in sys.path:
        sys.path.insert(0, p)

import herramientas as H            # noqa: E402  (Python determinista, no carga ningún LLM)
import verificacion as V            # noqa: E402
import puente                       # noqa: E402

DATOS, SALIDAS = str(H.DIR_DATOS), str(H.DIR_SALIDAS)
REPORTE = os.path.join(DIR_SUP, "reporte_ciclo_supervision.md")
ESCENARIOS = {"pico_matutino": "Pico matutino", "valle": "Valle",
              "evento_especial": "Evento especial", "falla_sensores": "Falla de sensores"}
INTER = ["I1", "I2", "I3", "I4"]
TAREAS = [("Diagnóstico de desempeño", "Analista de Telemetría Vial", "Variable {escenario}", "salidas/tarea_1_diagnostico.txt"),
          ("Propuesta de ajuste v1", "Ingeniero de Planes de Señalización", "Tarea 1", "salidas/propuesta_v1.json"),
          ("Auditoría normativa, ciclo 1", "Auditor Normativo y de Seguridad Vial", "Tarea 2", "salidas/tarea_3_auditoria_v1.txt"),
          ("Corrección v2", "Ingeniero de Planes de Señalización", "Tareas 1, 2 y 3", "salidas/propuesta_v2.json"),
          ("Auditoría y publicación, ciclo 2", "Auditor Normativo y de Seguridad Vial", "Tarea 4", "datos/configuracion_aprobada.json"),
          ("Síntesis para la coordinación", "Sintetizador de Movilidad", "Tareas 1 a 5", "reporte_ciclo_supervision.md")]
REGLAS = [("R0", "Esquema y completitud"), ("R1", "Verde vehicular"), ("R2", "Ciclo máximo"), ("R3", "Despeje peatonal"),
          ("R4", "Consistencia de fases"), ("R5", "Espera peatonal máxima"), ("R6", "Espera máxima por carril")]
COLUMNAS_CSV = ["escenario", "interseccion", "controlador", "replica", "espera_veh_media_s",
                "espera_peat_media_s", "spillback_eventos_h"]
S1, S2 = "#2a78d6", "#eb6834"      # series: vs. plan fijo, vs. máxima presión


def leer_json(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def vigente():
    return leer_json(os.path.join(DATOS, "configuracion_vigente.json"))


def candidata():
    """Configuración que publicó el Auditor (con ajuste humano revalidado, si lo hay)."""
    return st.session_state.get("ajustada") or leer_json(os.path.join(DATOS, "configuracion_aprobada.json"))


def dictamen(archivo=None, config=None):
    """Veredicto literal del validador R0–R6 (código de la Fase 3)."""
    try:
        if config is not None:
            return json.loads(H.ValidadorReglasDuras()._run(propuesta_json=json.dumps(config)))
        return json.loads(H.ValidadorReglasDuras()._run(archivo=archivo))
    except Exception as e:
        return {"veredicto": "NO DISPONIBLE", "violaciones": [], "advertencias": [str(e)]}


def violaciones_por_regla(d):
    return {r: sum(1 for v in d.get("violaciones", []) if f"({r})" in v) for r, _ in REGLAS}


def comparacion(escenario, metrica):
    try:
        return json.loads(H.ComparadorLineasBase()._run(escenario=escenario, metrica=metrica))["por_interseccion"]
    except Exception:
        return {}


def validar_csv(contenido: bytes):
    """Devuelve la lista de problemas del CSV de telemetría (vacía si es válido)."""
    try:
        filas = list(csv.DictReader(io.StringIO(contenido.decode("utf-8"))))
    except Exception:
        return ["El archivo no se pudo leer como CSV en UTF-8."]
    if not filas:
        return ["El archivo está vacío."]
    faltan = [c for c in COLUMNAS_CSV if c not in filas[0]]
    if faltan:
        return [f"Faltan columnas: {', '.join(faltan)}."]
    grupos = {}
    for f in filas:
        grupos.setdefault((f["escenario"], f["interseccion"], f["controlador"]), set()).add(f["replica"])
    cortos = [g for g, r in grupos.items() if len(r) < 10]
    if cortos:
        e, i, c = cortos[0]
        return [f"{len(cortos)} combinaciones no tienen 10 réplicas (por ejemplo {e} · {i} · {c}). "
                "El Analista no puede calcular la prueba de Wilcoxon con réplicas incompletas."]
    return []


def resumen_csv():
    try:
        with open(os.path.join(DATOS, "metricas_simulacion.csv"), encoding="utf-8") as f:
            filas = list(csv.DictReader(f))
        return f"{len(filas)} filas · {len({x['escenario'] for x in filas})} escenarios × " \
               f"{len({x['interseccion'] for x in filas})} intersecciones × {len({x['controlador'] for x in filas})} controladores"
    except Exception:
        return "sin archivo"


# ---------------- corrida del Crew en segundo plano ----------------
class Corrida:
    """Una corrida por servidor: el Crew escribe en disco, así que no se ejecutan dos a la vez."""

    def __init__(self):
        self.estado, self.hechas, self.t0, self.dur = "inactiva", 0, None, 0
        self.error, self.escenario, self.motor, self.eventos = "", "pico_matutino", "stub", []
        self.candado = threading.Lock()

    def anotar(self, txt):
        self.eventos.append(f"{time.strftime('%H:%M:%S')}  {txt}")

    def iniciar(self, escenario, motor, api_key="", delegacion=False):
        if self.estado == "corriendo":
            return
        self.estado, self.hechas, self.t0, self.error = "corriendo", 0, time.time(), ""
        self.escenario, self.motor, self.eventos = escenario, motor, []
        threading.Thread(target=self._hilo, args=(escenario, motor, api_key, delegacion), daemon=True).start()

    def _hilo(self, escenario, motor, api_key, delegacion):
        try:
            os.environ["SIGMA_LLM"] = motor
            os.environ["SIGMA_DELEGACION"] = "1" if delegacion else "0"
            if api_key:
                os.environ["GEMINI_API_KEY"] = api_key
            import crewai  # noqa: F401
            sc = importlib.reload(sys.modules["sigma_crew"]) if "sigma_crew" in sys.modules else importlib.import_module("sigma_crew")
            self.anotar(f"Crew armado · motor: {motor} · escenario: {escenario}")

            def al_terminar(salida):
                self.hechas += 1
                n = min(self.hechas, len(TAREAS))
                self.anotar(f"[AGENTE] {TAREAS[n - 1][1]} terminó «{TAREAS[n - 1][0]}»")

            sc.ejecutar(escenario, al_terminar_tarea=al_terminar)
            self.hechas, self.estado = len(TAREAS), "terminada"
            self.anotar("verificacion.py revisó el reporte")
        except Exception as e:                                   # noqa: BLE001
            msg = str(e)
            if api_key:
                msg = msg.replace(api_key, "***")
            self.error, self.estado = msg[:400], "error"
            self.anotar("La corrida falló")
        finally:
            self.dur = time.time() - (self.t0 or time.time())

    def segundos(self):
        return int(time.time() - self.t0) if self.estado == "corriendo" else int(self.dur)


@st.cache_resource
def corrida() -> Corrida:
    return Corrida()


def bitacora_herramientas():
    """Llamadas a herramientas de la última corrida, tomadas del log que deja CrewAI por tarea."""
    lineas = []
    for i, (_, agente, _, _) in enumerate(TAREAS, 1):
        nombre = ["diagnostico", "propuesta_v1", "auditoria_v1", "propuesta_v2", "auditoria_v2", "sintesis"][i - 1]
        ruta = os.path.join(SALIDAS, f"tarea_{i}_{nombre}.txt")
        if os.path.exists(ruta):
            lineas.append(f"[AGENTE] {agente}\n   salida guardada en salidas/tarea_{i}_{nombre}.txt")
    return "\n".join(lineas)


# ---------------- liberación humana ----------------
def decision():
    return st.session_state.get("decision", "pendiente")


def liberar(config, quien):
    lib = dict(config, liberacion={"por": quien, "fecha": datetime.datetime.now().isoformat(timespec="seconds"),
                                   "fuente": "configuracion_aprobada.json"})
    with open(puente.RUTA_LIBERADA, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=2)
    _bitacora("liberada", quien, config.get("version", ""), "")


def rechazar(config, quien, motivo):
    _bitacora("rechazada", quien, (config or {}).get("version", ""), motivo)


def _bitacora(accion, quien, version, motivo):
    nueva = not os.path.exists(puente.RUTA_BITACORA)
    with open(puente.RUTA_BITACORA, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if nueva:
            w.writerow(["fecha", "accion", "usuario", "version", "motivo"])
        w.writerow([datetime.datetime.now().isoformat(timespec="seconds"), accion, quien, version, motivo])


def aviso_datos():
    st.caption("Telemetría sintética de la Fase 3 (semilla fija). El mapa y el simulador sí son los reales del campus.")

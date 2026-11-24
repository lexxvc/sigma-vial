# ============================================================
# SIGMA-Vial · API de la consola de supervisión táctica (Fase 4)
#
#   uvicorn api.main:app --port 8000        → http://localhost:8000
#
# Tres responsabilidades separadas, como propone la Fase 4 (§5.2–5.3):
#   lanzar el ciclo      POST /api/ciclos
#   transmitir su avance GET  /api/ciclos/{id}/eventos   (Server-Sent Events)
#   consultar/decidir    GET  /api/ciclos/{id}/resultados · POST …/ajustar · …/autorizar · …/rechazar
# Además sirve la capa de tiempo real (simulador SUMO del campus) en /api/sim/*.
# ============================================================
import asyncio
import csv
import hashlib
import io
import json
import os
import shutil
import threading
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:                                       # .env opcional (GEMINI_API_KEY, SIGMA_LLM_DEFECTO…)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except Exception:
    pass

from . import crew_runner as CR   # noqa: E402
from . import db                  # noqa: E402

H = CR.H
RAIZ = CR.RAIZ
DATOS = H.DIR_DATOS
WEB = os.path.join(RAIZ, "web")
REPORTE = os.path.join(CR.DIR_SUP, "reporte_ciclo_supervision.md")
RUTA_LIBERADA = os.path.join(CR.DIR_SUP, "datos", "configuracion_liberada.json")
CSV = DATOS / "metricas_simulacion.csv"
COLUMNAS_CSV = ["escenario", "interseccion", "controlador", "replica", "espera_veh_media_s",
                "espera_peat_media_s", "spillback_eventos_h"]
ESCENARIOS = {
    "pico_matutino": {"nombre": "Pico matutino", "franja": "07:00 – 09:00",
                      "perfil": "Entrada a clases: pulso peatonal intenso hacia Plaza Central (I3) y fila vehicular "
                                "desde el acceso principal. Es el escenario de la corrida de demostración."},
    "valle": {"nombre": "Valle", "franja": "11:00 – 13:00",
              "perfil": "Demanda baja (≈38 % vehicular y 30 % peatonal respecto al pico). Prueba que el sistema "
                        "no sobrerreacciona cuando no hay presión."},
    "evento_especial": {"nombre": "Evento especial", "franja": "18:00 – 20:00",
                        "perfil": "Graduación o partido: +18 % vehicular y +65 % peatonal. Máxima presión sobre "
                                  "los cruces peatonales."},
    "falla_sensores": {"nombre": "Falla de sensores", "franja": "07:00 – 09:00",
                       "perfil": "Demanda de pico con detectores caídos: el Analista debe distinguir el ruido "
                                 "de la telemetría de un problema real."},
}
REGLAS = [
    ("R1", "Verde vehicular", "Entre 8 s y 60 s por fase", "duras"),
    ("R2", "Ciclo máximo", "No mayor a 120 s", "duras"),
    ("R3", "Despeje peatonal", "Ancho del cruce / 1.0 m/s", "duras"),
    ("R4", "Consistencia de fases", "Verdes + ámbar 3 s + todo-rojo 2 s ≤ ciclo", "duras"),
    ("R5", "Espera peatonal máxima", "Ciclo − verde peatonal ≤ 60 s", "duras"),
    ("R0", "Esquema y completitud", "4 intersecciones, claves y rangos válidos", "complementaria"),
    ("R6", "Espera máxima por carril", "Ningún acceso espera más de 60 s", "complementaria"),
]

app = FastAPI(title="SIGMA-Vial · Consola de supervisión táctica", version="1.0")
db.iniciar()
_subida = {"nombre": None, "problemas": []}       # último archivo de telemetría que cargó el operador


# ---------------- utilidades deterministas ----------------
def leer(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def validar(config=None, archivo=None):
    """Veredicto literal de validador_reglas_duras (código de la Fase 3, sin LLM)."""
    if config is not None:
        return json.loads(H.ValidadorReglasDuras()._run(propuesta_json=json.dumps(config)))
    return json.loads(H.ValidadorReglasDuras()._run(archivo=archivo))


def dictamen_por_regla(d):
    out = []
    for r, nombre, desc, grupo in REGLAS:
        viol = [v for v in d.get("violaciones", []) if f"({r}" in v]
        out.append({"regla": r, "nombre": nombre, "descripcion": desc, "grupo": grupo,
                    "cumple": not viol, "violaciones": viol})
    return out


def comparar(escenario, metrica):
    try:
        return json.loads(H.ComparadorLineasBase()._run(escenario=escenario, metrica=metrica))["por_interseccion"]
    except Exception:
        return {}


def validar_csv(texto):
    try:
        filas = list(csv.DictReader(io.StringIO(texto)))
    except Exception:
        return ["El archivo no se pudo leer como CSV en UTF-8."]
    if not filas:
        return ["El archivo está vacío."]
    faltan = [c for c in COLUMNAS_CSV if c not in filas[0]]
    if faltan:
        return [f"Formato no reconocido. Se esperan las columnas {', '.join(COLUMNAS_CSV)}; "
                f"faltan: {', '.join(faltan)}."]
    grupos = {}
    for f in filas:
        grupos.setdefault((f["escenario"], f["interseccion"], f["controlador"]), set()).add(f["replica"])
    cortos = [g for g, r in grupos.items() if len(r) < 10]
    if cortos:
        e, i, c = cortos[0]
        return [f"{len(cortos)} combinaciones tienen menos de 10 réplicas (p. ej. {e} · {i} · {c}). "
                "La prueba de Wilcoxon pareada requiere 10."]
    return []


def candidata(c: CR.Ciclo | None):
    if c is not None and c.ajuste:
        return c.ajuste
    return leer(DATOS / "configuracion_aprobada.json")


def ciclo(ciclo_id) -> CR.Ciclo:
    c = CR.CICLOS.get(ciclo_id)
    if c is None:
        raise HTTPException(404, "Ciclo no encontrado")
    return c


def plan_txt(p):
    return f"{p['verde_principal_s']} / {p['verde_secundario_s']} / {p['verde_peatonal_s']}"


# ---------------- estado del sistema y fuentes (vista 1) ----------------
@app.get("/api/sistema")
def sistema():
    filas = []
    try:
        with open(CSV, encoding="utf-8") as f:
            filas = list(csv.DictReader(f))
    except Exception:
        pass
    norma_txt = (DATOS / "normativa.json").read_bytes()
    topo = leer(DATOS / "topologia_circuito.json") or {}
    act = CR.ACTUAL
    fuentes = [
        {"archivo": "metricas_simulacion.csv", "ok": bool(filas), "kb": round(CSV.stat().st_size / 1024, 1),
         "detalle": f"{len(filas)} filas · {len({x['escenario'] for x in filas})} escenarios · "
                    f"{len({x['controlador'] for x in filas})} controladores · 10 réplicas · telemetría sintética"},
        {"archivo": "normativa.json", "ok": True, "kb": round(len(norma_txt) / 1024, 1),
         "detalle": f"R0–R6 activas · suma {hashlib.sha256(norma_txt).hexdigest()[:6]}"},
    ]
    if _subida["nombre"]:
        fuentes.append({"archivo": _subida["nombre"], "ok": not _subida["problemas"], "subida": True,
                        "detalle": " ".join(_subida["problemas"]) or "Validado y en uso para el siguiente ciclo"})
    return {"escenarios": ESCENARIOS, "reglas": [dict(zip(("regla", "nombre", "descripcion", "grupo"), r)) for r in REGLAS],
            "normativa": leer(DATOS / "normativa.json"), "fuentes": fuentes,
            "listo": all(f["ok"] for f in fuentes),
            "intersecciones": {k: v["nombre"] for k, v in topo.get("intersecciones", {}).items()},
            "motores": {"gemini": bool(os.getenv("GEMINI_API_KEY")), "stub": True,
                        "ollama": bool(os.getenv("OLLAMA_HOST"))},
            "motor_defecto": os.getenv("SIGMA_LLM_DEFECTO", "gemini" if os.getenv("GEMINI_API_KEY") else "stub"),
            "ciclo_actual": act.resumen() if act else None}


class Subida(BaseModel):
    nombre: str
    contenido: str


@app.post("/api/fuentes")
def subir_fuente(s: Subida):
    problemas = validar_csv(s.contenido)
    _subida.update(nombre=s.nombre, problemas=problemas)
    if not problemas:
        respaldo = CSV.with_suffix(".csv.original")
        if not respaldo.exists():
            shutil.copy(CSV, respaldo)
        CSV.write_text(s.contenido, encoding="utf-8")
    return {"ok": not problemas, "problemas": problemas}


@app.post("/api/fuentes/restablecer")
def restablecer_fuentes():
    respaldo = CSV.with_suffix(".csv.original")
    if respaldo.exists():
        shutil.copy(respaldo, CSV)
    _subida.update(nombre=None, problemas=[])
    return {"ok": True}


# ---------------- ciclo de supervisión (vista 2) ----------------
class ConfiguracionCiclo(BaseModel):
    escenario: str = "pico_matutino"
    motor: str = Field("stub", pattern="^(stub|gemini|ollama|openai)$")


@app.post("/api/ciclos")
def iniciar_ciclo(cfg: ConfiguracionCiclo):
    """Lanza el ciclo y devuelve de inmediato su identificador."""
    if cfg.escenario not in ESCENARIOS:
        raise HTTPException(422, "Escenario desconocido")
    if _subida["problemas"]:
        raise HTTPException(422, "Hay una fuente de telemetría que no valida: corrígela antes de iniciar")
    try:
        c = CR.iniciar(cfg.escenario, cfg.motor)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ciclo_id": c.id, "numero": c.numero, "estado": c.estado}


@app.get("/api/ciclos/{ciclo_id}")
def estado_ciclo(ciclo_id: str):
    return ciclo(ciclo_id).resumen()


@app.post("/api/ciclos/{ciclo_id}/detener")
def detener(ciclo_id: str):
    c = ciclo(ciclo_id)
    c.detener = True
    c.anotar("sistema", "OPERADOR", "Solicitud de detener el ciclo; se aplica en el siguiente paso del Crew")
    return {"ok": True}


@app.get("/api/ciclos/{ciclo_id}/eventos")
async def eventos(ciclo_id: str, desde: int = 0):
    """Transmite el avance del Crew conforme ocurre (Server-Sent Events)."""
    c = ciclo(ciclo_id)

    async def flujo():
        enviados = set(range(1, desde + 1))
        while True:
            # Los oyentes de CrewAI corren en un pool de hilos: se ordena por marca de
            # tiempo y se espera un instante para no emitir fuera de orden.
            limite = time.time() - 0.3 if c.estado == "en_ejecucion" else float("inf")
            listos = sorted((e for e in list(c.eventos) if e["seq"] not in enviados and e["t"] <= limite),
                            key=lambda e: (e["t"], e["seq"]))
            for e in listos:
                enviados.add(e["seq"])
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
            yield f"event: estado\ndata: {json.dumps(c.resumen(), ensure_ascii=False)}\n\n"
            if c.estado != "en_ejecucion" and len(enviados) >= len(c.eventos):
                yield "event: fin\ndata: {}\n\n"
                return
            await asyncio.sleep(0.4)

    return StreamingResponse(flujo(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------- resultados y decisión humana (vista 3) ----------------
@app.get("/api/ciclos/{ciclo_id}/resultados")
def resultados(ciclo_id: str):
    c = ciclo(ciclo_id)
    if c.estado == "en_ejecucion":
        raise HTTPException(409, "El ciclo sigue en ejecución")
    cand, vig = candidata(c), leer(DATOS / "configuracion_vigente.json")
    if cand is None:
        raise HTTPException(404, "El ciclo no produjo una configuración publicable")
    d_cand, d_vig = validar(config=cand), validar(archivo="vigente")
    peat, veh = comparar(c.escenario, "espera_peat_media_s"), comparar(c.escenario, "espera_veh_media_s")
    spill = comparar(c.escenario, "spillback_eventos_h")
    topo = (leer(DATOS / "topologia_circuito.json") or {}).get("intersecciones", {})
    filas = []
    for k in ["I1", "I2", "I3", "I4"]:
        pv, pc = vig["planes_por_interseccion"][k], cand["planes_por_interseccion"][k]
        fp = peat.get(k, {}).get("plan_fijo_webster", {})
        fv = veh.get(k, {}).get("plan_fijo_webster", {})
        viol_v = [v for v in d_vig["violaciones"] if v.startswith(k + ":")]
        filas.append({"id": k, "nombre": topo.get(k, {}).get("nombre", k),
                      "ancho_cruce_m": topo.get(k, {}).get("ancho_cruce_m"),
                      "vigente": plan_txt(pv) + f" · ciclo {pv['ciclo_s']} s",
                      "propuesto": plan_txt(pc) + f" · ciclo {pc['ciclo_s']} s",
                      "peat": fp, "veh": fv, "reglas_vigente": sorted({CR.parsear_violacion(v)[2] for v in viol_v})})
    media = lambda dic, campo: round(sum(v.get("plan_fijo_webster", {}).get(campo, 0) for v in dic.values()) /
                                     max(1, len(dic)), 1)
    ciclos_c = sorted({p["ciclo_s"] for p in cand["planes_por_interseccion"].values()})
    ciclos_v = sorted({p["ciclo_s"] for p in vig["planes_por_interseccion"].values()})
    i3 = cand["planes_por_interseccion"]["I3"]
    perdidas = 3 * ((leer(DATOS / "normativa.json") or {}).get("ambar_s", 3) + 2)
    por_regla = dictamen_por_regla(d_cand)
    hallazgo = [v for v in d_vig["violaciones"] if "(R3" in v]
    return {
        "ciclo": c.resumen(), "version": cand.get("version"), "huella": CR.huella(cand), "ajustada": bool(c.ajuste),
        "configuracion": cand, "vigente": vig, "dictamen": d_cand, "por_regla": por_regla,
        "duras_cumplidas": sum(r["cumple"] for r in por_regla if r["grupo"] == "duras"),
        "hallazgo_vigente": {"violaciones": d_vig["violaciones"], "r3": hallazgo},
        "kpi": {"espera_peat_sigma": media(peat, "media_sigma"), "espera_peat_fijo": media(peat, "media_base"),
                "spill_sigma": media(spill, "media_sigma"), "spill_fijo": media(spill, "media_base"),
                "ciclo_comun": ciclos_c[0] if len(ciclos_c) == 1 else None, "ciclos_antes": ciclos_v,
                "desfases": [p.get("desfase_s", 0) for p in cand["planes_por_interseccion"].values()]},
        "reparto_i3": {"principal": i3["verde_principal_s"], "secundario": i3["verde_secundario_s"],
                       "peatonal": i3["verde_peatonal_s"], "perdidas": perdidas, "ciclo": i3["ciclo_s"]},
        "comparativa": filas, "justificacion": cand.get("justificacion", {}),
        "liberada": leer(RUTA_LIBERADA),
    }


class Ajuste(BaseModel):
    planes_por_interseccion: dict
    operador: str = "Operador en turno"


@app.post("/api/ciclos/{ciclo_id}/ajustar")
def ajustar(ciclo_id: str, a: Ajuste):
    """El operador modifica la propuesta; el validador decide si la acepta (no el operador)."""
    c = ciclo(ciclo_id)
    base = json.loads(json.dumps(candidata(c)))
    for k, p in a.planes_por_interseccion.items():
        if k in base["planes_por_interseccion"]:
            base["planes_por_interseccion"][k].update({x: int(v) for x, v in p.items()})
    base["version"] = base.get("version", "v").split("+")[0] + "+ajuste-operador"
    d = validar(config=base)
    ts = time.strftime("%H:%M:%S")
    if d["veredicto"] != "APROBADO":
        for v in d["violaciones"]:
            inter, param, regla = CR.parsear_violacion(v)
            db.ejecutar("INSERT INTO vetos VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (c.id, c.numero, ts, "Ajuste humano", "ajuste", inter, "Operador", regla, param,
                         "Veto automático"))
        c.anotar("veto", "VALIDADOR", f"Ajuste del operador rechazado: {len(d['violaciones'])} violaciones", d)
        raise HTTPException(422, {"mensaje": "El ajuste no pasa las reglas duras", "violaciones": d["violaciones"]})
    c.ajuste = base
    db.ejecutar("INSERT INTO decisiones VALUES (?,?,?,?,?,?,?,?)",
                (c.id, c.numero, ts, "ajuste", a.operador, base["version"], CR.huella(base), ""))
    c.anotar("aprobado", "VALIDADOR", "Ajuste del operador revalidado: APROBADO")
    return {"ok": True, "huella": CR.huella(base), "version": base["version"]}


class Autorizacion(BaseModel):
    version_revisada: str
    operador: str = "Operador en turno"


@app.post("/api/ciclos/{ciclo_id}/autorizar")
def autorizar(ciclo_id: str, cuerpo: Autorizacion):
    """Acción irreversible: publica hacia la capa de tiempo real. Tercera barrera de validación."""
    c = ciclo(ciclo_id)
    if c.estado != "terminado":
        raise HTTPException(409, "El ciclo no ha terminado")
    cand = candidata(c)
    if cand is None or CR.huella(cand) != cuerpo.version_revisada:
        raise HTTPException(409, "La propuesta cambió desde que se revisó. Vuelve a revisarla antes de autorizar.")
    d = validar(config=cand)                      # tercera barrera: no se confía en el botón del navegador
    if d["veredicto"] != "APROBADO":
        raise HTTPException(422, {"mensaje": "La propuesta no pasa las reglas duras", "violaciones": d["violaciones"]})
    ts = CR.ahora_iso()
    lib = dict(cand, liberacion={"por": cuerpo.operador, "fecha": ts, "ciclo": c.numero,
                                 "huella": cuerpo.version_revisada})
    with open(RUTA_LIBERADA, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=2)
    db.ejecutar("INSERT INTO decisiones VALUES (?,?,?,?,?,?,?,?)",
                (c.id, c.numero, ts, "autorizada", cuerpo.operador, cand.get("version"), cuerpo.version_revisada, ""))
    c.decision = {"accion": "autorizada", "operador": cuerpo.operador, "fecha": ts}
    c.anotar("publicado", "OPERADOR", f"{cuerpo.operador} autorizó {cand.get('version')} → configuracion_liberada.json")
    return {"publicado": True, "archivo": "configuracion_liberada.json", "version": cand.get("version"),
            "huella": cuerpo.version_revisada, "fecha": ts}


class Rechazo(BaseModel):
    motivo: str = ""
    operador: str = "Operador en turno"


@app.post("/api/ciclos/{ciclo_id}/rechazar")
def rechazar(ciclo_id: str, r: Rechazo):
    c = ciclo(ciclo_id)
    cand = candidata(c) or {}
    ts = CR.ahora_iso()
    db.ejecutar("INSERT INTO decisiones VALUES (?,?,?,?,?,?,?,?)",
                (c.id, c.numero, ts, "rechazada", r.operador, cand.get("version", ""), CR.huella(cand), r.motivo))
    c.decision = {"accion": "rechazada", "operador": r.operador, "fecha": ts, "motivo": r.motivo}
    c.anotar("veto", "OPERADOR", f"{r.operador} rechazó la propuesta: {r.motivo or 'sin motivo'}")
    return {"ok": True}


@app.get("/api/ciclos/{ciclo_id}/reporte")
def reporte(ciclo_id: str):
    ciclo(ciclo_id)
    if not os.path.exists(REPORTE):
        raise HTTPException(404, "Sin reporte")
    return FileResponse(REPORTE, media_type="text/markdown", filename="reporte_ciclo_supervision.md")


# ---------------- auditoría (vista 4) ----------------
@app.get("/api/bitacora")
def bitacora():
    return {"vetos": db.consultar("SELECT * FROM vetos ORDER BY rowid DESC LIMIT 200"),
            "decisiones": db.consultar("SELECT * FROM decisiones ORDER BY rowid DESC LIMIT 100"),
            "ciclos": db.consultar("SELECT * FROM ciclos ORDER BY rowid DESC LIMIT 30")}


@app.get("/api/bitacora.csv")
def bitacora_csv():
    filas = db.consultar("SELECT * FROM vetos ORDER BY rowid DESC")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["hora", "ciclo", "tarea", "archivo", "interseccion", "agente", "regla", "parametro", "resultado"])
    for f in filas:
        w.writerow([f["ts"], f["numero"], f["tarea"], f["archivo"], f["interseccion"], f["agente"], f["regla"],
                    f["parametro"], f["resultado"]])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=bitacora_vetos.csv"})


# ---------------- capa de tiempo real: simulador SUMO del campus ----------------
_sim = {"motor": None, "geo": None, "error": "", "iniciando": False}


def _arrancar_sim():
    if _sim["motor"] is not None or _sim["iniciando"]:
        return
    _sim["iniciando"] = True

    def hilo():
        try:
            import sys
            sys.path.insert(0, os.path.join(RAIZ, "app"))
            from motor import Motor
            m = Motor(etiqueta="consola")
            _sim["geo"] = m.geometria()
            threading.Thread(target=m.bucle, daemon=True).start()
            _sim["motor"] = m
        except Exception as e:                   # noqa: BLE001
            _sim["error"] = str(e)[:300]
        finally:
            _sim["iniciando"] = False
    threading.Thread(target=hilo, daemon=True).start()


@app.on_event("startup")
def _al_iniciar():
    if os.getenv("SIGMA_SIN_SIM") != "1":
        _arrancar_sim()


@app.get("/api/sim/red")
def sim_red():
    _arrancar_sim()
    if _sim["geo"] is None:
        raise HTTPException(503, _sim["error"] or "El simulador está arrancando")
    return _sim["geo"]


@app.get("/api/sim/estado")
def sim_estado():
    m = _sim["motor"]
    if m is None:
        raise HTTPException(503, _sim["error"] or "El simulador está arrancando")
    with m.lock:
        return m.estado


class Comando(BaseModel):
    accion: str
    acceso: str | None = None
    destino: str | None = None
    interseccion: str | None = None
    n: int = 1
    valor: float | None = None
    nombre: str | None = None


@app.post("/api/sim/cmd")
def sim_cmd(c: Comando):
    m = _sim["motor"]
    if m is None:
        raise HTTPException(503, "El simulador está arrancando")
    args = {"autos": (c.acceso, c.n), "ambulancia": (c.acceso, c.destino), "velocidad": (c.valor,),
            "demanda": (c.valor,), "preempcion": (c.valor,), "plan": (c.nombre,), "rafaga": (c.n or 30,),
            "todo_rojo": (c.interseccion, c.valor), "extender": (c.interseccion, c.valor)}.get(c.accion, ())
    m.cmds.put((c.accion, *args))
    return {"ok": True}


# ---------------- interfaz ----------------
@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


app.mount("/web", StaticFiles(directory=WEB), name="web")

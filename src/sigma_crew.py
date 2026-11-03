# ============================================================
# SIGMA-Vial · Capa deliberativa multiagente (CrewAI)
# 4 agentes: Analista de Tráfico → Planificador de Fases →
#            Coordinador del Circuito → Supervisor de Seguridad
# ============================================================
import json
from typing import List, Optional
from pydantic import BaseModel, Field
from crewai import Agent, Task, Crew, Process
from crewai.tools import tool
from sigma_sim import REGLAS, SAT_FLOW

FOTO: dict = {}          # estado del circuito que entrega SUMO (se asigna antes de correr el Crew)
HORIZONTE_S = 180        # horizonte de planeación (≈ 2-3 ciclos)
PESOS = {"veh": 1.0, "ped": 1.0, "esp_veh": 0.02, "esp_ped": 0.03, "aguas_abajo": 0.5}


def set_foto(f):
    FOTO.clear(); FOTO.update(f)


def _ciclo(verdes):
    return sum(verdes) + len(verdes) * (REGLAS["ambar_s"] + REGLAS["todo_rojo_s"])


def _filas(i):
    """Fila por carril; si un sensor falla se usa el promedio de los demás carriles (modo degradado)."""
    carriles = FOTO["intersecciones"][i]["carriles"]
    vivos = [c["vehiculos_detenidos"] for c in carriles.values() if c]
    prom = round(sum(vivos) / len(vivos), 1) if vivos else 3.0
    return {ln: ({"fila": c["vehiculos_detenidos"], "estado": "ok"} if c else
                 {"fila": prom, "estado": "degradado", "nota": "sin lectura; promedio histórico, incertidumbre alta"})
            for ln, c in carriles.items()}


# ---------------- Herramientas (tools) ----------------
@tool("leer_estado_interseccion")
def leer_estado_interseccion(interseccion: str) -> str:
    """Devuelve las lecturas crudas de sensores de una intersección (I1, I2, I3 o I4):
    vehículos detenidos, ocupación y espera por carril, peatones esperando, fases y verdes actuales.
    Un carril con valor null tiene el sensor en falla."""
    return json.dumps(FOTO["intersecciones"].get(interseccion.strip(), {"error": "id no válido"}), ensure_ascii=False)


@tool("estimar_fila")
def estimar_fila(interseccion: str) -> str:
    """Estima la fila de cada carril de una intersección (I1-I4). Marca como 'degradado' el carril
    cuyo sensor falló y le asigna un valor de respaldo con alta incertidumbre."""
    i = interseccion.strip()
    return json.dumps({"interseccion": i, "filas": _filas(i),
                       "fases_verde": FOTO["intersecciones"][i]["fases_verde"]}, ensure_ascii=False)


@tool("calcular_utilidad")
def calcular_utilidad(interseccion: str, verdes_s: List[int]) -> str:
    """Calcula la utilidad esperada U de un plan de verdes (lista de segundos, uno por fase verde)
    para una intersección en un horizonte de 180 s:
    U = vehículos que cruzan + peatones atendidos − penalización espera vehicular
        − penalización espera peatonal − penalización por ocupación aguas abajo."""
    i = interseccion.strip(); d = FOTO["intersecciones"][i]
    if len(verdes_s) != d["num_fases_verde"]:
        return json.dumps({"error": f"{i} tiene {d['num_fases_verde']} fases verdes; diste {len(verdes_s)}"})
    filas, C = _filas(i), _ciclo(verdes_s)
    ciclos = HORIZONTE_S / C
    cruzan = esp_veh = 0.0
    for f, g in zip(d["fases_verde"], verdes_s):
        q = sum(filas[ln]["fila"] for ln in f["carriles_servidos"] if ln in filas)
        cap = g * SAT_FLOW / 3600 * max(1, len(f["carriles_servidos"])) * ciclos
        cruzan += min(q * 1.5, cap)                      # demanda en el horizonte ≈ 1.5 × fila actual
        esp_veh += q * (C - g) / 2                       # espera promedio en rojo
    ped = d["peatones_esperando"]
    esp_ped = ped * C / 2
    abajo = max([t["ocupacion_pct"] for t in FOTO["tramos_compartidos"].values()] or [0])
    U = (PESOS["veh"] * cruzan + PESOS["ped"] * (ped if C / 2 <= REGLAS["espera_max_s"] else 0)
         - PESOS["esp_veh"] * esp_veh - PESOS["esp_ped"] * esp_ped - PESOS["aguas_abajo"] * abajo / 10)
    return json.dumps({"interseccion": i, "verdes_s": verdes_s, "ciclo_s": C, "utilidad": round(U, 2),
                       "veh_que_cruzan": round(cruzan, 1), "espera_peatonal_media_s": C / 2})


@tool("consultar_calendario")
def consultar_calendario(hora: str) -> str:
    """Devuelve el régimen de demanda según el calendario académico para una hora 'HH:MM'
    (pico por cambio de clase, valle o salida)."""
    h, m = map(int, hora.strip().split(":")); t = h * 60 + m
    picos = [(7 * 60, 7 * 60 + 40, "pico_entrada"), (8 * 60 + 50, 9 * 60 + 10, "pico_cambio_clase"),
             (10 * 60 + 50, 11 * 60 + 10, "pico_cambio_clase"), (14 * 60, 14 * 60 + 30, "pico_salida")]
    reg = next((n for a, b, n in picos if a <= t <= b), "valle")
    return json.dumps({"hora": hora, "regimen": reg,
                       "recomendacion": "ciclos cortos y alta frecuencia peatonal" if reg != "valle" else "plan base"})


@tool("ocupacion_tramos")
def ocupacion_tramos() -> str:
    """Devuelve la ocupación (%) de los tramos que conectan intersecciones del circuito y alerta de spillback
    cuando supera 70 %. También informa vehículos de emergencia aproximándose."""
    t = {k: dict(v, alerta_spillback=v["ocupacion_pct"] > 70) for k, v in FOTO["tramos_compartidos"].items()}
    return json.dumps({"tramos": t, "emergencia": FOTO.get("emergencia")}, ensure_ascii=False)


@tool("validar_reglas")
def validar_reglas(plan_json: str) -> str:
    """Valida de forma determinista un plan completo contra las reglas duras de seguridad.
    Entrada: JSON {"I1": [verdes...], "I2": [...], "I3": [...], "I4": [...]}.
    Reglas: verde 8-60 s, ámbar 3 s, todo-rojo 2 s, ciclo ≤ 120 s, espera peatonal ≤ 60 s,
    número de fases correcto. Devuelve violaciones y un plan corregido."""
    plan = json.loads(plan_json) if isinstance(plan_json, str) else plan_json
    viol, corregido = [], {}
    for i, d in FOTO["intersecciones"].items():
        v = plan.get(i)
        if not v or len(v) != d["num_fases_verde"]:
            viol.append(f"{i}: número de fases incorrecto o faltante → se usa plan fijo")
            corregido[i] = d["verdes_actuales_s"]; continue
        v2 = [min(max(int(g), REGLAS["verde_min_s"]), REGLAS["verde_max_s"]) for g in v]
        if v2 != list(v):
            viol.append(f"{i}: verdes fuera de [8, 60] s → recortados a {v2}")
        while _ciclo(v2) > REGLAS["ciclo_max_s"]:
            k = v2.index(max(v2)); v2[k] -= 1
        if _ciclo(v2) / 2 > REGLAS["espera_max_s"]:
            viol.append(f"{i}: espera peatonal > 60 s")
        if _ciclo(v) > REGLAS["ciclo_max_s"]:
            viol.append(f"{i}: ciclo {_ciclo(v)} s > 120 s → ajustado a {_ciclo(v2)} s")
        corregido[i] = v2
    return json.dumps({"aprobado_sin_cambios": not viol, "violaciones": viol, "plan_corregido": corregido,
                       "nota": "ámbar 3 s y todo-rojo 2 s se insertan automáticamente en cada cambio de fase"},
                      ensure_ascii=False)


# ---------------- Salida estructurada del Crew ----------------
class PlanInterseccion(BaseModel):
    interseccion: str = Field(description="I1, I2, I3 o I4")
    verdes_s: List[int] = Field(description="duración en s de cada fase verde, en orden")
    justificacion: str


class PlanFinal(BaseModel):
    aprobado: bool
    planes: List[PlanInterseccion]
    vetos_o_correcciones: List[str]
    prioridad_emergencia: Optional[str] = None
    reporte_markdown: str = Field(description="resumen ejecutivo del plan para el operador")


# ---------------- Agentes ----------------
def crear_crew(llm, verbose=True, log="sigma_crew_log.txt"):
    analista = Agent(
        role="Analista de Tráfico del campus",
        goal="Estimar con la mayor precisión posible las filas y la demanda vehicular y peatonal de cada "
             "intersección, identificando sensores degradados y su incertidumbre.",
        backstory="Ingeniero de tránsito con años operando detectores de lazo y cámaras. Desconfía de las "
                  "lecturas aisladas: siempre contrasta carriles y marca lo que no puede observar. "
                  "No propone tiempos de semáforo; su trabajo es describir el estado del mundo.",
        tools=[leer_estado_interseccion, estimar_fila], allow_delegation=False, llm=llm, verbose=verbose)

    planificador = Agent(
        role="Planificador de Fases (Controlador basado en utilidad)",
        goal="Proponer para cada intersección la duración de sus fases verdes que maximice la utilidad "
             "esperada U, dando servicio suficiente a peatones.",
        backstory="Especialista en control adaptativo de semáforos inspirado en SCATS y en control por máxima "
                  "presión. Compara al menos tres alternativas por intersección con la herramienta de utilidad "
                  "y elige la mejor. Sabe que los ciclos largos castigan a los peatones.",
        tools=[calcular_utilidad, estimar_fila], allow_delegation=False, llm=llm, verbose=verbose)

    coordinador = Agent(
        role="Coordinador del Circuito",
        goal="Ajustar los planes locales para que el circuito funcione como un todo: evitar spillback entre "
             "intersecciones, aplicar el régimen del calendario académico y dar paso a emergencias.",
        backstory="Operador del centro de control que ve todo el circuito. Sabe que lo que una intersección "
                  "suelta, la siguiente lo recibe. Si un tramo se satura, pide al Planificador recortar el "
                  "verde aguas arriba.",
        tools=[consultar_calendario, ocupacion_tramos, calcular_utilidad],
        allow_delegation=True, llm=llm, verbose=verbose)   # puede delegar recálculos al Planificador

    supervisor = Agent(
        role="Supervisor de Seguridad",
        goal="Garantizar que ningún plan viole las reglas duras de seguridad; vetar o corregir lo que no cumpla "
             "y emitir el plan final aprobado.",
        backstory="Auditor de seguridad vial, deliberadamente conservador. No optimiza: verifica. Toda su "
                  "decisión se apoya en el resultado de la herramienta validar_reglas, nunca en su intuición.",
        tools=[validar_reglas], allow_delegation=False, llm=llm, verbose=verbose)

    # ---------------- Tareas ----------------
    t1 = Task(
        description="Hora {hora}. Para cada intersección I1, I2, I3 e I4 usa estimar_fila (y si hace falta "
                    "leer_estado_interseccion) y reporta la fila por fase verde, los peatones esperando, los "
                    "carriles degradados y el nivel de congestión (bajo/medio/alto).",
        expected_output="JSON con una entrada por intersección: {fila_por_fase: [...], peatones, "
                        "carriles_degradados: [...], congestion}.",
        agent=analista)
    t2 = Task(
        description="Con la estimación del Analista, para cada intersección evalúa con calcular_utilidad al menos "
                    "3 planes de verdes distintos (respetando el número de fases verdes de cada una) y elige el "
                    "de mayor U. Recuerda: verdes entre 8 y 60 s.",
        expected_output="JSON {I1: {verdes_s, utilidad, ciclo_s}, I2: ..., I3: ..., I4: ...} con las "
                        "alternativas evaluadas y la elegida.",
        agent=planificador, context=[t1])
    t3 = Task(
        description="Consulta el calendario para la hora {hora} y la ocupación de tramos. Ajusta los planes del "
                    "Planificador para evitar spillback y da prioridad al vehículo de emergencia reportado "
                    "(más verde en la fase que lo atiende en su acceso y su destino).",
        expected_output="JSON con el plan coordinado {I1: [verdes], I2: [...], I3: [...], I4: [...]} y la "
                        "lista de ajustes aplicados con su razón.",
        agent=coordinador, context=[t1, t2])
    t4 = Task(
        description="Valida el plan coordinado con validar_reglas pasando un JSON {\"I1\": [...], ...}. Si hay "
                    "violaciones, usa el plan_corregido. Emite el plan final.",
        expected_output="Plan final estructurado con aprobado, planes por intersección, vetos o correcciones, "
                        "prioridad de emergencia y un reporte en Markdown para el operador.",
        agent=supervisor, context=[t3], output_pydantic=PlanFinal)

    return Crew(agents=[analista, planificador, coordinador, supervisor], tasks=[t1, t2, t3, t4],
                process=Process.sequential, verbose=verbose, output_log_file=log,
                max_rpm=10)  # límite de llamadas/min para no saturar el plan gratuito del LLM

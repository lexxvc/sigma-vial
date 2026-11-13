"""
SIGMA-Vial — Fase 3
Herramientas personalizadas (tools) del Crew.

Principio de diseno: TODA herramienta es determinista y esta escrita en Python
puro. El LLM decide QUE herramienta invocar y con que argumentos, e interpreta
el resultado; nunca calcula una metrica ni juzga el cumplimiento de una regla
dura por su cuenta. Esto traslada al mundo de CrewAI el mismo principio del
Supervisor de la Fase 2: lo verificable no se delega al razonamiento.
"""

import json
import math
from pathlib import Path
from typing import Optional, Type

import pandas as pd
from pydantic import BaseModel, Field
from scipy.stats import wilcoxon

from crewai.tools import BaseTool

DIR_DATOS = Path(__file__).parent / "datos"
DIR_SALIDAS = Path(__file__).parent / "salidas"

_METRICAS = ["espera_veh_media_s", "espera_veh_p95_s", "espera_peat_media_s",
             "espera_peat_p95_s", "cola_max_veh", "spillback_eventos_h",
             "paradas_por_veh", "veh_atendidos_h", "peat_atendidos_h", "co2_kg_h"]

# Subconjunto que devuelve el lector de telemetria si no se piden metricas
# concretas. Mantiene la salida corta: un modelo local de ~8B pierde el hilo
# con salidas de miles de tokens.
_METRICAS_CLAVE = ["espera_veh_media_s", "espera_peat_media_s",
                   "spillback_eventos_h", "veh_atendidos_h"]

# Propuestas y configuracion que las herramientas de auditoria leen por nombre
# en lugar de recibirlas copiadas por el LLM (ver _resolver_propuesta).
_FUENTES = {
    "vigente": DIR_DATOS / "configuracion_vigente.json",
    "propuesta_v1": DIR_SALIDAS / "propuesta_v1.json",
    "propuesta_v2": DIR_SALIDAS / "propuesta_v2.json",
}


def _cargar_metricas() -> pd.DataFrame:
    return pd.read_csv(DIR_DATOS / "metricas_simulacion.csv")


def _json(obj, compacto: bool = False) -> str:
    return json.dumps(obj, ensure_ascii=False,
                      indent=None if compacto else 2,
                      separators=(",", ":") if compacto else None)


def _extraer_json(texto: str) -> str:
    """Devuelve el primer objeto JSON completo dentro de `texto`.

    La salida de una tarea del Crew es texto libre: un LLM puede envolver el
    JSON en una cerca de codigo markdown o anteponer una frase. Se localiza el
    primer '{' y se decodifica desde ahi; json lanza ValueError si no hay uno
    valido.
    """
    inicio = texto.find("{")
    if inicio < 0:
        raise ValueError("no se encontro un objeto JSON")
    obj, _ = json.JSONDecoder().raw_decode(texto[inicio:])
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _resolver_propuesta(archivo: Optional[str],
                        propuesta_json: Optional[str]) -> str:
    """Obtiene el texto JSON a validar o publicar.

    Se prefiere `archivo` (vigente | propuesta_v1 | propuesta_v2): la
    herramienta lee lo que el Ingeniero realmente produjo, en vez de una copia
    retipeada por el LLM del Auditor. Asi el dictamen recae sobre el artefacto
    exacto y un modelo pequeno no tiene que reproducir 60 lineas de JSON sin
    errores. `propuesta_json` queda como alternativa explicita.
    """
    if archivo:
        if archivo not in _FUENTES:
            raise ValueError(f"archivo invalido '{archivo}'. "
                             f"Validos: {list(_FUENTES)}")
        ruta = _FUENTES[archivo]
        if not ruta.exists():
            raise ValueError(f"'{archivo}' aun no existe: la tarea que lo "
                             f"produce no se ha ejecutado.")
        return _extraer_json(ruta.read_text(encoding="utf-8"))
    if propuesta_json:
        return _extraer_json(propuesta_json)
    raise ValueError("indica 'archivo' o 'propuesta_json'")


# --------------------------------------------------------------------------
# 1. Lector de telemetria
# --------------------------------------------------------------------------
class EntradaTelemetria(BaseModel):
    escenario: str = Field(
        description="pico_matutino | valle | evento_especial | falla_sensores")
    interseccion: Optional[str] = Field(
        default=None, description="I1..I4. Omitir para ver todas.")
    metricas: Optional[list[str]] = Field(
        default=None,
        description=("Metricas a incluir. Omitir para las cuatro principales: "
                     + ", ".join(_METRICAS_CLAVE)))


class LectorTelemetriaSUMO(BaseTool):
    name: str = "lector_telemetria_sumo"
    description: str = (
        "Lee la telemetria de la simulacion y devuelve, por interseccion y por "
        "controlador, la media y la desviacion estandar de las metricas sobre "
        "las 10 replicas. Usar para diagnosticar donde esta el problema. "
        "Argumentos: escenario (obligatorio), interseccion (opcional), "
        "metricas (opcional; por omision las cuatro principales)."
    )
    args_schema: Type[BaseModel] = EntradaTelemetria

    def _run(self, escenario: str, interseccion: Optional[str] = None,
             metricas: Optional[list[str]] = None) -> str:
        metricas = metricas or _METRICAS_CLAVE
        invalidas = [m for m in metricas if m not in _METRICAS]
        if invalidas:
            return f"ERROR: metricas invalidas {invalidas}. Validas: {_METRICAS}"
        df = _cargar_metricas()
        df = df[df["escenario"] == escenario]
        if df.empty:
            return f"ERROR: escenario '{escenario}' no existe."
        if interseccion:
            df = df[df["interseccion"] == interseccion]
            if df.empty:
                return f"ERROR: interseccion '{interseccion}' no existe."

        g = df.groupby(["interseccion", "controlador"])[metricas]
        resumen = g.agg(["mean", "std"]).round(2)

        salida = {"escenario": escenario, "replicas_por_celda": 10, "datos": {}}
        for (inter, ctrl), fila in resumen.iterrows():
            salida["datos"].setdefault(inter, {})[ctrl] = {
                m: {"media": float(fila[(m, "mean")]),
                    "desv": float(fila[(m, "std")])}
                for m in metricas
            }
        return _json(salida, compacto=True)


# --------------------------------------------------------------------------
# 2. Comparador contra las lineas base
# --------------------------------------------------------------------------
class EntradaComparacion(BaseModel):
    escenario: str = Field(description="Escenario a evaluar.")
    metrica: str = Field(
        description=f"Una de: {', '.join(_METRICAS)}")


class ComparadorLineasBase(BaseTool):
    name: str = "comparador_lineas_base"
    description: str = (
        "Compara SIGMA-Vial contra las dos lineas base (plan_fijo_webster y "
        "maxima_presion) para una metrica, por interseccion. Devuelve la "
        "variacion porcentual y el valor p de una prueba de Wilcoxon pareada "
        "sobre las 10 replicas. Usar para saber si una diferencia es real o "
        "ruido. Argumentos: escenario, metrica."
    )
    args_schema: Type[BaseModel] = EntradaComparacion

    def _run(self, escenario: str, metrica: str) -> str:
        if metrica not in _METRICAS:
            return f"ERROR: metrica invalida. Validas: {_METRICAS}"
        df = _cargar_metricas()
        df = df[df["escenario"] == escenario]
        if df.empty:
            return f"ERROR: escenario '{escenario}' no existe."

        resultado = {"escenario": escenario, "metrica": metrica, "por_interseccion": {}}
        for inter in sorted(df["interseccion"].unique()):
            sub = df[df["interseccion"] == inter].sort_values("replica")
            sigma = sub[sub["controlador"] == "sigma_vial"][metrica].to_numpy()
            bloque = {}
            for base in ("plan_fijo_webster", "maxima_presion"):
                ref = sub[sub["controlador"] == base][metrica].to_numpy()
                variacion = (sigma.mean() - ref.mean()) / ref.mean() * 100
                try:
                    _, p = wilcoxon(sigma, ref)
                except ValueError:
                    p = float("nan")
                significativo = bool(p < 0.05)
                # En todas las metricas de esta herramienta menos es mejor.
                # La lectura se da en palabras: un modelo pequeno confunde el
                # signo de la variacion y en una corrida concluyo "mejora" en
                # una interseccion donde la espera peatonal subia 64 %.
                if not significativo:
                    lectura = "SIN DIFERENCIA SIGNIFICATIVA"
                elif variacion < 0:
                    lectura = "MEJORA"
                else:
                    lectura = "EMPEORA"
                bloque[base] = {
                    "media_sigma": round(float(sigma.mean()), 2),
                    "media_base": round(float(ref.mean()), 2),
                    "variacion_pct": round(float(variacion), 1),
                    "p_wilcoxon": round(float(p), 5),
                    "significativo_95": significativo,
                    "lectura": lectura,
                }
                if lectura == "EMPEORA":
                    resultado.setdefault("empeoran", []).append(
                        f"{inter} frente a {base}: {variacion:+.1f}%")
            resultado["por_interseccion"][inter] = bloque
        resultado.setdefault("empeoran", [])
        resultado["nota"] = ("variacion_pct negativa = SIGMA-Vial mejor (menos "
                             "espera o menos spillback); positiva = peor.")
        return _json(resultado, compacto=True)


# --------------------------------------------------------------------------
# 3. Lector de la configuracion vigente y del contexto del circuito
# --------------------------------------------------------------------------
class EntradaContexto(BaseModel):
    recurso: str = Field(
        description="configuracion | normativa | calendario | topologia | bitacora")


class LectorContextoOperativo(BaseTool):
    name: str = "lector_contexto_operativo"
    description: str = (
        "Lee un recurso del contexto operativo del circuito. Opciones: "
        "'configuracion' (pesos de utilidad y planes vigentes que ejecuta la "
        "capa de tiempo real), 'normativa' (reglas duras y parametros de "
        "cruce peatonal), 'calendario' (pulsos academicos y eventos), "
        "'topologia' (intersecciones y longitud de los tramos), 'bitacora' "
        "(incidentes y vetos registrados por el Supervisor). "
        "Argumento: recurso."
    )
    args_schema: Type[BaseModel] = EntradaContexto

    def _run(self, recurso: str) -> str:
        mapa = {
            "configuracion": "configuracion_vigente.json",
            "normativa": "normativa.json",
            "calendario": "calendario_academico.json",
            "topologia": "topologia_circuito.json",
        }
        if recurso == "bitacora":
            df = pd.read_csv(DIR_DATOS / "bitacora_supervisor.csv")
            conteo = (df.groupby(["escenario", "interseccion", "tipo"])
                        .size().reset_index(name="eventos"))
            return _json({"total_eventos": int(len(df)),
                          "desglose": conteo.to_dict(orient="records")})
        if recurso not in mapa:
            return f"ERROR: recurso invalido. Validos: {list(mapa) + ['bitacora']}"
        with open(DIR_DATOS / mapa[recurso], encoding="utf-8") as f:
            return f.read()


# --------------------------------------------------------------------------
# 4. Calculadora de Webster
# --------------------------------------------------------------------------
class EntradaWebster(BaseModel):
    interseccion: Optional[str] = Field(
        default=None,
        description="I1..I4. Omitir para obtener el plan de todas.")
    saturacion_vph: float = Field(
        default=1800.0, description="Flujo de saturacion por carril (veh/h).")


def calcular_planes_factibles(saturacion_vph: float = 1800.0) -> dict:
    """Plan base por Webster (1958) restringido por la normativa.

    Webster libre puede dar un ciclo que viola las reglas duras. Aqui se toma
    el ciclo de Webster y se sube, si hace falta, hasta el menor ciclo comun
    en que las cuatro intersecciones cumplen R1-R6 a la vez. Con N fases
    exclusivas y un verde propio de al menos ciclo - E (E = espera maxima),
    el ciclo no puede pasar de (N*E - L) / (N - 1), donde L son las perdidas.
    La busqueda se hace en codigo: es un calculo, no una decision del LLM.

    Supuestos (se devuelven en la salida): fase peatonal exclusiva una vez
    por ciclo; reparto 60/40 del flujo entre acceso principal y secundario,
    porque no hay aforo por acceso.
    """
    with open(DIR_DATOS / "normativa.json", encoding="utf-8") as f:
        n = json.load(f)
    with open(DIR_DATOS / "topologia_circuito.json", encoding="utf-8") as f:
        topo = json.load(f)["intersecciones"]

    fases = n.get("fases_por_ciclo", 3)
    perdida_fase = n["ambar_s"] + n["todo_rojo_s"]
    L = perdida_fase * fases
    e_carril = n["espera_max_carril_s"]
    e_peat = n["espera_max_cruce_peatonal_s"]
    v_min0, v_max = n["verde_veh_min_s"], n["verde_veh_max_s"]

    def v_min(ciclo):
        return max(v_min0, ciclo - e_carril)

    def p_min(inter, ciclo):
        despeje = n["anchos_cruce_m"][inter] / n["velocidad_cruce_peatonal_m_s"]
        return math.ceil(max(despeje, ciclo - e_peat))

    X = 0.90          # grado de saturacion objetivo de cada acceso
    REPARTO = (0.6, 0.4)

    def v_req(inter, ciclo):
        """Verde minimo de cada acceso: el normativo o el que da capacidad
        suficiente (flujo / saturacion * ciclo / X), el que sea mayor."""
        flujo = topo[inter]["demanda_base_vph"]
        return [max(v_min(ciclo),
                    math.ceil(flujo * r / saturacion_vph * ciclo / X))
                for r in REPARTO]

    def factible(inter, ciclo):
        verdes = v_req(inter, ciclo)
        return (max(verdes) <= v_max and
                sum(verdes) + p_min(inter, ciclo) + L <= ciclo)

    # Ciclo de Webster libre por interseccion (dos fases vehiculares criticas).
    webster = {}
    for inter, info in topo.items():
        Y = info["demanda_base_vph"] / saturacion_vph
        webster[inter] = (1.5 * L + 5) / (1 - Y) if Y < 0.95 else None
    if any(w is None for w in webster.values()):
        return {"error": "alguna interseccion tiene Y >= 0.95; Webster no "
                         "converge", "webster_libre_s": webster}

    inicio = max(40, math.ceil(max(webster.values())))
    ciclo = next((c for c in range(inicio, n["ciclo_max_s"] + 1)
                  if all(factible(i, c) for i in topo)), None)
    ciclo_max_factible = ((fases * min(e_carril, e_peat) - L) / (fases - 1))
    if ciclo is None:
        return {"error": "no existe un ciclo comun que cumpla R1-R6",
                "ciclo_max_factible_s": ciclo_max_factible}

    planes = {}
    for inter, info in topo.items():
        vp_req, vs_req = v_req(inter, ciclo)
        pm = p_min(inter, ciclo)
        sobrante = ciclo - L - (vp_req + vs_req + pm)
        a_peaton = sobrante // 2            # mitad del margen al cruce
        a_veh = sobrante - a_peaton
        extra_p = round(a_veh * REPARTO[0])
        principal = vp_req + extra_p
        secundario = vs_req + (a_veh - extra_p)
        planes[inter] = {
            "ciclo_s": ciclo,
            "verde_principal_s": int(principal),
            "verde_secundario_s": int(secundario),
            "verde_peatonal_s": int(pm + a_peaton),
        }
    # Desfases para onda verde: el circuito es un anillo I1->I2->I3->I4->I1 y
    # el desfase de cada intersecion es el de la anterior mas el tiempo de
    # recorrido del tramo, modulo el ciclo. Longitud del tramo: 7 m por
    # vehiculo de capacidad; velocidad de 30 km/h (8.33 m/s).
    with open(DIR_DATOS / "topologia_circuito.json", encoding="utf-8") as f:
        tramos = json.load(f)["tramos"]
    desfase = {tramos[0][0]: 0}
    for origen, destino, cap_veh in tramos[:-1]:
        viaje = cap_veh * 7.0 / 8.33
        desfase[destino] = int(round(desfase[origen] + viaje)) % ciclo
    for inter, plan in planes.items():
        plan["desfase_s"] = desfase.get(inter, 0)

    with open(DIR_DATOS / "configuracion_vigente.json", encoding="utf-8") as f:
        vigente = json.load(f)
    propuesta_base = {
        "version": "v1.1-base",
        "pesos_utilidad": vigente["pesos_utilidad"],
        "planes_por_interseccion": planes,
        "umbral_spillback_pct": vigente["umbral_spillback_pct"],
        "periodo_decision_s": vigente["periodo_decision_s"],
        "justificacion": {},
    }
    return {
        "ciclo_comun_s": ciclo,
        "ciclo_webster_libre_s": {k: round(v, 1) for k, v in webster.items()},
        "ciclo_max_factible_s": round(ciclo_max_factible, 1),
        "planes_sugeridos": planes,
        "propuesta_base": propuesta_base,
        "supuestos": [
            "desfase_s: recorrido de 7 m por vehiculo de capacidad del tramo "
            "a 30 km/h, acumulado a lo largo del anillo I1-I2-I3-I4",
            f"{fases} fases exclusivas por ciclo (principal, secundaria, "
            "peatonal), perdidas de {} s por fase".format(perdida_fase),
            "flujo repartido 60/40 entre acceso principal y secundario",
            f"cada acceso con grado de saturacion objetivo de {X}",
        ],
    }


class CalculadoraWebster(BaseTool):
    name: str = "calculadora_webster"
    description: str = (
        "Calcula el plan base de cada interseccion con el metodo de Webster "
        "(1958) RESTRINGIDO por la normativa: devuelve un ciclo comun y los "
        "verdes (principal, secundario y peatonal) que ya cumplen las reglas "
        "R1 a R6, junto con el ciclo maximo factible. Usarla como punto de "
        "partida de toda propuesta de planes; no inventes ciclos fuera de lo "
        "que devuelve. Argumentos opcionales: interseccion, saturacion_vph."
    )
    args_schema: Type[BaseModel] = EntradaWebster

    def _run(self, interseccion: Optional[str] = None,
             saturacion_vph: float = 1800.0) -> str:
        res = calcular_planes_factibles(saturacion_vph)
        if "planes_sugeridos" in res:
            # Comprobacion independiente con el validador de reglas duras,
            # sobre la propuesta base completa.
            res["validado_por_reglas_duras"] = (json.loads(
                ValidadorReglasDuras()._run(
                    propuesta_json=json.dumps(res["propuesta_base"]))
            )["veredicto"] == "APROBADO")
            if interseccion:
                if interseccion not in res["planes_sugeridos"]:
                    return f"ERROR: interseccion '{interseccion}' no existe."
                res["planes_sugeridos"] = {
                    interseccion: res["planes_sugeridos"][interseccion]}
                res.pop("propuesta_base")
        return _json(res)


# --------------------------------------------------------------------------
# 5. Validador de reglas duras  (el poder de veto del Auditor)
# --------------------------------------------------------------------------
class EntradaValidacion(BaseModel):
    # El LLM solo puede indicar QUE archivo validar. La herramienta acepta
    # tambien un JSON (propuesta_json) para uso interno desde Python, pero no
    # se expone en el esquema: en una corrida el Auditor reescribio la
    # propuesta a mano, incompleta, y el validador juzgo un artefacto distinto
    # del que produjo el Ingeniero.
    archivo: str = Field(
        description=("Que validar, por nombre: 'vigente' (configuracion que "
                     "se ejecuta hoy), 'propuesta_v1' o 'propuesta_v2' (salidas "
                     "del Ingeniero). No copies ningun JSON."))


class ValidadorReglasDuras(BaseTool):
    name: str = "validador_reglas_duras"
    description: str = (
        "Valida una propuesta de planes de senalizacion contra las reglas "
        "duras y la normativa de cruce peatonal. Devuelve un dictamen "
        "estructurado con veredicto APROBADO o RECHAZADO y la lista de "
        "violaciones. Es la unica fuente autorizada para declarar que una "
        "propuesta cumple. Argumento: archivo ('vigente' | 'propuesta_v1' | "
        "'propuesta_v2'). No acepta JSON copiado: valida el archivo tal cual."
    )
    args_schema: Type[BaseModel] = EntradaValidacion

    def _run(self, archivo: Optional[str] = None,
             propuesta_json: Optional[str] = None) -> str:
        with open(DIR_DATOS / "normativa.json", encoding="utf-8") as f:
            n = json.load(f)
        try:
            prop = json.loads(_resolver_propuesta(archivo, propuesta_json))
        except ValueError as e:      # JSONDecodeError es subclase de ValueError
            return _json({"veredicto": "RECHAZADO",
                          "violaciones": [f"Entrada no valida: {e}"]})

        planes = prop.get("planes_por_interseccion", {})
        if not planes:
            return _json({"veredicto": "RECHAZADO",
                          "violaciones": ["La propuesta no contiene "
                                          "'planes_por_interseccion'."]})

        violaciones, advertencias = [], []

        # R0 — esquema y completitud. Una propuesta que omite intersecciones
        # o inventa nombres de parametros no es una configuracion aplicable,
        # aunque lo que si trae cumpla R1-R6.
        esperadas = set(n["anchos_cruce_m"])
        faltan = sorted(esperadas - set(planes))
        sobran = sorted(set(planes) - esperadas)
        if faltan:
            violaciones.append(f"Faltan planes para {', '.join(faltan)} (R0)")
        if sobran:
            violaciones.append(f"Intersecciones desconocidas: "
                               f"{', '.join(sobran)} (R0)")
        for inter, p in planes.items():
            for clave in ("ciclo_s", "verde_principal_s", "verde_secundario_s",
                          "verde_peatonal_s", "desfase_s"):
                if not isinstance(p.get(clave), (int, float)):
                    violaciones.append(f"{inter}: falta o no es numerico "
                                       f"'{clave}' (R0)")
        with open(DIR_DATOS / "configuracion_vigente.json",
                  encoding="utf-8") as f:
            vigente = json.load(f)
        pesos = prop.get("pesos_utilidad")
        claves_pesos = set(vigente["pesos_utilidad"])
        if not isinstance(pesos, dict) or set(pesos) != claves_pesos:
            violaciones.append("pesos_utilidad debe tener exactamente las "
                               f"claves {sorted(claves_pesos)} (R0)")
        elif any(not isinstance(v, (int, float)) or v < 0
                 or v > n["peso_max"] for v in pesos.values()):
            violaciones.append(f"los pesos deben estar entre 0 y "
                               f"{n['peso_max']} (R0)")
        umbral = prop.get("umbral_spillback_pct")
        if (not isinstance(umbral, (int, float))
                or not n["umbral_spillback_min"] <= umbral
                <= n["umbral_spillback_max"]):
            violaciones.append(
                f"umbral_spillback_pct {umbral} fuera de "
                f"[{n['umbral_spillback_min']}, {n['umbral_spillback_max']}] "
                "(R0; es una fraccion de la capacidad del tramo)")
        periodo = prop.get("periodo_decision_s")
        if (not isinstance(periodo, (int, float))
                or not 1 <= periodo <= n["periodo_decision_max_s"]):
            violaciones.append(
                f"periodo_decision_s {periodo} fuera de "
                f"[1, {n['periodo_decision_max_s']}] s (R0)")

        for inter, p in planes.items():
            ciclo = p.get("ciclo_s", 0)
            vp = p.get("verde_principal_s", 0)
            vs = p.get("verde_secundario_s", 0)
            vpeat = p.get("verde_peatonal_s", 0)

            # R1 — verde vehicular dentro de limites
            for etiqueta, v in (("principal", vp), ("secundario", vs)):
                if v < n["verde_veh_min_s"]:
                    violaciones.append(
                        f"{inter}: verde {etiqueta} {v}s < minimo "
                        f"{n['verde_veh_min_s']}s (R1)")
                if v > n["verde_veh_max_s"]:
                    violaciones.append(
                        f"{inter}: verde {etiqueta} {v}s > maximo "
                        f"{n['verde_veh_max_s']}s (R1)")

            # R2 — ciclo maximo
            if ciclo > n["ciclo_max_s"]:
                violaciones.append(
                    f"{inter}: ciclo {ciclo}s > maximo {n['ciclo_max_s']}s (R2)")

            # R3 — tiempo de despeje peatonal
            ancho = n["anchos_cruce_m"].get(inter)
            if ancho is None:
                advertencias.append(f"{inter}: sin ancho de cruce registrado")
            else:
                minimo = ancho / n["velocidad_cruce_peatonal_m_s"]
                if vpeat < minimo:
                    violaciones.append(
                        f"{inter}: verde peatonal {vpeat}s < despeje minimo "
                        f"{minimo:.1f}s para un cruce de {ancho}m a "
                        f"{n['velocidad_cruce_peatonal_m_s']} m/s (R3)")

            # R4 — consistencia del ciclo (fases + perdidas)
            perdidas = ((n["ambar_s"] + n["todo_rojo_s"])
                        * n.get("fases_por_ciclo", 3))
            suma = vp + vs + vpeat + perdidas
            if suma > ciclo:
                violaciones.append(
                    f"{inter}: las fases suman {suma}s (incluye {perdidas}s de "
                    f"ambar y todo-rojo) y exceden el ciclo de {ciclo}s (R4)")

            # R5 — espera maxima implicita por cruce peatonal
            espera_peat_max = ciclo - vpeat
            if espera_peat_max > n["espera_max_cruce_peatonal_s"]:
                violaciones.append(
                    f"{inter}: espera peatonal maxima implicita "
                    f"{espera_peat_max}s > limite "
                    f"{n['espera_max_cruce_peatonal_s']}s (R5)")

            # R6 — espera maxima por carril. La Fase 1-2 fija 60 s por carril
            # igual que por cruce peatonal; un acceso espera, como maximo, el
            # ciclo menos su propio verde.
            for etiqueta, v in (("principal", vp), ("secundario", vs)):
                espera_carril = ciclo - v
                if espera_carril > n["espera_max_carril_s"]:
                    violaciones.append(
                        f"{inter}: espera maxima del acceso {etiqueta} "
                        f"{espera_carril}s > limite "
                        f"{n['espera_max_carril_s']}s (R6)")

        return _json({
            "veredicto": "RECHAZADO" if violaciones else "APROBADO",
            "reglas_evaluadas": ["R0 esquema y completitud",
                                 "R1 verde vehicular", "R2 ciclo maximo",
                                 "R3 despeje peatonal", "R4 consistencia de fases",
                                 "R5 espera peatonal maxima",
                                 "R6 espera maxima por carril"],
            "violaciones": violaciones,
            "advertencias": advertencias,
            "intersecciones_evaluadas": sorted(planes),
        })


# --------------------------------------------------------------------------
# 6. Escritor de la configuracion aprobada
# --------------------------------------------------------------------------
class EntradaPublicacion(BaseModel):
    archivo: str = Field(
        description=("Que publicar, por nombre: 'ultima_valida' (publica "
                     "propuesta_v2 si cumple las reglas duras y, si no, "
                     "propuesta_v1 si cumple), 'propuesta_v1' o 'propuesta_v2'. "
                     "Usa 'ultima_valida'. No copies ningun JSON."))


class PublicadorConfiguracion(BaseTool):
    name: str = "publicador_configuracion"
    description: str = (
        "Publica la configuracion aprobada en el archivo que lee la capa de "
        "tiempo real (configuracion_aprobada.json). Rechaza la publicacion si "
        "la configuracion no pasa antes el validador de reglas duras. "
        "Argumento: archivo ('ultima_valida' | 'propuesta_v1' | "
        "'propuesta_v2')."
    )
    args_schema: Type[BaseModel] = EntradaPublicacion

    def _run(self, archivo: Optional[str] = None,
             configuracion_json: Optional[str] = None) -> str:
        if archivo == "vigente":
            return _json({"publicado": False,
                          "motivo": "la configuracion vigente no se republica"})
        if archivo == "ultima_valida":
            # Regla determinista: se publica la propuesta mas reciente que
            # pase el validador. Evita que una "correccion" del Ingeniero que
            # empeora una propuesta ya valida deje el ciclo sin publicar.
            archivo = None
            for candidato in ("propuesta_v2", "propuesta_v1"):
                if json.loads(ValidadorReglasDuras()._run(archivo=candidato)
                              )["veredicto"] == "APROBADO":
                    archivo = candidato
                    break
            if archivo is None:
                return _json({"publicado": False,
                              "motivo": "ninguna propuesta pasa las reglas "
                                        "duras"})
        # Segunda barrera: se revalida aqui, de modo que el veto no depende de
        # que el agente Auditor se comporte bien.
        dictamen = json.loads(ValidadorReglasDuras()._run(
            archivo=archivo, propuesta_json=configuracion_json))
        if dictamen["veredicto"] != "APROBADO":
            return _json({"publicado": False,
                          "motivo": "la configuracion no pasa las reglas duras",
                          "violaciones": dictamen["violaciones"]})
        texto = _resolver_propuesta(archivo, configuracion_json)
        destino = DIR_DATOS / "configuracion_aprobada.json"
        with open(destino, "w", encoding="utf-8") as f:
            f.write(texto)
        return _json({"publicado": True, "archivo": str(destino.name),
                      "fuente": archivo or "configuracion_json"})


HERRAMIENTAS = {
    "telemetria": LectorTelemetriaSUMO(),
    "comparador": ComparadorLineasBase(),
    "contexto": LectorContextoOperativo(),
    "webster": CalculadoraWebster(),
    "validador": ValidadorReglasDuras(),
    "publicador": PublicadorConfiguracion(),
}

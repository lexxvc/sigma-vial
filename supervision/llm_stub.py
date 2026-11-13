"""
SIGMA-Vial — Fase 3
Servidor LLM determinista para correr el Crew sin clave de API.

Expone un endpoint compatible con la API de OpenAI (/v1/chat/completions) que
CrewAI consume como cualquier otro proveedor. El Crew, los agentes, las tareas,
el paso de contexto entre tareas y TODAS las llamadas a herramientas son
reales; lo unico sustituido es el modelo de lenguaje, que aqui sigue un guion
fijo en lugar de razonar.

Para que sirve:
  - ejecutar y depurar el Crew sin costo ni clave de API;
  - hacer la corrida de demostracion reproducible.

Limitacion que hay que declarar: un guion no razona. Las decisiones que aqui
estan fijas las toma el LLM real en produccion. Para la corrida con
razonamiento genuino, usar Ollama u otro proveedor (ver README).

Nota importante: el guion NO puede hacer trampa con el validador de reglas
duras. La propuesta del ciclo 1 esta deliberadamente mal construida y el
validador la rechaza de verdad; el resultado APROBADO del ciclo 2 lo produce
la herramienta, no el guion.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from herramientas import HERRAMIENTAS

PUERTO = 8765
DIR_DATOS = Path(__file__).parent / "datos"


def _accion(herramienta: str, argumentos: dict) -> dict:
    """Llamada a herramienta en el formato de function calling de OpenAI,
    que es el que usa el ejecutor de CrewAI."""
    return {"tipo": "tool_call", "nombre": herramienta, "argumentos": argumentos}


def _respuesta_final(texto: str) -> dict:
    return {"tipo": "final", "contenido": texto}


def _config_vigente_str() -> str:
    with open(DIR_DATOS / "configuracion_vigente.json", encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------
# Contenido de las respuestas finales, derivado de datos reales
# --------------------------------------------------------------------------
def _diagnostico() -> str:
    comp_peat = json.loads(HERRAMIENTAS["comparador"]._run(
        escenario="pico_matutino", metrica="espera_peat_media_s"))
    comp_veh = json.loads(HERRAMIENTAS["comparador"]._run(
        escenario="pico_matutino", metrica="espera_veh_media_s"))
    comp_sb = json.loads(HERRAMIENTAS["comparador"]._run(
        escenario="pico_matutino", metrica="spillback_eventos_h"))

    hallazgos = []
    for inter in ["I1", "I2", "I3", "I4"]:
        p = comp_peat["por_interseccion"][inter]["plan_fijo_webster"]
        v = comp_veh["por_interseccion"][inter]["plan_fijo_webster"]
        s = comp_sb["por_interseccion"][inter]["plan_fijo_webster"]
        severidad = ("critica" if p["variacion_pct"] > 20
                     else "media" if s["variacion_pct"] > 20 else "nula")
        hallazgos.append({
            "interseccion": inter,
            "espera_peatonal_vs_webster_pct": p["variacion_pct"],
            "espera_vehicular_vs_webster_pct": v["variacion_pct"],
            "spillback_vs_webster_pct": s["variacion_pct"],
            "p_wilcoxon_peatonal": p["p_wilcoxon"],
            "severidad": severidad,
        })

    pct_i3 = next(h["espera_peatonal_vs_webster_pct"] for h in hallazgos
                  if h["interseccion"] == "I3")
    return json.dumps({
        "escenario_analizado": "pico_matutino",
        "replicas": 10,
        "hallazgos": hallazgos,
        "conclusion": (
            "SIGMA-Vial supera a ambas lineas base en espera vehicular en las "
            "cuatro intersecciones, con p<0.01. Dos desviaciones rompen el "
            f"patron. En I3 la espera peatonal es {pct_i3}% PEOR que el plan fijo, "
            "justo en la interseccion con mayor demanda peatonal del circuito "
            "(680 peatones/hora). En I2 los eventos de spillback suben respecto "
            "a la linea base, consistente con que el tramo I2->I3 es el mas "
            "corto del circuito (capacidad 14 vehiculos). Ambas desviaciones "
            "apuntan a la configuracion vigente, no al algoritmo de control: "
            "el peso de espera peatonal (0.6) es menor que el de espera "
            "vehicular (0.8), y el umbral de spillback (0.85) se activa "
            "demasiado tarde para un tramo tan corto."),
        "prioridad_de_atencion": ["I3", "I2"],
    }, ensure_ascii=False, indent=2)


def _propuesta_v1() -> str:
    """Primer intento. Corrige los pesos pero conserva los ciclos largos
    heredados de la Fase 2, lo que lo hace invalido frente a R4 y R5."""
    return json.dumps({
        "version": "v1.1-fase3",
        "pesos_utilidad": {
            "w_veh_atendidos": 1.0, "w_peat_atendidos": 1.0,
            "w_espera_veh": 0.8, "w_espera_peat": 1.4,
            "w_ocupacion_aguas_abajo": 0.9,
        },
        "planes_por_interseccion": {
            "I1": {"ciclo_s": 95, "verde_principal_s": 42, "verde_secundario_s": 26,
                   "verde_peatonal_s": 18, "desfase_s": 0},
            "I2": {"ciclo_s": 85, "verde_principal_s": 36, "verde_secundario_s": 22,
                   "verde_peatonal_s": 12, "desfase_s": 20},
            "I3": {"ciclo_s": 100, "verde_principal_s": 38, "verde_secundario_s": 20,
                   "verde_peatonal_s": 27, "desfase_s": 42},
            "I4": {"ciclo_s": 95, "verde_principal_s": 48, "verde_secundario_s": 24,
                   "verde_peatonal_s": 14, "desfase_s": 58},
        },
        "umbral_spillback_pct": 0.70,
        "periodo_decision_s": 5,
        "justificacion": {
            "w_espera_peat": "Sube de 0.6 a 1.4 para corregir el sesgo en I3.",
            "w_ocupacion_aguas_abajo": "Sube de 0.4 a 0.9 por el tramo corto.",
            "umbral_spillback_pct": "Baja de 0.85 a 0.70 para retener antes.",
            "I3": "Verde peatonal de 11 s a 27 s y ciclo de 85 s a 100 s.",
        },
    }, ensure_ascii=False, indent=2)


def _propuesta_v2() -> str:
    """Correccion tras el veto. Ciclo comun de 80 s en las cuatro
    intersecciones: satisface la espera peatonal maxima de 60 s y ademas
    habilita la coordinacion por desfase, que exige ciclo comun."""
    return json.dumps({
        "version": "v1.2-fase3",
        "pesos_utilidad": {
            "w_veh_atendidos": 1.0, "w_peat_atendidos": 1.0,
            "w_espera_veh": 0.8, "w_espera_peat": 1.4,
            "w_ocupacion_aguas_abajo": 0.9,
        },
        "planes_por_interseccion": {
            "I1": {"ciclo_s": 80, "verde_principal_s": 24, "verde_secundario_s": 20,
                   "verde_peatonal_s": 20, "desfase_s": 0},
            "I2": {"ciclo_s": 80, "verde_principal_s": 24, "verde_secundario_s": 20,
                   "verde_peatonal_s": 20, "desfase_s": 18},
            "I3": {"ciclo_s": 80, "verde_principal_s": 22, "verde_secundario_s": 20,
                   "verde_peatonal_s": 22, "desfase_s": 38},
            "I4": {"ciclo_s": 80, "verde_principal_s": 25, "verde_secundario_s": 20,
                   "verde_peatonal_s": 20, "desfase_s": 58},
        },
        "umbral_spillback_pct": 0.70,
        "periodo_decision_s": 5,
        "justificacion": {
            "ciclo_comun_80s": (
                "El veto revelo que los limites de espera maxima (60 s por "
                "carril y por cruce) acotan el ciclo mucho mas que el maximo de "
                "120 s. Con tres fases exclusivas y 15 s de perdidas, cada "
                "verde debe ser al menos ciclo - 60 s, lo que exige "
                "ciclo <= 82.5 s. Un ciclo comun de 80 s con verdes de al menos "
                "20 s cumple todo y, ademas, habilita la coordinacion por "
                "desfase, que exige ciclo comun en todo el circuito."),
            "w_espera_peat": (
                "Sube de 0.6 a 1.4. Con 0.6 la utilidad compensaba la espera "
                "peatonal con flujo vehicular, lo que explica el deterioro de "
                "I3."),
            "w_ocupacion_aguas_abajo": (
                "Sube de 0.4 a 0.9 y el umbral de spillback baja de 0.85 a 0.70 "
                "para proteger el tramo corto I2->I3."),
            "I3": (
                "Verde peatonal de 11 s a 22 s. El plan vigente ni siquiera "
                "cubria el tiempo de despeje de un cruce de 14 m, por lo que "
                "era invalido ademas de ineficiente."),
            "costo_asumido": (
                "El verde vehicular principal baja en las cuatro "
                "intersecciones. Se espera un aumento de la espera vehicular, "
                "aceptable frente a la prioridad peatonal."),
        },
    }, ensure_ascii=False, indent=2)


def _dictamen_v1() -> str:
    d_vig = json.loads(HERRAMIENTAS["validador"]._run(
        propuesta_json=_config_vigente_str()))
    d_p1 = json.loads(HERRAMIENTAS["validador"]._run(
        propuesta_json=_propuesta_v1()))
    return json.dumps({
        "ciclo_de_auditoria": 1,
        "dictamen_configuracion_vigente": {
            "veredicto": d_vig["veredicto"],
            "violaciones": d_vig["violaciones"],
            "observacion": (
                "La configuracion que la capa de tiempo real ejecuta HOY no "
                "pasa las reglas duras. Es un hallazgo de seguridad, no de "
                "desempeno, y debe escalarse de inmediato."),
        },
        "dictamen_propuesta_v1.1": {
            "veredicto": d_p1["veredicto"],
            "violaciones": d_p1["violaciones"],
        },
        "reglas_evaluadas": d_p1["reglas_evaluadas"],
        "autorizacion_de_publicacion": False,
        "instruccion_al_ingeniero": (
            "La propuesta v1.1 corrige los pesos pero conserva ciclos de 85 a "
            "100 s. Con la fase peatonal servida una vez por ciclo, la espera "
            "peatonal maxima es ciclo menos verde peatonal y rebasa el limite "
            "de 60 s en las cuatro intersecciones. Reducir el ciclo. No se "
            "autoriza excepcion por mejora de desempeno."),
    }, ensure_ascii=False, indent=2)


def _dictamen_v2() -> str:
    d_p2 = json.loads(HERRAMIENTAS["validador"]._run(
        propuesta_json=_propuesta_v2()))
    # La publicacion real ocurre en la llamada a herramienta del Auditor
    # (archivo='propuesta_v2'); aqui solo se reporta su resultado.
    pub = {"publicado": d_p2["veredicto"] == "APROBADO",
           "archivo": "configuracion_aprobada.json"}
    return json.dumps({
        "ciclo_de_auditoria": 2,
        "dictamen_propuesta_v1.2": {
            "veredicto": d_p2["veredicto"],
            "violaciones": d_p2["violaciones"],
            "advertencias": d_p2["advertencias"],
        },
        "reglas_evaluadas": d_p2["reglas_evaluadas"],
        "autorizacion_de_publicacion": d_p2["veredicto"] == "APROBADO",
        "publicacion": pub,
    }, ensure_ascii=False, indent=2)


def _reporte() -> str:
    return """# SIGMA-Vial — Reporte del ciclo de supervision tactica

**Escenario:** pico matutino · 10 replicas por configuracion
**Configuracion evaluada:** v1.0-fase2 · **Publicada:** v1.2-fase3

## 1. Que se encontro

SIGMA-Vial cumple su objetivo vehicular: reduce la espera media frente al plan
fijo de Webster en las cuatro intersecciones, con significancia estadistica
(Wilcoxon, p<0.01). El hallazgo relevante es otro.

En **I3 (Plaza Central)** la espera peatonal media resulto **64.3% peor que la
linea base**, justo en la interseccion con mayor demanda peatonal del circuito
(680 peatones/hora). La causa no es el algoritmo de control sino la funcion de
utilidad: con un peso de espera peatonal de 0.6 contra 0.8 de espera vehicular,
al Controlador le convenia postergar la fase peatonal para servir flujo
vehicular.

En **I2 (Estacionamiento Norte)** los eventos de spillback aumentaron respecto
a la linea base. El tramo I2 a I3 es el mas corto del circuito, con capacidad
para 14 vehiculos, y el umbral de spillback vigente de 0.85 se activa demasiado
tarde para protegerlo.

## 2. Dos hallazgos de seguridad

**El primero: la configuracion vigente es invalida.** Al auditarla contra las
reglas duras, el plan de I3 resulto no conforme. Su verde peatonal de 11 s es
inferior al tiempo de despeje de 14.0 s que exige un cruce de 14 m a 1.0 m/s, y
su espera peatonal maxima implicita de 74 s rebasa el limite de 60 s. Lo que la
capa de tiempo real ejecuta hoy no debio publicarse.

**El segundo: los limites de espera acotan el ciclo mucho mas que el maximo de
120 s.** El veto a la primera propuesta lo dejo en evidencia. Con fases
exclusivas, la espera de un carril o cruce es el ciclo menos su verde. Con tres
fases y 15 s de perdidas, cada verde debe ser al menos ciclo - 60 s, lo que
exige un ciclo de 82.5 s o menos. El maximo de 120 s de la Fase 2 no es
alcanzable salvo que la fase peatonal sea concurrente o se sirva dos veces por
ciclo.

## 3. El ciclo de correccion

La propuesta **v1.1** corrigio los pesos de utilidad pero conservo los ciclos
largos heredados. El Auditor la **rechazo**: violaba R5 en las cuatro
intersecciones y R4 en dos (ademas de R6). No se autorizo excepcion por mejora
de desempeno.

La propuesta **v1.2** adopto un ciclo comun de 80 s. Satisface los limites
y ademas habilita la coordinacion por desfase, que exige ciclo comun en todo el
circuito. Fue **aprobada** y publicada.

## 4. Que cambio

| Parametro | v1.0 vigente | v1.2 publicada | Motivo |
|---|---|---|---|
| w_espera_peat | 0.6 | 1.4 | Corrige el sesgo contra el peaton |
| w_ocupacion_aguas_abajo | 0.4 | 0.9 | Protege el tramo corto I2 a I3 |
| umbral_spillback | 0.85 | 0.70 | Activa la retencion antes |
| Ciclo (las cuatro) | 85 a 95 s | 80 s comun | Cumple R5 y habilita coordinacion |
| I3 verde peatonal | 11 s | 22 s | Cumple el despeje de 14.0 s y R5 |
| I1 verde principal | 44 s | 24 s | Costo asumido del ajuste |

## 5. A costa de que

El verde vehicular principal baja en las cuatro intersecciones, de modo que se
espera un aumento de la espera vehicular media. El criterio aplicado es la
prioridad peatonal de la piramide de movilidad (SEDATU, 2019). La magnitud real
del costo debera medirse volviendo a simular con la configuracion v1.2, lo que
constituye el siguiente ciclo de supervision.

## 6. Trazabilidad

| Elemento | Origen |
|---|---|
| Diagnostico | `metricas_simulacion.csv` · 480 filas · 4 escenarios · 3 controladores · 10 replicas |
| Significancia | `comparador_lineas_base` · Wilcoxon pareada |
| Reglas duras | `normativa.json` via `validador_reglas_duras` |
| Incidentes | `bitacora_supervisor.csv` |
| Publicacion | `configuracion_aprobada.json` |
"""


# --------------------------------------------------------------------------
# Guiones por tarea
# --------------------------------------------------------------------------
GUIONES = {
    "diagnostico": [
        _accion("lector_contexto_operativo", {"recurso": "configuracion"}),
        _accion("lector_telemetria_sumo", {"escenario": "pico_matutino"}),
        _accion("comparador_lineas_base",
                {"escenario": "pico_matutino", "metrica": "espera_peat_media_s"}),
        _accion("comparador_lineas_base",
                {"escenario": "pico_matutino", "metrica": "spillback_eventos_h"}),
        _accion("lector_contexto_operativo", {"recurso": "bitacora"}),
        lambda: _respuesta_final(_diagnostico()),
    ],
    "propuesta_v1": [
        _accion("lector_contexto_operativo", {"recurso": "topologia"}),
        _accion("calculadora_webster", {}),
        _accion("lector_contexto_operativo", {"recurso": "calendario"}),
        lambda: _respuesta_final(_propuesta_v1()),
    ],
    "auditoria_v1": [
        _accion("lector_contexto_operativo", {"recurso": "normativa"}),
        _accion("validador_reglas_duras", {"archivo": "vigente"}),
        _accion("validador_reglas_duras", {"archivo": "propuesta_v1"}),
        lambda: _respuesta_final(_dictamen_v1()),
    ],
    "propuesta_v2": [
        _accion("lector_contexto_operativo", {"recurso": "normativa"}),
        lambda: _respuesta_final(_propuesta_v2()),
    ],
    "auditoria_v2": [
        _accion("validador_reglas_duras", {"archivo": "propuesta_v2"}),
        _accion("publicador_configuracion", {"archivo": "propuesta_v2"}),
        lambda: _respuesta_final(_dictamen_v2()),
    ],
    "sintesis": [
        lambda: _respuesta_final(_reporte()),
    ],
}


def _identificar_tarea(prompt: str) -> str:
    p = prompt.lower()
    if "sintetizador de movilidad" in p:
        return "sintesis"
    if "auditor normativo" in p:
        # El segundo ciclo de auditoria llega con la propuesta v1.2 en contexto.
        return "auditoria_v2" if "v1.2-fase3" in p else "auditoria_v1"
    if "ingeniero de planes" in p:
        # El rechazo del Auditor llega en el contexto de la re-propuesta.
        return "propuesta_v2" if "instruccion_al_ingeniero" in p else "propuesta_v1"
    return "diagnostico"


def _paso(cuerpo: dict) -> int:
    """El numero de respuestas de herramienta ya presentes en la conversacion
    indica en que paso del guion vamos."""
    return sum(1 for m in cuerpo.get("messages", []) if m.get("role") == "tool")


class Manejador(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # silencia el log HTTP para no ensuciar la salida del Crew

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        cuerpo = json.loads(self.rfile.read(n) or b"{}")
        prompt = "\n".join(
            m.get("content") or "" for m in cuerpo.get("messages", []))

        guion = GUIONES[_identificar_tarea(prompt)]
        i = min(_paso(cuerpo), len(guion) - 1)
        paso = guion[i]
        accion = paso() if callable(paso) else paso

        if accion["tipo"] == "tool_call":
            mensaje = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": f"call_{i}_{int(time.time()*1000)}",
                    "type": "function",
                    "function": {
                        "name": accion["nombre"],
                        "arguments": json.dumps(accion["argumentos"],
                                                ensure_ascii=False),
                    },
                }],
            }
            motivo, contenido = "tool_calls", accion["nombre"]
        else:
            mensaje = {"role": "assistant", "content": accion["contenido"]}
            motivo, contenido = "stop", accion["contenido"]

        respuesta = {
            "id": f"stub-{int(time.time()*1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": cuerpo.get("model", "sigma-stub"),
            "choices": [{"index": 0, "message": mensaje,
                         "finish_reason": motivo}],
            "usage": {"prompt_tokens": len(prompt) // 4,
                      "completion_tokens": len(contenido) // 4,
                      "total_tokens": (len(prompt) + len(contenido)) // 4},
        }
        datos = json.dumps(respuesta).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)


def iniciar_en_hilo(puerto: int = PUERTO) -> HTTPServer:
    servidor = HTTPServer(("127.0.0.1", puerto), Manejador)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor


if __name__ == "__main__":
    print(f"LLM stub escuchando en http://127.0.0.1:{PUERTO}/v1")
    HTTPServer(("127.0.0.1", PUERTO), Manejador).serve_forever()

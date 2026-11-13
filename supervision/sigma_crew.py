"""
SIGMA-Vial — Fase 3
Capa MAS de supervision tactica, implementada con CrewAI.

ARQUITECTURA EN DOS CAPAS
-------------------------
Capa de tiempo real (Fases 1 y 2, NO vive aqui): Monitor, Controlador de
interseccion y Supervisor de seguridad corren sobre SUMO/TraCI con logica
determinista y cadencia de segundos. Deciden QUE hace el semaforo ahora.

Capa MAS de supervision tactica (esta, la Fase 3): un Crew de CrewAI que corre
FUERA del lazo de control, con cadencia de jornada. Lee la telemetria que
produjo la simulacion, diagnostica, propone ajustes de configuracion, los
audita contra la normativa y publica la version aprobada. Decide COMO DEBE
COMPORTARSE el sistema.

El LLM nunca entra en el lazo de control: no cumpliria el periodo de decision
de 5 s ni permitiria verificar las reglas duras de forma determinista.

USO
---
    python sigma_crew.py                  # modo stub, sin clave de API
    SIGMA_LLM=ollama python sigma_crew.py # Ollama local
    SIGMA_LLM=openai OPENAI_API_KEY=... python sigma_crew.py
"""

import asyncio
import concurrent.futures
import json
import os
import sys
from typing import Any

from crewai import Agent, Crew, LLM, Process, Task
from pydantic import BaseModel

from herramientas import (DIR_SALIDAS, HERRAMIENTAS, _extraer_json,
                          calcular_planes_factibles)
from verificacion import verificar_reporte

DIR_REPORTE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "reporte_ciclo_supervision.md")

# Las propuestas del Ingeniero se guardan a disco y el Auditor las valida por
# nombre (archivo='propuesta_v1'), no por una copia retipeada por el LLM.
DIR_SALIDAS.mkdir(exist_ok=True)


# Fase 4 — escritura de salidas por callback.
# CrewAI trata output_file como ruta relativa: en Linux/macOS le quita la "/"
# inicial y el archivo termina en <cwd>/home/.../salidas/, de modo que el
# Auditor no encuentra la propuesta y nunca publica. En Windows no se notaba.
# Cada tarea escribe su salida con este callback, con ruta absoluta, y avisa a
# la consola de supervision (si hay una escuchando) que termino.
_AL_TERMINAR_TAREA = None


def _tarea_lista(ruta=None):
    def callback(salida):
        if ruta is not None:
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(str(salida.raw))
        if _AL_TERMINAR_TAREA is not None:
            _AL_TERMINAR_TAREA(salida)
    return callback


# ==========================================================================
# Seleccion del modelo de lenguaje
# ==========================================================================
def construir_llm() -> LLM:
    proveedor = os.getenv("SIGMA_LLM", "stub").lower()

    if proveedor == "stub":
        # Servidor local determinista. El Crew, los agentes, las tareas y las
        # llamadas a herramientas son reales; solo el razonamiento esta guiado
        # por un guion fijo, para que la corrida sea reproducible y sin costo.
        import llm_stub
        try:
            llm_stub.iniciar_en_hilo()
        except OSError:
            pass    # ya esta escuchando (el modulo se recargo desde la consola)
        return LLM(model="openai/sigma-stub",
                   base_url=f"http://127.0.0.1:{llm_stub.PUERTO}/v1",
                   api_key="stub-sin-clave",
                   temperature=0.0)

    if proveedor == "ollama":
        # sigma-qwen25 = qwen2.5:7b con num_ctx ampliado (ver Modelfile.qwen25).
        # El contexto por omision de Ollama es corto y truncaria los prompts
        # del Crew sin avisar.
        modelo = os.getenv("SIGMA_MODELO", "sigma-qwen25")
        return LLM(model=f"ollama/{modelo}",
                   base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
                   temperature=0.2,
                   # Un modelo de 7B puede entrar en un bucle de generacion
                   # que no termina: en una corrida cada llamada agoto los
                   # 10 min del servidor y el Crew quedo colgado media hora.
                   # El tope de tokens y el timeout hacen que falle rapido y
                   # CrewAI reintente.
                   max_tokens=3000,
                   timeout=240)

    if proveedor == "gemini":
        # Opcion para la nube (Streamlit Cloud no puede correr Ollama).
        return LLM(model=os.getenv("SIGMA_MODELO", "gemini/gemini-2.5-flash"),
                   temperature=0.2, api_key=os.getenv("GEMINI_API_KEY"))

    if proveedor == "openai":
        return LLM(model=os.getenv("SIGMA_MODELO", "gpt-4o-mini"), temperature=0.2)

    if proveedor == "anthropic":
        return LLM(model=os.getenv("SIGMA_MODELO",
                                   "anthropic/claude-sonnet-4-5"), temperature=0.2)

    raise ValueError(f"SIGMA_LLM desconocido: {proveedor}")


LLM_CREW = construir_llm()

# Delegacion del Ingeniero hacia el Analista (ver Bloque B). Con un modelo
# local de ~8B la delegacion confundio a los agentes en la corrida de prueba,
# por lo que queda apagada por omision; SIGMA_DELEGACION=1 la activa.
DELEGACION_INGENIERO = os.getenv("SIGMA_DELEGACION", "0") == "1"


# ==========================================================================
# Esquema de la propuesta. Se usa como output_pydantic de las tareas del
# Ingeniero para que la salida sea JSON valido y no prosa: un modelo pequeno
# tiende a explicar lo que hizo en lugar de entregar el artefacto.
# ==========================================================================
class PlanInterseccion(BaseModel):
    ciclo_s: int
    verde_principal_s: int
    verde_secundario_s: int
    verde_peatonal_s: int
    desfase_s: int


# ==========================================================================
# Guardrail de las propuestas: lo que es calculo no se deja a la opinion.
# ==========================================================================
# Los planes (ciclo, verdes, desfases) los calcula calculadora_webster y
# cumplen R0-R6 por construccion; el criterio del Ingeniero esta en los pesos
# de utilidad y el umbral de spillback. En 3 de 5 corridas con qwen2.5:7b el
# modelo altero los planes pese a la instruccion de copiarlos y la propuesta
# quedo invalida. El guardrail le devuelve el error con los valores exactos
# (2 intentos) y, como ultimo recurso, los fija el codigo, de modo que la
# tarea nunca falla ni se cuelga. Todo cambio hecho por codigo queda anotado
# en la justificacion. Con el stub no se aplica: su propuesta v1.1 es
# deliberadamente invalida para demostrar el veto.
_INTENTOS_GUARDRAIL: dict[str, int] = {}
_PESO_MAX = 5.0
_UMBRAL_MIN, _UMBRAL_MAX = 0.3, 0.95


def _guardrail_propuesta(nombre: str):
    def guardrail(salida) -> tuple[bool, Any]:
        base = calcular_planes_factibles()["propuesta_base"]
        try:
            prop = json.loads(_extraer_json(str(salida.raw)))
        except ValueError:
            return False, "Responde unicamente con el JSON de la propuesta."

        problemas = []
        if prop.get("planes_por_interseccion") != base["planes_por_interseccion"]:
            problemas.append("planes_por_interseccion")
        if prop.get("periodo_decision_s") != base["periodo_decision_s"]:
            problemas.append("periodo_decision_s")
        umbral = prop.get("umbral_spillback_pct")
        if not isinstance(umbral, (int, float)) or not _UMBRAL_MIN <= umbral <= _UMBRAL_MAX:
            problemas.append("umbral_spillback_pct")
        pesos = prop.get("pesos_utilidad", {})
        if any(not isinstance(v, (int, float)) or not 0 <= v <= _PESO_MAX
               for v in pesos.values()):
            problemas.append("pesos_utilidad")
        if not problemas:
            return True, str(salida.raw)

        _INTENTOS_GUARDRAIL[nombre] = _INTENTOS_GUARDRAIL.get(nombre, 0) + 1
        if _INTENTOS_GUARDRAIL[nombre] <= 2:
            return False, (
                f"Modificaste valores que no debias o que estan fuera de rango: "
                f"{', '.join(problemas)}. Usa EXACTAMENTE planes_por_interseccion="
                f"{json.dumps(base['planes_por_interseccion'])} y "
                f"periodo_decision_s={base['periodo_decision_s']}. "
                f"umbral_spillback_pct debe estar entre {_UMBRAL_MIN} y "
                f"{_UMBRAL_MAX} (fraccion, no porcentaje) y cada peso entre 0 y "
                f"{_PESO_MAX}. Responde unicamente con el JSON.")

        # Ultimo recurso: el codigo fija lo que el modelo no logro respetar.
        prop["planes_por_interseccion"] = base["planes_por_interseccion"]
        prop["periodo_decision_s"] = base["periodo_decision_s"]
        if "umbral_spillback_pct" in problemas:
            prop["umbral_spillback_pct"] = base["umbral_spillback_pct"]
        if "pesos_utilidad" in problemas:
            prop["pesos_utilidad"] = {
                k: min(max(v, 0), _PESO_MAX) if isinstance(v, (int, float))
                else base["pesos_utilidad"].get(k, 1.0)
                for k, v in pesos.items()}
        prop.setdefault("justificacion", {})
        if isinstance(prop["justificacion"], dict):
            prop["justificacion"]["restricciones_fijadas_por_codigo"] = (
                "El modelo no respeto: " + ", ".join(problemas)
                + ". Esos valores se tomaron de calculadora_webster / "
                  "configuracion vigente.")
        return True, json.dumps(prop, ensure_ascii=False)
    return guardrail


class PesosUtilidad(BaseModel):
    w_veh_atendidos: float
    w_peat_atendidos: float
    w_espera_veh: float
    w_espera_peat: float
    w_ocupacion_aguas_abajo: float


class PlanesCircuito(BaseModel):
    """Las cuatro intersecciones son obligatorias: en una corrida el modelo
    entrego solo I1 e I4 y la propuesta paso por incompleta."""
    I1: PlanInterseccion
    I2: PlanInterseccion
    I3: PlanInterseccion
    I4: PlanInterseccion


class PropuestaConfig(BaseModel):
    version: str
    pesos_utilidad: PesosUtilidad
    planes_por_interseccion: PlanesCircuito
    umbral_spillback_pct: float    # fraccion de la capacidad del tramo, 0-1
    periodo_decision_s: int
    justificacion: dict[str, str]


# ==========================================================================
# BLOQUE B — Agentes especializados
# ==========================================================================

analista = Agent(
    role="Analista de Telemetria Vial",
    goal=(
        "Determinar, con evidencia estadistica y no con impresiones, en que "
        "interseccion y bajo que escenario el desempeno de SIGMA-Vial se aparta "
        "de sus metas, y senalar si la causa esta en la configuracion o en la "
        "demanda."
    ),
    backstory=(
        "Ingeniera de transito con doce anios calibrando modelos microscopicos "
        "para municipios. Aprendio a desconfiar de los promedios: en su primer "
        "proyecto reporto una mejora del 18% que resulto ser el efecto de dos "
        "replicas atipicas, y desde entonces no afirma nada que no sostenga una "
        "prueba estadistica. Trabaja por interseccion y por escenario, nunca con "
        "el agregado del circuito, porque sabe que un promedio global esconde "
        "justo la interseccion que falla."
    ),
    tools=[HERRAMIENTAS["telemetria"], HERRAMIENTAS["comparador"],
           HERRAMIENTAS["contexto"]],
    llm=LLM_CREW,
    allow_delegation=False,   # produce evidencia; no dirige a nadie
    verbose=True,
    max_iter=12,
)

ingeniero = Agent(
    role="Ingeniero de Planes de Senalizacion",
    goal=(
        "Convertir el diagnostico en una propuesta concreta y justificada de "
        "nuevos pesos de la funcion de utilidad y nuevos planes de semaforo, "
        "maximizando el desempeno sin inventar valores que la normativa no "
        "permita."
    ),
    backstory=(
        "Formado en la escuela clasica de ingenieria de transito: calcula "
        "Webster a mano antes de confiar en cualquier optimizador. Paso cinco "
        "anios operando un centro de control urbano, donde aprendio que un "
        "parametro que mejora una interseccion suele empeorar la siguiente, y "
        "por eso razona siempre sobre el circuito completo. Cuando el "
        "diagnostico que recibe no le alcanza para decidir, lo pide; no rellena "
        "el hueco con supuestos."
    ),
    tools=[HERRAMIENTAS["webster"], HERRAMIENTAS["contexto"],
           HERRAMIENTAS["telemetria"]],
    llm=LLM_CREW,
    # Unico agente al que se le puede habilitar delegacion: pedir al Analista
    # un desglose adicional cuando el diagnostico no basta (ver arriba).
    allow_delegation=DELEGACION_INGENIERO,
    verbose=True,
    max_iter=12,
)

auditor = Agent(
    role="Auditor Normativo y de Seguridad Vial",
    goal=(
        "Impedir que se publique cualquier configuracion que viole una regla "
        "dura o la normativa de cruce peatonal, sin importar cuanto mejore las "
        "metricas de desempeno."
    ),
    backstory=(
        "Viene de auditoria de seguridad vial, no de optimizacion de trafico. "
        "Investigo un atropellamiento en un cruce cuyo tiempo de verde peatonal "
        "se habia recortado tres segundos para mejorar un indicador de flujo, y "
        "esa investigacion define como trabaja: el desempeno nunca justifica una "
        "excepcion a una regla dura. No emite juicios propios sobre si un plan "
        "cumple; ejecuta el validador y reporta su veredicto literal, porque una "
        "regla dura verificada por opinion deja de ser una regla dura."
    ),
    tools=[HERRAMIENTAS["validador"], HERRAMIENTAS["contexto"],
           HERRAMIENTAS["publicador"]],
    llm=LLM_CREW,
    allow_delegation=False,   # el veto no se delega
    verbose=True,
    max_iter=12,
)

sintetizador = Agent(
    role="Sintetizador de Movilidad",
    goal=(
        "Entregar a la coordinacion operativa del campus un reporte que explique "
        "que cambio, por que, con que evidencia y a costa de que, en lenguaje "
        "comprensible para quien no es ingeniero de transito."
    ),
    backstory=(
        "Comunicador tecnico en proyectos de movilidad urbana. Su criterio es "
        "que una decision que no se puede explicar no se puede sostener en una "
        "reunion, y que ocultar el costo de un cambio destruye la confianza mas "
        "rapido que el cambio mismo. Por eso siempre escribe que se sacrifico "
        "para obtener la mejora."
    ),
    tools=[],   # solo sintetiza el contexto que recibe; no toca los datos
    llm=LLM_CREW,
    allow_delegation=False,
    verbose=True,
    max_iter=8,
)


# ==========================================================================
# BLOQUE C — Tareas y flujo de informacion
# ==========================================================================

t_diagnostico = Task(
    description=(
        "Diagnostica el desempeno de SIGMA-Vial en el escenario {escenario}.\n"
        "1. Lee la configuracion vigente para saber con que parametros se "
        "   obtuvieron estos resultados.\n"
        "2. Lee la telemetria del escenario y compara SIGMA-Vial contra las dos "
        "   lineas base en espera peatonal y en eventos de spillback.\n"
        "3. Revisa la bitacora del Supervisor por si hay vetos o fallas "
        "   concentrados en alguna interseccion.\n"
        "Reporta solo diferencias con p<0.05 en la prueba de Wilcoxon. "
        "Distingue si la causa probable es la configuracion o la demanda.\n"
        "El comparador ya dice en palabras si SIGMA-Vial MEJORA o EMPEORA en "
        "cada intersección (campo 'lectura') y lista en 'empeoran' los casos "
        "peores que la linea base: usa esa lectura, no reinterpretes el signo. "
        "La prioridad_de_atencion debe poner primero las intersecciones donde "
        "SIGMA-Vial EMPEORA con significancia; si en una interseccion "
        "SIGMA-Vial mejora en todo, su severidad es baja."
    ),
    expected_output=(
        "JSON con las claves: escenario_analizado, replicas, hallazgos (lista "
        "con interseccion, variacion porcentual frente a la linea base en "
        "espera peatonal, espera vehicular y spillback, p_wilcoxon y "
        "severidad), conclusion y prioridad_de_atencion (lista ordenada de "
        "intersecciones)."
    ),
    agent=analista,
    callback=_tarea_lista(),
)

t_propuesta_v1 = Task(
    description=(
        "Con el diagnostico recibido, propon una nueva version de la "
        "configuracion. Ajusta los pesos de la funcion de utilidad y los planes "
        "de las intersecciones senaladas como prioritarias. Consulta la "
        "topologia para considerar la longitud de los tramos y el calendario "
        "academico para los pulsos de demanda.\n"
        "Llama PRIMERO a calculadora_webster (sin argumentos). Devuelve "
        "'propuesta_base': una configuracion completa que ya cumple las "
        "reglas duras (cuatro intersecciones, ciclo comun, verdes y desfases). "
        "Copiala tal cual y modifica SOLO lo que el diagnostico justifique: "
        "los pesos de utilidad (cada peso entre 0 y 5; si el diagnostico "
        "muestra que la espera peatonal EMPEORA en alguna interseccion, sube "
        "w_espera_peat, y si el spillback EMPEORA sube "
        "w_ocupacion_aguas_abajo; si nada empeora deja los pesos como estan; "
        "justifica cada cambio con la cifra del diagnostico) y, si hace falta, "
        "umbral_spillback_pct (fraccion de la capacidad del tramo, entre 0.3 y "
        "0.95). NO cambies planes_por_interseccion ni periodo_decision_s: "
        "cualquier cambio ahi las invalida. Pon version 'v1.1' y llena "
        "'justificacion' con una entrada por cada parametro que cambies. "
        "Cada parametro que cambies debe llevar su "
        "justificacion. No propongas valores fuera de la normativa. "
        "Responde unicamente con el JSON, sin texto adicional."
    ),
    expected_output=(
        "JSON con version, pesos_utilidad, planes_por_interseccion (ciclo_s, "
        "verde_principal_s, verde_secundario_s, verde_peatonal_s, desfase_s "
        "por interseccion), umbral_spillback_pct, periodo_decision_s y un "
        "objeto justificacion con una entrada por parametro modificado."
    ),
    agent=ingeniero,
    context=[t_diagnostico],       # depende de la salida del diagnostico
    output_pydantic=PropuestaConfig,
    guardrail=(None if os.getenv("SIGMA_LLM", "stub").lower() == "stub"
               else _guardrail_propuesta("v1")),
    callback=_tarea_lista(DIR_SALIDAS / "propuesta_v1.json"),
)

t_auditoria_v1 = Task(
    description=(
        "Audita la propuesta recibida contra las reglas duras y la normativa.\n"
        "1. Lee la normativa vigente.\n"
        "2. Valida PRIMERO la configuracion vigente, para dejar constancia de "
        "   si lo que hoy se ejecuta cumple o no "
        "   (validador_reglas_duras con archivo='vigente').\n"
        "3. Valida la propuesta recibida "
        "   (validador_reglas_duras con archivo='propuesta_v1').\n"
        "Reporta el veredicto literal del validador. No sustituyas su resultado "
        "por tu propio criterio ni apruebes nada por mejora de desempeno. Si "
        "rechazas, incluye una instruccion concreta para el Ingeniero que "
        "explique la causa raiz de la violacion, no solo que regla fallo."
    ),
    expected_output=(
        "JSON con ciclo_de_auditoria, dictamen_configuracion_vigente "
        "(veredicto, violaciones, observacion), dictamen_propuesta (veredicto, "
        "violaciones), reglas_evaluadas, autorizacion_de_publicacion booleana "
        "y, si procede, instruccion_al_ingeniero."
    ),
    agent=auditor,
    callback=_tarea_lista(),
    context=[t_propuesta_v1],
)

t_propuesta_v2 = Task(
    description=(
        "Si el dictamen recibido autorizo la publicacion, devuelve la misma "
        "propuesta sin cambios. Si la rechazo, corrige la propuesta atacando la "
        "causa raiz que senala el Auditor, no solo el sintoma. Consulta la "
        "normativa para no reincidir. Documenta en la justificacion que "
        "revelo el veto, porque un limite que obliga a rediseniar es un "
        "hallazgo que la coordinacion debe conocer. Responde unicamente con "
        "el JSON, sin texto adicional."
    ),
    expected_output=(
        "JSON con la misma estructura de la propuesta anterior, version "
        "incrementada, y un objeto justificacion que incluya tanto los "
        "parametros modificados como el costo asumido por el cambio."
    ),
    agent=ingeniero,
    # Recibe el problema original, la propuesta rechazada y el veto. Sin
    # t_propuesta_v1 no podria devolver "la misma propuesta" si fue aprobada,
    # ni corregir con fidelidad la rechazada.
    context=[t_diagnostico, t_propuesta_v1, t_auditoria_v1],
    output_pydantic=PropuestaConfig,
    guardrail=(None if os.getenv("SIGMA_LLM", "stub").lower() == "stub"
               else _guardrail_propuesta("v2")),
    callback=_tarea_lista(DIR_SALIDAS / "propuesta_v2.json"),
)

t_auditoria_v2 = Task(
    description=(
        "Audita la propuesta corregida. Valida con validador_reglas_duras "
        "(archivo='propuesta_v2'). Despues llama a publicador_configuracion "
        "con archivo='ultima_valida': publica la propuesta v2 si cumple las "
        "reglas duras y, si no, la v1 si esa si las cumple; si ninguna las "
        "cumple, no publica nada. Reporta el veredicto literal del validador "
        "y el resultado de la publicacion, incluida la fuente publicada."
    ),
    expected_output=(
        "JSON con ciclo_de_auditoria, dictamen_propuesta (veredicto, "
        "violaciones, advertencias), reglas_evaluadas, "
        "autorizacion_de_publicacion y publicacion (resultado del publicador)."
    ),
    agent=auditor,
    callback=_tarea_lista(),
    context=[t_propuesta_v2],
)

t_sintesis = Task(
    description=(
        "Redacta el reporte del ciclo de supervision para la coordinacion "
        "operativa del campus, a partir del diagnostico, las dos propuestas y "
        "los dos dictamenes. Explica el hallazgo principal, los hallazgos de "
        "seguridad, por que la primera propuesta fue rechazada, que se cambio "
        "y a costa de que. Incluye una seccion de trazabilidad que diga de que "
        "archivo salio cada cosa.\n"
        "REGLAS: usa unicamente cifras que aparezcan en el contexto recibido. "
        "NO incluyas tabla de parametros (el sistema la agrega despues, "
        "generada por codigo). NO inventes costos monetarios, tiempos de "
        "implementacion, horas de trabajo ni mejoras cuantificadas: el costo "
        "de un cambio es lo que se sacrifica en desempeno o en restricciones, "
        "y si no se midio, dilo expresamente. No afirmes cumplimiento de reglas "
        "que el dictamen no confirme. Responde en Markdown, sin envolverlo en "
        "una cerca de codigo."
    ),
    expected_output=(
        "Reporte en Markdown con encabezados, una tabla comparativa de "
        "parametros, una seccion explicita sobre el costo asumido y una "
        "seccion final de trazabilidad."
    ),
    agent=sintetizador,
    context=[t_diagnostico, t_propuesta_v1, t_auditoria_v1,
             t_propuesta_v2, t_auditoria_v2],
    callback=_tarea_lista(DIR_REPORTE),
)


# ==========================================================================
# BLOQUE A — Orquestacion
# ==========================================================================
# Process.sequential: el flujo tiene una cadena de dependencias estricta
# (diagnosticar -> proponer -> auditar -> corregir -> auditar -> comunicar) en
# la que cada etapa consume el producto de la anterior. Un proceso jerarquico
# anadiria un agente gerente que decide a quien asignar cada paso, lo que aqui
# no aporta (el orden no es una incognita) y si introduce no determinismo e
# inflacion de tokens.
#
# El ciclo de correccion no se obtiene con un bucle del motor, sino
# desdoblando la revision en dos pasadas y pasando el dictamen de la primera
# como contexto de la re-propuesta. Es un lazo de retroalimentacion explicito
# y acotado: no puede iterar indefinidamente, y queda auditable paso por paso.
# La flexibilidad restante se obtiene con allow_delegation en el Ingeniero,
# que puede devolverle trabajo al Analista sin romper la cadena.
crew = Crew(
    agents=[analista, ingeniero, auditor, sintetizador],
    tasks=[t_diagnostico, t_propuesta_v1, t_auditoria_v1,
           t_propuesta_v2, t_auditoria_v2, t_sintesis],
    process=Process.sequential,
    verbose=True,
    tracing=False,   # evita que CrewAI pregunte si comparte la traza (20 s)
)


def limpiar_salidas():
    """Borra lo que dejo una corrida anterior, para que la ausencia de
    configuracion_aprobada.json signifique 'esta corrida no publico nada' y no
    se confunda con el resultado de otra (p. ej. la del stub)."""
    _INTENTOS_GUARDRAIL.clear()
    for f in list(DIR_SALIDAS.glob("*.json")) + list(DIR_SALIDAS.glob("*.txt")) + [
            DIR_SALIDAS.parent / "datos" / "configuracion_aprobada.json"]:
        f.unlink(missing_ok=True)


def ejecutar(escenario: str = "pico_matutino", al_terminar_tarea=None):
    """Corrida completa: limpia salidas previas, ejecuta el Crew y verifica el
    reporte del Sintetizador con codigo determinista (ver verificacion.py).
    Es el punto de entrada que usan tanto main() como el notebook.

    Jupyter y Colab ya tienen un bucle de eventos corriendo, y CrewAI 1.15
    rechaza crew.kickoff() sincrono dentro de uno. En ese caso se ejecuta en
    un hilo aparte, que no tiene bucle."""
    # Fase 4: la consola de supervision pasa un callback para pintar el avance
    # de cada tarea. No cambia el flujo del Crew.
    global _AL_TERMINAR_TAREA
    _AL_TERMINAR_TAREA = al_terminar_tarea
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _ejecutar_sync(escenario)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_ejecutar_sync, escenario).result()


def _ejecutar_sync(escenario: str):
    # Un modelo local pequeno a veces devuelve una respuesta vacia o una
    # llamada a herramienta mal formada (Ollama responde 500) y CrewAI aborta
    # toda la corrida. Se reintenta la corrida completa hasta 3 veces.
    for intento in (1, 2, 3):
        limpiar_salidas()
        try:
            resultado = crew.kickoff(inputs={"escenario": escenario})
            break
        except Exception as error:      # noqa: BLE001
            print(f"[reintento {intento}/3] La corrida fallo: "
                  f"{type(error).__name__}: {str(error)[:200]}")
            if intento == 3:
                raise
    # Se excluye la ultima tarea (el reporte): sus cifras son lo que se verifica.
    fuentes = [str(t.output.raw) for t in crew.tasks[:-1] if t.output]
    # Trazabilidad: la salida textual de cada tarea queda en salidas/.
    nombres = ["diagnostico", "propuesta_v1", "auditoria_v1",
               "propuesta_v2", "auditoria_v2", "sintesis"]
    for i, (t, nombre) in enumerate(zip(crew.tasks, nombres), 1):
        if t.output:
            (DIR_SALIDAS / f"tarea_{i}_{nombre}.txt").write_text(
                str(t.output.raw), encoding="utf-8")
    print(verificar_reporte(DIR_REPORTE, fuentes))
    return resultado


def main():
    escenario = sys.argv[1] if len(sys.argv) > 1 else "pico_matutino"
    print("=" * 78)
    print(f"SIGMA-Vial · ciclo de supervision tactica · escenario: {escenario}")
    print(f"Proveedor LLM: {os.getenv('SIGMA_LLM', 'stub')}")
    print("=" * 78)

    resultado = ejecutar(escenario)

    print("\n" + "=" * 78)
    print("RESULTADO CONSOLIDADO DEL CREW")
    print("=" * 78)
    print(resultado)
    return resultado


if __name__ == "__main__":
    main()

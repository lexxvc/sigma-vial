# Vista 1 · Configurar ciclo (capa de entrada)
import os, shutil

import pandas as pd
import streamlit as st

from vistas import comun as C

st.title("Configurar ciclo de supervisión")
st.write("Elige el escenario y los datos de entrada. El equipo de agentes revisa la jornada, propone ajustes "
         "y los audita contra las reglas duras antes de que tú decidas.")
C.aviso_datos()
co = C.corrida()
izq, der = st.columns([5, 4], gap="large")

with izq:
    st.subheader("Parámetros de entrada")
    a, b = st.columns(2)
    escenario = a.selectbox("Escenario a analizar", list(C.ESCENARIOS), format_func=C.ESCENARIOS.get,
                            help="Variable {escenario} del kickoff del Crew.")
    b.number_input("Umbral de significancia (p)", value=0.05, disabled=True,
                   help="Fijo en la descripción de la Tarea 1 (Wilcoxon pareada sobre 10 réplicas).")

    st.markdown("**Telemetría de la jornada**")
    archivo = st.file_uploader("Reemplazar metricas_simulacion.csv", type="csv", label_visibility="collapsed")
    problemas = []
    if archivo is not None:
        contenido = archivo.getvalue()
        problemas = C.validar_csv(contenido)
        if problemas:
            st.error("**El archivo no es válido.** " + " ".join(problemas) +
                     " Carga el CSV completo o quita el archivo para usar la telemetría actual.", icon=":material/error:")
        elif st.session_state.get("csv_cargado") != archivo.file_id:
            destino = os.path.join(C.DATOS, "metricas_simulacion.csv")
            if not os.path.exists(destino + ".original"):
                shutil.copy(destino, destino + ".original")
            with open(destino, "wb") as f:
                f.write(contenido)
            st.session_state.csv_cargado = archivo.file_id
            st.success("Telemetría reemplazada y validada.", icon=":material/check_circle:")
    if not problemas:
        st.info(f"`metricas_simulacion.csv` · {C.resumen_csv()} × 10 réplicas", icon=":material/description:")

    motor = st.radio("Motor de razonamiento", ["stub", "gemini", "ollama"], horizontal=False,
                     format_func={"stub": "Guion fijo (stub) · solo depura el flujo, no demuestra razonamiento",
                                  "gemini": "Gemini 2.5 Flash · requiere tu API key",
                                  "ollama": "qwen2.5:7b vía Ollama · solo en una máquina con Ollama"}.get)
    clave = ""
    if motor == "gemini":
        clave = st.text_input("API key de Gemini", type="password",
                              help="Solo se usa para esta corrida; no se guarda.") or os.environ.get("GEMINI_API_KEY", "")
    a, b = st.columns(2)
    deleg = a.toggle("Delegación del Ingeniero", value=False, help="Apagada por evidencia de la Fase 3 (D.9).")
    b.text_input("Pasadas del lazo de corrección", "2 (fijo por diseño)", disabled=True)

    st.markdown("**Reglas duras aplicadas** (no editables desde la consola)")
    st.markdown(" ".join(f":gray-badge[{r} {n}]" for r, n in C.REGLAS))

    falta_clave = motor == "gemini" and not clave
    ocupado = co.estado == "corriendo"
    if st.button("Iniciar ciclo de supervisión", type="primary", disabled=bool(problemas) or falta_clave or ocupado):
        for k in ("decision", "ajustada", "eval_sumo", "motivo"):
            st.session_state.pop(k, None)
        co.iniciar(escenario, motor, clave, deleg)
        st.switch_page("vistas/ejecucion.py")
    if problemas:
        st.caption("Corrige la telemetría para habilitar el inicio.")
    elif falta_clave:
        st.caption("Escribe la API key para usar Gemini.")
    elif ocupado:
        st.caption("Ya hay una corrida en curso; síguela en Ejecución de agentes.")
    else:
        st.caption("Seis tareas en orden. Nada se libera sin tu aprobación.")

with der:
    v = C.vigente() or {}
    d = C.dictamen("vigente")
    n = len(d.get("violaciones", []))
    st.subheader(f"Configuración vigente · {v.get('version', '—')}")
    if n:
        st.error(f"{n} violaciones de reglas duras", icon=":material/warning:")
    else:
        st.success("Cumple R0 a R6", icon=":material/check_circle:")
    planes = v.get("planes_por_interseccion", {})
    st.dataframe(pd.DataFrame(planes).T.rename(columns={
        "ciclo_s": "Ciclo", "verde_principal_s": "V. princ.", "verde_secundario_s": "V. sec.",
        "verde_peatonal_s": "V. peat.", "desfase_s": "Desfase"}), width="stretch")
    st.caption("Segundos. Pesos de utilidad: " + " · ".join(f"`{k}` {x}" for k, x in v.get("pesos_utilidad", {}).items()))
    st.page_link("vistas/operacion.py", label="Ver el circuito en el mapa y el simulador", icon=":material/traffic:")

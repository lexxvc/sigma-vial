# ============================================================
# SIGMA-Vial · Consola de supervisión + operación en vivo (Streamlit)
# Local:  streamlit run streamlit_app.py
# Nube:   Streamlit Community Cloud → repo de GitHub → este archivo
#
# Dos capas, como en la documentación del proyecto:
#   · Operación en vivo  → mapa del campus y simulador SUMO (tiempo real, sin LLM)
#   · Supervisión        → equipo CrewAI de la Fase 3 y decisión humana (Fase 4)
# ============================================================
import os

import streamlit as st

st.set_page_config(page_title="SIGMA-Vial", page_icon="🚦", layout="wide")

try:                                    # clave opcional para correr el Crew con Gemini en la nube
    if "GEMINI_API_KEY" in st.secrets:
        os.environ.setdefault("GEMINI_API_KEY", st.secrets["GEMINI_API_KEY"])
except Exception:
    pass

paginas = {
    "Supervisión": [
        st.Page("vistas/configurar.py", title="1 · Configurar ciclo", icon=":material/tune:", default=True),
        st.Page("vistas/ejecucion.py", title="2 · Ejecución de agentes", icon=":material/account_tree:"),
        st.Page("vistas/resultados.py", title="3 · Resultados y decisión", icon=":material/fact_check:"),
        st.Page("vistas/auditoria.py", title="4 · Auditoría y trazabilidad", icon=":material/gavel:"),
    ],
    "Tiempo real": [
        st.Page("vistas/operacion.py", title="Operación en vivo", icon=":material/traffic:"),
    ],
}
st.navigation(paginas).run()

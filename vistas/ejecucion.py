# Vista 2 · Ejecución de agentes (capa de procesamiento)
import os

import streamlit as st

from vistas import comun as C

st.title("Ejecución de agentes")
st.write("Sigue qué agente trabaja y qué decide el código determinista. Cada salida queda guardada y atribuida.")
co = C.corrida()


@st.fragment(run_every=1.0 if co.estado == "corriendo" else None)
def panel():
    n, total = co.hechas, len(C.TAREAS)
    s = co.segundos()
    if co.estado == "terminada":
        st.success(f"**Ciclo terminado: {total} de {total} tareas** en {s // 60:02d}:{s % 60:02d}. "
                   "Revisa la candidata antes de liberarla.", icon=":material/check_circle:")
    elif co.estado == "error":
        st.error(f"**La corrida falló.** {co.error}", icon=":material/error:")
    elif co.estado == "inactiva":
        st.info("No hay una corrida en curso. Abajo se muestra la última que quedó en disco.", icon=":material/schedule:")
    a, b, c = st.columns([4, 1, 1])
    a.progress(n / total if co.estado != "inactiva" else 0.0,
               text=(f"Tarea {min(n + 1, total)} de {total} en curso · {s // 60:02d}:{s % 60:02d}" if co.estado == "corriendo"
                     else f"Escenario: {C.ESCENARIOS.get(co.escenario, co.escenario)} · Motor: {co.motor}"))
    if c.button("Revisar resultados", type="primary", disabled=co.estado == "corriendo", width="stretch"):
        st.switch_page("vistas/resultados.py")
    if b.button("Nuevo ciclo", disabled=co.estado == "corriendo", width="stretch"):
        st.switch_page("vistas/configurar.py")

    izq, der = st.columns([5, 4], gap="large")
    with izq:
        st.subheader("Tareas del equipo")
        st.caption("Process.sequential")
        for i, (tarea, agente, ctx, salida) in enumerate(C.TAREAS):
            existe = os.path.exists(os.path.join(C.DIR_SUP, salida))
            if co.estado == "corriendo":
                estado = "complete" if i < n else "running" if i == n else "error"
                marca = ":green-badge[Completada]" if i < n else ":blue-badge[En curso]" if i == n else ":gray-badge[En espera]"
            else:
                estado = "complete" if existe else "error"
                marca = ":green-badge[Completada]" if existe else ":gray-badge[Sin salida]"
            with st.container(border=True):
                st.markdown(f"**{i + 1}. {tarea}** {marca}  \n:gray[{agente} · contexto: {ctx}]")
                if estado == "complete":
                    st.caption(f"`{salida}`")
    with der:
        st.subheader("Bitácora")
        st.code("\n".join(co.eventos) or C.bitacora_herramientas() or "La bitácora aparece cuando inicia la corrida.",
                language=None, height=230)
        st.subheader("Barreras deterministas")
        st.caption("Código Python, sin LLM")
        if co.estado == "corriendo":
            st.write("Se evalúan al terminar la corrida.")
        else:
            dv, d1, d2 = C.dictamen("vigente"), C.dictamen("propuesta_v1"), C.dictamen("propuesta_v2")
            def chip(nombre, d):
                ver = d["veredicto"]
                color = "green" if ver == "APROBADO" else "red" if ver == "RECHAZADO" else "gray"
                return f":{color}-badge[{nombre}: {ver.lower()}]"
            st.markdown(f"**Validador R0–R6** · {chip('vigente', dv)} {chip('v1', d1)} {chip('v2', d2)}")
            pub = C.leer_json(os.path.join(C.DATOS, "configuracion_aprobada.json"))
            st.markdown("**Publicador** · " + (f":green-badge[candidata {pub.get('version', '')} publicada]" if pub
                                              else ":red-badge[no publicó ninguna configuración]"))
            if os.path.exists(C.REPORTE):
                rep = open(C.REPORTE, encoding="utf-8").read()
                falt = C.V.archivos_inexistentes(rep)
                st.markdown("**Verificación del reporte** · " + (f":orange-badge[{len(falt)} archivos citados no existen]"
                                                                 if falt else ":green-badge[archivos citados existen]"))
    if co.estado != "corriendo" and st.session_state.get("_viva"):
        st.session_state._viva = False
        st.rerun()


st.session_state._viva = co.estado == "corriendo"
panel()

# Vista 4 · Auditoría y trazabilidad
import os

import pandas as pd
import streamlit as st

from vistas import comun as C

st.title("Auditoría y trazabilidad")
st.write("Veredictos literales del validador, origen de cada salida y lo que cada agente puede y no puede hacer.")

fuentes = {"Vigente": C.dictamen("vigente"), "Propuesta v1": C.dictamen("propuesta_v1"), "Propuesta v2": C.dictamen("propuesta_v2")}
izq, der = st.columns([5, 4], gap="large")
with izq:
    st.subheader("Dictamen por regla dura")
    st.caption("Emitido por `validador_reglas_duras` · reportado por el Auditor Normativo")
    por = {n: C.violaciones_por_regla(d) for n, d in fuentes.items()}
    filas = [{"Regla": f"{r} {nom}", **{n: ("Cumple" if not por[n][r] else f"{por[n][r]} violación(es)")
                                        if fuentes[n]["veredicto"] != "NO DISPONIBLE" else "—" for n in fuentes}} for r, nom in C.REGLAS]
    filas.append({"Regla": "Veredicto", **{n: f"{d['veredicto']}" + (f" · {len(d['violaciones'])}" if d["violaciones"] else "")
                                           for n, d in fuentes.items()}})

    def color(v):
        if "violación" in str(v) or "RECHAZADO" in str(v):
            return "background-color: rgba(179,38,30,.14)"
        if v == "Cumple" or "APROBADO" in str(v):
            return "background-color: rgba(23,117,74,.14)"
        return ""
    st.dataframe(pd.DataFrame(filas).style.map(color, subset=list(fuentes)), hide_index=True, width="stretch")
with der:
    cual = st.segmented_control("Violaciones de", list(fuentes), default="Vigente") or "Vigente"
    viol = fuentes[cual].get("violaciones", [])
    st.subheader(f"Violaciones · {cual.lower()} ({len(viol)})")
    if not viol:
        st.success("Sin violaciones.", icon=":material/check_circle:")
    for inter in C.INTER:
        propias = [v for v in viol if v.startswith(inter)]
        if propias:
            with st.expander(f"{inter} · {len(propias)}", expanded=inter == "I3"):
                for v in propias:
                    st.error(v.split(": ", 1)[-1], icon=":material/close:")
    for v in [v for v in viol if not v[:2] in C.INTER]:
        st.error(v, icon=":material/close:")

st.subheader("Trazabilidad del ciclo")
filas = [{"#": i, "Tarea": t, "Agente": a, "Contexto que consume": c, "Salida": s,
          "En disco": "sí" if os.path.exists(os.path.join(C.DIR_SUP, s)) else "no"} for i, (t, a, c, s) in enumerate(C.TAREAS, 1)]
filas.append({"#": 7, "Tarea": "Liberación a operación", "Agente": "Coordinación operativa (persona)",
              "Contexto que consume": "Reporte, dictámenes y advertencias", "Salida": "datos/configuracion_liberada.json",
              "En disco": {"liberada": "sí", "rechazada": "rechazada"}.get(C.decision(), "pendiente")})
st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch")

if os.path.exists(C.puente.RUTA_BITACORA):
    st.subheader("Bitácora de decisiones humanas")
    st.dataframe(pd.read_csv(C.puente.RUTA_BITACORA), hide_index=True, width="stretch")

st.subheader("Equipo de agentes")
st.caption("Límites impuestos por construcción (Bloque B de la Fase 3)")
AG = [("Analista de Telemetría Vial", ["lector_telemetria_sumo", "comparador_lineas_base", "lector_contexto_operativo"], "No puede publicar ni escribir configuración."),
      ("Ingeniero de Planes", ["calculadora_webster", "lector_contexto_operativo", "lector_telemetria_sumo"], "No puede validar ni publicar."),
      ("Auditor Normativo", ["validador_reglas_duras", "lector_contexto_operativo", "publicador_configuracion"], "No puede calcular métricas de desempeño."),
      ("Sintetizador de Movilidad", [], "Sin herramientas: no puede consultar datos nuevos.")]
for col, (nombre, tools, limite) in zip(st.columns(4), AG):
    with col.container(border=True):
        st.markdown(f"**{nombre}**")
        st.markdown("\n".join(f"- `{t}`" for t in tools) or "_Sin herramientas_")
        st.caption(limite)

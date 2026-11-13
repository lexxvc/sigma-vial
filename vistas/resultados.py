# Vista 3 · Resultados y decisión (capa de salida y control operativo)
import copy, json, os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from vistas import comun as C

co = C.corrida()
vig, can = C.vigente() or {}, C.candidata()
dec = C.decision()
METRICAS = {"espera_peat_media_s": "Espera peatonal", "espera_veh_media_s": "Espera vehicular",
            "spillback_eventos_h": "Spillback (eventos/h)"}

st.title("Resultados y decisión")
st.write(f"Resumen del ciclo sobre el escenario {C.ESCENARIOS.get(co.escenario, co.escenario).lower()}. "
         "Revisa la evidencia, lee el reporte con sus advertencias y decide si la candidata se libera.")
C.aviso_datos()

if co.estado == "corriendo":
    st.warning("Hay una corrida en curso. Los resultados se actualizan cuando termine.", icon=":material/schedule:")
    st.page_link("vistas/ejecucion.py", label="Ver la ejecución", icon=":material/account_tree:")
    st.stop()
if not can:
    st.error("**El Auditor no publicó ninguna configuración en este ciclo.** La vigente sigue en ejecución. "
             "Revisa los dictámenes en Auditoría y trazabilidad o inicia un nuevo ciclo.", icon=":material/block:")
    st.stop()

d_vig, d_can = C.dictamen("vigente"), C.dictamen(config=can)
n_vig, n_can = len(d_vig.get("violaciones", [])), len(d_can.get("violaciones", []))

# ---------------- diálogos ----------------
@st.dialog("Liberar la configuración")
def dlg_aprobar():
    st.write(f"La versión **{can.get('version', '')}** sustituye a **{vig.get('version', '')}** como configuración "
             "de la capa de tiempo real. La decisión queda a tu nombre en la bitácora.")
    st.warning("Lee las advertencias del reporte. La telemetría del diagnóstico es sintética.", icon=":material/warning:")
    quien = st.text_input("Tu nombre", key="quien")
    ok = st.checkbox("Revisé el dictamen del validador y las advertencias del reporte.")
    if st.button("Liberar configuración", type="primary", disabled=not (ok and quien.strip())):
        if C.dictamen(config=can)["veredicto"] != "APROBADO":       # segunda barrera, igual que el publicador
            st.error("El validador rechazó la configuración; no se libera.")
            return
        C.liberar(can, quien.strip())
        st.session_state.decision = "liberada"
        st.session_state.aviso = "Configuración liberada y registrada en la bitácora."
        st.rerun()


@st.dialog("Rechazar la candidata")
def dlg_rechazar():
    st.write("La configuración vigente seguirá en ejecución. El motivo queda en la bitácora.")
    quien = st.text_input("Tu nombre", key="quien_r")
    motivo = st.text_area("Motivo del rechazo", help="Mínimo 10 caracteres.")
    if st.button("Rechazar candidata", disabled=len(motivo.strip()) < 10 or not quien.strip()):
        C.rechazar(can, quien.strip(), motivo.strip())
        st.session_state.decision, st.session_state.motivo = "rechazada", motivo.strip()
        st.rerun()


@st.dialog("Ajustar pesos antes de liberar")
def dlg_ajustar():
    st.write("Puedes mover lo que el Ingeniero decide. Los planes de semáforo los fija la calculadora y no se editan aquí.")
    w = st.slider("Peso de espera peatonal · w_espera_peat", 0.0, 5.0, float(can["pesos_utilidad"]["w_espera_peat"]), 0.1,
                  help=f"Vigente: {vig.get('pesos_utilidad', {}).get('w_espera_peat', '—')}")
    u = st.slider("Umbral de spillback · umbral_spillback_pct", 0.30, 0.95, float(can["umbral_spillback_pct"]), 0.05)
    st.text_input("Planes por intersección", f"Ciclo {can['planes_por_interseccion']['I1']['ciclo_s']} s · calculados por calculadora_webster",
                  disabled=True)
    nueva = copy.deepcopy(can)
    nueva["pesos_utilidad"]["w_espera_peat"], nueva["umbral_spillback_pct"] = round(w, 2), round(u, 2)
    cambio = nueva != can
    d = C.dictamen(config=nueva)
    if d["veredicto"] == "APROBADO":
        st.success("Validador: APROBADO. R0 a R6 sin violaciones con estos valores.", icon=":material/check_circle:")
    else:
        st.error("Validador: RECHAZADO. " + " · ".join(d.get("violaciones", [])[:3]), icon=":material/error:")
    if st.button("Guardar ajuste", type="primary", disabled=not cambio or d["veredicto"] != "APROBADO"):
        nueva["ajuste_humano"] = True
        st.session_state.ajustada = nueva
        st.session_state.pop("eval_sumo", None)
        st.session_state.aviso = "Ajuste guardado y revalidado. Sigue pendiente de liberación."
        st.rerun()


if st.session_state.get("aviso"):
    st.toast(st.session_state.pop("aviso"), icon=":material/check_circle:")

# ---------------- estado y controles ----------------
if dec == "liberada":
    st.success("**Configuración liberada.** Ya puedes aplicarla en el simulador desde Operación en vivo "
               "(plan «Supervisión»).", icon=":material/check_circle:")
elif dec == "rechazada":
    st.error(f"**Candidata rechazada. La vigente sigue en ejecución e incumple {n_vig} condiciones de las reglas duras.** "
             f"Motivo: {st.session_state.get('motivo', '')}", icon=":material/warning:")
else:
    st.warning("**Pendiente de tu decisión.** El Auditor dejó una candidata que cumple las reglas duras.",
               icon=":material/pending_actions:")

with st.container(border=True):
    a, b, c, d, e = st.columns([4, 2, 2, 2, 2], vertical_alignment="center")
    a.markdown(f"**Configuración candidata {can.get('version', '')}**  \n:gray[publicada por el Auditor en "
               f"`configuracion_aprobada.json`{' · con ajuste humano revalidado' if can.get('ajuste_humano') else ''}]")
    bloqueada = dec != "pendiente" or d_can["veredicto"] != "APROBADO"
    if b.button("Aprobar y liberar", type="primary", disabled=bloqueada, width="stretch"):
        dlg_aprobar()
    if c.button("Ajustar pesos", disabled=bloqueada, width="stretch"):
        dlg_ajustar()
    if d.button("Rechazar", disabled=dec != "pendiente", width="stretch"):
        dlg_rechazar()
    e.download_button("Descargar", json.dumps(can, ensure_ascii=False, indent=2), "configuracion_candidata.json",
                      "application/json", width="stretch")
if dec == "liberada":
    st.page_link("vistas/operacion.py", label="Abrir el mapa y aplicar el plan «Supervisión»", icon=":material/traffic:")

# ---------------- indicadores ----------------
peat = C.comparacion(co.escenario, "espera_peat_media_s")
mejoran = [i for i in C.INTER if peat.get(i, {}).get("plan_fijo_webster", {}).get("lectura") == "MEJORA"]
empeoran = [i for i in C.INTER if peat.get(i, {}).get("plan_fijo_webster", {}).get("lectura") == "EMPEORA"]
k = st.columns(4)
k[0].metric("Violaciones de reglas duras", f"{n_vig} → {n_can}", help="Vigente frente a candidata, según el validador R0–R6.", border=True)
k[1].metric("Mejoran en espera peatonal", f"{len(mejoran)} de 4", help="Frente al plan fijo de Webster, p < 0.05.", border=True)
k[2].metric("Empeoran (p < 0.05)", ", ".join(empeoran) or "ninguna", border=True)
k[3].metric("Corrida", f"{len(C.TAREAS)}/{len(C.TAREAS)} tareas" if co.estado != "error" else "con error",
            help=f"Motor: {co.motor}", border=True)

# ---------------- evidencia ----------------
izq, der = st.columns([5, 4], gap="large")
with izq:
    st.subheader("Diagnóstico: SIGMA-Vial vigente frente a las líneas base")
    st.caption("Calculado por `comparador_lineas_base` (herramienta del Analista de Telemetría Vial) · Wilcoxon pareada, 10 réplicas")
    met = st.segmented_control("Métrica", list(METRICAS), format_func=METRICAS.get, default="espera_peat_media_s",
                               label_visibility="collapsed") or "espera_peat_media_s"
    datos = C.comparacion(co.escenario, met)
    filas = [{"Intersección": i, "Línea base": {"plan_fijo_webster": "vs. plan fijo (Webster)", "maxima_presion": "vs. máxima presión"}[b],
              "Variación %": x["variacion_pct"], "p": x["p_wilcoxon"],
              "Lectura": x["lectura"].capitalize() if x["significativo_95"] else "Sin diferencia significativa"}
             for i in C.INTER for b, x in datos.get(i, {}).items()]
    tabla = pd.DataFrame(filas)
    ver_tabla = st.toggle("Ver como tabla")
    if tabla.empty:
        st.info("Sin datos de comparación para este escenario.")
    elif ver_tabla:
        st.dataframe(tabla, hide_index=True, width="stretch")
    else:
        vals = tabla["Variación %"]
        tope = float(vals.abs().quantile(0.9)) * 1.6 + 10          # un valor atípico no debe aplastar la escala
        fig = go.Figure()
        for nombre, color in (("vs. plan fijo (Webster)", C.S1), ("vs. máxima presión", C.S2)):
            t = tabla[tabla["Línea base"] == nombre]
            fig.add_bar(y=t["Intersección"], x=t["Variación %"].clip(-tope, tope), name=nombre, orientation="h",
                        marker=dict(color=color, cornerradius=4,
                                    pattern_shape=["" if "significativa" not in l else "/" for l in t["Lectura"]]),
                        text=[f"{v:+.1f} %" + ("" if "significativa" not in l else " n.s.") for v, l in zip(t["Variación %"], t["Lectura"])],
                        textposition="outside", cliponaxis=False,
                        customdata=t[["Lectura", "p"]], hovertemplate="%{y} · " + nombre + "<br>%{text}<br>%{customdata[0]} (p = %{customdata[1]})<extra></extra>")
        fig.add_vline(x=0, line_width=1.5, line_color="#5d6c78")
        fig.update_layout(barmode="group", height=360, margin=dict(l=10, r=40, t=10, b=10), bargap=0.3, bargroupgap=0.08,
                          legend=dict(orientation="h", y=1.12), yaxis=dict(autorange="reversed"),
                          xaxis=dict(title="← mejora · empeora →", ticksuffix=" %", range=[-tope * 1.25, tope * 1.25]))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        if (vals.abs() > tope).any():
            st.caption("Las barras que rebasan la escala están recortadas; la etiqueta muestra el valor real.")
    st.caption("Negativo significa menos espera o menos bloqueos. Trama rayada: diferencia no significativa.")

with der:
    st.subheader("Qué cambia: vigente → candidata")
    st.caption("Planes de `calculadora_webster` · pesos del Ingeniero de Planes · tabla generada por código")
    pv, pc = vig.get("planes_por_interseccion", {}), can.get("planes_por_interseccion", {})
    nombres = {"ciclo_s": "Ciclo", "verde_principal_s": "V. princ.", "verde_secundario_s": "V. sec.",
               "verde_peatonal_s": "V. peat.", "desfase_s": "Desfase"}
    st.dataframe(pd.DataFrame({i: {n: (f"{pv[i][k]} → {pc[i][k]}" if pv[i][k] != pc[i][k] else str(pc[i][k]))
                                   for k, n in nombres.items()} for i in C.INTER if i in pv and i in pc}).T, width="stretch")
    pesos = {k: (vig.get("pesos_utilidad", {}).get(k), x) for k, x in can.get("pesos_utilidad", {}).items()}
    pesos["umbral_spillback_pct"] = (vig.get("umbral_spillback_pct"), can.get("umbral_spillback_pct"))
    st.dataframe(pd.DataFrame([{"Parámetro": k, "Vigente": a, "Candidata": b, "Cambió": "sí" if a != b else ""}
                               for k, (a, b) in pesos.items()]), hide_index=True, width="stretch")

# ---------------- prueba en el simulador real ----------------
st.subheader("Prueba en el simulador del campus")
st.caption("Corre el circuito real en SUMO con la misma demanda y semilla para cada configuración. "
           "Cierra el pendiente de la Fase 3: evaluar desempeño, no solo cumplimiento.")
if st.button("Simular vigente y candidata en SUMO", icon=":material/play_arrow:"):
    from motor import Motor
    with st.spinner("Simulando 15 minutos de pulso de cambio de clase para cada configuración…"):
        m = Motor(etiqueta="geo_eval")
        res = {}
        for nombre, cfg in (("Plan fijo de la red", None), (f"Vigente {vig.get('version', '')}", vig),
                            (f"Candidata {can.get('version', '')}", can)):
            res[nombre] = C.puente.simular_configuracion(m.net_file, m.build, cfg, etiqueta=f"eval_{len(res)}")
        st.session_state.eval_sumo = res
if st.session_state.get("eval_sumo"):
    res = st.session_state.eval_sumo
    st.dataframe(pd.DataFrame({n: {"Vehículos": r["vehiculos"], "Espera promedio (s)": r["espera_promedio_s"],
                                   "Espera p95 (s)": r["espera_p95_s"], "Espera máx. continua (s)": r["espera_max_continua_s"],
                                   "Teleports por bloqueo": r["teleports_bloqueo"]} for n, r in res.items()}).T, width="stretch")
    ajustes = [f"**{n} · {i}**: {a}" for n, r in res.items() if r["traduccion"] for i, v in r["traduccion"].items() for a in v["ajustes"]]
    if ajustes:
        with st.expander(f"Ajustes al llevar cada configuración a la red real ({len(ajustes)})"):
            st.markdown("\n".join(f"- {a}" for a in ajustes))
    st.caption("Una semilla, solo vehículos: la red no modela peatones, así que la fase peatonal cuenta como rojo. "
               "El plan fijo de la red no tiene fase peatonal. Sirve para detectar un deterioro grave, no como prueba estadística.")

# ---------------- reporte ----------------
st.subheader("Reporte para la coordinación")
st.caption("Redactado por el Sintetizador de Movilidad · `reporte_ciclo_supervision.md` · requiere revisión humana")
if os.path.exists(C.REPORTE):
    rep = open(C.REPORTE, encoding="utf-8").read()
    falt = C.V.archivos_inexistentes(rep)
    a, b = st.columns(2)
    if "Todas las cifras del reporte aparecen" in rep:
        a.success("Cifras verificadas contra las salidas de las tareas.", icon=":material/check_circle:")
    else:
        a.warning("Hay cifras sin respaldo; revisa el anexo de verificación al final del reporte.", icon=":material/warning:")
    if falt:
        b.warning(f"{len(falt)} archivos citados no existen: " + ", ".join(f"`{f}`" for f in falt), icon=":material/warning:")
    else:
        b.success("Los archivos citados existen.", icon=":material/check_circle:")
    with st.expander("Leer el reporte completo", expanded=False):
        st.markdown(rep)
    st.download_button("Descargar reporte (.md)", rep, "reporte_ciclo_supervision.md", "text/markdown")
else:
    st.info("Este ciclo no dejó reporte.")

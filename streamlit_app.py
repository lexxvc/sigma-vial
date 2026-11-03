# ============================================================
# SIGMA-Vial · Simulación en vivo en la nube (Streamlit)
# Local:  streamlit run streamlit_app.py
# Nube:   Streamlit Community Cloud → repo de GitHub → este archivo
# ============================================================
import os, sys, time, uuid, weakref

import plotly.graph_objects as go
import streamlit as st

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(RAIZ, "app"))
from motor import Motor, ACCESOS, SALIDAS, PASO_S  # noqa: E402

st.set_page_config(page_title="SIGMA-Vial en vivo", page_icon="🚦", layout="wide")

# Clave del LLM desde los Secrets de Streamlit (opcional, solo para el Crew en vivo)
try:
    if "GEMINI_API_KEY" in st.secrets:
        os.environ["GEMINI_API_KEY"] = st.secrets["GEMINI_API_KEY"]
except Exception:
    pass

COL_VEH = {0: "#ecf0f1", 1: "#f39c12", 2: "#e74c3c"}
NOMBRE_PLAN = {"fijo": "Plan fijo", "crew_guardado": "Crew (guardado)", "crew_vivo": "Crew en vivo"}


@st.cache_resource
def red_compartida():
    """Construye la red SUMO una sola vez por servidor y devuelve su geometría."""
    m = Motor(etiqueta="geo")
    return m.geometria()


def cerrar_etiqueta(etiqueta):
    try:
        import traci
        traci.getConnection(etiqueta).close()
    except Exception:
        pass


def motor_sesion():
    if "motor" not in st.session_state:
        red_compartida()
        m = Motor(etiqueta=f"s_{uuid.uuid4().hex[:8]}")
        m._iniciar()
        weakref.finalize(m, cerrar_etiqueta, m.etiqueta)   # libera SUMO cuando la sesión termina
        st.session_state.motor = m
        st.session_state.ultimo = time.time()
    return st.session_state.motor


m = motor_sesion()
GEO = red_compartida()
cmd = m.cmds.put

# ---------------- barra lateral: controles ----------------
with st.sidebar:
    st.markdown("### 🚦 SIGMA-Vial")
    c1, c2 = st.columns(2)
    if c1.button("⏸ Pausa" if m.corriendo else "▶ Iniciar", type="primary", use_container_width=True):
        cmd(("toggle",)); st.session_state.ultimo = time.time(); m._procesar_cmds(); st.rerun()
    if c2.button("↺ Reiniciar", use_container_width=True):
        cmd(("reiniciar",))
    vel = st.select_slider("Velocidad", [0.5, 1, 2, 5, 10, 20], value=m.velocidad if m.velocidad in (0.5, 1, 2, 5, 10, 20) else 1,
                           format_func=lambda v: f"{v}×")
    if vel != m.velocidad:
        cmd(("velocidad", vel))
    dem = st.slider("Demanda de fondo (veh/min)", 0, 90, int(m.demanda_vpm), 5)
    if dem != m.demanda_vpm:
        cmd(("demanda", dem))

    st.markdown("#### Plan semafórico")
    p1, p2 = st.columns(2)
    if p1.button("Plan fijo", use_container_width=True, type="primary" if m.plan_nombre == "fijo" else "secondary"):
        cmd(("plan", "fijo"))
    if p2.button("Crew (guardado)", use_container_width=True,
                 type="primary" if m.plan_nombre == "crew_guardado" else "secondary"):
        cmd(("plan", "crew_guardado"))
    if st.button("🤖 Replanificar con Crew en vivo", use_container_width=True,
                 disabled=m.crew["estado"] == "deliberando"):
        cmd(("crew",))
    pre = st.toggle("Preempción para ambulancias", value=m.preempcion)
    if pre != m.preempcion:
        cmd(("preempcion", pre))

    st.markdown("#### Accesos")
    destino = st.selectbox("Destino de la ambulancia", [""] + list(SALIDAS),
                           format_func=lambda k: "aleatorio" if not k else SALIDAS[k]["nombre"])
    for k, a in ACCESOS.items():
        st.caption(a["nombre"])
        b1, b2, b3 = st.columns(3)
        if b1.button("+1", key=f"a1_{k}", use_container_width=True):
            cmd(("autos", k, 1))
        if b2.button("+10", key=f"a10_{k}", use_container_width=True):
            cmd(("autos", k, 10))
        if b3.button("🚑", key=f"amb_{k}", use_container_width=True):
            cmd(("ambulancia", k, destino))
    if st.button("🔔 Cambio de clase (+40 autos)", use_container_width=True):
        cmd(("rafaga", 40))


# ---------------- dibujo ----------------
def figura_mapa(e):
    fig = go.Figure()
    xs, ys = [], []
    for c in GEO["calles"]:
        xs += [p[0] for p in c] + [None]; ys += [p[1] for p in c] + [None]
    fig.add_scatter(x=xs, y=ys, mode="lines", line=dict(color="#3a3f4b", width=7), hoverinfo="skip")
    nx, ny = [], []
    for n in GEO["nodos"]:
        nx += [p[0] for p in n] + [n[0][0], None]; ny += [p[1] for p in n] + [n[0][1], None]
    fig.add_scatter(x=nx, y=ny, mode="lines", fill="toself", fillcolor="#3a3f4b", line=dict(width=0), hoverinfo="skip")
    if e["luces"]:
        fig.add_scatter(x=[l[0] for l in e["luces"]], y=[l[1] for l in e["luces"]], mode="markers", hoverinfo="skip",
                        marker=dict(symbol="square", size=7, color=[l[2] for l in e["luces"]]))
    normales = [v for v in e["vehiculos"] if v[3] != 3]
    amb = [v for v in e["vehiculos"] if v[3] == 3]
    if normales:
        fig.add_scatter(x=[v[0] for v in normales], y=[v[1] for v in normales], mode="markers", hoverinfo="skip",
                        marker=dict(size=7, color=[COL_VEH[v[3]] for v in normales], line=dict(width=0)))
    if amb:
        fig.add_scatter(x=[v[0] for v in amb], y=[v[1] for v in amb], mode="markers", hoverinfo="skip",
                        marker=dict(symbol="star", size=20, color="#3498db", line=dict(color="white", width=1.5)))
    acc = GEO["accesos"]
    fig.add_scatter(x=[a["x"] for a in acc.values()], y=[a["y"] for a in acc.values()], mode="markers",
                    marker=dict(size=13, color="#c0392b", line=dict(color="white", width=1)),
                    text=[a["nombre"] for a in acc.values()], hoverinfo="text")
    preempt = set(e["preempcion_activa"])
    for k, n in GEO["intersecciones"].items():
        fig.add_annotation(x=n["x"] + 10, y=n["y"] - 12, text=f"<b>{k}{' 🚑' if k in preempt else ''}</b>",
                           showarrow=False, font=dict(color="#3498db" if k in preempt else "#7fb3e6", size=13))
    x0, y0, x1, y1 = GEO["bbox"]
    fig.update_layout(height=640, showlegend=False, margin=dict(l=0, r=0, t=0, b=0), uirevision="mapa",
                      paper_bgcolor="#0f1115", plot_bgcolor="#0f1115",
                      xaxis=dict(visible=False, range=[x0 - 20, x1 + 20]),
                      yaxis=dict(visible=False, range=[y0 - 20, y1 + 20], scaleanchor="x"))
    return fig


def figura_hist(h):
    fig = go.Figure()
    if h:
        t = [p[0] / 60 for p in h]
        fig.add_scatter(x=t, y=[p[1] for p in h], name="en red", line=dict(color="#ecf0f1", width=2))
        fig.add_scatter(x=t, y=[p[2] for p in h], name="detenidos", line=dict(color="#e74c3c", width=2))
    fig.update_layout(height=220, margin=dict(l=10, r=10, t=30, b=10), paper_bgcolor="#171a21", plot_bgcolor="#171a21",
                      font=dict(color="#8b93a3", size=11), legend=dict(orientation="h", y=1.15),
                      xaxis=dict(title="min", gridcolor="#2a2f3a"), yaxis=dict(gridcolor="#2a2f3a"))
    return fig


@st.fragment(run_every=0.6 if m.corriendo else None)
def vista():
    ahora = time.time()
    m._procesar_cmds()
    if m.corriendo:
        dt = min(ahora - st.session_state.ultimo, 2.0)          # tope por si la pestaña se durmió
        for _ in range(max(1, round(dt * m.velocidad / PASO_S))):
            m._paso()
    st.session_state.ultimo = ahora
    e = m._foto_ui()
    k = e["metricas"]
    t = e["t"]
    cols = st.columns(6)
    cols[0].metric("Tiempo simulado", f"{int(t // 60):02d}:{int(t % 60):02d}")
    cols[1].metric("Vehículos en red", k["en_red"])
    cols[2].metric("Detenidos", k["detenidos"])
    cols[3].metric("Espera prom. (s)", k["espera_prom_s"] if k["completados"] else "–")
    cols[4].metric("Salidas / min", k["salidas_min"] or "–")
    cols[5].metric("🚑 último cruce (s)", round(k["amb_ultimo_s"]) if k["amb_ultimo_s"] else "–")
    izq, der = st.columns([3, 2])
    with izq:
        st.plotly_chart(figura_mapa(e), use_container_width=True, config={"displayModeBar": False}, key="mapa")
        st.caption("⬜ en movimiento · 🟧 detenido < 30 s · 🟥 detenido > 30 s · ⭐ ambulancia · 🔴 acceso")
    with der:
        verdes = " · ".join(f"**{i}** {'/'.join(map(str, v))} s" for i, v in e["plan"]["verdes"].items())
        st.markdown(f"**Plan activo:** {NOMBRE_PLAN.get(e['plan']['nombre'], e['plan']['nombre'])}  \n{verdes}")
        c = e["crew"]
        if c["estado"] == "deliberando":
            st.info("⏳ " + c["mensaje"])
        elif c["estado"] == "error":
            st.warning(c["mensaje"])
        elif c["estado"] == "listo":
            st.success(c["mensaje"])
        st.plotly_chart(figura_hist(e["hist"]), use_container_width=True, config={"displayModeBar": False}, key="hist")
        st.markdown("**Eventos**")
        st.code("\n".join(e["log"][:12]) or "—", language=None)
        if c.get("reporte"):
            with st.expander("📄 Reporte del Crew"):
                st.markdown(c["reporte"])
    # el ritmo del fragmento depende de si corre o no: si cambió, recargar todo
    if (m.corriendo and not st.session_state.get("_auto")) or (not m.corriendo and st.session_state.get("_auto")):
        st.session_state._auto = m.corriendo
        st.rerun()


st.session_state.setdefault("_auto", m.corriendo)
vista()

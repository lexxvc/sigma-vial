# ============================================================
# SIGMA-Vial · Animación de la simulación (plan fijo vs Crew)
# ============================================================
import traci, sumolib, matplotlib
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.collections import LineCollection
import sigma_sim as sim

COLOR_LUZ = {"G": "#2ecc71", "g": "#2ecc71", "y": "#f1c40f", "r": "#e74c3c", "s": "#e74c3c", "o": "#999"}


def grabar(net, rou, plan=None, seed=42, t_ini=420, t_fin=780, cada=2):
    """Corre SUMO y guarda posiciones de vehículos y estado de semáforos cada `cada` s."""
    sim._iniciar(net, rou, seed)
    tls = [v["tls"] for v in sim.INTERSECCIONES.values()]
    for k, t in zip(sim.INTERSECCIONES, tls):
        fases = traci.trafficlight.getAllProgramLogics(t)[0].phases
        fijos = [int(p.duration) for p in fases if "y" not in p.state]
        verdes = plan.get(k, fijos) if plan else fijos
        traci.trafficlight.setProgramLogic(t, sim._logica_con_todo_rojo(t, verdes))
        traci.trafficlight.setProgram(t, "sigma")
    # posición de la línea de alto de cada enlace semaforizado
    luces = {}
    for t in tls:
        luces[t] = []
        for links in traci.trafficlight.getControlledLinks(t):
            ln = links[0][0] if links else None
            luces[t].append(traci.lane.getShape(ln)[-1] if ln else None)
    frames = []
    for paso in range(t_fin + 1):
        traci.simulationStep()
        if paso < t_ini or paso % cada:
            continue
        vehs = []
        for v in traci.vehicle.getIDList():
            x, y = traci.vehicle.getPosition(v)
            vehs.append((x, y, traci.vehicle.getWaitingTime(v), v == "AMB_01"))
        semaf = []
        for t in tls:
            st = traci.trafficlight.getRedYellowGreenState(t)
            semaf += [(p[0], p[1], COLOR_LUZ.get(c, "#999")) for p, c in zip(luces[t], st) if p]
        frames.append((paso, vehs, semaf))
    traci.close()
    return frames


def animar(net_file, paneles, salida="simulacion.gif", fps=12):
    """paneles = {"Plan fijo": frames, "SIGMA-Vial (Crew)": frames} → GIF lado a lado."""
    net = sumolib.net.readNet(net_file)
    segs = [e.getShape() for e in net.getEdges()]
    (x0, y0), (x1, y1) = net.getBBoxXY()
    n = len(paneles)
    fig, axs = plt.subplots(1, n, figsize=(6 * n, 6.6), facecolor="#111")
    axs = [axs] if n == 1 else axs
    artistas = []
    for ax, titulo in zip(axs, paneles):
        ax.set_facecolor("#111"); ax.set_aspect("equal"); ax.axis("off")
        ax.add_collection(LineCollection(segs, colors="#555", linewidths=3))
        ax.set_xlim(x0 - 15, x1 + 15); ax.set_ylim(y0 - 15, y1 + 15)
        ax.set_title(titulo, color="white", fontsize=13)
        for k, d in sim.INTERSECCIONES.items():
            nx, ny = net.getNode(d["tls"]).getCoord()
            ax.text(nx + 8, ny - 14, k, color="#3498db", fontsize=10, weight="bold")
        luz = ax.scatter([], [], s=55, marker="s", zorder=3)
        veh = ax.scatter([], [], s=22, zorder=4, edgecolors="none")
        amb = ax.scatter([], [], s=140, marker="*", c="#3498db", zorder=5)
        txt = ax.text(0.02, 0.02, "", transform=ax.transAxes, color="white", fontsize=10, family="monospace")
        artistas.append((luz, veh, amb, txt))
    for xpos, (lbl, col) in zip([0.22, 0.38, 0.54, 0.70, 0.82],
                                [("● en movimiento", "#ecf0f1"), ("● detenido < 30 s", "#f39c12"),
                                 ("● detenido > 30 s", "#e74c3c"), ("★ ambulancia", "#3498db"),
                                 ("■ semáforo", "#2ecc71")]):
        fig.text(xpos, 0.015, lbl, color=col, ha="center", fontsize=10)
    listas = list(paneles.values())
    total = min(len(f) for f in listas)

    def actualizar(i):
        out = []
        for (luz, veh, amb, txt), frames in zip(artistas, listas):
            t, vehs, semaf = frames[i]
            luz.set_offsets([(x, y) for x, y, _ in semaf] or [(0, 0)])
            luz.set_color([c for *_, c in semaf] or ["#111"])
            normales = [v for v in vehs if not v[3]]
            veh.set_offsets([(x, y) for x, y, *_ in normales] or [(0, 0)])
            veh.set_color([("#e74c3c" if w > 30 else "#f39c12" if w > 0 else "#ecf0f1")
                           for _, _, w, _ in normales] or ["#111"])
            a = [(x, y) for x, y, _, es in vehs if es]
            amb.set_offsets(a or [(-9999, -9999)])
            det = sum(1 for v in vehs if v[2] > 0)
            txt.set_text(f"t sim = {t//60:02d}:{t%60:02d} min"+f"  vehículos: {len(vehs):3d}  detenidos: {det:3d}")
            out += [luz, veh, amb, txt]
        return out

    anim = FuncAnimation(fig, actualizar, frames=total, interval=1000 / fps, blit=True)
    anim.save(salida, writer=PillowWriter(fps=fps))
    plt.close(fig)
    return salida

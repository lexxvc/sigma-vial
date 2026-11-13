# ============================================================
# SIGMA-Vial · Puente entre la capa de supervisión (Fase 3) y el simulador
#
# La configuración que audita el Crew describe cada intersección con tres
# fases: verde principal, verde secundario y verde peatonal exclusivo.
# La red real del campus (data/mapa.osm) tiene otra forma: I1-I3 tienen dos
# fases vehiculares e I4 tiene cuatro (una por acceso). Este módulo traduce
# una cosa en la otra y deja constancia de cada ajuste que tuvo que hacer.
# ============================================================
import json, os, statistics, subprocess, sys

import traci
import sigma_sim as sim

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_SUP = os.path.join(RAIZ, "supervision")
RUTA_LIBERADA = os.path.join(DIR_SUP, "datos", "configuracion_liberada.json")
RUTA_BITACORA = os.path.join(DIR_SUP, "datos", "bitacora_liberaciones.csv")


def cargar_liberada():
    """Configuración aprobada por una persona en la consola, o None."""
    try:
        with open(RUTA_LIBERADA, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def repartir(plan, n_fases):
    """Reparte verde principal y secundario entre las fases verdes reales.

    Fases pares → acceso principal, impares → secundario. Si al repartir una
    fase queda por debajo del verde mínimo (8 s) se sube a 8 s y se anota.
    Devuelve (verdes por fase, verde peatonal, lista de ajustes)."""
    vmin = sim.REGLAS["verde_min_s"]
    grupos = {"principal": [i for i in range(n_fases) if i % 2 == 0],
              "secundario": [i for i in range(n_fases) if i % 2 == 1]}
    verdes, ajustes = [0] * n_fases, []
    for nombre, idx in grupos.items():
        total = int(plan[f"verde_{nombre}_s"])
        parte = total // len(idx)
        if parte < vmin:
            ajustes.append(f"verde {nombre} de {total} s repartido en {len(idx)} fases daría {parte} s; "
                           f"se usa el mínimo de {vmin} s por fase")
            parte = vmin
        for i in idx:
            verdes[i] = parte
    return verdes, int(plan["verde_peatonal_s"]), ajustes


def traducir(config, fases_por_inter):
    """config (formato Fase 3) → {inter: {"verdes": [...], "peatonal": s, "ciclo_real_s": s, "ajustes": [...]}}."""
    perdida = sim.REGLAS["ambar_s"] + sim.REGLAS["todo_rojo_s"]
    salida = {}
    for inter, n in fases_por_inter.items():
        plan = config["planes_por_interseccion"][inter]
        verdes, peat, ajustes = repartir(plan, n)
        ciclo = sum(verdes) + (n + 1) * perdida + peat
        if ciclo != plan["ciclo_s"]:
            ajustes.append(f"ciclo declarado {plan['ciclo_s']} s; en la red real resulta {ciclo} s "
                           f"({n} fases vehiculares + fase peatonal)")
        salida[inter] = {"verdes": verdes, "peatonal": peat, "ciclo_real_s": ciclo, "ajustes": ajustes}
    return salida


def logica(conn, tls_id, verdes, peatonal):
    """Programa del semáforo: cada verde → ámbar 3 s → todo-rojo 2 s, y al final
    una fase peatonal exclusiva (todo en rojo para los vehículos)."""
    base = conn.trafficlight.getAllProgramLogics(tls_id)[0]
    f_verde = [p for p in base.phases if "y" not in p.state and set(p.state) != {"r"}]
    f_ambar = [p for p in base.phases if "y" in p.state]
    rojo = "r" * len(f_verde[0].state)
    fases = []
    for i, pv in enumerate(f_verde):
        fases.append(traci.trafficlight.Phase(verdes[i], pv.state))
        if i < len(f_ambar):
            fases.append(traci.trafficlight.Phase(sim.REGLAS["ambar_s"], f_ambar[i].state))
        fases.append(traci.trafficlight.Phase(sim.REGLAS["todo_rojo_s"], rojo))
    if peatonal:
        fases.append(traci.trafficlight.Phase(peatonal, rojo))            # cruce peatonal exclusivo
        fases.append(traci.trafficlight.Phase(sim.REGLAS["ambar_s"] + sim.REGLAS["todo_rojo_s"], rojo))  # despeje
    return traci.trafficlight.Logic("supervision", 0, 0, fases)


def fases_verdes(conn, tls_ids):
    out = {}
    for k, t in tls_ids.items():
        ph = conn.trafficlight.getAllProgramLogics(t)[0].phases
        out[k] = len([p for p in ph if "y" not in p.state and set(p.state) != {"r"}])
    return out


def aplicar(conn, tls_ids, config):
    """Carga la configuración en una conexión TraCI viva. Devuelve la traducción usada."""
    trad = traducir(config, fases_verdes(conn, tls_ids))
    for k, t in tls_ids.items():
        conn.trafficlight.setProgramLogic(t, logica(conn, t, trad[k]["verdes"], trad[k]["peatonal"]))
        conn.trafficlight.setProgram(t, "supervision")
    return trad


# ---------------- evaluación en el simulador (pendiente de la Fase 3) ----------------
def _demanda(net, build, periodo, seed, fin):
    rou = os.path.join(build, f"eval_{seed}_{periodo}_{fin}.rou.xml")
    if not os.path.exists(rou):
        subprocess.run([sys.executable, os.path.join(sim.SUMO_HOME, "tools", "randomTrips.py"), "-n", net,
                        "-o", os.path.join(build, "eval_trips.xml"), "-r", rou, "-b", "0", "-e", str(fin),
                        "-p", str(periodo), "--fringe-factor", "20", "--min-distance", "150",
                        "--seed", str(seed), "--validate"], check=True, capture_output=True)
    return rou


def simular_configuracion(net, build, config=None, seed=42, fin=900, periodo=3.0, etiqueta="eval"):
    """Corre el circuito real con una configuración (o con el plan fijo de la red
    si config es None) y devuelve métricas vehiculares. La red no modela peatones:
    la fase peatonal cuenta como tiempo en rojo para los vehículos."""
    rou = _demanda(net, build, periodo, seed, fin)
    traci.start([sim.SUMO_BIN, "-n", net, "-r", rou, "--seed", str(seed), "--no-warnings", "--no-step-log",
                 "--time-to-teleport", "120", "--waiting-time-memory", "10000"], label=etiqueta)
    T = traci.getConnection(etiqueta)
    try:
        tls = {k: v["tls"] for k, v in sim.INTERSECCIONES.items()}
        trad = aplicar(T, tls, config) if config else None
        esperas, max_cont, tele = {}, 0.0, 0
        for paso in range(fin + 600):
            T.simulationStep()
            tele += T.simulation.getStartingTeleportNumber()
            vivos = T.vehicle.getIDList()
            if paso >= fin and T.simulation.getMinExpectedNumber() == 0:
                break
            for v in vivos:
                esperas[v] = T.vehicle.getAccumulatedWaitingTime(v)
                max_cont = max(max_cont, T.vehicle.getWaitingTime(v))
        vals = sorted(esperas.values())
        return {"vehiculos": len(vals),
                "espera_promedio_s": round(statistics.mean(vals), 1) if vals else 0.0,
                "espera_p95_s": round(vals[int(.95 * len(vals))], 1) if vals else 0.0,
                "espera_max_continua_s": round(max_cont, 1),
                "teleports_bloqueo": tele,
                "traduccion": trad}
    finally:
        T.close()

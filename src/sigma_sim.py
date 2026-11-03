# ============================================================
# SIGMA-Vial · Capa de simulación (SUMO + TraCI)
# Genera el estado del circuito, aplica planes y mide desempeño
# ============================================================
import os, sys, json, subprocess, statistics
import sumo, traci, sumolib

SUMO_HOME = sumo.SUMO_HOME
SUMO_BIN = os.path.join(SUMO_HOME, "bin", "sumo")

# Intersecciones semaforizadas del circuito interno (ids de la red SUMO)
INTERSECCIONES = {
    "I1": {"tls": "7040847506", "nombre": "Glorieta - entrada desde Orión Norte"},
    "I2": {"tls": "8436756891", "nombre": "Ramal oriente (estacionamientos)"},
    "I3": {"tls": "8436756880", "nombre": "Ramal poniente"},
    "I4": {"tls": "cluster_8437175637_8437175639", "nombre": "Acceso norte"},
}
# Tramos que conectan intersecciones (para detectar spillback)
TRAMOS_COMPARTIDOS = {"I1->I2": "954517814#0", "I1->I3": "753567886#0", "I2->I4": "908619328#1"}

# Restricciones duras (PEAS, Fase 2)
REGLAS = {"ambar_s": 3, "todo_rojo_s": 2, "verde_min_s": 8, "verde_max_s": 60,
          "ciclo_max_s": 120, "espera_max_s": 60}

SAT_FLOW = 1800  # veh/h por carril (valor típico; calibrar con aforos)


def construir_red(osm="data/mapa.osm", net="campus_tls.net.xml"):
    tls = ",".join(v["tls"] for v in INTERSECCIONES.values())
    subprocess.run([os.path.join(SUMO_HOME, "bin", "netconvert"), "--osm-files", osm, "-o", net, "--geometry.remove",
                    "--roundabouts.guess", "--junctions.join", "--keep-edges.by-vclass", "passenger",
                    "--remove-edges.isolated", "--no-warnings", "--tls.set", tls,
                    "--tls.default-type", "static"], check=True)
    return net


def generar_demanda(net, rou="routes.rou.xml", periodo=3.0, seed=42, fin=900):
    """Pulso de cambio de clase: 1 vehículo cada `periodo` s (≈1,200 veh/h con 3 s) durante `fin` s."""
    subprocess.run([sys.executable, os.path.join(SUMO_HOME, "tools", "randomTrips.py"),
                    "-n", net, "-o", "trips.xml", "-r", rou, "-b", "0", "-e", str(fin),
                    "-p", str(periodo), "--fringe-factor", "20", "--min-distance", "150",
                    "--seed", str(seed), "--validate"], check=True, capture_output=True)
    # Vehículo de emergencia que entra por el acceso norte (I4) a los 600 s (archivo aparte)
    ruta = _ruta_ambulancia(net)
    if ruta:
        with open("amb.rou.xml", "w") as f:
            f.write('<routes>\n<vType id="ambulancia" vClass="emergency" speedFactor="1.3"/>\n'
                    f'<route id="r_amb" edges="{" ".join(ruta)}"/>\n'
                    '<vehicle id="AMB_01" type="ambulancia" route="r_amb" depart="600" departLane="best"/>\n</routes>\n')
        rou = rou + ",amb.rou.xml"
    return rou


def _ruta_ambulancia(net_file):
    net = sumolib.net.readNet(net_file)
    try:
        o, d = net.getEdge("-908659205#0"), net.getEdge("-753567886#0")
        path, _ = net.getShortestPath(o, d)
        return [e.getID() for e in path] if path else None
    except Exception:
        return None


def _iniciar(net, rou, seed):
    if traci.isLoaded():
        traci.close()
    traci.start([SUMO_BIN, "-n", net, "-r", rou, "--seed", str(seed), "--no-warnings",
                 "--no-step-log", "--time-to-teleport", "120", "--waiting-time-memory", "10000"])


def _logica_con_todo_rojo(tls_id, verdes, conn=None):
    """Construye el programa del semáforo: verde → ámbar 3 s → todo-rojo 2 s."""
    base = (conn or traci).trafficlight.getAllProgramLogics(tls_id)[0]
    fases_verde = [p for p in base.phases if "y" not in p.state]
    fases_ambar = [p for p in base.phases if "y" in p.state]
    nuevas = []
    for i, pv in enumerate(fases_verde):
        dur = verdes[i] if i < len(verdes) else pv.duration
        nuevas.append(traci.trafficlight.Phase(dur, pv.state))
        pa = fases_ambar[i] if i < len(fases_ambar) else None
        if pa:
            nuevas.append(traci.trafficlight.Phase(REGLAS["ambar_s"], pa.state))
        nuevas.append(traci.trafficlight.Phase(REGLAS["todo_rojo_s"], "r" * len(pv.state)))
    return traci.trafficlight.Logic("sigma", 0, 0, nuevas)


def simular(net, rou, plan=None, seed=42, fin=900, fallas=("I2",), snapshot_en=540):
    """Corre la simulación. Si `plan` es None se usa el plan fijo (línea base).
    Devuelve métricas y una foto del estado a los `snapshot_en` s (entrada del Crew)."""
    _iniciar(net, rou, seed)
    tls_ids = {k: v["tls"] for k, v in INTERSECCIONES.items()}
    verdes_fijos = {}
    for k, t in tls_ids.items():
        fases = traci.trafficlight.getAllProgramLogics(t)[0].phases
        verdes_fijos[k] = [int(p.duration) for p in fases if "y" not in p.state]
        verdes = plan.get(k, verdes_fijos[k]) if plan else verdes_fijos[k]
        traci.trafficlight.setProgramLogic(t, _logica_con_todo_rojo(t, verdes))
        traci.trafficlight.setProgram(t, "sigma")
    esperas, max_espera, spill, foto, amb_t0, amb_t1, tele = {}, 0.0, 0, None, None, None, 0
    for paso in range(fin + 600):
        traci.simulationStep()
        tele += traci.simulation.getStartingTeleportNumber()
        vivos = traci.vehicle.getIDList()
        if paso >= fin and traci.simulation.getMinExpectedNumber() == 0:
            break
        for v in vivos:
            w = traci.vehicle.getAccumulatedWaitingTime(v)
            esperas[v] = w
            max_espera = max(max_espera, traci.vehicle.getWaitingTime(v))
        if "AMB_01" in vivos and amb_t0 is None:
            amb_t0 = paso
        if amb_t0 is not None and amb_t1 is None and "AMB_01" not in vivos:
            amb_t1 = paso
        for e in TRAMOS_COMPARTIDOS.values():
            if traci.edge.getLastStepOccupancy(e) > 0.85:
                spill += 1
        if paso == snapshot_en and plan is None:
            foto = _foto(tls_ids, verdes_fijos, paso, fallas, vivos)
    m = {"vehiculos": len(esperas),
         "espera_promedio_s": round(statistics.mean(esperas.values()), 1) if esperas else 0,
         "espera_p95_s": round(sorted(esperas.values())[int(.95 * len(esperas))], 1) if esperas else 0,
         "espera_max_continua_s": round(max_espera, 1),
         "segundos_con_spillback": spill,
         "teleports_bloqueo": tele,
         "tiempo_ambulancia_s": (amb_t1 - amb_t0) if amb_t0 and amb_t1 else None}
    traci.close()
    return m, foto


def _foto(tls_ids, verdes_fijos, t, fallas, vivos, conn=None):
    """Estado observable por los sensores (PEAS-S). Los carriles con falla no reportan."""
    T = conn or traci
    foto = {"hora_simulada": "09:00", "t_sim_s": t, "intersecciones": {}, "tramos_compartidos": {},
            "emergencia": None}
    for k, tls in tls_ids.items():
        carriles = {}
        for i, ln in enumerate(dict.fromkeys(T.trafficlight.getControlledLanes(tls))):
            falla = k in fallas and i == 0
            carriles[ln] = None if falla else {
                "vehiculos_detenidos": T.lane.getLastStepHaltingNumber(ln),
                "vehiculos_total": T.lane.getLastStepVehicleNumber(ln),
                "ocupacion_pct": round(T.lane.getLastStepOccupancy(ln) * 100, 1),
                "espera_acumulada_s": round(T.lane.getWaitingTime(ln), 1),
                "longitud_m": round(T.lane.getLength(ln), 1)}
        foto["intersecciones"][k] = {"nombre": INTERSECCIONES[k]["nombre"], "tls": tls,
                                     "fase_actual": T.trafficlight.getPhase(tls),
                                     "verdes_actuales_s": verdes_fijos[k],
                                     "num_fases_verde": len(verdes_fijos[k]),
                                     "fases_verde": _fases_servidas(tls, T),
                                     "peatones_esperando": {"I1": 14, "I2": 22, "I3": 6, "I4": 9}[k],
                                     "carriles": carriles}
    for nom, e in TRAMOS_COMPARTIDOS.items():
        foto["tramos_compartidos"][nom] = {"edge": e, "ocupacion_pct": round(T.edge.getLastStepOccupancy(e) * 100, 1),
                                           "vehiculos": T.edge.getLastStepVehicleNumber(e)}
    foto["emergencia"] = {"tipo": "ambulancia", "acceso": "I4", "eta_s": 60, "destino": "I3"}
    return foto


def _fases_servidas(tls, conn=None):
    """Qué carriles atiende cada fase verde (índice de fase verde → carriles de entrada)."""
    T = conn or traci
    logic = T.trafficlight.getAllProgramLogics(tls)[0]
    links = T.trafficlight.getControlledLinks(tls)
    salida, i = [], 0
    for ph in logic.phases:
        if "y" in ph.state or set(ph.state) == {"r"}:
            continue
        carriles = sorted({links[j][0][0] for j, c in enumerate(ph.state) if c in "Gg" and links[j]})
        salida.append({"fase_verde": i, "carriles_servidos": carriles})
        i += 1
    return salida

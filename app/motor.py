# ============================================================
# SIGMA-Vial · Motor de simulación en tiempo real (SUMO + TraCI)
# Un solo hilo es dueño de TraCI; la interfaz manda comandos por cola.
# ============================================================
import os, sys, json, math, queue, random, threading, time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "src"))

import traci, sumolib          # noqa: E402
import sigma_sim as sim        # noqa: E402

PASO_S = 0.25                  # resolución de la simulación (s)
COLOR_LUZ = {"G": "#2ecc71", "g": "#2ecc71", "y": "#f1c40f", "Y": "#f1c40f",
             "r": "#e74c3c", "s": "#e74c3c", "o": "#888", "O": "#888"}

# Accesos (bordes de entrada) y salidas de la red del campus
ACCESOS = {
    "sur":   {"edge": "753567883#0",  "nombre": "Acceso sur (Orión Norte → I1)"},
    "norte": {"edge": "1319837490",   "nombre": "Acceso norte (→ I4)"},
    "est_poniente": {"edge": "-908619329#1", "nombre": "Estacionamiento poniente (I3)"},
    "est_oriente":  {"edge": "-908619327",   "nombre": "Estacionamiento oriente (I2/I4)"},
    "est_norponiente": {"edge": "-954517819#1", "nombre": "Estacionamiento norponiente"},
}
SALIDAS = {
    "sur":   {"edge": "753567884#1",  "nombre": "Salida sur"},
    "norte": {"edge": "1319837491",   "nombre": "Salida norte"},
    "est_poniente": {"edge": "908619329#1", "nombre": "Estacionamiento poniente"},
    "est_oriente":  {"edge": "908619327",   "nombre": "Estacionamiento oriente"},
    "est_norponiente": {"edge": "954517819#1", "nombre": "Estacionamiento norponiente"},
}
PESO_ACCESO = {"sur": 0.45, "norte": 0.35, "est_poniente": 0.07, "est_oriente": 0.07, "est_norponiente": 0.06}


def cargar_plan_guardado():
    try:
        with open(os.path.join(RAIZ, "results", "plan_final.json"), encoding="utf-8") as f:
            pf = json.load(f)
        return {p["interseccion"]: p["verdes_s"] for p in pf["planes"]}
    except Exception:
        return {"I1": [8, 8], "I2": [9, 9], "I3": [10, 8], "I4": [12, 8, 8, 8]}


class Motor:
    def __init__(self, osm=None, seed=42, sumo_gui=False, etiqueta="default"):
        self.osm = osm or os.path.join(RAIZ, "data", "mapa.osm")
        self.build = os.path.join(RAIZ, "build"); os.makedirs(self.build, exist_ok=True)
        self.net_file = os.path.join(self.build, "campus_tls.net.xml")
        self.seed, self.sumo_gui = seed, sumo_gui
        self.etiqueta = etiqueta            # una conexión TraCI por sesión (multiusuario)
        self.T = None
        self.cmds = queue.Queue()
        self.lock = threading.Lock()
        self.estado = {}
        self.corriendo = False
        self.velocidad = 1.0               # × tiempo real
        self.demanda_vpm = 20.0            # vehículos/min de fondo
        self.preempcion = True
        self.plan_nombre = "fijo"
        self.plan_guardado = cargar_plan_guardado()
        self.crew = {"estado": "inactivo", "mensaje": "", "reporte": "", "plan": None}
        self.log = []
        self._construir_red()

    # ---------------- red ----------------
    def _construir_red(self):
        if not os.path.exists(self.net_file) or os.path.getmtime(self.net_file) < os.path.getmtime(self.osm):
            cwd = os.getcwd(); os.chdir(self.build)
            try:
                sim.construir_red(osm=self.osm, net=self.net_file)
            finally:
                os.chdir(cwd)
        self.net = sumolib.net.readNet(self.net_file)

    def geometria(self):
        """Datos estáticos para dibujar: calles, intersecciones, accesos."""
        (x0, y0), (x1, y1) = self.net.getBBoxXY()
        calles = [[[round(x, 1), round(y, 1)] for x, y in e.getShape()] for e in self.net.getEdges()]
        inter = {k: dict(zip("xy", map(lambda v: round(v, 1), self.net.getNode(d["tls"]).getCoord())), nombre=d["nombre"])
                 for k, d in sim.INTERSECCIONES.items()}
        acc = {}
        for k, a in ACCESOS.items():
            x, y = self.net.getEdge(a["edge"]).getShape()[0]
            acc[k] = {"nombre": a["nombre"], "x": round(x, 1), "y": round(y, 1)}
        nodos = [[[round(x, 1), round(y, 1)] for x, y in n.getShape()] for n in self.net.getNodes() if len(n.getShape()) > 2]
        return {"bbox": [x0, y0, x1, y1], "calles": calles, "nodos": nodos, "intersecciones": inter, "accesos": acc,
                "salidas": {k: v["nombre"] for k, v in SALIDAS.items()}}

    # ---------------- ciclo de vida ----------------
    def _iniciar(self):
        self.cerrar()
        binario = sim.SUMO_BIN + ("-gui" if self.sumo_gui else "")
        args = [binario, "-n", self.net_file, "--seed", str(self.seed), "--step-length", str(PASO_S),
                "--no-warnings", "--no-step-log", "--time-to-teleport", "120", "--waiting-time-memory", "10000"]
        if self.sumo_gui:
            args += ["--start", "--quit-on-end"]
        traci.start(args, label=self.etiqueta)
        self.T = traci.getConnection(self.etiqueta)
        random.seed(self.seed)
        self.T.vehicletype.copy("DEFAULT_VEHTYPE", "ambulancia")
        self.T.vehicletype.setVehicleClass("ambulancia", "emergency")
        self.T.vehicletype.setSpeedFactor("ambulancia", 1.3)
        self.T.vehicletype.setColor("ambulancia", (52, 152, 219, 255))
        self.tls = {k: v["tls"] for k, v in sim.INTERSECCIONES.items()}
        self.verdes_fijos = {}
        for k, t in self.tls.items():
            fases = self.T.trafficlight.getAllProgramLogics(t)[0].phases
            self.verdes_fijos[k] = [int(p.duration) for p in fases if "y" not in p.state]
        self.luces = {}
        for t in self.tls.values():
            pos = []
            for links in self.T.trafficlight.getControlledLinks(t):
                pos.append(self.T.lane.getShape(links[0][0])[-1] if links else None)
            self.luces[t] = pos
        self.rutas = {}
        for ka, a in ACCESOS.items():
            for ks, s in SALIDAS.items():
                if ka == ks:
                    continue
                r = self.T.simulation.findRoute(a["edge"], s["edge"])
                if r.edges:
                    rid = f"r_{ka}_{ks}"
                    self.T.route.add(rid, list(r.edges))
                    self.rutas.setdefault(ka, []).append((ks, rid))
        self.n_veh = 0
        self.preempt = {}
        self.amb_inicio, self.amb_tiempos = {}, []
        self.esperas_fin, self.esperas_vivos, self.llegadas = [], {}, []
        self.hist = []
        self._aplicar_plan(self.plan_nombre, registrar=False)
        self._evento("Simulación iniciada")

    def cerrar(self):
        if self.T is not None:
            try:
                self.T.close()
            except Exception:
                pass
            self.T = None

    def _evento(self, txt):
        t = self.T.simulation.getTime() if self.T else 0
        self.log = ([f"{int(t // 60):02d}:{int(t % 60):02d}  {txt}"] + self.log)[:40]

    def _aplicar_plan(self, nombre, verdes=None, registrar=True):
        if nombre == "fijo":
            plan = self.verdes_fijos
        elif nombre == "crew_guardado":
            plan = self.plan_guardado
        else:
            plan = verdes or self.crew.get("plan") or self.plan_guardado
        for k, t in self.tls.items():
            v = plan.get(k, self.verdes_fijos[k])
            self.T.trafficlight.setProgramLogic(t, sim._logica_con_todo_rojo(t, v, conn=self.T))
            self.T.trafficlight.setProgram(t, "sigma")
        self.plan_nombre, self.plan_verdes = nombre, {k: plan.get(k, self.verdes_fijos[k]) for k in self.tls}
        self.preempt.clear()
        if registrar:
            self._evento(f"Plan aplicado: {nombre} {self.plan_verdes}")

    # ---------------- vehículos ----------------
    def _agregar(self, acceso, n=1, tipo="auto", destino=None):
        opciones = self.rutas.get(acceso, [])
        if not opciones:
            return
        for _ in range(n):
            if destino:
                elegidas = [o for o in opciones if o[0] == destino] or opciones
            else:
                elegidas = opciones
            ks, rid = random.choice(elegidas)
            self.n_veh += 1
            vid = f"{'AMB' if tipo == 'ambulancia' else 'v'}_{self.n_veh}"
            self.T.vehicle.add(vid, rid, typeID="ambulancia" if tipo == "ambulancia" else "DEFAULT_VEHTYPE",
                              depart="now", departLane="best", departSpeed="max")
            if tipo == "ambulancia":
                self.amb_inicio[vid] = self.T.simulation.getTime()
                self._evento(f"🚑 Ambulancia {vid} entra por {acceso} → {SALIDAS[ks]['nombre']}")
        if tipo != "ambulancia":
            self._evento(f"+{n} auto(s) en {ACCESOS[acceso]['nombre']}")

    def _demanda_fondo(self):
        p = self.demanda_vpm / 60 * PASO_S
        for k, w in PESO_ACCESO.items():
            if random.random() < p * w:
                self._agregar_silencioso(k)

    def _agregar_silencioso(self, acceso):
        opciones = self.rutas.get(acceso)
        if opciones:
            self.n_veh += 1
            self.T.vehicle.add(f"v_{self.n_veh}", random.choice(opciones)[1], depart="now", departLane="best")

    # ---------------- preempción de emergencia ----------------
    def _preempcion(self, vivos):
        activos = set()
        if self.preempcion:
            for v in vivos:
                if not v.startswith("AMB"):
                    continue
                for tls, idx, dist, st in self.T.vehicle.getNextTLS(v)[:1]:
                    if dist > 60:
                        continue
                    activos.add(tls)
                    if tls not in self.preempt:
                        links = self.T.trafficlight.getControlledLinks(tls)
                        carril = links[idx][0][0] if links[idx] else None
                        verde = [i for i, l in enumerate(links) if l and l[0][0] == carril] or [idx]
                        actual = self.T.trafficlight.getRedYellowGreenState(tls)
                        ambar = "".join("y" if (c in "Gg" and i not in verde) else c for i, c in enumerate(actual))
                        objetivo = "".join("G" if i in verde else "r" for i in range(len(actual)))
                        necesita_ambar = ambar != actual
                        self.preempt[tls] = {"t": self.T.simulation.getTime(), "obj": objetivo, "ambar": necesita_ambar}
                        self.T.trafficlight.setRedYellowGreenState(tls, ambar if necesita_ambar else objetivo)
                        nombre = next(k for k, t in self.tls.items() if t == tls)
                        self._evento(f"Preempción en {nombre} para {v}")
        ahora = self.T.simulation.getTime()
        for tls in list(self.preempt):
            p = self.preempt[tls]
            if tls not in activos:
                self.T.trafficlight.setProgram(tls, "sigma")
                del self.preempt[tls]
            elif p["ambar"] and ahora - p["t"] >= sim.REGLAS["ambar_s"]:
                self.T.trafficlight.setRedYellowGreenState(tls, p["obj"]); p["ambar"] = False

    # ---------------- Crew en vivo ----------------
    def _replanificar_crew(self, api_key=None):
        if self.crew["estado"] == "deliberando":
            return
        api_key = (api_key or "").strip() or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            self.crew.update(estado="error", mensaje="Escribe tu API key de Gemini para usar el Crew en vivo.")
            self._evento("Crew: falta la API key"); return
        vivos = self.T.vehicle.getIDList()
        foto = sim._foto(self.tls, self.verdes_fijos, int(self.T.simulation.getTime()), (), vivos, conn=self.T)
        foto["emergencia"] = None
        amb = [v for v in vivos if v.startswith("AMB")]
        if amb:
            foto["emergencia"] = {"tipo": "ambulancia", "id": amb[0], "en_red": True}
        self.crew.update(estado="deliberando", mensaje="4 agentes analizando el estado actual…")
        self._evento("Crew: deliberando con la foto actual")
        threading.Thread(target=self._hilo_crew, args=(foto, api_key), daemon=True).start()

    def _hilo_crew(self, foto, api_key):
        try:
            import nest_asyncio; nest_asyncio.apply()
        except Exception:
            pass
        try:
            from crewai import LLM
            import sigma_crew as sc
            sc.set_foto(foto)
            llm = LLM(model=os.environ.get("SIGMA_MODELO", "gemini/gemini-2.5-flash"), temperature=0.2,
                      api_key=api_key)          # la clave del usuario solo vive en esta llamada
            res = sc.crear_crew(llm, verbose=False, log=os.path.join(self.build, "sigma_crew_log.txt")).kickoff(
                inputs={"hora": time.strftime("%H:%M")})
            pf = res.pydantic
            plan = {p.interseccion: p.verdes_s for p in pf.planes}
            val = json.loads(sc.validar_reglas.run(plan_json=json.dumps(plan)))
            self.crew.update(estado="listo", mensaje="Plan validado por el Supervisor.",
                             reporte=pf.reporte_markdown, plan=val["plan_corregido"])
            self.cmds.put(("plan", "crew_vivo"))
        except Exception as e:
            msg = str(e).replace(api_key, "***") if api_key else str(e)
            if any(w in msg.lower() for w in ("api key", "api_key", "permission", "401", "403", "invalid")):
                msg = "La API key no es válida o no tiene permisos. Revísala y vuelve a intentar."
            self.crew.update(estado="error", mensaje=f"Error del Crew: {msg}")

    # ---------------- bucle ----------------
    def _procesar_cmds(self):
        while not self.cmds.empty():
            c = self.cmds.get()
            accion = c[0]
            if accion == "play":
                self.corriendo = True
            elif accion == "pausa":
                self.corriendo = False
            elif accion == "toggle":
                self.corriendo = not self.corriendo
            elif accion == "reiniciar":
                self._iniciar()
            elif accion == "velocidad":
                self.velocidad = max(0.25, min(float(c[1]), 50))
            elif accion == "demanda":
                self.demanda_vpm = max(0.0, min(float(c[1]), 120))
            elif accion == "preempcion":
                self.preempcion = bool(c[1])
            elif accion == "autos":
                self._agregar(c[1], int(c[2]))
            elif accion == "ambulancia":
                self._agregar(c[1], 1, "ambulancia", c[2] or None)
            elif accion == "rafaga":
                for k, w in PESO_ACCESO.items():
                    self._agregar(k, max(1, round(int(c[1]) * w)))
            elif accion == "plan":
                self._aplicar_plan(c[1])
            elif accion == "crew":
                self._replanificar_crew(c[1] if len(c) > 1 else None)

    def _paso(self):
        self._demanda_fondo()
        self.T.simulationStep()
        vivos = self.T.vehicle.getIDList()
        t = self.T.simulation.getTime()
        for v in self.T.simulation.getArrivedIDList():
            if v in self.esperas_vivos:
                self.esperas_fin.append(self.esperas_vivos.pop(v))
            self.llegadas.append(t)
            if v in self.amb_inicio:
                dt = t - self.amb_inicio.pop(v)
                self.amb_tiempos.append(dt)
                self._evento(f"🚑 {v} salió en {dt:.0f} s")
        for v in vivos:
            self.esperas_vivos[v] = self.T.vehicle.getAccumulatedWaitingTime(v)
        self._preempcion(vivos)
        if abs(t % 5) < 1e-6:   # cada 5 s
            det = sum(1 for v in vivos if self.T.vehicle.getSpeed(v) < 0.1)
            self.hist = (self.hist + [[t, len(vivos), det]])[-240:]

    def _foto_ui(self):
        vivos = self.T.vehicle.getIDList()
        t = self.T.simulation.getTime()
        vehs, det = [], 0
        for v in vivos:
            x, y = self.T.vehicle.getPosition(v)
            w = self.T.vehicle.getWaitingTime(v)
            amb = v.startswith("AMB")
            det += w > 0
            vehs.append([round(x, 1), round(y, 1), round(self.T.vehicle.getAngle(v)), 3 if amb else (2 if w > 30 else 1 if w > 0 else 0)])
        luces = []
        for tls, pos in self.luces.items():
            st = self.T.trafficlight.getRedYellowGreenState(tls)
            luces += [[round(p[0], 1), round(p[1], 1), COLOR_LUZ.get(c, "#888")] for p, c in zip(pos, st) if p]
        esperas = self.esperas_fin[-200:]
        rec = [x for x in self.llegadas if x > t - 300]
        return {"t": t, "corriendo": self.corriendo, "vehiculos": vehs, "luces": luces,
                "metricas": {"en_red": len(vivos), "detenidos": det,
                             "espera_prom_s": round(sum(esperas) / len(esperas), 1) if esperas else 0,
                             "salidas_min": round(len(rec) / (min(t, 300) / 60), 1) if t > 30 else 0,
                             "completados": len(self.esperas_fin),
                             "amb_ultimo_s": self.amb_tiempos[-1] if self.amb_tiempos else None,
                             "amb_prom_s": round(sum(self.amb_tiempos) / len(self.amb_tiempos)) if self.amb_tiempos else None},
                "plan": {"nombre": self.plan_nombre, "verdes": self.plan_verdes},
                "preempcion_activa": [k for k, v in self.tls.items() if v in self.preempt],
                "config": {"velocidad": self.velocidad, "demanda_vpm": self.demanda_vpm, "preempcion": self.preempcion},
                "crew": {k: v for k, v in self.crew.items() if k != "plan"}, "hist": self.hist, "log": self.log}

    def bucle(self):
        self._iniciar()
        ultimo_ui = 0
        while True:
            try:
                self._procesar_cmds()
                if self.corriendo:
                    inicio = time.time()
                    for _ in range(max(1, round(self.velocidad))):
                        self._paso()
                    periodo = PASO_S / min(self.velocidad, 1.0)
                    time.sleep(max(0.0, periodo - (time.time() - inicio)))
                else:
                    time.sleep(0.05)
                if time.time() - ultimo_ui > 0.1:
                    with self.lock:
                        self.estado = self._foto_ui()
                    ultimo_ui = time.time()
            except traci.exceptions.FatalTraCIError:
                break
            except Exception as e:     # un comando mal formado no debe tumbar la simulación
                self._evento(f"Error: {e}")

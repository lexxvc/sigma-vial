# ============================================================
# SIGMA-Vial · Servidor de la interfaz en tiempo real
# Uso:  python app/servidor.py            → abre http://localhost:5000
#       python app/servidor.py --sumo-gui → además abre la ventana de SUMO
# ============================================================
import argparse, os, sys, threading, webbrowser

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from motor import Motor  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
import logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)   # sin spam de peticiones en la consola/Colab
app = Flask(__name__, static_folder=os.path.join(AQUI, "static"))
motor: Motor = None
_geo = None


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/red")
def red():
    return jsonify(_geo)


@app.get("/api/estado")
def estado():
    with motor.lock:
        return jsonify(motor.estado)


@app.post("/api/cmd")
def cmd():
    d = request.get_json(force=True)
    accion = d.get("accion")
    args = {"autos": (d.get("acceso"), d.get("n", 1)), "ambulancia": (d.get("acceso"), d.get("destino")),
            "velocidad": (d.get("valor"),), "demanda": (d.get("valor"),), "preempcion": (d.get("valor"),),
            "plan": (d.get("nombre"),), "rafaga": (d.get("n", 30),),
            "crew": (d.get("api_key"),)}.get(accion, ())
    motor.cmds.put((accion, *args))
    return jsonify(ok=True)


def lanzar(puerto=5000, sumo_gui=False, abrir=True, bloquear=True):
    """Arranca el motor y el servidor. En Colab/Jupyter usar bloquear=False."""
    global motor, _geo
    motor = Motor(sumo_gui=sumo_gui)
    _geo = motor.geometria()
    threading.Thread(target=motor.bucle, daemon=True).start()
    if abrir:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{puerto}")).start()
    kw = dict(host="0.0.0.0", port=puerto, debug=False, use_reloader=False, threaded=True)
    if bloquear:
        app.run(**kw)
    else:
        threading.Thread(target=app.run, kwargs=kw, daemon=True).start()
    return motor


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="SIGMA-Vial en tiempo real")
    p.add_argument("--puerto", type=int, default=5000)
    p.add_argument("--sumo-gui", action="store_true", help="abre también la ventana nativa de SUMO")
    p.add_argument("--no-abrir", action="store_true", help="no abrir el navegador automáticamente")
    a = p.parse_args()
    lanzar(a.puerto, a.sumo_gui, not a.no_abrir)

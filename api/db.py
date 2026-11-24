# ============================================================
# SIGMA-Vial · Persistencia (SQLite)
# Historial consultable de ciclos, eventos del Crew, vetos y decisiones humanas.
# Es lo que alimenta la vista 4 (bitácora). En producción se cambia por
# PostgreSQL sin tocar el resto: todas las consultas pasan por este módulo.
# ============================================================
import json
import os
import sqlite3
import threading

RUTA = os.getenv("SIGMA_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                           "data", "sigma.db"))
_candado = threading.Lock()

ESQUEMA = """
CREATE TABLE IF NOT EXISTS ciclos (
    id TEXT PRIMARY KEY, numero INTEGER, inicio TEXT, fin TEXT, escenario TEXT, motor TEXT,
    estado TEXT, error TEXT, version_candidata TEXT, huella TEXT
);
CREATE TABLE IF NOT EXISTS eventos (
    ciclo_id TEXT, seq INTEGER, ts TEXT, tipo TEXT, agente TEXT, texto TEXT, datos TEXT
);
CREATE TABLE IF NOT EXISTS vetos (
    ciclo_id TEXT, numero INTEGER, ts TEXT, tarea TEXT, archivo TEXT, interseccion TEXT,
    agente TEXT, regla TEXT, parametro TEXT, resultado TEXT
);
CREATE TABLE IF NOT EXISTS decisiones (
    ciclo_id TEXT, numero INTEGER, ts TEXT, accion TEXT, operador TEXT, version TEXT,
    huella TEXT, motivo TEXT
);
"""


def _conexion():
    os.makedirs(os.path.dirname(RUTA), exist_ok=True)
    c = sqlite3.connect(RUTA, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def iniciar():
    with _candado, _conexion() as c:
        c.executescript(ESQUEMA)


def ejecutar(sql, args=()):
    with _candado, _conexion() as c:
        c.execute(sql, args)


def consultar(sql, args=()):
    with _candado, _conexion() as c:
        return [dict(f) for f in c.execute(sql, args).fetchall()]


def siguiente_numero():
    fila = consultar("SELECT COALESCE(MAX(numero), 8490) AS n FROM ciclos")
    return fila[0]["n"] + 1


def guardar_evento(ciclo_id, ev):
    ejecutar("INSERT INTO eventos VALUES (?,?,?,?,?,?,?)",
             (ciclo_id, ev["seq"], ev["ts"], ev["tipo"], ev.get("agente", ""), ev.get("texto", ""),
              json.dumps(ev.get("datos", {}), ensure_ascii=False)))

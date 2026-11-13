"""
SIGMA-Vial — Fase 3
Generador de telemetria sintetica del circuito vial.

Produce los archivos que consume el Crew de CrewAI, con la misma estructura que
tendrian las salidas reales de SUMO (tripinfo.xml agregado, detectores E1/E2,
bitacora del Supervisor). Se usa mientras el modelo del circuito en SUMO no
esta disponible; al existir, basta sustituir estos CSV por los que exporte
SUMO conservando los nombres de columna.

Semilla fija: la corrida es reproducible.
"""

import json
import os
import random
from pathlib import Path

SEMILLA = 20260917
DIR_DATOS = Path(__file__).parent / "datos"

# --- Topologia del circuito -------------------------------------------------
# NOTA PARA EL EQUIPO: sustituir por los nombres reales de las intersecciones
# del circuito una vez levantado el aforo en campo.
INTERSECCIONES = {
    "I1": {
        "nombre": "Acceso Principal",
        "accesos": 4,
        "cruces_peatonales": 2,
        "ancho_cruce_m": 12.0,
        "demanda_base_vph": 820,
        "demanda_peatonal_base_pph": 240,
    },
    "I2": {
        "nombre": "Estacionamiento Norte",
        "accesos": 3,
        "cruces_peatonales": 2,
        "ancho_cruce_m": 9.5,
        "demanda_base_vph": 540,
        "demanda_peatonal_base_pph": 410,
    },
    "I3": {
        "nombre": "Plaza Central",
        "accesos": 3,
        "cruces_peatonales": 3,
        "ancho_cruce_m": 14.0,
        "demanda_base_vph": 380,
        "demanda_peatonal_base_pph": 680,
    },
    "I4": {
        "nombre": "Salida Oriente",
        "accesos": 4,
        "cruces_peatonales": 1,
        "ancho_cruce_m": 10.5,
        "demanda_base_vph": 760,
        "demanda_peatonal_base_pph": 150,
    },
}

# Tramos del circuito: (origen, destino, longitud util en vehiculos)
# La capacidad del tramo es lo que determina si una fila provoca spillback.
TRAMOS = [
    ("I1", "I2", 22),
    ("I2", "I3", 14),   # tramo corto: el eslabon debil del circuito
    ("I3", "I4", 25),
    ("I4", "I1", 30),
]

ESCENARIOS = {
    "pico_matutino": {"factor_veh": 1.00, "factor_peat": 1.00, "franja": "07:00-09:00"},
    "valle": {"factor_veh": 0.38, "factor_peat": 0.30, "franja": "11:00-13:00"},
    "evento_especial": {"factor_veh": 1.18, "factor_peat": 1.65, "franja": "18:00-20:00"},
    "falla_sensores": {"factor_veh": 0.95, "factor_peat": 0.92, "franja": "07:00-09:00"},
}

# Controladores comparados. Los dos primeros son las lineas base de la Fase 2.
CONTROLADORES = ["plan_fijo_webster", "maxima_presion", "sigma_vial"]

N_REPLICAS = 10


def _perfil_controlador(ctrl, inter_id, esc, cfg_esc):
    """Caracteristicas medias de desempeno por controlador.

    Los valores reproducen el comportamiento esperado segun la literatura:
    el plan fijo se degrada con la demanda, maxima presion maneja bien el
    vehiculo pero ignora al peaton, y SIGMA-Vial mejora ambos salvo donde
    la configuracion vigente esta mal calibrada (I3, por el peso peatonal).
    """
    info = INTERSECCIONES[inter_id]
    carga = cfg_esc["factor_veh"] * info["demanda_base_vph"] / 600.0
    carga_peat = cfg_esc["factor_peat"] * info["demanda_peatonal_base_pph"] / 300.0

    if ctrl == "plan_fijo_webster":
        espera_veh = 26 + 30 * carga ** 2
        espera_peat = 34 + 10 * carga_peat
        spillback = 1.4 * max(0.0, carga - 1.0)
    elif ctrl == "maxima_presion":
        espera_veh = 20 + 15 * carga ** 2
        espera_peat = 38 + 16 * carga_peat          # castiga al peaton
        spillback = 0.5 * max(0.0, carga - 1.2)
    else:  # sigma_vial
        espera_veh = 17 + 12 * carga ** 2
        espera_peat = 22 + 7 * carga_peat
        spillback = 0.35 * max(0.0, carga - 1.25)
        # Desajuste deliberado: en I3 el peso peatonal vigente es muy bajo
        # para la demanda peatonal real. Es el hallazgo que debe encontrar
        # el Crew.
        if inter_id == "I3":
            espera_peat *= 2.35
        # El tramo I2->I3 es corto: la configuracion vigente no lo protege.
        if inter_id == "I2":
            spillback += 0.9 * max(0.0, carga - 0.75)

    if esc == "falla_sensores":
        espera_veh *= 1.22
        espera_peat *= 1.15
        spillback *= 1.5

    return espera_veh, espera_peat, spillback


def generar_metricas(rng):
    filas = []
    for esc, cfg_esc in ESCENARIOS.items():
        for inter_id, info in INTERSECCIONES.items():
            for ctrl in CONTROLADORES:
                ev, ep, sb = _perfil_controlador(ctrl, inter_id, esc, cfg_esc)
                for rep in range(1, N_REPLICAS + 1):
                    r = lambda s: rng.gauss(0, s)  # noqa: E731
                    espera_veh = max(3.0, ev + r(ev * 0.11))
                    espera_peat = max(4.0, ep + r(ep * 0.13))
                    filas.append({
                        "escenario": esc,
                        "interseccion": inter_id,
                        "controlador": ctrl,
                        "replica": rep,
                        "semilla": 1000 + rep,
                        "espera_veh_media_s": round(espera_veh, 2),
                        "espera_veh_p95_s": round(espera_veh * rng.uniform(2.0, 2.5), 2),
                        "espera_peat_media_s": round(espera_peat, 2),
                        "espera_peat_p95_s": round(espera_peat * rng.uniform(1.7, 2.1), 2),
                        "cola_max_veh": round(max(1.0, espera_veh / 2.6 + r(1.5))),
                        "spillback_eventos_h": round(max(0.0, sb + r(0.25)), 2),
                        "paradas_por_veh": round(max(0.3, espera_veh / 22 + r(0.12)), 2),
                        "veh_atendidos_h": round(
                            INTERSECCIONES[inter_id]["demanda_base_vph"]
                            * cfg_esc["factor_veh"] * rng.uniform(0.93, 1.0)),
                        "peat_atendidos_h": round(
                            info["demanda_peatonal_base_pph"]
                            * cfg_esc["factor_peat"] * rng.uniform(0.94, 1.0)),
                        "co2_kg_h": round(espera_veh * 0.41 + rng.uniform(-1.5, 1.5), 2),
                    })
    return filas


def generar_incidentes(rng):
    """Bitacora del Supervisor: vetos, fallas y prioridades de emergencia."""
    tipos = [
        ("veto_regla_dura", "Plan propuesto con verde peatonal inferior al tiempo de despeje"),
        ("falla_sensor", "Detector de area sin datos por mas de 120 s"),
        ("prioridad_emergencia", "Vehiculo de clase emergency detectado aguas arriba"),
        ("degradacion_plan_fijo", "Mensajes del Coordinador ausentes por mas de 2 ciclos"),
        ("spillback_detectado", "Fila alcanzo la interseccion anterior"),
    ]
    filas = []
    for esc in ESCENARIOS:
        n = 14 if esc == "falla_sensores" else 6
        for _ in range(n):
            inter = rng.choice(list(INTERSECCIONES))
            if esc == "falla_sensores":
                tipo, desc = tipos[rng.choice([1, 1, 1, 3, 4])]
            else:
                tipo, desc = rng.choice(tipos)
            # El tramo corto I2->I3 concentra los spillback.
            if tipo == "spillback_detectado":
                inter = "I2" if rng.random() < 0.7 else inter
            filas.append({
                "escenario": esc,
                "interseccion": inter,
                "t_simulacion_s": rng.randrange(0, 7200),
                "tipo": tipo,
                "descripcion": desc,
            })
    filas.sort(key=lambda f: (f["escenario"], f["t_simulacion_s"]))
    return filas


def configuracion_vigente():
    """Configuracion que ejecuta hoy la capa de tiempo real (Fase 2).

    Es la entrada que el Crew debe diagnosticar y, en su caso, proponer
    modificar. Los pesos corresponden a la funcion de utilidad del
    Controlador de interseccion.
    """
    return {
        "version": "v1.0-fase2",
        "pesos_utilidad": {
            "w_veh_atendidos": 1.0,
            "w_peat_atendidos": 1.0,
            "w_espera_veh": 0.8,
            "w_espera_peat": 0.6,
            "w_ocupacion_aguas_abajo": 0.4,
        },
        "planes_por_interseccion": {
            "I1": {"ciclo_s": 95, "verde_principal_s": 44, "verde_secundario_s": 28,
                   "verde_peatonal_s": 16, "desfase_s": 0},
            "I2": {"ciclo_s": 85, "verde_principal_s": 40, "verde_secundario_s": 24,
                   "verde_peatonal_s": 14, "desfase_s": 22},
            "I3": {"ciclo_s": 85, "verde_principal_s": 46, "verde_secundario_s": 22,
                   "verde_peatonal_s": 11, "desfase_s": 44},
            "I4": {"ciclo_s": 95, "verde_principal_s": 50, "verde_secundario_s": 26,
                   "verde_peatonal_s": 12, "desfase_s": 61},
        },
        "umbral_spillback_pct": 0.85,
        "periodo_decision_s": 5,
    }


def normativa():
    """Reglas duras y parametros normativos que aplica el Auditor.

    Los tiempos provienen de la ficha de las Fases 1-2 y estan marcados ahi
    como "[Confirmar con normativa local]": son parametros de diseno del
    proyecto, no una cita normativa verificada. La velocidad de cruce
    peatonal de 1.0 m/s es el valor conservador adoptado para personas con
    movilidad reducida.
    """
    return {
        "fases_por_ciclo": 3,   # principal + secundaria + peatonal (exclusiva)
        "ambar_s": 3,
        "todo_rojo_s": 2,
        "verde_veh_min_s": 8,
        "verde_veh_max_s": 60,
        "ciclo_max_s": 120,
        "espera_max_carril_s": 60,
        "espera_max_cruce_peatonal_s": 60,
        "velocidad_cruce_peatonal_m_s": 1.0,
        # Limites de esquema (R0): evitan configuraciones inaplicables.
        "peso_max": 5.0,
        "umbral_spillback_min": 0.3,    # fraccion de la capacidad del tramo
        "umbral_spillback_max": 0.95,
        "periodo_decision_max_s": 10,   # la ficha fija 5 s
        "anchos_cruce_m": {k: v["ancho_cruce_m"] for k, v in INTERSECCIONES.items()},
        "prioridad_movilidad": [
            "peaton", "ciclista", "transporte_publico", "vehiculo_particular",
        ],
    }


def calendario_academico():
    return {
        "jornada": {"inicio": "06:30", "fin": "22:30"},
        "pulsos": [
            {"nombre": "entrada_matutina", "franja": "06:50-07:20", "intensidad": "alta"},
            {"nombre": "cambio_clase_1", "franja": "08:55-09:10", "intensidad": "media"},
            {"nombre": "cambio_clase_2", "franja": "10:55-11:10", "intensidad": "media"},
            {"nombre": "comida", "franja": "14:00-15:00", "intensidad": "media"},
            {"nombre": "entrada_vespertina", "franja": "17:50-18:20", "intensidad": "alta"},
            {"nombre": "salida_nocturna", "franja": "21:50-22:20", "intensidad": "alta"},
        ],
        "dias_inhabiles_proximos": ["2026-11-02", "2026-11-16"],
        "eventos_campus": [
            {"nombre": "Ceremonia de titulacion", "fecha": "2026-11-07",
             "franja": "17:00-21:00", "afluencia_extra_peatonal_pct": 65},
        ],
    }


def main():
    rng = random.Random(SEMILLA)
    DIR_DATOS.mkdir(parents=True, exist_ok=True)

    metricas = generar_metricas(rng)
    cols = list(metricas[0].keys())
    with open(DIR_DATOS / "metricas_simulacion.csv", "w", encoding="utf-8") as f:
        f.write(",".join(cols) + "\n")
        for fila in metricas:
            f.write(",".join(str(fila[c]) for c in cols) + "\n")

    incidentes = generar_incidentes(rng)
    cols_i = list(incidentes[0].keys())
    with open(DIR_DATOS / "bitacora_supervisor.csv", "w", encoding="utf-8") as f:
        f.write(",".join(cols_i) + "\n")
        for fila in incidentes:
            f.write(",".join(str(fila[c]) for c in cols_i) + "\n")

    for nombre, obj in [
        ("configuracion_vigente.json", configuracion_vigente()),
        ("normativa.json", normativa()),
        ("calendario_academico.json", calendario_academico()),
        ("topologia_circuito.json", {"intersecciones": INTERSECCIONES, "tramos": TRAMOS}),
    ]:
        with open(DIR_DATOS / nombre, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)

    print(f"Telemetria generada en {DIR_DATOS}")
    print(f"  metricas_simulacion.csv   {len(metricas)} filas")
    print(f"  bitacora_supervisor.csv   {len(incidentes)} filas")
    print("  configuracion_vigente.json, normativa.json,")
    print("  calendario_academico.json, topologia_circuito.json")


if __name__ == "__main__":
    main()

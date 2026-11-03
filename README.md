# SIGMA-Vial · Control semafórico multiagente con CrewAI + SUMO

Sistema que optimiza los tiempos de verde de 4 intersecciones del circuito interno de un campus.
Una simulación en **SUMO** entrega el estado de tráfico a un equipo de **4 agentes LLM (CrewAI)** que
deliberan un plan; un validador determinista garantiza las reglas de seguridad y el plan se re-simula
contra el plan fijo.

![Simulación](results/figuras/simulacion.gif)

## Arquitectura

| Agente | Rol | Herramientas |
|---|---|---|
| Analista de Tráfico | Estima filas y demanda; detecta sensores degradados | `leer_estado_interseccion`, `estimar_fila` |
| Planificador de Fases | Maximiza la utilidad esperada U por intersección | `calcular_utilidad`, `estimar_fila` |
| Coordinador del Circuito | Evita spillback, aplica calendario, prioriza emergencias | `consultar_calendario`, `ocupacion_tramos`, `calcular_utilidad` |
| Supervisor de Seguridad | Valida reglas duras y emite el plan final | `validar_reglas` |

**Reglas duras:** verde 8–60 s · ámbar 3 s · todo-rojo 2 s · ciclo ≤ 120 s · espera peatonal ≤ 60 s.

## Resultados (misma demanda y semilla)

| Métrica | Plan fijo | SIGMA-Vial | Mejora |
|---|---:|---:|---:|
| Espera promedio (s) | 229.9 | 64.9 | 71.8 % |
| Espera p95 (s) | 694.0 | 173.0 | 75.1 % |
| Espera máx. continua (s) | 150.0 | 120.0 | 20.0 % |
| Teleports por bloqueo | 48 | 4 | 91.7 % |
| Tiempo de ambulancia (s) | 750 | 149 | 80.1 % |

![Comparación](results/figuras/comparacion.png)

Detalle: [`results/reporte_crew.md`](results/reporte_crew.md) · [`results/plan_final.json`](results/plan_final.json)

## Estructura

```
sigma-vial/
├── notebooks/SIGMA_Vial_CrewAI.ipynb   # flujo completo con resultados
├── src/
│   ├── sigma_sim.py    # red, demanda, simulación y métricas (SUMO + TraCI)
│   ├── sigma_crew.py   # agentes, tareas y herramientas (CrewAI)
│   └── sigma_anim.py   # animación plan fijo vs Crew
├── data/               # mapa.osm del campus
├── results/            # figuras, reporte y plan final
├── requirements.txt
└── .env.example
```

## Uso

1. Coloca el mapa en `data/mapa.osm` (ver [`data/README.md`](data/README.md)).
2. Configura `GEMINI_API_KEY` (Colab Secrets o variable de entorno; ver `.env.example`).
3. Abre `notebooks/SIGMA_Vial_CrewAI.ipynb` en Colab o local y ejecuta las celdas en orden.

```bash
pip install -r requirements.txt
```

LLM por defecto: `gemini/gemini-2.5-flash` (temperatura 0.2).

## Autor

Alejandro Victoria Castelán · Maestría en Big Data e IA — materia de Agentes Inteligentes.

---
title: SIGMA-Vial
emoji: 🚦
colorFrom: red
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# SIGMA-Vial · Control semafórico multiagente con CrewAI + SUMO

Sistema para el circuito vial de la Universidad Anáhuac Puebla, en dos capas, como lo define la
documentación del proyecto (Fases 1 a 4):

| | Capa de tiempo real | Capa de supervisión |
|---|---|---|
| Qué decide | Qué hace el semáforo ahora | Cómo debe comportarse el sistema (pesos, planes, umbrales) |
| Tecnología | **SUMO + TraCI** sobre el mapa real del campus (`data/mapa.osm`), sin LLM | **CrewAI**: Analista, Ingeniero, Auditor y Sintetizador (Fase 3) |
| Dónde está | `src/sigma_sim.py`, `app/motor.py` | `supervision/` |
| Interfaz | Página «Operación en vivo» | Páginas 1 a 4 de la consola (Fase 4) |

![Simulación](results/figuras/simulacion.gif)

## Consola de supervisión (Fase 4)

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

1. **Configurar ciclo**: escenario, telemetría (se valida el CSV), motor de razonamiento.
2. **Ejecución de agentes**: avance de las 6 tareas y barreras deterministas (validador, publicador, verificación).
3. **Resultados y decisión**: diagnóstico frente a las líneas base, cambios vigente → candidata, **prueba en el
   simulador del campus** y los controles *Aprobar y liberar*, *Ajustar pesos*, *Rechazar* y *Descargar*.
4. **Auditoría y trazabilidad**: dictamen R0–R6, violaciones por intersección y bitácora de decisiones humanas.

Al liberar una configuración, la página **Operación en vivo** puede aplicarla al simulador con el plan
«Supervisión». La traducción vive en `src/puente.py`.

Motores disponibles: guion fijo (`stub`, sin clave; no demuestra razonamiento), Gemini (con tu API key) y
qwen2.5:7b por Ollama (solo en una máquina con Ollama).

### Límites que conviene conocer

- La telemetría del diagnóstico (`supervision/datos/metricas_simulacion.csv`) sigue siendo **sintética**.
  El siguiente paso es generarla con el simulador (ver «Pendientes»).
- La configuración de la Fase 3 supone 3 fases por intersección. En la red real I4 tiene 4 fases vehiculares;
  el puente reparte los verdes y **anota cada ajuste** (se ven en la consola).
- La red no modela peatones: la fase peatonal cuenta como rojo para los vehículos y la espera peatonal no se mide.
- La prueba en el simulador usa una sola semilla: detecta deterioros graves, no es una prueba estadística.

### Pendientes

- Generar `metricas_simulacion.csv` con SUMO: 4 escenarios × 3 controladores × 10 semillas.
- Agregar peatones y cruces a la red (`netconvert --sidewalks.guess --crossings.guess`).
- Implementar las líneas base (Webster y máxima presión) y el Controlador por utilidad en TraCI.

### Código anterior

`legacy/sigma_crew_tiempo_real.py` es el primer Crew (Analista, Planificador, Coordinador, Supervisor), que
replanificaba los verdes en vivo con un LLM. Se retiró del lazo de control porque la Fase 3 concluyó que un
modelo de lenguaje no debe decidir el semáforo. `notebooks/SIGMA_Vial_CrewAI.ipynb` y `results/` documentan
esa versión.

## ☁️ Correr en la nube gratis — Google Colab (recomendado)

[![Abrir en Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/lexxvc/sigma-vial/blob/main/notebooks/app_en_vivo_colab.ipynb)

1. Clic en el botón **Open in Colab**.
2. **Entorno de ejecución → Ejecutar todo**.
3. Al terminar, clic en **👉 Abrir SIGMA-Vial en vivo**.

## ☁️ Otra opción — Docker (Hugging Face Spaces u otra nube con contenedores)

El repo incluye `Dockerfile`; la app escucha en el puerto `7860`.

## ☁️ Alternativa — Streamlit Community Cloud

La app está lista para **Streamlit Community Cloud** (gratis):

1. Entra a **https://share.streamlit.io** e inicia sesión con tu cuenta de GitHub.
2. **Create app** → *Deploy a public app from GitHub*.
3. Repository: `lexxvc/sigma-vial` · Branch: `main` · Main file path: `streamlit_app.py`.
4. En **Advanced settings** elige Python **3.12** y, si quieres correr el Crew de supervisión con Gemini, pega en *Secrets*:
   ```toml
   GEMINI_API_KEY = "tu_api_key"
   ```
5. **Deploy**. La primera vez tarda unos minutos en instalar; después tendrás un link `https://….streamlit.app` que abre desde cualquier navegador o celular.

![App en Streamlit](results/figuras/app_streamlit.png)

Cada persona que abre el link tiene su propia simulación independiente.

## Interfaz en tiempo real (local)

![App en vivo](results/figuras/app_en_vivo.png)

App web local para ver el circuito funcionando y meter tráfico a mano:

- **Accesos:** botones `+1`, `+10` y `🚑` en los 5 accesos (sur, norte y 3 estacionamientos), o clic sobre el acceso en el mapa (Shift + clic = ambulancia).
- **Cambio de clase:** ráfaga de 40 autos repartidos entre los accesos.
- **Plan semafórico en caliente:** plan fijo de la red ↔ configuración liberada en la consola de supervisión.
- **Preempción de emergencia:** verde forzado (con ámbar previo) cuando una ambulancia se acerca a menos de 60 m de un semáforo.
- **Métricas en vivo:** vehículos en red, detenidos, espera promedio, salidas/min y tiempo de cruce de ambulancias.

```bash
pip install -r requirements.txt
python app/servidor.py            # abre http://localhost:5000
python app/servidor.py --sumo-gui # además abre la ventana nativa de SUMO
```

En Windows basta con doble clic en `iniciar_app.bat`.

## Estructura

```
sigma-vial/
├── streamlit_app.py       # enrutador: consola de supervisión + operación en vivo
├── vistas/                # una página por vista (configurar, ejecucion, resultados, auditoria, operacion)
├── supervision/           # Crew de la Fase 3: sigma_crew.py, herramientas.py, verificacion.py, datos/, salidas/
├── src/
│   ├── sigma_sim.py       # red, demanda, simulación y métricas (SUMO + TraCI)
│   ├── puente.py          # configuración de supervisión → programas de semáforo; simulación de evaluación
│   └── sigma_anim.py
├── app/                   # motor de tiempo real, API Flask e interfaz local
├── data/mapa.osm          # mapa del campus
├── legacy/                # primer Crew, fuera de uso
├── notebooks/ · results/  # corridas y figuras de la versión anterior
└── requirements.txt · packages.txt · Dockerfile
```

## Autor

Alejandro Victoria Castelán · Maestría en Big Data e IA — materia de Agentes Inteligentes.

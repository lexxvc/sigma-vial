# SIGMA-Vial — Fase 3

Capa de supervisión implementada con **CrewAI**: un equipo de cuatro agentes de IA que revisa
cómo se comporta el sistema de semáforos del circuito vial de la Universidad Anáhuac Puebla,
propone ajustes, los audita contra las reglas de seguridad y publica la versión aprobada.

> **¿Quieres probarlo ya?** Lee primero [PRUEBA_RAPIDA.md](PRUEBA_RAPIDA.md). El documento de la
> entrega (`P3_AI_00621893.pdf`) explica el diseño completo.

## Idea en dos capas

| | Capa de tiempo real (Fases 1-2) | Capa de supervisión (Fase 3, este código) |
|---|---|---|
| Qué decide | Qué hace el semáforo **ahora** | Cómo debe **comportarse** el sistema |
| Cadencia | Cada 5 segundos | Una vez por jornada |
| Tecnología | Python + SUMO, determinista | CrewAI + modelo de lenguaje |
| Agentes | Monitor, Controlador, Supervisor | Analista, Ingeniero, Auditor, Sintetizador |

El modelo de lenguaje **nunca** entra en el lazo de control del semáforo: no responde a tiempo ni
permite verificar las reglas de seguridad de forma exacta. La capa de tiempo real **no está
implementada** en este repositorio; el Crew solo escribe `datos/configuracion_aprobada.json`.

## Archivos

```
sigma_crew.py           Agentes, tareas, Crew y guardrail. Punto de entrada: ejecutar()
herramientas.py         6 herramientas (Python normal, sin IA): leer datos, comparar, calcular, validar, publicar
verificacion.py         Revisión automática del reporte final (sin IA)
generar_telemetria.py   Genera los datos de prueba (sintéticos, semilla fija)
llm_stub.py             Modelo "de mentira" con guion fijo, solo para probar el código
Modelfile.qwen25        Alias de Ollama (qwen2.5:7b con contexto de 16k): modelo de la demostración
Modelfile.llama31       Ídem para llama3.1:8b (alternativa descartada)
requirements.txt        Versiones fijadas de las librerías
SIGMA_Vial_Fase3.ipynb  Notebook ejecutado, con las salidas de la corrida de demostración
datos/                  Datos de entrada y configuracion_aprobada.json (salida)
salidas/                Propuestas v1/v2 del Ingeniero y la salida de cada tarea (tarea_*.txt)
reporte_ciclo_supervision.md   Reporte del Sintetizador + verificación automática
corrida_demostracion.log       Log completo de la corrida del notebook
```

## Instalación

Requiere **Python 3.12** (CrewAI no funciona con Python 3.14). Usa un entorno virtual:

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (PowerShell)
pip install -r requirements.txt
python generar_telemetria.py
```

## Ejecución

**Modo de prueba, sin modelo ni internet (stub):**

```bash
python sigma_crew.py
```

El equipo, las tareas y las herramientas son reales; solo el "razonamiento" sigue un guion fijo.
Sirve para comprobar que todo funciona. **No demuestra razonamiento de IA.**

**Con un modelo de lenguaje real, local (Ollama):**

```bash
ollama pull qwen2.5:7b
ollama create sigma-qwen25 -f Modelfile.qwen25     # una sola vez
$env:SIGMA_LLM="ollama"; python sigma_crew.py      # PowerShell
# SIGMA_LLM=ollama python sigma_crew.py            # Linux / Mac
```

**Ojo:** cada corrida sobrescribe `datos/configuracion_aprobada.json`, `salidas/` y
`reporte_ciclo_supervision.md`, que en esta carpeta son los de la demostración real. Prueba en una copia.

Con una GPU de 8 GB cada corrida tarda entre 3 y 12 minutos. Variables opcionales:
`SIGMA_MODELO` (otro modelo de Ollama) y `SIGMA_DELEGACION=1` (activa la delegación del Ingeniero,
apagada por omisión). También existen `SIGMA_LLM=openai` y `SIGMA_LLM=anthropic` con su clave de API.

## Qué garantiza el diseño y qué no

Lo impone el **código** (no depende de la IA):

- El **validador** revisa siete reglas (R0 a R6): esquema y completitud, verdes mínimo y máximo,
  ciclo máximo, despeje peatonal, consistencia de fases y esperas máximas por cruce y por carril. Es la
  única fuente de veredictos. El LLM solo puede indicar el **nombre del archivo** a validar.
- El **publicador** revalida antes de escribir y elige la última propuesta que cumpla las reglas
  (`ultima_valida`). Nunca escribió una configuración inválida.
- La **calculadora de Webster** entrega una propuesta base completa que ya cumple las reglas (ciclo común,
  verdes y desfases). El Ingeniero solo ajusta los pesos y justifica.
- Un **guardrail** devuelve al Ingeniero el error con los valores exactos si altera los planes; como
  último recurso, el código los fija.
- `verificacion.py` marca en el reporte el estado de publicación, las cifras que no aparecen en ninguna
  salida y los archivos citados que no existen, y agrega la tabla antes/después.
- Si una corrida falla (respuesta vacía del modelo), se reintenta hasta 3 veces.

**No** está garantizado:

- Un modelo local de ~7B es **inconsistente**. El texto del reporte puede contener errores o
  afirmaciones cualitativas incorrectas que la verificación numérica no detecta.
- La telemetría es **sintética**. El desajuste de la intersección I3 (espera peatonal ×2.35) se
  **sembró a propósito** para comprobar que el equipo lo diagnostica; no es un hallazgo sobre el circuito real.
- Las propuestas se revisan contra las reglas, **no se simula su desempeño**.
- Los parámetros normativos (ámbar 3 s, todo-rojo 2 s, 1.0 m/s, esperas de 60 s) son los de la ficha de
  las Fases 1-2, marcados allí como «[Confirmar con normativa local]».
- Supuestos de la calculadora: fase peatonal exclusiva una vez por ciclo; flujo 60/40 entre acceso
  principal y secundario; grado de saturación objetivo de 0.9.

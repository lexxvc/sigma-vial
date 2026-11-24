# Guion de la demostración en vivo · 5 minutos

**Antes de entrar al salón**
- Abrir https://lexxco-sigma-vial.hf.space 10 minutos antes (si el Space estaba dormido tarda 1–2 min en despertar).
- Nombre del operador escrito en la barra superior.
- Una corrida previa de prueba con Gemini en los últimos 30 minutos (confirma clave y cuota).
- Vista 4 abierta una vez para que la red de SUMO ya esté construida.
- Plan B en otra pestaña: la misma consola con el motor *Guion fijo* (o Colab / Hugging Face).

| Tiempo | Vista | Qué hacer | Qué decir (idea, no texto literal) |
|---|---|---|---|
| 0:00–0:40 | 1 · Configuración | Escenario *Pico matutino*, motor *Gemini*. Clic en **Probar con un aforo de campo**. | «El operador no puede lanzar un ciclo con datos que no validan: el botón se deshabilita y dice por qué.» |
| 0:40–0:55 | 1 | **Restablecer fuentes** → **Iniciar ciclo**. | «Las reglas R1–R5 están en solo lectura; las evalúa código, no el modelo.» |
| 0:55–2:30 | 2 · Ejecución | Filtrar el log por *Herramientas* y luego por *Vetos*. Señalar el hallazgo de I3 y el veto a v1.1. | «Cada línea dice qué agente actuó y con qué herramienta. El Analista encontró que I3 está 64 % peor. El Ingeniero propuso v1.1; el validador la vetó por R4, R5 y R6, y el Auditor no puede autorizar excepciones. El Ingeniero corrige a v1.2 con ciclo común de 80 s.» |
| 2:30–3:40 | 3 · Resultados | Mostrar el hallazgo R3, la comparativa y el dictamen. **Ajustar propuesta** → verde peatonal de I3 = 10 → *Validar*. | «Ni el operador puede saltarse una regla dura: su ajuste también se veta.» Cancelar. |
| 3:40–4:10 | 3 | **Aprobar y publicar** → casilla → *Autorizar*. | «Es la acción irreversible. El servidor valida por tercera vez y exige la huella exacta de lo que revisé.» |
| 4:10–5:00 | 4 · Mapa | **Aplicar ahora en el simulador**. *Cambio de clase (+40)*, *Ambulancia*, seleccionar I3 → *Forzar todo-rojo*. Bajar a la bitácora. | «La configuración aprobada ya corre sobre la red real del campus en SUMO. La bitácora registra cada veto y quién autorizó qué.» |

**Sobre el veto con Gemini.** En la corrida de prueba con Gemini (23 nov) el Ingeniero usó la
`calculadora_webster`, propuso un ciclo común de 57 s y su v1.1 pasó el validador a la primera, así que el
Auditor no vetó a ningún agente. El veto se demostró igual en dos puntos: el **hallazgo sobre la configuración
vigente** (13 violaciones, R3 en I3) y el **ajuste humano** de la vista 3. Dilo así: «el Ingeniero acertó porque
se apoyó en una herramienta determinista; el veto existe y lo vemos ahora cuando el operador intenta recortar
el cruce peatonal». El lazo completo veto → corrección de la Fase 3 se reproduce siempre con el motor
*Guion fijo* (~40 s), útil si el profesor lo pide.

**Si Gemini falla** (cuota, red): la vista 2 muestra el error en rojo. Cambia el motor a *Guion fijo* en la
vista 1 y relanza: tarda ~40 s y recorre el mismo flujo con herramientas y vetos reales. Dilo abiertamente:
«este motor tiene el razonamiento guionado; las herramientas y los vetos son reales».

**Si el simulador no carga**: la vista 4 sigue mostrando la bitácora; enseña el modal de publicación y el
archivo `configuracion_liberada.json`.

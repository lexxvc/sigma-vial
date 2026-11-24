# Reporte de Plan de Tráfico Aprobado

**Estado General:** El plan ha sido **APROBADO CON CORRECCIONES**.

## Resumen Ejecutivo:
El plan coordinado inicial presentaba una violación de las reglas de seguridad en la intersección I1, donde la duración de una fase verde estaba fuera del rango permitido. Esta fase ha sido corregida automáticamente para cumplir con la normativa. Las demás intersecciones cumplen con las reglas de seguridad.

Se ha implementado una **prioridad de emergencia** en las intersecciones I3 e I4 para facilitar el paso de un vehículo de emergencia.

## Detalles por Intersección:

### Intersección I1:
- **Plan de Verdes (s):** [8, 8]
- **Justificación:** El plan inicial de [5, 8] para I1 fue corregido a [8, 8] debido a que los verdes estaban fuera del rango permitido [8, 60] segundos. La justificación original para el ajuste a [5, 8] era mitigar el spillback detectado en el tramo I1->I3 (ocupación del 74.7%), buscando aumentar la utilidad esperada y reducir el ciclo total de la intersección. Sin embargo, la regla de seguridad prevalece.

### Intersección I2:
- **Plan de Verdes (s):** [9, 9]
- **Justificación:** Para mejorar la coordinación con las intersecciones I3 y I4, y mantener el régimen de 'ciclos cortos y alta frecuencia peatonal'. Aunque el plan [7, 7] ofrecía una utilidad ligeramente superior (26.97 vs 25.4), el plan [9, 9] establece un ciclo de 28 segundos, lo que facilita la coordinación con I3 (también con un ciclo de 28s) y I4 (con un ciclo de 56s, el doble). La espera peatonal se mantiene en un nivel razonable (14s).

### Intersección I3:
- **Plan de Verdes (s):** [10, 8]
- **Justificación:** Para priorizar el paso del vehículo de emergencia hacia su destino. Se ha aumentado el verde de la fase que atiende el acceso del vehículo de emergencia (asumiendo que es la primera fase) de 8 a 10 segundos. Esta duración de verde, aunque ligeramente menor que otras opciones de máxima prioridad, permite un ciclo de 28 segundos, lo que es crucial para la coordinación con I2 y para mantener el principio de 'ciclos cortos' en la medida de lo posible, sin comprometer significativamente la prioridad de la emergencia. La utilidad esperada es de 11.61.

### Intersección I4:
- **Plan de Verdes (s):** [12, 8, 8, 8]
- **Justificación:** Para dar prioridad al acceso del vehículo de emergencia. Se ha aumentado significativamente el verde de la fase que atiende el acceso del vehículo de emergencia (asumiendo que es la primera fase) de 8 a 12 segundos. Esta extensión asegura un paso más rápido para la ambulancia. El ciclo resultante es de 56 segundos, lo que permite una coordinación efectiva al ser el doble del ciclo de I2 e I3. Si bien esto implica una mayor espera peatonal (28s) y una menor utilidad general (1.87), es una compensación necesaria para la prioridad de la emergencia, según lo indicado por la directriz "si es posible" para los ciclos cortos.

## Vetos o Correcciones Aplicadas:
- I1: verdes fuera de [8, 60] s → recortados a [8, 8]

## Prioridad de Emergencia:
Prioridad de paso para vehículo de emergencia en I3 e I4.
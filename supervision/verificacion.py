"""
SIGMA-Vial — Fase 3
Verificacion determinista del reporte final del Sintetizador.

El Sintetizador no tiene herramientas, pero eso solo le quita el acceso a los
datos: no le impide inventar cifras ni afirmar cumplimientos que no ocurrieron.
En la corrida de prueba con modelos locales escribio "cumpliendo con las reglas
duras" cuando el sistema habia rechazado todo, e invento un costo de dinero y
una tabla antes/despues. Este modulo cierra ese hueco en codigo, despues del
Crew, con dos comprobaciones que no dependen de ningun LLM:

  1. Estado de publicacion: si el Auditor no publico ninguna configuracion, el
     reporte lleva un encabezado que lo dice, sin importar lo que afirme.
  2. Cifras sin respaldo: todo numero del reporte debe aparecer en alguna
     salida de las tareas anteriores o en los archivos de datos. Los que no,
     se listan. Es una heuristica: puede dejar pasar una cifra reutilizada
     fuera de contexto, pero detecta cifras inventadas.
"""

import re
from pathlib import Path

DIR_DATOS = Path(__file__).parent / "datos"
_NUM = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")


def _numeros(texto: str) -> set[str]:
    return {m.group(0).replace(",", ".") for m in _NUM.finditer(texto)}


def _normaliza(n: str) -> str:
    """'80.0' y '80' son la misma cifra."""
    try:
        v = float(n)
        return str(int(v)) if v == int(v) else str(v)
    except ValueError:
        return n


def cifras_sin_respaldo(reporte: str, fuentes: list[str]) -> list[str]:
    """Numeros del reporte que no aparecen en ninguna fuente."""
    permitidas = set()
    for ruta in DIR_DATOS.glob("*.json"):
        permitidas |= {_normaliza(n)
                       for n in _numeros(ruta.read_text(encoding="utf-8"))}
    # Conteos de filas de los CSV: el reporte los cita en su trazabilidad.
    for ruta in DIR_DATOS.glob("*.csv"):
        filas = len(ruta.read_text(encoding="utf-8").strip().splitlines()) - 1
        permitidas.add(str(filas))
    for f in fuentes:
        permitidas |= {_normaliza(n) for n in _numeros(f)}

    sueltas = []
    # Se ignoran los numerales de encabezados/listas ("## 3.", "1)") y los
    # anios de las citas. Las cifras de un digito SI se revisan: "2 dias" o
    # "3 horas" de implementacion fueron justo lo que invento el modelo.
    cuerpo = re.sub(r"(?m)^\s*(#+\s*)?\d+[.)]\s", "", reporte)
    for n in sorted(_numeros(cuerpo), key=float):
        if re.fullmatch(r"(19|20)\d\d", n):
            continue
        if _normaliza(n) not in permitidas:
            sueltas.append(n)
    return sueltas


_ARCHIVO = re.compile(r"[\w\-]+\.(?:json|csv|md|py|log|ipynb|txt)\b")


def archivos_inexistentes(reporte: str) -> list[str]:
    """Nombres de archivo citados en el reporte que no existen en el proyecto.

    El Sintetizador debe decir 'de que archivo salio cada cosa' y, sin acceso
    a los datos, tiende a inventar nombres plausibles (hallazgos_I1.json).
    """
    raiz = Path(__file__).parent
    reales = {p.name for p in raiz.rglob("*")
              if p.is_file() and ".venv" not in p.parts}
    citados = {m.group(0) for m in _ARCHIVO.finditer(reporte)}
    return sorted(c for c in citados if c not in reales)


def tabla_parametros() -> str:
    """Tabla antes/despues generada por codigo a partir de los dos archivos
    de configuracion. Sustituye a la que el LLM podria inventar."""
    import json
    ruta_v = DIR_DATOS / "configuracion_vigente.json"
    ruta_a = DIR_DATOS / "configuracion_aprobada.json"
    if not ruta_a.exists():
        return ""
    v = json.loads(ruta_v.read_text(encoding="utf-8"))
    a = json.loads(ruta_a.read_text(encoding="utf-8"))
    filas = [f"| Parametro | {v.get('version', 'vigente')} | "
             f"{a.get('version', 'aprobada')} |", "|---|---|---|"]
    for k in sorted(set(v.get("pesos_utilidad", {})) |
                    set(a.get("pesos_utilidad", {}))):
        filas.append(f"| peso {k} | {v.get('pesos_utilidad', {}).get(k, '-')} | "
                     f"{a.get('pesos_utilidad', {}).get(k, '-')} |")
    for k in ("umbral_spillback_pct", "periodo_decision_s"):
        filas.append(f"| {k} | {v.get(k, '-')} | {a.get(k, '-')} |")
    pv, pa = v.get("planes_por_interseccion", {}), a.get("planes_por_interseccion", {})
    for inter in sorted(set(pv) | set(pa)):
        for k in ("ciclo_s", "verde_principal_s", "verde_secundario_s",
                  "verde_peatonal_s", "desfase_s"):
            filas.append(f"| {inter} {k} | {pv.get(inter, {}).get(k, '-')} | "
                         f"{pa.get(inter, {}).get(k, '-')} |")
    return "\n".join(filas)


def verificar_reporte(ruta_reporte: Path, salidas_tareas: list[str]) -> str:
    """Antepone el estado de publicacion y anexa las cifras sin respaldo.

    Devuelve un resumen de una linea para imprimir en consola.
    """
    ruta_reporte = Path(ruta_reporte)
    if not ruta_reporte.exists():
        return "Reporte no encontrado; no hay nada que verificar."
    texto = ruta_reporte.read_text(encoding="utf-8").strip()
    # Un modelo pequeno suele envolver todo el reporte en una cerca de codigo.
    texto = re.sub(r"^```(?:markdown|md)?\s*\n", "", texto)
    texto = re.sub(r"\n```\s*$", "", texto).strip()

    publicado = (DIR_DATOS / "configuracion_aprobada.json").exists()
    if publicado:
        banner = ("> **Verificacion automatica:** el Auditor publico una "
                  "configuracion aprobada (`configuracion_aprobada.json`).\n\n")
    else:
        banner = ("> **Verificacion automatica: NO SE PUBLICO NINGUNA "
                  "CONFIGURACION.** El Auditor la rechazo o no llego a "
                  "publicarla. Toda afirmacion de este reporte sobre "
                  "cumplimiento de las reglas duras o mejoras logradas debe "
                  "leerse con ese hecho presente.\n\n")

    sueltas = cifras_sin_respaldo(texto, salidas_tareas)
    if sueltas:
        anexo = ("\n\n---\n**Verificacion automatica de cifras.** Las "
                 "siguientes cifras del reporte no aparecen en ninguna salida "
                 "de las tareas anteriores ni en los datos y pueden ser "
                 "invencion del modelo: " + ", ".join(sueltas) + ".\n")
    else:
        anexo = ("\n\n---\n**Verificacion automatica de cifras.** Todas las "
                 "cifras del reporte aparecen en las salidas de las tareas "
                 "anteriores o en los datos.\n")

    fantasma = archivos_inexistentes(texto)
    if fantasma:
        anexo += ("\n**Verificacion automatica de archivos.** El reporte cita "
                  "archivos que no existen en el proyecto: "
                  + ", ".join(f"`{f}`" for f in fantasma)
                  + ". La trazabilidad de esos elementos no es confiable.\n")

    tabla = tabla_parametros()
    bloque_tabla = ("\n\n---\n## Tabla de parametros antes y despues "
                    "(generada por codigo)\n\n" + tabla + "\n") if tabla else ""
    ruta_reporte.write_text(banner + texto + bloque_tabla + anexo,
                            encoding="utf-8")
    estado = "publicada" if publicado else "SIN PUBLICAR"
    return (f"Verificacion del reporte: configuracion {estado}; "
            f"{len(sueltas)} cifra(s) sin respaldo"
            + (f" ({', '.join(sueltas[:8])})" if sueltas else "")
            + f"; {len(fantasma)} archivo(s) citado(s) inexistente(s)"
            + (f" ({', '.join(fantasma[:4])})" if fantasma else "") + ".")

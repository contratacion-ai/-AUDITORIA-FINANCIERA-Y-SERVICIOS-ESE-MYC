"""Extracción tolerante: de un archivo cualquiera a tablas con las columnas comunes."""
from __future__ import annotations

from pathlib import Path

from ..config import COMBUSTIBLE, DESPLAZAMIENTOS, ITEMS, SERVICIOS
from . import ancho, pdf, tabular
from .comunes import ERROR, INFO, Incidencia, Tabla, normalizar
from .lectura import leer_archivo

# Hojas de las plantillas que no contienen datos.
HOJAS_DE_APOYO = {"instrucciones", "catalogo", "listas"}


def extraer_archivo(nombre: str, contenido: bytes, item: str | None = None
                    ) -> tuple[list[Tabla], list[Incidencia]]:
    """Lee el archivo y devuelve las tablas reconocidas y lo que no se pudo interpretar.

    ``item``: si se indica, solo se aceptan tablas de ese ítem (los desplazamientos
    se aceptan junto con combustible porque son su fuente complementaria).
    """
    rejillas, inc = leer_archivo(nombre, contenido)
    tablas: list[Tabla] = []
    for r in rejillas:
        if not r.filas or normalizar(r.hoja) in HOJAS_DE_APOYO:
            continue
        t, i = ([], [])
        if item in (None, SERVICIOS):
            t, i = ancho.extraer(r, nombre)
        if not t and not any(x.severidad == ERROR for x in i):
            t2, i2 = tabular.extraer(r, item, nombre)
            t, i = t2, i + i2
        if not t and not i:
            i = [Incidencia(INFO, "Hoja omitida: no contiene una tabla con la estructura de "
                                  + (ITEMS.get(item, "ningún ítem") if item else "ningún ítem")
                                  + " (faltan columnas de fecha/periodo, valor o sede).", hoja=r.hoja)]
        tablas += t
        inc += i
    if not tablas and Path(nombre).suffix.lower() == ".pdf" and item in (None, SERVICIOS):
        t, i = pdf.extraer_de_texto(rejillas)
        if t:
            inc = [x for x in inc if x.severidad != INFO]
        tablas += t
        inc += i
    if item:
        permitidos = {item, DESPLAZAMIENTOS} if item == COMBUSTIBLE else {item}
        for t in [t for t in tablas if t.item not in permitidos]:
            inc.append(Incidencia(ERROR, f"La hoja contiene datos de «{ITEMS.get(t.item, t.item)}», no de "
                                         f"«{ITEMS[item]}». No se carga.", hoja=t.hoja))
        tablas = [t for t in tablas if t.item in permitidos]
    if not tablas and not any(x.severidad == ERROR for x in inc):
        inc.append(Incidencia(ERROR, "No se reconoció ninguna tabla en el archivo. Use la plantilla del ítem o "
                                     "revise que los encabezados estén en una sola fila."))
    for x in inc:
        x.archivo = nombre
    return tablas, inc

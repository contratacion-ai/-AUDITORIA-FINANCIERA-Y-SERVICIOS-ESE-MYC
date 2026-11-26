"""Parseo de archivos planos RIPS (TXT) según el Anexo Técnico No. 1.

Reglas del Anexo aplicadas aquí (numeral 1, "Características de los archivos
planos"):

* separador de campos: coma; un campo vacío igual se delimita (``,,``);
* ningún dato encerrado entre comillas;
* sin caracteres especiales de fin de registro o archivo.

Decisiones ante casos que el Anexo no resuelve:

* Un registro con más o menos campos que los definidos NO se intenta
  "acomodar" (p. ej. una coma dentro del nombre de un servicio en AT): se
  reporta ``CANTIDAD_CAMPOS`` con la línea original y se excluye del
  análisis, porque cualquier reacomodo sería una suposición sobre el dato.
  La única excepción es un separador sobrante al final (``...,``), que se
  tolera con advertencia ``SEPARADOR_FINAL``.
* Los espacios al inicio/fin de cada campo se recortan (el Anexo sólo admite
  el espacio como carácter dentro de textos, no como relleno).
* Las comillas que encierran un campo completo se retiran y se advierte.
* Las líneas en blanco se ignoran con advertencia.
* La codificación se detecta probando UTF-8 (con o sin BOM), Windows-1252 y
  Latin-1, en ese orden (configurable).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from .especificacion import especificacion
from .lectura import ArchivoRips

# "\r\r\n" aparece en TXT generados por algunos software de facturación; se
# trata como un único fin de línea para no crear registros vacíos ficticios.
_RE_LINEAS = re.compile(r"\r*\n|\r")
COLUMNAS_META = ["envio_id", "archivo", "linea"]


@dataclass
class ResultadoParseo:
    tipo: str
    datos: pd.DataFrame
    incidencias: list[dict] = field(default_factory=list)
    codificacion: str = "utf-8"
    lineas_totales: int = 0  # líneas no vacías (base del total declarado en CT)
    registros_validos: int = 0
    registros_rechazados: int = 0
    lineas_reparadas: int = 0


def decodificar(contenido: bytes, codificaciones: list[str]) -> tuple[str, str]:
    for enc in codificaciones:
        try:
            return contenido.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return contenido.decode("latin-1", errors="replace"), "latin-1"


def reparar_mojibake(texto: str) -> tuple[str, bool]:
    """Revierte la doble codificación UTF-8 -> Windows-1252 -> UTF-8."""
    if "Ã" not in texto and "Â" not in texto:
        return texto, False
    for enc in ("cp1252", "latin-1"):
        try:
            reparado = texto.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if reparado != texto:
            return reparado, True
    return texto, False


def parsear_archivo(
    archivo: ArchivoRips,
    envio_id: str,
    codificaciones: list[str] | None = None,
    reparar: bool = True,
) -> ResultadoParseo:
    """Convierte un TXT RIPS en un DataFrame de texto con una columna por campo.

    Todas las columnas se conservan como texto (``str``) para no perder ceros
    a la izquierda ni alterar códigos; la conversión de tipos se hace en la
    validación y en la exportación.
    """
    spec = especificacion(archivo.tipo)
    n = spec.num_campos
    texto, enc = decodificar(archivo.contenido, codificaciones or ["utf-8-sig", "cp1252", "latin-1"])
    res = ResultadoParseo(tipo=archivo.tipo, datos=pd.DataFrame(), codificacion=enc)
    base = {"envio_id": envio_id, "archivo": archivo.ruta, "tipo_archivo": archivo.tipo}

    if enc.replace("-sig", "") != "utf-8":
        res.incidencias.append({**base, "regla": "CODIFICACION_NO_UTF8", "valor": enc,
                                "mensaje": f"Archivo leído como {enc}"})

    filas: list[list[str]] = []
    lineas_idx: list[int] = []
    comillas = 0
    lineas = _RE_LINEAS.split(texto)
    # Una línea vacía al final del archivo es sólo el salto de línea final.
    if lineas and lineas[-1] == "":
        lineas.pop()
    for num, linea in enumerate(lineas, start=1):
        if not linea.strip():
            res.incidencias.append({**base, "linea": num, "regla": "LINEA_VACIA"})
            continue
        res.lineas_totales += 1
        if reparar:
            linea, cambio = reparar_mojibake(linea)
            res.lineas_reparadas += cambio
        campos = [c.strip() for c in linea.split(",")]
        if len(campos) == n + 1 and campos[-1] == "":
            campos.pop()
            res.incidencias.append({**base, "linea": num, "regla": "SEPARADOR_FINAL"})
        if len(campos) != n:
            res.registros_rechazados += 1
            res.incidencias.append({
                **base, "linea": num, "regla": "CANTIDAD_CAMPOS", "valor": linea[:250],
                "mensaje": f"Se esperaban {n} campos y se encontraron {len(campos)}",
            })
            continue
        for i, c in enumerate(campos):
            if len(c) >= 2 and c[0] == c[-1] and c[0] in "\"'":
                campos[i] = c[1:-1].strip()
                comillas += 1
        filas.append(campos)
        lineas_idx.append(num)

    if comillas:
        res.incidencias.append({**base, "regla": "COMILLAS", "valor": str(comillas),
                                "mensaje": f"{comillas} campo(s) venían entre comillas; se retiraron"})
    if res.lineas_reparadas:
        res.incidencias.append({**base, "regla": "TEXTO_REPARADO", "valor": str(res.lineas_reparadas),
                                "mensaje": f"{res.lineas_reparadas} línea(s) con doble codificación reparadas"})
    if res.lineas_totales == 0:
        res.incidencias.append({**base, "regla": "ARCHIVO_VACIO"})

    df = pd.DataFrame(filas, columns=spec.columnas, dtype="string") if filas else pd.DataFrame(
        {c: pd.Series(dtype="string") for c in spec.columnas})
    df.insert(0, "envio_id", envio_id)
    df.insert(1, "archivo", archivo.ruta)
    df.insert(2, "linea", pd.array(lineas_idx, dtype="Int64"))
    res.datos = df
    res.registros_validos = len(df)
    return res

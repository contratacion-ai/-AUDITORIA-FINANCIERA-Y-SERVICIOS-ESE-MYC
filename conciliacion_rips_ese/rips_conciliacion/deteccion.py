"""Detección automática de la versión de RIPS a partir del contenido.

Reglas (en orden):

1. Si el contenido es JSON (aunque la extensión sea .txt) y contiene las
   llaves de la Resolución 2275 de 2023 (``numFactura`` y ``usuarios`` con
   ``servicios``) -> 2275.
2. Si el contenido es texto plano separado por comas y el tipo de archivo
   (AF, US, AC, AP, AU, AH, AN, AM, AT, CT) puede establecerse por el
   prefijo del nombre o, si el nombre no es estándar, por la forma del
   contenido, y el número de campos coincide con el anexo técnico de la
   Resolución 3374 de 2000 -> 3374.
3. En cualquier otro caso -> ``NO DETERMINADA`` con el motivo explícito.
   Nunca se asume una versión por defecto.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import PurePosixPath

from .esquemas import ESQUEMAS_3374
from .modelos import VERSION_2275, VERSION_3374, VERSION_NO_DETERMINADA

_RE_NOMBRE_3374 = re.compile(
    r"^(CT|AF|US|AC|AP|AU|AH|AN|AM|AT)(?![A-Z])", re.IGNORECASE
)
_RE_FECHA = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_RE_HORA = re.compile(r"^\d{1,2}:\d{2}$")
_RE_NUM = re.compile(r"^-?\d+(\.\d+)?$")
_TIPOS_ID = {
    "CC",
    "CE",
    "TI",
    "RC",
    "PA",
    "AS",
    "MS",
    "NU",
    "CD",
    "SC",
    "PE",
    "PT",
    "CN",
    "DE",
    "SI",
}


@dataclass
class Deteccion:
    version: str
    tipo: str | None = None  # tipo de archivo 3374 (AF, AC...) o "JSON"
    motivo: str = ""
    contenido_json: object | None = None
    texto: str | None = None


def decodificar(contenido: bytes) -> str:
    """Decodifica UTF-8 (con o sin BOM) y si falla Windows-1252/Latin-1."""
    for codificacion in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return contenido.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return contenido.decode("latin-1", errors="replace")  # pragma: no cover


def tipo_por_nombre(nombre: str) -> str | None:
    m = _RE_NOMBRE_3374.match(PurePosixPath(nombre).name)
    return m.group(1).upper() if m else None


def tipo_por_contenido(campos: list[str]) -> str | None:
    """Infiere el tipo de archivo 3374 por la forma de una línea."""
    n = len(campos)
    c = [x.strip() for x in campos]
    if n == 4:
        return "CT"
    if n == 19:
        return "AH"
    if n == 15:
        return "AP"
    if n == 11:
        return "AT"
    if n == 17:
        if _RE_HORA.match(c[5]):
            return "AU"
        if _RE_FECHA.match(c[5]) and _RE_FECHA.match(c[6]):
            return "AF"
        if _RE_FECHA.match(c[4]) and _RE_NUM.match(c[14] or "0"):
            return "AC"
        return None
    if n == 14:
        if _RE_HORA.match(c[5]):
            return "AN"
        if c[0].upper() in _TIPOS_ID and c[10].upper() in ("M", "F", "I"):
            return "US"
        if all(_RE_NUM.match(x or "0") for x in c[11:14]):
            return "AM"
    return None


def detectar(nombre: str, contenido: bytes) -> Deteccion:
    texto = decodificar(contenido)
    inicio = texto.lstrip()[:1]

    if inicio in ("{", "["):
        try:
            datos = json.loads(texto, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            return Deteccion(
                VERSION_NO_DETERMINADA,
                motivo=f"El contenido parece JSON pero es inválido: línea {exc.lineno}, columna {exc.colno}: {exc.msg}",
            )
        documentos = datos if isinstance(datos, list) else [datos]
        if documentos and all(
            isinstance(d, dict) and "numFactura" in d and "usuarios" in d
            for d in documentos
        ):
            return Deteccion(
                VERSION_2275,
                "JSON",
                "Llaves numFactura/usuarios de la Resolución 2275 de 2023",
                datos,
            )
        llaves = (
            sorted(documentos[0].keys())[:15]
            if documentos and isinstance(documentos[0], dict)
            else []
        )
        return Deteccion(
            VERSION_NO_DETERMINADA,
            motivo="JSON sin las llaves obligatorias 'numFactura' y 'usuarios' de la Resolución 2275 de 2023. "
            f"Llaves encontradas: {llaves}",
        )

    lineas = [ln for ln in texto.splitlines() if ln.strip()]
    if not lineas:
        return Deteccion(VERSION_NO_DETERMINADA, motivo="Archivo vacío", texto=texto)

    tipo = tipo_por_nombre(nombre)
    origen = "nombre del archivo"
    if tipo is None:
        tipo = tipo_por_contenido(lineas[0].split(","))
        origen = "contenido de la primera línea"
    if tipo is None:
        return Deteccion(
            VERSION_NO_DETERMINADA,
            motivo=f"Texto plano no reconocible: el nombre no tiene prefijo RIPS (AF, US, AC, AP, AU, AH, AN, AM, AT, CT) "
            f"y la primera línea tiene {len(lineas[0].split(','))} campos que no corresponden a ninguna estructura conocida",
            texto=texto,
        )

    esperado = ESQUEMAS_3374[tipo].num_campos
    coinciden = sum(1 for ln in lineas if len(ln.split(",")) == esperado)
    if coinciden == 0:
        return Deteccion(
            VERSION_NO_DETERMINADA,
            tipo=tipo,
            motivo=f"Archivo tipo {tipo} (por {origen}) pero ninguna línea tiene los {esperado} campos "
            f"de la Resolución 3374 de 2000 (primera línea: {len(lineas[0].split(','))} campos)",
            texto=texto,
        )
    return Deteccion(
        VERSION_3374,
        tipo=tipo,
        motivo=f"Tipo {tipo} por {origen}; {coinciden} de {len(lineas)} líneas con {esperado} campos",
        texto=texto,
    )

"""Interpretación de fechas y meses (RIPS y nombres de carpetas)."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from functools import lru_cache

_FORMATOS = (
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%d-%m-%Y",
    "%Y/%m/%d",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
)

MESES = {
    "ENERO": 1,
    "ENE": 1,
    "JANUARY": 1,
    "JAN": 1,
    "FEBRERO": 2,
    "FEB": 2,
    "FEBRUARY": 2,
    "MARZO": 3,
    "MAR": 3,
    "MARCH": 3,
    "ABRIL": 4,
    "ABR": 4,
    "APRIL": 4,
    "APR": 4,
    "MAYO": 5,
    "MAY": 5,
    "JUNIO": 6,
    "JUN": 6,
    "JUNE": 6,
    "JULIO": 7,
    "JUL": 7,
    "JULY": 7,
    "AGOSTO": 8,
    "AGO": 8,
    "AUGUST": 8,
    "AUG": 8,
    "SEPTIEMBRE": 9,
    "SETIEMBRE": 9,
    "SEP": 9,
    "SET": 9,
    "SEPT": 9,
    "SEPTEMBER": 9,
    "OCTUBRE": 10,
    "OCT": 10,
    "OCTOBER": 10,
    "NOVIEMBRE": 11,
    "NOV": 11,
    "NOVEMBER": 11,
    "DICIEMBRE": 12,
    "DIC": 12,
    "DECEMBER": 12,
    "DEC": 12,
}


def parse_fecha(valor) -> date | None:
    """Devuelve la fecha o ``None`` si está vacía. Lanza ``ValueError`` si es inválida."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return _parse_texto(str(valor).strip())


@lru_cache(maxsize=200_000)
def _parse_texto(texto: str) -> date | None:
    """Las fechas se repiten mucho en archivos grandes: se guarda el resultado de cada texto ya visto."""
    if not texto or texto.lower() in ("nan", "nat", "none", "null"):
        return None
    for fmt in _FORMATOS:
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Fecha inválida: {texto!r}")


def mes_de_fecha(valor) -> str | None:
    try:
        f = parse_fecha(valor)
    except ValueError:
        return None
    return f"{f.year:04d}-{f.month:02d}" if f else None


def _sin_tildes(texto: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


_RE_ANIO_MES = re.compile(r"(?<!\d)(20\d{2})[-_ .]?(0[1-9]|1[0-2])(?!\d)")
_RE_MES_ANIO = re.compile(r"(?<!\d)(0[1-9]|1[0-2])[-_ .](20\d{2})(?!\d)")
_RE_ANIO = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_RE_PALABRAS = re.compile(r"[A-Z]+")


def mes_de_nombre(nombre: str) -> tuple[int | None, int | None]:
    """Extrae (año, mes) de un nombre de carpeta/archivo.

    Reconoce ``2024-01``, ``202401``, ``01-2024``, ``ENERO``, ``ENE_2024``,
    ``01_ENERO``... Devuelve ``(None, None)`` si no hay mes.
    """
    texto = _sin_tildes(nombre).upper()
    m = _RE_ANIO_MES.search(texto)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _RE_MES_ANIO.search(texto)
    if m:
        return int(m.group(2)), int(m.group(1))
    mes = None
    for palabra in _RE_PALABRAS.findall(texto):
        if palabra in MESES:
            mes = MESES[palabra]
            break
    if mes is None:
        return None, None
    anio = _RE_ANIO.search(texto)
    return (int(anio.group(1)) if anio else None), mes


def anio_de_nombre(nombre: str) -> int | None:
    m = _RE_ANIO.search(nombre)
    return int(m.group(1)) if m else None

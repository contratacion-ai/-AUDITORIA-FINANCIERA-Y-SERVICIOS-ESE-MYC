"""Utilidades de lectura tolerante: normalización de texto, números, fechas y periodos."""
from __future__ import annotations

import datetime as dt
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ..config import MESES, MESES_ABREV

ERROR = "Error"            # la fila NO se carga
ADVERTENCIA = "Advertencia"  # la fila se carga, pero queda señalada
INFO = "Información"       # nota sobre lo que se omitió o se interpretó


@dataclass
class Incidencia:
    """Algo que no se pudo interpretar o que merece revisión: dónde y por qué."""
    severidad: str
    motivo: str
    hoja: str = ""
    fila: int | None = None
    columna: str = ""
    valor: str = ""
    archivo: str = ""

    def como_dict(self) -> dict:
        return {"archivo": self.archivo, "hoja": self.hoja, "fila": self.fila,
                "columna": self.columna, "severidad": self.severidad,
                "motivo": self.motivo, "valor": self.valor}


@dataclass
class Tabla:
    """Tabla extraída de un archivo, todavía sin validar contra el catálogo.

    ``datos`` usa las columnas crudas comunes (ver ``esquema.COLUMNAS_CRUDAS``) más
    ``_hoja`` y ``_fila`` para poder señalar el origen exacto de cada problema.
    """
    item: str
    formato: str
    hoja: str
    datos: pd.DataFrame
    descripcion: str = ""
    columnas_usadas: dict = field(default_factory=dict)
    columnas_ignoradas: list = field(default_factory=list)


def sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def normalizar(valor: Any) -> str:
    """Texto en minúsculas, sin tildes ni signos, con espacios simples."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return ""
    t = sin_tildes(str(valor)).lower()
    t = re.sub(r"[^a-z0-9ñ]+", " ", t)
    return t.strip()


def normalizar_codigo(valor: Any) -> str:
    """Códigos de cuenta, centro de costo o placa: mayúsculas, sin espacios ni guiones.

    Un 12345.0 leído de Excel se trata como 12345.
    """
    if vacio(valor):
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    t = sin_tildes(str(valor)).upper().strip()
    t = re.sub(r"\.0$", "", t)
    return re.sub(r"[^A-Z0-9]", "", t)


def vacio(valor: Any) -> bool:
    if valor is None:
        return True
    if isinstance(valor, float) and math.isnan(valor):
        return True
    if valor is pd.NaT:
        return True
    return isinstance(valor, str) and valor.strip() in ("", "-", "—", "–")


_TEXTO_SIN_DATO = {"n a", "na", "no aplica", "no", "no marca", "sin registro", "pendiente",
                   "sin reporte", "no refiere", "ninguno", "s d", "sd"}


def a_numero(valor: Any) -> float | None:
    """Convierte a número aceptando formatos colombianos ("$ 1.234.567,50", "37,2").

    Devuelve None si está vacío o no es un número (el llamador decide si eso es un error).
    """
    if vacio(valor) or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, (dt.datetime, dt.date, dt.time)):
        return None
    t = str(valor).strip()
    if normalizar(t) in _TEXTO_SIN_DATO:
        return None
    t = re.sub(r"(?i)\b(cop|gal(ones)?|kwh?|m3|km|lts?|litros)\b", "", t)
    t = t.replace("$", "").replace(" ", "").replace(" ", "")
    if not re.fullmatch(r"[-+]?[\d.,]+", t):
        return None
    if "," in t and "." in t:
        # El separador decimal es el que aparece de último.
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        partes = t.split(",")
        t = t.replace(",", "") if len(partes) > 2 or len(partes[-1]) == 3 else t.replace(",", ".")
    elif t.count(".") > 1 or (t.count(".") == 1 and len(t.split(".")[-1]) == 3 and len(t.split(".")[0]) <= 3
                              and t.split(".")[0].lstrip("-+") not in ("", "0")):
        # "1.234.567" o "12.500": puntos de miles (convención colombiana).
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def a_fecha(valor: Any) -> tuple[dt.date | None, str]:
    """Devuelve (fecha, nota). Corrige años de dos dígitos tecleados como 0026 -> 2026."""
    if vacio(valor):
        return None, ""
    nota = ""
    f: dt.date | None = None
    if isinstance(valor, pd.Timestamp):
        valor = valor.to_pydatetime()
    if isinstance(valor, dt.datetime):
        f = valor.date()
    elif isinstance(valor, dt.date):
        f = valor
    else:
        t = str(valor).strip()
        m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", t)
        m2 = re.match(r"^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})", t)
        try:
            if m2:
                f = dt.date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
            elif m:
                f = dt.date(max(int(m.group(3)), 1), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None, f"fecha no válida: {t!r}"
        if f is None:
            return None, f"fecha no reconocida: {t!r}"
    if f.year < 100:
        f = f.replace(year=2000 + f.year)
        nota = f"año digitado como {f.year - 2000:04d}; se interpretó como {f.year}"
    return f, nota


def periodo_de_fecha(f: dt.date) -> str:
    return f"{f.year:04d}-{f.month:02d}"


def mes_de_texto(valor: Any) -> int | None:
    """Número de mes (1-12) si el texto es un nombre o abreviatura de mes."""
    t = normalizar(valor)
    if not t:
        return None
    t = t.replace("setiembre", "septiembre")
    for i, nombre in enumerate(MESES, start=1):
        if t == nombre:
            return i
    for i, ab in enumerate(MESES_ABREV, start=1):
        if t == ab:
            return i
    return None


def a_periodo(valor: Any, anio_defecto: int | None = None) -> str | None:
    """Interpreta "2026-10", "10/2026", "octubre 2026", "Octubre" (con año por defecto) o una fecha."""
    if vacio(valor):
        return None
    if isinstance(valor, (dt.datetime, dt.date, pd.Timestamp)):
        f, _ = a_fecha(valor)
        return periodo_de_fecha(f) if f else None
    t = normalizar(valor)
    m = re.fullmatch(r"(20\d{2}) ?(\d{1,2})", t)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(\d{1,2}) (20\d{2})", t)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.fullmatch(r"([a-zñ]+)(?: de)?(?: (20\d{2}))?", t)
    if m:
        mes = mes_de_texto(m.group(1))
        anio = int(m.group(2)) if m.group(2) else anio_defecto
        if mes and anio:
            return f"{anio:04d}-{mes:02d}"
        return None
    f, _ = a_fecha(valor)
    return periodo_de_fecha(f) if f else None


def periodo_valido(p: str) -> bool:
    return bool(re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", p or ""))


def periodo_anterior(p: str, n: int = 1) -> str:
    anio, mes = int(p[:4]), int(p[5:7])
    total = anio * 12 + (mes - 1) - n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def nombre_periodo(p: str) -> str:
    """'2026-10' -> 'oct 2026'."""
    return f"{MESES_ABREV[int(p[5:7]) - 1]} {p[:4]}" if periodo_valido(p) else str(p)


def buscar_anio(textos: list[Any]) -> int | None:
    """Primer año 20xx que aparezca en una lista de textos (títulos, nombre de archivo)."""
    for t in textos:
        if vacio(t):
            continue
        m = re.search(r"(?<!\d)(20[1-4]\d)(?!\d)", str(t))
        if m:
            return int(m.group(1))
    return None


def es_fila_total(celdas: list[Any]) -> bool:
    for c in celdas:
        if isinstance(c, str) and re.match(r"^\s*(sub\s*)?total(es)?\b", sin_tildes(c).lower()):
            return True
    return False

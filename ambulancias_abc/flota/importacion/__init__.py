"""Lectores de archivos fuente. Devuelven registros y hallazgos de calidad.

No escriben en la base de datos: la persistencia está en ``flota.servicios``.
Ningún lector corrige cifras: lo que no cuadra se reporta como hallazgo.
"""

import re
import unicodedata

ERRORES_EXCEL = ("#REF!", "#DIV/0!", "#N/A", "#VALUE!", "#NAME?", "#NUM!", "#NULL!", "[object Object]")


def normalizar(texto) -> str:
    """Mayúsculas, sin tildes y con espacios simples (para comparar nombres)."""
    if texto is None:
        return ""
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip().upper()


def normalizar_cups(codigo) -> str:
    """'601T01..' -> '601T01'. Quita puntos, espacios y pasa a mayúsculas."""
    return re.sub(r"[^0-9A-Z]", "", normalizar(codigo))


def hallazgo(fuente, ubicacion, tipo, descripcion, severidad="MEDIA", valor=None):
    return {
        "fuente": fuente,
        "ubicacion": ubicacion,
        "tipo": tipo,
        "descripcion": descripcion,
        "severidad": severidad,
        "valor": "" if valor is None else str(valor)[:200],
    }


def es_error_excel(valor) -> bool:
    return isinstance(valor, str) and any(e in valor for e in ERRORES_EXCEL)

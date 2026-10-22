"""Limpieza y normalización de datos de los archivos de carga.

Cubre: espacios y caracteres invisibles, tildes (para comparar códigos y encabezados),
separadores decimales y de miles, símbolos de moneda, negativos contables, fechas en
varios formatos y celdas vacías.
"""
import hashlib
import hmac
import math
import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

VACIOS = {"", "NAN", "NONE", "NULL", "N/A", "NA", "#N/A", "-", "--", "#REF!", "#VALUE!", "#DIV/0!", "#¡VALOR!", "#¡REF!"}


class ErrorValor(ValueError):
    pass


def es_vacio(valor):
    if valor is None:
        return True
    if isinstance(valor, float) and math.isnan(valor):
        return True
    if isinstance(valor, str):
        return valor.strip().upper() in VACIOS
    return False


def texto(valor):
    """Texto limpio conservando tildes y mayúsculas originales."""
    if es_vacio(valor):
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    s = unicodedata.normalize("NFC", str(valor))
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C" or ch in " ")
    s = s.replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def sin_tildes(s):
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def clave(valor):
    """Clave de comparación: sin tildes, mayúsculas, solo letras/dígitos separados por un espacio."""
    s = sin_tildes(texto(valor)).upper()
    s = re.sub(r"[^A-Z0-9%]+", " ", s)
    return s.strip()


def codigo(valor):
    """Código limpio: sin espacios sobrantes, mayúsculas; números de Excel sin '.0'."""
    if es_vacio(valor):
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return re.sub(r"\s+", " ", sin_tildes(texto(valor))).upper().strip()


def solo_digitos(valor):
    return re.sub(r"\D", "", codigo(valor))


_NUM_LIMPIO = re.compile(r"[^\d,.\-+eE]")


def numero(valor, separador_decimal="auto"):
    """Convierte a Decimal. separador_decimal: 'auto', ',' o '.'.

    Ejemplos: '1.234.567,89' → 1234567.89 · '1,234,567.89' → 1234567.89 · '$ (1.000)' → -1000
    · '1.234' (auto) → 1234 (miles, costumbre colombiana) · '12,5' → 12.5
    """
    if es_vacio(valor):
        return None
    if isinstance(valor, bool):
        raise ErrorValor("valor lógico no numérico")
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, int):
        return Decimal(valor)
    if isinstance(valor, float):
        if math.isnan(valor) or math.isinf(valor):
            return None
        return Decimal(repr(valor)).quantize(Decimal("0.0001"))
    s = texto(valor)
    negativo = False
    if s.startswith("(") and s.endswith(")"):
        negativo, s = True, s[1:-1]
    if s.endswith("-"):
        negativo, s = True, s[:-1]
    s = _NUM_LIMPIO.sub("", s)
    if s.startswith("-"):
        negativo, s = not negativo, s[1:]
    s = s.lstrip("+")
    if not s or not re.search(r"\d", s):
        raise ErrorValor(f"'{valor}' no es un número")
    if "e" in s.lower() and re.fullmatch(r"\d+(\.\d+)?[eE][+-]?\d+", s):
        normal = s
    else:
        normal = _normalizar_separadores(s, separador_decimal)
    try:
        d = Decimal(normal)
    except InvalidOperation as exc:
        raise ErrorValor(f"'{valor}' no es un número") from exc
    return -d if negativo else d


def _normalizar_separadores(s, sep):
    if sep == ",":
        return s.replace(".", "").replace(",", ".")
    if sep == ".":
        return s.replace(",", "")
    tiene_p, tiene_c = "." in s, "," in s
    if tiene_p and tiene_c:
        decimal_sep = "," if s.rfind(",") > s.rfind(".") else "."
        miles = "." if decimal_sep == "," else ","
        return s.replace(miles, "").replace(decimal_sep, ".")
    sep_unico = "." if tiene_p else "," if tiene_c else None
    if sep_unico is None:
        return s
    partes = s.split(sep_unico)
    if len(partes) > 2:
        if all(len(p) == 3 for p in partes[1:]):
            return "".join(partes)
        raise ErrorValor(f"'{s}' tiene separadores inconsistentes")
    entero, frac = partes
    if len(frac) == 3 and entero and entero != "0":
        return entero + frac  # '1.234' / '1,234' → miles
    return entero + "." + frac


FORMATOS_FECHA = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y", "%d/%m/%y", "%Y%m%d",
                  "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M")


def fecha(valor, formato=""):
    if es_vacio(valor):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if hasattr(valor, "to_pydatetime"):
        return valor.to_pydatetime().date()
    if isinstance(valor, (int, float)) and 20000 < float(valor) < 80000:
        return (datetime(1899, 12, 30) + timedelta(days=float(valor))).date()  # serial de Excel
    s = texto(valor)
    formatos = (formato,) if formato else FORMATOS_FECHA
    for f in formatos:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    raise ErrorValor(f"'{valor}' no es una fecha válida" + (f" con formato {formato}" if formato else ""))


def si_no(valor):
    return clave(valor) in {"SI", "S", "X", "1", "TRUE", "VERDADERO", "YES"}


def seudonimo(documento, llave):
    """Seudónimo irreversible del paciente (Ley 1581 de 2012: datos de salud son sensibles).

    Usa solo el número de documento para que RIPS y facturación se crucen aunque uno no traiga el tipo.
    """
    doc = solo_digitos(documento) or codigo(documento)
    if not doc:
        return ""
    mensaje = doc.encode()
    return hmac.new(llave.encode(), mensaje, hashlib.sha256).hexdigest()[:40]

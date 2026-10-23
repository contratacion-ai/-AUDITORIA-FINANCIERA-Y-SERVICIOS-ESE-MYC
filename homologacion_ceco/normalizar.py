"""Limpieza y normalización de valores: texto, códigos, montos (en centavos exactos) y periodos.

Los montos se manejan como enteros en centavos para que las sumas sean exactas (sin errores de
punto flotante) y la conciliación cuadre al peso.
"""
import math
import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

VACIOS = {"", "NAN", "NONE", "NULL", "N/A", "NA", "#N/A", "-", "--", "#REF!", "#VALUE!", "#DIV/0!", "#¡VALOR!", "#¡REF!",
          "NAT"}

MESES = {"ENERO": 1, "ENE": 1, "FEBRERO": 2, "FEB": 2, "MARZO": 3, "MAR": 3, "ABRIL": 4, "ABR": 4, "MAYO": 5, "MAY": 5,
         "JUNIO": 6, "JUN": 6, "JULIO": 7, "JUL": 7, "AGOSTO": 8, "AGO": 8, "SEPTIEMBRE": 9, "SETIEMBRE": 9, "SEP": 9,
         "SEPT": 9, "SET": 9, "OCTUBRE": 10, "OCT": 10, "NOVIEMBRE": 11, "NOV": 11, "DICIEMBRE": 12, "DIC": 12}


class ErrorValor(ValueError):
    """Valor que no se puede interpretar; el mensaje se usa como motivo de rechazo."""


def es_vacio(valor):
    if valor is None:
        return True
    if isinstance(valor, float) and math.isnan(valor):
        return True
    if valor.__class__.__name__ in ("NaTType", "NAType"):
        return True
    if isinstance(valor, str):
        return valor.strip().upper() in VACIOS
    return False


def texto(valor):
    """Texto limpio: sin caracteres de control ni espacios repetidos; conserva tildes y mayúsculas."""
    if es_vacio(valor):
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    s = reparar_mojibake(unicodedata.normalize("NFC", str(valor)).replace(" ", " "))
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C" or ch == " ")
    return re.sub(r"\s+", " ", s).strip()


def reparar_mojibake(s):
    """Repara texto UTF-8 que el sistema de origen exportó como Windows-1252: 'CAPITACIÃ“N' → 'CAPITACIÓN'.

    Solo actúa si aparece 'Ã' o 'Â' y el resultado es UTF-8 válido; si no, devuelve el texto sin cambios.
    """
    if "Ã" not in s and "Â" not in s:
        return s
    try:
        return b"".join(_byte_cp1252(ch) for ch in s).decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _byte_cp1252(ch):
    try:
        return ch.encode("cp1252")
    except UnicodeEncodeError:
        return ch.encode("latin-1")  # 0x81, 0x8d, 0x8f, 0x90 y 0x9d no tienen carácter en cp1252


def sin_tildes(s):
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))


def clave(valor):
    """Clave de comparación: MAYÚSCULAS, sin tildes, solo letras y dígitos separados por un espacio."""
    s = sin_tildes(texto(valor)).upper().replace("Ñ", "N")
    return re.sub(r"[^A-Z0-9]+", " ", s).strip()


def codigo(valor):
    """Código alfanumérico en mayúsculas, sin espacios ni '.0' de Excel."""
    if es_vacio(valor):
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return re.sub(r"\s+", "", sin_tildes(texto(valor))).upper()


def codigo_ceco(valor):
    """Código del CECO del estado financiero.

    Acepta 1020201, 1020201.0, ' 1020201 ', '1020201 - HSLA-ARAUQUITA-CONSULTA EXTERNA' o '102-02-01'.
    Si el valor empieza por letras (p. ej. un código de resolución A10) se devuelve tal cual en mayúsculas.
    """
    if es_vacio(valor):
        return ""
    if isinstance(valor, float):
        if not valor.is_integer():
            raise ErrorValor(f"'{valor}' no es un código de centro de costos")
        valor = int(valor)
    s = texto(valor)
    m = re.match(r"^\s*(\d[\d.\-\s]*\d|\d)(?:\s*[-–:|]\s*\D.*|\s+\D.*)?$", s)
    if m:
        digitos = re.sub(r"\D", "", m.group(1))
        return digitos.lstrip("0") or "0"
    return codigo(s)


def cuenta(valor):
    """Cuenta contable: solo dígitos ('5101-01' → '510101'). Error si trae letras."""
    if es_vacio(valor):
        return ""
    if isinstance(valor, float):
        if not valor.is_integer():
            raise ErrorValor(f"'{valor}' no es una cuenta contable")
        valor = int(valor)
    s = texto(valor)
    if re.search(r"[A-Za-z]", s):
        raise ErrorValor(f"'{s}' no es una cuenta contable (contiene letras)")
    return re.sub(r"\D", "", s)


_NUM_LIMPIO = re.compile(r"[^\d,.\-+eE]")


def centavos(valor, separador_decimal="auto"):
    """Convierte un monto a centavos (int). Devuelve None si la celda está vacía.

    '1.234.567,89' → 123456789 · '1,234,567.89' → 123456789 · '$ (1.000)' → -100000 · '1.234' → 123400
    (miles, costumbre colombiana) · '12,5' → 1250 · 1500000.0 → 150000000.
    """
    d = decimal(valor, separador_decimal)
    if d is None:
        return None
    return int((d * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def decimal(valor, separador_decimal="auto"):
    if es_vacio(valor):
        return None
    if isinstance(valor, bool):
        raise ErrorValor("valor lógico, no numérico")
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, int):
        return Decimal(valor)
    if isinstance(valor, float):
        if math.isinf(valor):
            raise ErrorValor("valor infinito")
        return Decimal(repr(valor))
    if hasattr(valor, "item"):  # escalares de numpy
        return decimal(valor.item(), separador_decimal)
    if isinstance(valor, (datetime, date)):
        raise ErrorValor(f"'{valor}' es una fecha, no un monto")
    s = re.sub(r"(?i)\$|\bCOP\b", "", texto(valor)).strip()
    negativo = False
    if s.startswith("(") and s.endswith(")"):
        negativo, s = True, s[1:-1]
    if s.endswith("-"):
        negativo, s = True, s[:-1]
    if re.search(r"[A-DF-Za-df-z]", s):
        raise ErrorValor(f"'{valor}' no es un número")
    s = _NUM_LIMPIO.sub("", s)
    if s.startswith("-"):
        negativo, s = not negativo, s[1:]
    s = s.lstrip("+")
    if not s or not re.search(r"\d", s):
        raise ErrorValor(f"'{valor}' no es un número")
    if re.fullmatch(r"\d+(\.\d+)?[eE][+-]?\d+", s):
        normal = s
    else:
        normal = _separadores(s, separador_decimal)
    try:
        d = Decimal(normal)
    except InvalidOperation as exc:
        raise ErrorValor(f"'{valor}' no es un número") from exc
    return -d if negativo else d


def _separadores(s, sep):
    if sep == ",":
        return s.replace(".", "").replace(",", ".")
    if sep == ".":
        return s.replace(",", "")
    tiene_p, tiene_c = "." in s, "," in s
    if tiene_p and tiene_c:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        miles = "." if dec == "," else ","
        if s.count(dec) > 1:
            raise ErrorValor(f"'{s}' tiene separadores inconsistentes")
        return s.replace(miles, "").replace(dec, ".")
    unico = "." if tiene_p else "," if tiene_c else None
    if unico is None:
        return s
    partes = s.split(unico)
    if len(partes) > 2:
        if all(len(p) == 3 for p in partes[1:]):
            return "".join(partes)
        raise ErrorValor(f"'{s}' tiene separadores inconsistentes")
    entero, frac = partes
    if len(frac) == 3 and entero and entero != "0":
        return entero + frac  # '1.234' / '1,234' → miles
    return entero + "." + frac


def pesos(cent):
    """Centavos → Decimal en pesos con 2 decimales."""
    return (Decimal(int(cent)) / 100).quantize(Decimal("0.01"))


def formato_pesos(cent):
    """Centavos → texto con formato colombiano: 1234567 → '$ 12.345,67'."""
    signo = "-" if cent < 0 else ""
    entero, dec = divmod(abs(int(cent)), 100)
    return f"{signo}$ {entero:,}".replace(",", ".") + f",{dec:02d}"


def _periodo_de_fecha(d):
    return f"{d.year:04d}-{d.month:02d}"


def periodo(valor):
    """Periodo contable 'AAAA-MM' desde fechas, seriales de Excel o textos.

    Admite: datetime, 46053 (serial), '2026-01', '2026/01', '01/2026', '202601', '31/01/2026', '2026-01-31',
    'ENERO 2026', 'ENE-2026', 'Enero de 2026'.
    """
    if es_vacio(valor):
        return ""
    if isinstance(valor, datetime) or isinstance(valor, date):
        return _periodo_de_fecha(valor)
    if hasattr(valor, "to_pydatetime"):
        return _periodo_de_fecha(valor.to_pydatetime())
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        n = float(valor)
        if 190001 <= n <= 210012 and n.is_integer() and 1 <= int(n) % 100 <= 12:
            return f"{int(n) // 100:04d}-{int(n) % 100:02d}"
        if 20000 < n < 80000:
            return _periodo_de_fecha(datetime(1899, 12, 30) + timedelta(days=n))
        raise ErrorValor(f"'{valor}' no es un periodo")
    s = clave(valor)
    m = re.fullmatch(r"(\d{4}) (\d{1,2})(?: (\d{1,2}))?(?: \d{1,2} \d{2}(?: \d{2})?)?", s)
    if m and 1 <= int(m.group(2)) <= 12 and 1900 <= int(m.group(1)) <= 2100:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(?:(\d{1,2}) )?(\d{1,2}) (\d{4})(?: \d{1,2} \d{2}(?: \d{2})?)?", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})?", s)
    if m and 1 <= int(m.group(2)) <= 12 and 1900 <= int(m.group(1)) <= 2100:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.fullmatch(r"(?:\d{1,2} (?:DE )?)?([A-Z]+)(?: DE| DEL)? (\d{4})", s) or re.fullmatch(r"([A-Z]+) (\d{2})", s)
    if m and m.group(1) in MESES:
        anio = int(m.group(2))
        anio = anio + 2000 if anio < 100 else anio
        return f"{anio:04d}-{MESES[m.group(1)]:02d}"
    raise ErrorValor(f"'{texto(valor)}' no es un periodo (use AAAA-MM)")


def periodo_en_nombre(nombre):
    """Busca un periodo en el nombre de un archivo: 'EF_2026-01.xlsx', 'costos enero 2026.csv', '202603.xlsx'."""
    s = clave(nombre)
    m = re.search(r"(?<!\d)(20\d{2}) ?(0[1-9]|1[0-2])(?!\d)", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.search(r"(?<!\d)(0[1-9]|1[0-2]) (20\d{2})(?!\d)", s)
    if m:
        return f"{m.group(2)}-{m.group(1)}"
    for palabra in s.split():
        if palabra in MESES and len(palabra) > 3:
            anio = re.search(r"(?<!\d)(20\d{2})(?!\d)", s)
            if anio:
                return f"{anio.group(1)}-{MESES[palabra]:02d}"
    return ""


def valor_para_traza(valor):
    """Representación corta y estable de un valor original para la bitácora de transformaciones."""
    if es_vacio(valor):
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    if isinstance(valor, datetime):
        return valor.isoformat(sep=" ")
    return str(valor)[:200]

"""Conversión exacta de valores monetarios y cantidades a ``Decimal``.

Nunca se usa ``float`` para dinero: cualquier valor que no pueda
interpretarse de forma exacta y sin ambigüedad lanza ``ValorInvalido``
para que se reporte como incidencia en lugar de estimarse.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

CERO = Decimal("0")

_LIMPIAR = re.compile(r"[\s$ ]|COP", re.IGNORECASE)
_SOLO_NUMERO = re.compile(r"^[+-]?\d+(\.\d+)?$")


class ValorInvalido(ValueError):
    """El texto no representa un número exacto."""


def a_decimal(
    valor, separador_decimal: str = "auto", vacio_es_cero: bool = False
) -> Decimal | None:
    """Convierte ``valor`` a ``Decimal`` sin pérdida de precisión.

    ``separador_decimal``:
      * ``"."``  -> la coma es separador de miles (``1,234,567.89``).
      * ``","``  -> el punto es separador de miles (``1.234.567,89``).
      * ``"auto"`` (por defecto):
          - si aparecen ambos símbolos, el último es el separador decimal;
          - si un símbolo aparece más de una vez, es separador de miles;
          - si aparece una sola vez, se toma como separador decimal.
        Cuando los archivos de la ESE usen punto de miles con un único
        grupo (``1.234``) debe fijarse ``separador_decimal=","``.

    Devuelve ``None`` para celdas vacías (o ``0`` si ``vacio_es_cero``).
    """
    if valor is None:
        return CERO if vacio_es_cero else None
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, bool):
        raise ValorInvalido(f"Valor booleano no numérico: {valor!r}")
    if isinstance(valor, int):
        return Decimal(valor)
    if isinstance(valor, float):
        if valor != valor:  # NaN
            return CERO if vacio_es_cero else None
        # repr() de float es la representación decimal más corta que
        # reproduce exactamente el valor leído de Excel.
        return Decimal(repr(valor))

    texto = _LIMPIAR.sub("", str(valor))
    if texto in ("", "-", "nan", "NaN", "None", "null", "NULL"):
        return CERO if vacio_es_cero else None

    negativo = False
    if texto.startswith("(") and texto.endswith(")"):
        negativo, texto = True, texto[1:-1]

    texto = _normalizar_separadores(texto, separador_decimal)
    if not _SOLO_NUMERO.match(texto):
        raise ValorInvalido(f"Valor numérico inválido: {valor!r}")
    try:
        numero = Decimal(texto)
    except InvalidOperation as exc:  # pragma: no cover - cubierto por la regex
        raise ValorInvalido(f"Valor numérico inválido: {valor!r}") from exc
    return -numero if negativo else numero


def _normalizar_separadores(texto: str, separador_decimal: str) -> str:
    if separador_decimal == ".":
        return texto.replace(",", "")
    if separador_decimal == ",":
        return texto.replace(".", "").replace(",", ".")
    if separador_decimal != "auto":
        raise ValueError(f"separador_decimal no soportado: {separador_decimal!r}")

    puntos, comas = texto.count("."), texto.count(",")
    if puntos and comas:
        if texto.rfind(",") > texto.rfind("."):
            return texto.replace(".", "").replace(",", ".")
        return texto.replace(",", "")
    if comas > 1:
        return texto.replace(",", "")
    if puntos > 1:
        return texto.replace(".", "")
    if comas == 1:
        return texto.replace(",", ".")
    return texto


def suma(valores) -> Decimal:
    """Suma exacta ignorando vacíos."""
    total = CERO
    for v in valores:
        if v is not None and not (isinstance(v, float) and v != v):
            total += v
    return total


_RE_DEC_COMA = re.compile(r"(\d,\d{1,2}$)|(\.\d{3},\d+$)|(\d\.\d{3}\.\d{3})")
_RE_DEC_PUNTO = re.compile(
    r"(\d\.\d{1,2}$)|(,\d{3}\.\d+$)|(\d,\d{3},\d{3})|(\d\.\d{4,}$)"
)
_RE_AMBIGUO = re.compile(r"^[+-]?\d{1,3}[.,]\d{3}$")


def inferir_separador(textos) -> tuple[str, str | None]:
    """Infiere el separador decimal de una columna/archivo completo.

    Devuelve ``(separador, advertencia)``. Busca evidencia inequívoca
    (``1.234,56`` o ``45,5`` -> coma decimal; ``1,234.56`` o ``45.5`` ->
    punto decimal). Si no hay evidencia y existen valores ambiguos como
    ``30.000`` se aplica la convención colombiana (punto de miles, coma
    decimal) y se devuelve una advertencia explícita.
    """
    coma = punto = ambiguos = 0
    for t in textos:
        if not isinstance(t, str):
            continue
        t = _LIMPIAR.sub("", t)
        if _RE_DEC_COMA.search(t):
            coma += 1
        elif _RE_DEC_PUNTO.search(t):
            punto += 1
        elif _RE_AMBIGUO.match(t):
            ambiguos += 1
    if coma and not punto:
        return ",", None
    if punto and not coma:
        return ".", None
    if coma and punto:
        return "auto", (
            f"Formatos numéricos mezclados ({coma} valores con coma decimal y {punto} con punto "
            "decimal); se interpreta cada valor por separado. Fije el separador decimal para mayor certeza."
        )
    if ambiguos:
        return ",", (
            f"{ambiguos} valores como '30.000' no indican el separador decimal; se aplica la convención "
            "colombiana (punto = miles, coma = decimales). Si no es correcto, fije el separador decimal en '.'"
        )
    return "auto", None

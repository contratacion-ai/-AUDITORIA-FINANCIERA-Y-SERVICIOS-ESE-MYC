"""Filtros de presentación. Formatean sin redondear: solo se quitan ceros finales."""

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def moneda(valor):
    """1234567.50 -> 1.234.567,5 (formato colombiano, sin redondeo)."""
    if valor is None or valor == "":
        return ""
    try:
        d = valor if isinstance(valor, Decimal) else Decimal(str(valor))
    except InvalidOperation:
        return valor
    signo = "-" if d < 0 else ""
    texto = format(abs(d), "f")
    entero, _, fraccion = texto.partition(".")
    fraccion = fraccion.rstrip("0")
    grupos = []
    while len(entero) > 3:
        grupos.insert(0, entero[-3:])
        entero = entero[:-3]
    grupos.insert(0, entero)
    return signo + ".".join(grupos) + ("," + fraccion if fraccion else "")


@register.filter
def obtener(diccionario, clave):
    try:
        return diccionario.get(clave)
    except AttributeError:
        return None


@register.filter
def es_numero(valor):
    if isinstance(valor, (int, float, Decimal)) and not isinstance(valor, bool):
        return True
    if isinstance(valor, str):
        try:
            Decimal(valor)
            return any(c.isdigit() for c in valor)
        except InvalidOperation:
            return False
    return False


@register.filter
def negativo(valor):
    try:
        return Decimal(str(valor)) < 0
    except (InvalidOperation, TypeError):
        return False


@register.filter
def etiqueta_estado(estado):
    return str(estado or "").replace("_", " ").capitalize()

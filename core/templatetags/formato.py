"""Formato colombiano: $1.234.567 y 12,3 %."""
from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _miles(entero):
    return f"{entero:,}".replace(",", ".")


@register.filter
def pesos(valor, decimales=0):
    if valor is None or valor == "":
        return "—"
    try:
        v = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return valor
    decimales = int(decimales)
    signo = "-" if v < 0 else ""
    v = abs(v).quantize(Decimal(1) / (10 ** decimales)) if decimales else abs(v).quantize(Decimal(1))
    entero, _, frac = f"{v:f}".partition(".")
    return f"{signo}${_miles(int(entero))}" + (f",{frac}" if decimales else "")


@register.filter
def numero(valor, decimales=0):
    if valor is None or valor == "":
        return "—"
    try:
        v = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return valor
    decimales = int(decimales)
    signo = "-" if v < 0 else ""
    v = abs(v).quantize(Decimal(1) / (10 ** decimales)) if decimales else abs(v).quantize(Decimal(1))
    entero, _, frac = f"{v:f}".partition(".")
    return f"{signo}{_miles(int(entero))}" + (f",{frac}" if decimales else "")


@register.filter
def pct(valor, decimales=1):
    if valor is None or valor == "":
        return "—"
    try:
        v = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return valor
    return f"{v:,.{int(decimales)}f}".replace(",", "X").replace(".", ",").replace("X", ".") + "%"


@register.filter
def pct_signo(valor, decimales=1):
    if valor is None or valor == "":
        return "—"
    texto = pct(valor, decimales)
    return texto if texto.startswith("-") else "+" + texto


@register.filter
def clase_var(valor):
    """Para costos: aumento = rojo, disminución = verde."""
    try:
        v = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return ""
    return "text-danger" if v > 0 else "text-success" if v < 0 else ""


@register.filter
def clase_margen(valor):
    try:
        v = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return ""
    return "text-success" if v > 0 else "text-danger" if v < 0 else ""


@register.filter
def get(dic, clave):
    try:
        return dic.get(clave)
    except AttributeError:
        return None


@register.filter
def a_decimal(valor):
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return None


@register.simple_tag(takes_context=True)
def url_con(context, **kwargs):
    """Conserva los parámetros GET actuales y reemplaza los indicados."""
    q = context["request"].GET.copy()
    for k, v in kwargs.items():
        if v in (None, ""):
            q.pop(k, None)
        else:
            q[k] = v
    return "?" + q.urlencode()


@register.filter
def sin_tildes(valor):
    from cargas.limpieza import sin_tildes as _st

    return _st(str(valor or ""))

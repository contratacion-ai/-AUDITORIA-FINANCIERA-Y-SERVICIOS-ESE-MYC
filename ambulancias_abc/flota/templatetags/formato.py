"""Formato colombiano: separador de miles '.', decimales ','."""

from django import template

register = template.Library()


def _fmt(valor, dec=0):
    if valor in (None, ""):
        return "—"
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return valor
    texto = f"{n:,.{dec}f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


@register.filter
def pesos(valor):
    t = _fmt(valor)
    return t if t == "—" else f"$ {t}"


@register.filter
def millones(valor):
    if valor in (None, ""):
        return "—"
    return f"$ {_fmt(float(valor) / 1e6, 1)} M"


@register.filter
def num(valor, dec=0):
    return _fmt(valor, int(dec))


@register.filter
def pct(valor):
    t = _fmt(valor, 1)
    return t if t == "—" else f"{t} %"


@register.filter
def signo(valor):
    """Clase CSS según el signo (para márgenes)."""
    try:
        return "neg" if float(valor) < 0 else "pos"
    except (TypeError, ValueError):
        return ""


@register.filter
def get(dic, clave):
    return dic.get(clave) if hasattr(dic, "get") else None

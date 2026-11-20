"""Datos de la factura electrónica de venta (FEV, XML DIAN) que acompaña al RIPS JSON.

El RIPS JSON de la Res. 2275 de 2023 no trae la EPS, el contrato ni el valor de
la factura: están en el XML de la FEV que se descarga junto a él
(``FHSA83039/FHSA83039.json`` + ``FHSA83039/FHSA83039.xml``). Este módulo lee de
ese XML (``AttachedDocument`` con la ``Invoice`` embebida, o la ``Invoice``
directa):

* ``numero``: ``cbc:ID`` de la factura (se usa para emparejar con ``numFactura``).
* ``eps_nombre`` / ``eps_nit``: ``cac:AccountingCustomerParty`` (adquiriente):
  ``cbc:RegistrationName`` y ``cbc:CompanyID``.
* ``valor``: ``cac:LegalMonetaryTotal/cbc:PayableAmount``.
* Campos del sector salud (``Name``/``Value`` de la extensión): ``NUMERO_CONTRATO``,
  ``MODALIDAD_PAGO`` («Pago por evento.», «Pago por capitación»…),
  ``COBERTURA_PLAN_BENEFICIOS`` y ``CODIGO_PRESTADOR``.

Se usan expresiones regulares sobre el texto (con las entidades HTML ya
convertidas), porque la factura embebida viene escapada dentro de un CDATA o
de ``cbc:Description``. Si el XML no tiene alguno de los campos, queda vacío.
"""

from __future__ import annotations

import html
import re

from .incidencias import RegistroIncidencias
from .lectura import descubrir_archivos

_CLIENTE = re.compile(r"<cac:AccountingCustomerParty>(.*?)</cac:AccountingCustomerParty>", re.S)
_NOMBRE = re.compile(r"<cbc:RegistrationName>\s*([^<]*?)\s*</cbc:RegistrationName>")
_NIT = re.compile(r"<cbc:CompanyID[^>]*>\s*([^<]*?)\s*</cbc:CompanyID>")
_VALOR = re.compile(r"<cbc:PayableAmount[^>]*>\s*([^<]*?)\s*</cbc:PayableAmount>")
_CAMPO = re.compile(r"<(?:\w+:)?Name>\s*([A-Z_]{4,})\s*</(?:\w+:)?Name>\s*<(?:\w+:)?Value[^>]*>([^<]*)<", re.S)
_ID_INVOICE = re.compile(r"<Invoice\b.*?<cbc:ID>\s*([^<]*?)\s*</cbc:ID>", re.S)
_ID = re.compile(r"<cbc:ID>\s*([A-Za-z0-9\-]+)\s*</cbc:ID>")


def modalidad(texto: str) -> str:
    """'Pago por evento.' -> EVENTO; capitación -> CAPITA; global prospectivo -> PGP."""
    t = (texto or "").upper()
    if "CAPITA" in t or "CÁPITA" in t:
        return "CAPITA"
    if "PROSPECTIV" in t or "PGP" in t:
        return "PGP"
    if "EVENTO" in t:
        return "EVENTO"
    return ""


def leer_fev(contenido: bytes) -> dict:
    texto = html.unescape(contenido.decode("utf-8", errors="replace"))
    cli = _CLIENTE.search(texto)
    bloque = cli.group(1) if cli else ""
    campos = {k: v.strip() for k, v in _CAMPO.findall(texto)}
    numero = _ID_INVOICE.search(texto) or _ID.search(texto)
    valor = _VALOR.search(texto)
    try:
        valor_num = float(valor.group(1)) if valor else None
    except ValueError:
        valor_num = None
    return {
        "numero": numero.group(1).strip() if numero else "",
        "eps_nombre": (_NOMBRE.search(bloque).group(1).strip() if _NOMBRE.search(bloque) else ""),
        "eps_nit": (_NIT.search(bloque).group(1).strip() if _NIT.search(bloque) else ""),
        "valor": valor_num,
        "contrato": campos.get("NUMERO_CONTRATO", ""),
        "modalidad_pago": campos.get("MODALIDAD_PAGO", ""),
        "modalidad": modalidad(campos.get("MODALIDAD_PAGO", "")),
        "cobertura": campos.get("COBERTURA_PLAN_BENEFICIOS", ""),
        "cod_prestador": campos.get("CODIGO_PRESTADOR", ""),
    }


def indice_fev(entradas) -> dict[str, dict]:
    """numFactura -> datos de la FEV, para todos los XML de ``entradas`` (carpetas o ZIP)."""
    indice: dict[str, dict] = {}
    for fuente in descubrir_archivos(list(entradas), RegistroIncidencias()):
        if not fuente.ruta.lower().endswith(".xml"):
            continue
        datos = leer_fev(fuente.contenido)
        if not datos["eps_nombre"] and not datos["eps_nit"]:
            continue  # no es una FEV (otro XML)
        claves = {datos["numero"], fuente.nombre.rsplit(".", 1)[0]}
        for clave in claves:
            if clave:
                indice.setdefault(clave.strip().upper(), {**datos, "archivo": fuente.ruta})
    return indice


__all__ = ["indice_fev", "leer_fev", "modalidad"]

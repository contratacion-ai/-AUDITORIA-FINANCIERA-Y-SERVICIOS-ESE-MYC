"""Lectura de facturas de servicios públicos en PDF cuando no traen una tabla reconocible.

Las facturas cambian de diseño según la empresa, así que aquí solo se buscan en el
texto los datos esenciales (cuenta, periodo, consumo, total). El resultado SIEMPRE
queda marcado para revisión en pantalla antes de guardarse: es una ayuda para no
digitar, no una lectura certificada.
"""
from __future__ import annotations

import re

import pandas as pd

from .. import esquema
from ..config import SERVICIOS
from .comunes import ADVERTENCIA, ERROR, Incidencia, Tabla, a_numero, a_periodo, sin_tildes
from .lectura import Rejilla

_NUM = r"\$?\s*([\d][\d.,]*)"
_PATRONES = {
    "codigo": [r"(?:cuenta(?: contrato)?|n[uú]mero de cuenta|c[oó]digo(?: de)? (?:usuario|suscriptor|cuenta)|niu|nic|"
               r"matr[ií]cula|suscriptor)\s*(?:no\.?|n[°º]|nro\.?|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-]{2,20})"],
    "periodo": [r"per[ií]odo(?: de)?(?: facturaci[oó]n| facturado| consumo)?\s*[:\-]?\s*([^\n]{3,40})",
                r"mes(?: facturado| de consumo)\s*[:\-]?\s*([^\n]{3,25})"],
    "documento": [r"factura(?: de (?:venta|servicios))?\s*(?:no\.?|n[°º]|nro\.?|#)\s*[:\-]?\s*([A-Z0-9\-]{3,20})"],
    "total_a_pagar": [r"total a pagar\s*[:\-]?\s*" + _NUM, r"valor a pagar\s*[:\-]?\s*" + _NUM,
                      r"total factura\s*[:\-]?\s*" + _NUM],
    "valor": [r"(?:total|valor)(?: del)? (?:mes|periodo|per[ií]odo|consumo)\s*[:\-]?\s*" + _NUM,
              r"valor facturado\s*[:\-]?\s*" + _NUM],
    "kwh": [r"consumo[^\n]{0,30}?([\d.,]+)\s*kwh", r"([\d.,]+)\s*kwh"],
    "m3": [r"consumo[^\n]{0,30}?([\d.,]+)\s*m ?3", r"([\d.,]+)\s*m ?3\b"],
}


def _buscar(texto: str, clave: str) -> str | None:
    for patron in _PATRONES[clave]:
        m = re.search(patron, texto, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def extraer_de_texto(rejillas: list[Rejilla]) -> tuple[list[Tabla], list[Incidencia]]:
    texto = "\n".join(r.texto for r in rejillas if r.texto)
    if not texto.strip():
        return [], []
    plano = sin_tildes(texto)
    codigo, periodo_txt = _buscar(texto, "codigo"), _buscar(texto, "periodo")
    total, valor = a_numero(_buscar(texto, "total_a_pagar")), a_numero(_buscar(texto, "valor"))
    kwh, m3 = a_numero(_buscar(plano, "kwh")), a_numero(_buscar(plano, "m3"))
    if total is None and valor is None:
        return [], [Incidencia(ERROR, "No se encontró en el PDF un valor facturado ni un total a pagar. Digite la "
                                      "factura en la plantilla de servicios públicos.", hoja="PDF")]
    periodo = None
    if periodo_txt:
        m = re.search(r"([a-zñ]{3,10})\.?(?: de| del|/|-)? ?(20\d{2})", sin_tildes(periodo_txt).lower())
        periodo = a_periodo(f"{m.group(1)} {m.group(2)}") if m else a_periodo(periodo_txt)
    es_energia = kwh is not None or re.search(r"energia|kwh|enelar", plano, re.IGNORECASE)
    fila = {
        "periodo": periodo, "codigo": codigo, "referencia": codigo,
        "concepto": "Energía eléctrica" if es_energia else "Acueducto",
        "cantidad": kwh if es_energia else m3, "unidad": "kWh" if es_energia else "m3",
        "valor": valor if valor is not None else total, "total_a_pagar": total,
        "documento": _buscar(texto, "documento"), "_hoja": "PDF", "_fila": 1, "_anio": None,
        "observacion": "Leído automáticamente de PDF",
    }
    df = pd.DataFrame([fila])
    for c in esquema.COLUMNAS_CRUDAS:
        if c not in df.columns:
            df[c] = None
    inc = [Incidencia(ADVERTENCIA, "Factura leída del texto del PDF: verifique cuenta, periodo, consumo y valores "
                                   "en la tabla de revisión antes de guardar.", hoja="PDF", fila=1)]
    if valor is None:
        inc.append(Incidencia(ADVERTENCIA, "No se encontró el valor del periodo; se usó el total a pagar, que puede "
                                           "incluir saldos anteriores.", hoja="PDF", fila=1, columna="valor"))
    return [Tabla(SERVICIOS, "pdf_texto", "PDF", df, descripcion="factura leída del texto del PDF (requiere revisión)")], inc

"""Exportación JSON con la misma organización hospital -> mes del Excel.

Estructura de una unidad (un hospital en un mes)::

    {
      "metadatos": {"norma": ..., "generado": ..., "version": ...},
      "hospital": "Hospital San Antonio",
      "periodo": "2026-05",
      "resumen": {...},
      "remisiones": [...],
      "registros": {"CT": [...], "AF": [...], "US": [...], ..., "AT": [...]},
      "conciliacion_facturas": [...],
      "incidencias": [...]
    }

El consolidado anida lo mismo: ``{"metadatos", "hospitales": {<hospital>:
{"meses": {<AAAA-MM>: {resumen, remisiones, registros, ...}}}}}``.

Decisiones: los nombres de campo son los técnicos de ``especificacion.py``;
los valores (tipo V/E) válidos se exportan como número y las fechas se
conservan en el formato del Anexo (dd/mm/aaaa). Un campo vacío se exporta
como ``null``. El archivo se escribe por partes (streaming) para soportar
lotes grandes sin cargar todo el JSON en memoria.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import IO

import pandas as pd

from . import __version__
from .especificacion import ORDEN_ARCHIVOS, especificacion
from .validacion import a_numero

NORMA = "Resolución 3374 de 2000 - Anexo Técnico No. 1"


def _metadatos(**extra) -> dict:
    return {"norma": NORMA, "generado": dt.datetime.now().isoformat(timespec="seconds"),
            "herramienta": "rips_auditoria", "version": __version__, **extra}


def _limpio(v):
    if v is None or v is pd.NA:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (pd.Timestamp, dt.datetime, dt.date)):
        return v.isoformat()
    if hasattr(v, "item"):  # numpy escalares
        return v.item()
    if v == "":
        return None
    return v


def registros_json(df: pd.DataFrame, tipo: str | None = None) -> list[dict]:
    if df is None or df.empty:
        return []
    out = df.copy()
    if tipo:
        for c in especificacion(tipo).campos:
            if c.tipo in ("V", "E") and c.nombre in out:
                num = a_numero(out[c.nombre])
                out[c.nombre] = num.astype(object).where(num.notna(), out[c.nombre].astype(object))
    cols = list(out.columns)
    return [{k: _limpio(v) for k, v in zip(cols, fila)} for fila in out.astype(object).itertuples(index=False, name=None)]


def _volcar_lista(fh: IO[str], filas: list[dict], sangria: str) -> None:
    fh.write("[")
    for i, f in enumerate(filas):
        fh.write(("," if i else "") + "\n" + sangria + json.dumps(f, ensure_ascii=False))
    fh.write(("\n" + sangria[:-1] if filas else "") + "]")


def _volcar_unidad(fh: IO[str], unidad, sangria: str, cabecera: dict | None = None) -> None:
    s1 = sangria + " "
    fh.write("{")
    partes = list((cabecera or {}).items()) + [("resumen", unidad.resumen())]
    for k, v in partes:
        fh.write(f"\n{s1}{json.dumps(k)}: {json.dumps(v, ensure_ascii=False, default=str)},")
    fh.write(f"\n{s1}\"remisiones\": ")
    _volcar_lista(fh, registros_json(unidad.envios), s1 + " ")
    fh.write(f",\n{s1}\"registros\": {{")
    for i, t in enumerate(ORDEN_ARCHIVOS):
        fh.write(("," if i else "") + f"\n{s1} \"{t}\": ")
        _volcar_lista(fh, registros_json(unidad.datos.get(t), t), s1 + "  ")
    fh.write(f"\n{s1}}},\n{s1}\"conciliacion_facturas\": ")
    _volcar_lista(fh, registros_json(unidad.conciliacion), s1 + " ")
    fh.write(f",\n{s1}\"incidencias\": ")
    _volcar_lista(fh, registros_json(unidad.incidencias), s1 + " ")
    fh.write(f"\n{sangria}}}")


def exportar_unidad_json(unidad, ruta: str | Path) -> Path:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8") as fh:
        _volcar_unidad(fh, unidad, "", {"metadatos": _metadatos(), "hospital": unidad.hospital,
                                        "periodo": unidad.periodo})
        fh.write("\n")
    return ruta


def exportar_consolidado_json(resultado, ruta: str | Path) -> Path:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8") as fh:
        fh.write("{\n \"metadatos\": " + json.dumps(_metadatos(), ensure_ascii=False) + ",")
        fh.write("\n \"indice\": ")
        _volcar_lista(fh, registros_json(resultado.resumen_unidades()), "  ")
        fh.write(",\n \"hospitales\": {")
        for i, h in enumerate(resultado.hospitales()):
            fh.write(("," if i else "") + f"\n  {json.dumps(h, ensure_ascii=False)}: {{\"meses\": {{")
            for j, p in enumerate(resultado.periodos(h)):
                fh.write(("," if j else "") + f"\n   {json.dumps(p)}: ")
                _volcar_unidad(fh, resultado.unidad(h, p), "   ")
            fh.write("\n  }}")
        fh.write("\n },\n \"archivos_ignorados\": ")
        arch = resultado.archivos
        ign = arch[arch["estado"] == "ignorado"] if not arch.empty else arch
        _volcar_lista(fh, registros_json(ign), "  ")
        fh.write("\n}\n")
    return ruta

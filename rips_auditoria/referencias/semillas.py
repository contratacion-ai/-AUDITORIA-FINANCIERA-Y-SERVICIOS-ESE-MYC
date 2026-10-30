"""Maestros parciales extraídos de los dashboards de referencia.

Se usan solo mientras no se cargue el maestro completo (``INFORME DE SERVICIOS ... .xlsx`` o
plantillas). Permiten clasificar y agrupar desde el primer día, pero NO reemplazan la Nota
Técnica completa: no traen costos unitarios por CUPS.
"""
from __future__ import annotations

import pandas as pd

from ..maestros import PLANTILLAS
from .dashboards import cargar_evento


def catalogo() -> pd.DataFrame:
    """Códigos del consolidado EVENTO del dashboard (grupo y marca OFERTADO/NO OFERTADO)."""
    d = cargar_evento().datos
    filas = [{"codigo": l[0], "descripcion": l[1], "grupo": l[5], "estado": l[6] or None} for l in d["cons"]["lines"]]
    df = pd.DataFrame(filas)
    for c in PLANTILLAS["catalogo"]:
        if c not in df:
            df[c] = None
    return df[PLANTILLAS["catalogo"]]


def nota_tecnica() -> pd.DataFrame:
    """Solo la lista de códigos de la NT (``ntcods``) con el grupo cuando se conoce."""
    d = cargar_evento().datos
    grupos = {l[0]: l[5] for l in d["cons"]["lines"]}
    grupos.update({r[0]: r[2] for r in d.get("top_cost", []) + d.get("top_freq", [])})
    df = pd.DataFrame({"codigo": d["nt"]["ntcods"]})
    df["grupo"] = df["codigo"].map(grupos)
    df["tipo_codigo"] = "CUPS"
    df["municipio"] = "TODOS"
    for c in PLANTILLAS["nota_tecnica"]:
        if c not in df:
            df[c] = None
    return df[PLANTILLAS["nota_tecnica"]]


def despacho_medicamentos(anio: int = 2026) -> pd.DataFrame:
    m = cargar_evento().datos["med"]
    return pd.DataFrame({"periodo": [f"{anio}-{i + 1:02d}" for i in range(len(m["gen_v"]))],
                         "unidades": m["gen_q"], "valor": m["gen_v"]})


SEMILLAS = {"catalogo": catalogo, "nota_tecnica": nota_tecnica, "despacho_medicamentos": despacho_medicamentos}

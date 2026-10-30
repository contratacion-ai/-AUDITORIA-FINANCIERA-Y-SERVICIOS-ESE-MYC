"""Frecuencias de la Nota Técnica Nueva EPS frente a lo registrado en los RIPS.

Parte del cruce detallado ``cruces.rips_vs_nota_tecnica`` (código × municipio × mes) y produce los
resúmenes que se usan para el seguimiento mes a mes:

* ``por_municipio_mes``: frecuencia prevista vs RIPS y % de ejecución de cada municipio en cada mes.
* ``por_grupo``: acumulado del periodo por grupo de servicio y municipio.
* ``por_codigo``: acumulado del periodo por código (previsto mensual × meses cargados) con ranking de
  sobreejecución y subejecución.
* ``fuera_nt``: servicios CAPITA en RIPS que no están en la Nota Técnica.

% de ejecución = frecuencia RIPS / frecuencia prevista × 100.
"""
from __future__ import annotations

import pandas as pd

from ..constantes import nombre_grupo


def _ejec(rips, prev):
    return (rips / prev.where(prev > 0)) * 100


def _estado(pct, tol):
    if pd.isna(pct):
        return "SIN_NT"
    if pct > 100 + tol:
        return "SOBREEJECUCION"
    if pct < 100 - tol:
        return "SUBEJECUCION"
    return "DENTRO"


def resumenes(cruce: pd.DataFrame, tolerancia_pct: float = 10.0) -> dict[str, pd.DataFrame]:
    if cruce is None or cruce.empty:
        return {"por_municipio_mes": pd.DataFrame(), "por_grupo": pd.DataFrame(), "por_codigo": pd.DataFrame(),
                "fuera_nt": pd.DataFrame()}
    c = cruce.copy()
    if "municipio" not in c:
        c["municipio"] = "RED"
    en_nt = c[c["frecuencia_prevista"] > 0]
    mm = (en_nt.groupby(["municipio", "mes"]).agg(codigos_nt=("codigo", "nunique"),
                                                  codigos_con_rips=("frecuencia_rips", lambda x: int((x > 0).sum())),
                                                  frecuencia_prevista=("frecuencia_prevista", "sum"),
                                                  frecuencia_rips=("frecuencia_rips", "sum"),
                                                  valor_exceso=("valor_exceso", "sum"))
          .reset_index())
    mm["ejecucion_pct"] = _ejec(mm["frecuencia_rips"], mm["frecuencia_prevista"])
    mm["estado"] = mm["ejecucion_pct"].map(lambda p: _estado(p, tolerancia_pct))
    meses = c.groupby("municipio")["mes"].nunique()
    gr = (en_nt.groupby(["municipio", "grupo"]).agg(frecuencia_prevista=("frecuencia_prevista", "sum"),
                                                    frecuencia_rips=("frecuencia_rips", "sum"),
                                                    valor_exceso=("valor_exceso", "sum")).reset_index())
    gr["meses"] = gr["municipio"].map(meses)
    gr["ejecucion_pct"] = _ejec(gr["frecuencia_rips"], gr["frecuencia_prevista"])
    gr["estado"] = gr["ejecucion_pct"].map(lambda p: _estado(p, tolerancia_pct))
    gr.insert(2, "grupo_nombre", gr["grupo"].map(lambda g: nombre_grupo(g) if isinstance(g, str) else g))
    cod = (en_nt.groupby(["municipio", "codigo"]).agg(descripcion=("descripcion", "first"), grupo=("grupo", "first"),
                                                      meses=("mes", "nunique"),
                                                      frecuencia_prevista_mes=("frecuencia_prevista", "mean"),
                                                      frecuencia_prevista=("frecuencia_prevista", "sum"),
                                                      frecuencia_rips=("frecuencia_rips", "sum"),
                                                      valor_exceso=("valor_exceso", "sum")).reset_index())
    cod["desviacion"] = cod["frecuencia_rips"] - cod["frecuencia_prevista"]
    cod["ejecucion_pct"] = _ejec(cod["frecuencia_rips"], cod["frecuencia_prevista"])
    cod["estado"] = cod["ejecucion_pct"].map(lambda p: _estado(p, tolerancia_pct))
    cod = cod.sort_values(["municipio", "desviacion"], ascending=[True, False]).reset_index(drop=True)
    fuera = (c[c["frecuencia_prevista"] == 0].groupby(["municipio", "codigo"])
             .agg(meses=("mes", "nunique"), frecuencia_rips=("frecuencia_rips", "sum")).reset_index()
             .sort_values("frecuencia_rips", ascending=False))
    return {"por_municipio_mes": mm, "por_grupo": gr, "por_codigo": cod, "fuera_nt": fuera}

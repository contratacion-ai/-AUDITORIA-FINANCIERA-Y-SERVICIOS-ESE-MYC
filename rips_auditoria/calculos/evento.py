"""Análisis EVENTO: Top 20, Pareto, aplicabilidad CAPITA, migrables y servicios de riesgo.

Los criterios de aplicabilidad (SÍ/NO/PARCIAL), la lista de 13+ migrables y la clasificación de
riesgo se toman de los dashboards de referencia (``OBS``, ``MIG``, ``RIE``) y se pueden ampliar en
``config/criterios_evento.yaml``.
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd
import yaml

from ..config import DIR_CONFIG
from ..constantes import nombre_grupo
from ..referencias.dashboards import cargar_evento


@lru_cache(maxsize=1)
def criterios() -> dict:
    ref = cargar_evento()
    out = {"aplicabilidad": {k: list(v) for k, v in ref.aplicabilidad.items()},
           "migrables": dict(ref.migrables),
           "riesgo": {c: {"servicio": n, "nivel": r} for c, n, r in ref.riesgo},
           "tratamiento": {k: list(v) for k, v in ref.tratamiento.items()}}
    extra = DIR_CONFIG / "criterios_evento.yaml"
    if extra.exists():
        with open(extra, encoding="utf-8") as fh:
            y = yaml.safe_load(fh) or {}
        for k in out:
            out[k].update(y.get(k) or {})
    return out


def por_codigo(servicios: pd.DataFrame) -> pd.DataFrame:
    """Consolidado por código (equivalente a la hoja 'Consolidado' de EVENTO)."""
    s = servicios[~servicios["es_episodio"]]
    g = s.groupby("codigo", dropna=True).agg(descripcion=("descripcion", "first"), grupo=("grupo", "first"),
                                             subgrupo=("subgrupo", "first"), estado=("estado_catalogo", "first"),
                                             en_nt=("en_nota_tecnica", "max"), frecuencia=("cantidad", "sum"),
                                             valor=("valor_total", "sum"), pacientes=("paciente", "nunique"),
                                             meses=("mes", "nunique")).reset_index()
    g["valor_unitario"] = g["valor"] / g["frecuencia"].where(g["frecuencia"] > 0)
    g["grupo_nombre"] = g["grupo"].map(nombre_grupo)
    return g


def pareto(df: pd.DataFrame, col: str = "valor") -> pd.DataFrame:
    d = df.sort_values(col, ascending=False).reset_index(drop=True)
    tot = d[col].sum()
    d["participacion_pct"] = d[col] / tot * 100 if tot else 0.0
    d["acumulado_pct"] = d["participacion_pct"].cumsum()
    d["clase"] = pd.cut(d["acumulado_pct"].shift(fill_value=0), [-1, 80, 95, 101], labels=["A", "B", "C"]).astype(str)
    d.insert(0, "rank", range(1, len(d) + 1))
    return d


def top20(cons: pd.DataFrame, por: str = "frecuencia") -> pd.DataFrame:
    c = criterios()["aplicabilidad"]
    t = cons.sort_values(por, ascending=False).head(20).reset_index(drop=True)
    t.insert(0, "n", range(1, len(t) + 1))
    t["aplica_capita"] = t["codigo"].map(lambda k: (c.get(k) or ["SIN CLASIFICAR"])[0])
    t["observacion"] = t["codigo"].map(lambda k: (c.get(k) or [None, "Revisar con contratación."])[1])
    return t


def migrables(cons: pd.DataFrame, n_meses: int, run_rate_capita: float) -> pd.DataFrame:
    """Simulación EVENTO→CAPITA: valor a tarifa EVENTO por mes (método del dashboard CAPITA·EVENTO)."""
    mig = criterios()["migrables"]
    idx = cons.set_index("codigo")
    filas = []
    for cod, nombre in mig.items():
        r = idx.loc[cod] if cod in idx.index else (idx.loc[cod + "."] if cod + "." in idx.index else None)
        if r is None or float(r["valor"]) <= 0:
            continue
        v_mes = float(r["valor"]) / n_meses
        filas.append({"codigo": cod, "servicio": nombre, "frecuencia": float(r["frecuencia"]),
                      "frecuencia_mes": float(r["frecuencia"]) / n_meses, "valor_unitario": r["valor_unitario"],
                      "valor_evento_mes": v_mes, "valor_anual": v_mes * 12,
                      "impacto_pct": v_mes / run_rate_capita * 100 if run_rate_capita else None,
                      "estado_catalogo": r["estado"], "en_nota_tecnica": bool(r["en_nt"]),
                      "pacientes": int(r["pacientes"])})
    return pd.DataFrame(filas)


def riesgo(cons: pd.DataFrame, n_meses: int, tope_traslados_mes: float | None,
           codigos_traslado: list[str]) -> pd.DataFrame:
    rie = criterios()["riesgo"]
    idx = cons.set_index("codigo")
    filas = []
    for cod, info in rie.items():
        if cod not in idx.index:
            continue
        r = idx.loc[cod]
        v_mes = float(r["valor"]) / n_meses
        es_tr = cod in codigos_traslado or "TRASLADO" in str(info["servicio"]).upper()
        filas.append({"codigo": cod, "servicio": info["servicio"], "nivel": info["nivel"], "frecuencia": r["frecuencia"],
                      "frecuencia_mes": float(r["frecuencia"]) / n_meses, "valor": r["valor"], "valor_mes": v_mes,
                      "valor_unitario": r["valor_unitario"], "estado_catalogo": r["estado"], "es_traslado": es_tr})
    df = pd.DataFrame(filas)
    if not df.empty:
        tr = df[df.es_traslado]["valor_mes"].sum()
        df.attrs["traslados_mes"] = tr
        df.attrs["tope_mensual"] = tope_traslados_mes
        df.attrs["exceso_tope"] = (tr - tope_traslados_mes) if tope_traslados_mes else None
    return df

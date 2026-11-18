"""Hoja COSTOS: cruce Costos × RIPS × Facturación × Notas Técnicas (Pandas).

Llave de cruce: CUPS (código interno 360 tras la regla de actividad / homologación de CUMS) y, cuando existe, EPS + modalidad.
Flujo (según el bloque «PARAMETRIZACION NT EPS»): NOTA TÉCNICA → RIPS por mes → COSTOS por mes → FACTURACIÓN por mes.
"""
import numpy as np, pandas as pd
from .db import df, TOL_PRODUCCION, NT_EXCESO, NT_BAJA
from .loaders import norm
from .analysis import _w, _pct, rips_h

COLS = ["cups", "descripcion", "servicio", "cant_nt", "cant_rips", "cant_fact", "cant_costo", "valor_rips", "valor_fact", "valor_costo",
        "costo_unit_real", "costo_unit_nt", "tarifa_fact", "margen_unit", "pct_margen", "dif_prod_costo_vs_fact", "estado"]

def cruce(mes=None, eps=None):
    """Una fila por CUPS con producción (NT, RIPS, facturación, costos), valores, costo unitario real vs de la NT y estado de auditoría."""
    w, p = _w(mes, "cups<>''")
    fw = w + (" AND eps_nombre=?" if eps else ""); fp = p + ((eps,) if eps else ())
    f = df(f"SELECT cups, MAX(servicio) servicio, SUM(cantidad) cant_fact, SUM(valor) valor_fact FROM fact{fw} GROUP BY cups", fp)
    r = rips_h(mes)
    if eps and not r.empty: r = r[r.eps == eps]
    r = (r[r.cups != ""].groupby("cups", as_index=False).agg(cant_rips=("cantidad", "sum"), valor_rips=("valor", "sum"))
         if not r.empty else pd.DataFrame(columns=["cups", "cant_rips", "valor_rips"]))
    c = df(f"SELECT cups, SUM(cantidad) cant_costo, SUM(valor) valor_costo FROM costo{w} GROUP BY cups", p)
    n = df("SELECT cups, MAX(descripcion) descripcion, SUM(eventos_mes) cant_nt FROM nota GROUP BY cups")
    u = df("SELECT cups, AVG(costo_unitario) costo_unit_nt FROM costo_unitario GROUP BY cups")
    for t in (f, r, c, n, u): t["cups"] = t.cups.astype(str).str.strip()
    m = f
    for t in (r, c, n, u): m = m.merge(t, on="cups", how="outer")
    if m.empty: return pd.DataFrame(columns=COLS)
    for k in ("cant_nt", "cant_rips", "cant_fact", "cant_costo", "valor_rips", "valor_fact", "valor_costo"): m[k] = m[k].fillna(0.0)
    m["servicio"] = m.servicio.fillna("")
    m["descripcion"] = m.descripcion.fillna("")
    m["costo_unit_real"] = np.where(m.cant_costo > 0, m.valor_costo / m.cant_costo.replace(0, np.nan), np.nan)
    m["tarifa_fact"] = np.where(m.cant_fact > 0, m.valor_fact / m.cant_fact.replace(0, np.nan), np.nan)
    base = m.tarifa_fact
    m["margen_unit"] = base - m.costo_unit_real.fillna(m.costo_unit_nt)
    m["pct_margen"] = np.where(base > 0, m.margen_unit / base.replace(0, np.nan), np.nan)
    ref = np.where(m.cant_rips > 0, m.cant_rips, m.cant_fact)
    m["dif_prod_costo_vs_fact"] = np.where(ref > 0, m.cant_costo / np.where(ref > 0, ref, np.nan) - 1, np.nan)
    def est(x):
        a = []
        if x.cant_costo > 0 and x.cant_fact == 0 and x.cant_rips == 0: a.append("COSTO SIN PRODUCCIÓN")
        if x.cant_fact > 0 and x.cant_costo == 0 and x.valor_costo == 0: a.append("FACTURADO SIN COSTO")
        if x.cant_fact > 0 and x.cant_rips == 0: a.append("Sin RIPS")
        if x.cant_costo > 0 and abs(x.dif_prod_costo_vs_fact) > TOL_PRODUCCION: a.append("PRODUCCIÓN COSTOS ≠ FACT/RIPS")
        if x.cant_nt > 0 and x.cant_fact > 0 and x.cant_fact / x.cant_nt - 1 > NT_EXCESO: a.append("EXCEDE EVENTOS NT")
        if x.cant_nt > 0 and x.cant_fact > 0 and x.cant_fact / x.cant_nt - 1 < NT_BAJA: a.append("Baja ejecución vs NT")
        if pd.notna(x.costo_unit_real) and pd.notna(x.costo_unit_nt) and x.costo_unit_nt > 0 and abs(x.costo_unit_real / x.costo_unit_nt - 1) > TOL_PRODUCCION: a.append("Costo unitario real ≠ NT")
        if pd.notna(x.margen_unit) and x.margen_unit < 0: a.append("MARGEN NEGATIVO")
        return "; ".join(a) or "OK"
    m["estado"] = m.apply(est, axis=1)
    return m[COLS].sort_values("valor_fact", ascending=False).reset_index(drop=True)

def resumen(mes=None):
    """Totales por estado y cobertura de cada fuente (para KPIs y gráfica del dashboard)."""
    m = cruce(mes)
    if m.empty: return dict(kpis={}, estados=[], por_servicio=[])
    est = m.estado.str.split("; ").explode().value_counts().rename_axis("estado").reset_index(name="cups").to_dict("records")
    sv = (m.assign(servicio=m.servicio.replace("", "SIN SERVICIO")).groupby("servicio", as_index=False)[["valor_fact", "valor_costo", "valor_rips"]].sum()
          .sort_values("valor_fact", ascending=False).head(15))
    k = dict(cups=len(m), cups_ok=int((m.estado == "OK").sum()), valor_fact=m.valor_fact.sum(), valor_costo=m.valor_costo.sum(), valor_rips=m.valor_rips.sum(),
             margen=m.valor_fact.sum() - m.valor_costo.sum(),
             cobertura_costo=float((m.valor_costo > 0).sum() / max((m.valor_fact > 0).sum(), 1)),
             cobertura_rips=float((m.cant_rips > 0).sum() / max((m.cant_fact > 0).sum(), 1)),
             cobertura_nt=float((m.cant_nt > 0).sum() / max((m.cant_fact > 0).sum(), 1)))
    return dict(kpis=k, estados=est, por_servicio=sv.to_dict("records"))

def validaciones(carga_id=None):
    w = " WHERE carga_id=?" if carga_id else ""
    return df(f"SELECT carga_id, fila, campo, severidad, mensaje, valor FROM carga_validacion{w} ORDER BY carga_id DESC, fila", (carga_id,) if carga_id else ())

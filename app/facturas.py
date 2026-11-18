"""Hoja FACTURAS GENERADAS (encabezado de factura de Software 360): estados, radicación, glosas, DIAN, recaudo y reglas F01–F12.

Fuente: «INFORME DE FACTURAS GENERADAS» de Software 360 (una fila por factura). Se cruza con la facturación por ámbito
(detalle por línea, tabla `factura`) y con el RIPS (`rips_factura`) por número de factura normalizado.
"""
from datetime import date, timedelta
from functools import lru_cache
import numpy as np, pandas as pd
from .db import df, PLAZO_RADICACION, DIAS_FACTURACION_TARDIA, REGLAS_FG
from .loaders import norm

COLS = ["mes", "factura", "sede", "tercero", "contrato", "tipo_contrato", "regimen", "ambito", "estado_factura", "estado_auditoria", "fecha_ingreso",
        "fecha_egreso", "fecha_factura", "fecha_radicado", "dias_habiles_radicacion", "subtotal", "cuota", "copago", "descuento", "total",
        "valor_excedente", "estado_dian", "cufe", "registrada_por", "alertas"]

# ───────── festivos de Colombia (Ley 51 de 1983 «Ley Emiliani») para contar días hábiles ─────────
def _pascua(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25; g = (b - f + 1) // 3; h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7; m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31; dia = (h + l - 7 * m + 114) % 31 + 1
    return date(y, mes, dia)

def _lunes(d): return d + timedelta(days=(7 - d.weekday()) % 7)

@lru_cache(maxsize=None)
def festivos_co(y):
    p = _pascua(y)
    fijos = [date(y, 1, 1), date(y, 5, 1), date(y, 7, 20), date(y, 8, 7), date(y, 12, 8), date(y, 12, 25)]
    trasladables = [date(y, 1, 6), date(y, 3, 19), date(y, 6, 29), date(y, 8, 15), date(y, 10, 12), date(y, 11, 1), date(y, 11, 11)]
    pascua = [p - timedelta(days=3), p - timedelta(days=2), p + timedelta(days=43), p + timedelta(days=64), p + timedelta(days=71)]
    return tuple(sorted(set(fijos + [_lunes(d) for d in trasladables] + pascua)))

def _festivos(anios): return [d for y in anios for d in festivos_co(y)]

def dias_habiles(desde, hasta):
    """Días hábiles (lunes a viernes sin festivos de Colombia) entre dos series de fechas 'AAAA-MM-DD'. NaN si falta alguna."""
    a = pd.to_datetime(desde, errors="coerce"); b = pd.to_datetime(hasta, errors="coerce"); ok = a.notna() & b.notna()
    out = pd.Series(np.nan, index=a.index)
    if ok.any():
        anios = range(int(min(a[ok].dt.year.min(), b[ok].dt.year.min())), int(max(a[ok].dt.year.max(), b[ok].dt.year.max())) + 1)
        hol = np.array(_festivos(anios), dtype="datetime64[D]")
        out[ok] = np.busday_count(a[ok].values.astype("datetime64[D]"), b[ok].values.astype("datetime64[D]"), holidays=hol)
    return out

# ───────── base y reglas ─────────
def _w(mes=None, sede=None, eps=None):
    cond, p = [], []
    for col, v in (("mes", mes), ("sede", sede), ("tercero", eps)):
        if v: cond.append(f"{col}=?"); p.append(v)
    return (" WHERE " + " AND ".join(cond) if cond else ""), tuple(p)

def fecha_corte():
    """Fecha de corte = la fecha más reciente que trae el informe (factura o radicado)."""
    d = df("SELECT MAX(MAX(fecha_factura), MAX(fecha_radicado)) f FROM fac_gen").f
    return d.iloc[0] if len(d) and d.iloc[0] else date.today().isoformat()

def base(mes=None, sede=None, eps=None):
    """Facturas con días hábiles de radicación, antigüedad de las pendientes y alertas F01–F12 (texto separado por '; ')."""
    w, p = _w(mes, sede, eps)
    d = df(f"SELECT * FROM fac_gen{w}", p)
    if d.empty: return d
    for c in ("sede", "tercero", "contrato", "ambito", "regimen", "tipo_contrato", "registrada_por", "estado_factura"): d[c] = d[c].replace("", "SIN DATO")
    corte = fecha_corte()
    d["dias_habiles_radicacion"] = dias_habiles(d.fecha_factura, d.fecha_radicado.replace("", None))
    pend = d.estado_factura.map(norm).str.contains("PENDIENTE")
    d["antiguedad_habiles"] = np.where(pend, dias_habiles(d.fecha_factura, pd.Series(corte, index=d.index)), np.nan)
    ref = d.fecha_egreso.where(d.fecha_egreso != "", d.fecha_ingreso)
    d["dias_atencion_a_factura"] = (pd.to_datetime(d.fecha_factura, errors="coerce") - pd.to_datetime(ref, errors="coerce")).dt.days
    calc = d.subtotal - d.cuota - d.copago - d.bono - d.descuento + d.iva
    ef = d.estado_factura.map(norm); dian = d.estado_dian.map(norm)
    reglas = {
        "F01": ef.str.contains("GLOSAD"),
        "F02": pend & (d.antiguedad_habiles > PLAZO_RADICACION),
        "F03": d.dias_habiles_radicacion > PLAZO_RADICACION,
        "F04": d.dias_habiles_radicacion < 0,
        "F05": dian.str.contains("NO RECIBIDA") | (d.cufe == "NO"),
        "F06": (calc - d.total).abs() > 1,
        "F07": d.dias_atencion_a_factura > DIAS_FACTURACION_TARDIA,
        "F08": (d.fecha_egreso != "") & (d.fecha_ingreso != "") & (d.fecha_egreso < d.fecha_ingreso),
        "F09": (d.fecha_anulacion != "") | ef.str.contains("ANULAD"),
        "F10": d.total <= 0,
        "F11": ((d.cuota + d.copago) > 0) & d.recibo_caja.isin(["", "0"]),
        "F12": d.valor_excedente > 0,
    }
    m = pd.DataFrame(reglas).fillna(False)
    d["alertas"] = ["; ".join(k for k, v in zip(m.columns, fila) if v) or "OK" for fila in m.values]
    return d

def detalle(mes=None, sede=None, eps=None, alerta=None, solo_alertas=False):
    d = base(mes, sede, eps)
    if d.empty: return pd.DataFrame(columns=COLS)
    if alerta: d = d[d.alertas.str.contains(alerta, regex=False)]
    if solo_alertas: d = d[d.alertas != "OK"]
    d = d.assign(alertas=d.alertas.map(lambda a: a if a == "OK" else "; ".join(f"{k} {REGLAS_FG[k]}" for k in a.split("; "))))
    return d[COLS].sort_values("total", ascending=False).reset_index(drop=True)

def _agr(d, por):
    g = d.groupby(por, dropna=False).agg(facturas=("factura", "size"), valor=("total", "sum"),
                                         radicado=("_rad", "sum"), glosado=("_glo", "sum"), pendiente=("_pen", "sum"),
                                         con_alerta=("_ale", "sum"), dias_radicacion=("dias_habiles_radicacion", "mean")).reset_index()
    g["pct_glosa"] = (g.glosado / g.valor.replace(0, np.nan)).fillna(0); g["pct_pendiente"] = (g.pendiente / g.valor.replace(0, np.nan)).fillna(0)
    return g.sort_values("valor", ascending=False)

def _marcas(d):
    ef = d.estado_factura.map(norm)
    return d.assign(_rad=np.where(ef.str.contains("RADICAD"), d.total, 0), _glo=np.where(ef.str.contains("GLOSAD"), d.total, 0),
                    _pen=np.where(ef.str.contains("PENDIENTE"), d.total, 0), _ale=(d.alertas != "OK").astype(int))

def _rangos(d, col, cortes, etiquetas):
    """Facturas y valor por rango (en el orden de los cortes)."""
    if d.empty: return []
    r = pd.cut(d[col], cortes, labels=etiquetas, right=True)
    g = d.groupby(r, observed=True).agg(facturas=("factura", "size"), valor=("total", "sum")).reset_index().rename(columns={col: "rango"})
    g["rango"] = g.rango.astype(str); return g.to_dict("records")

def resumen(mes=None, sede=None, eps=None):
    d = base(mes, sede, eps)
    if d.empty: return dict(kpis={}, vacio=True)
    d = _marcas(d); t = d.total.sum()
    rad = d[d.dias_habiles_radicacion.notna()]
    k = dict(facturas=len(d), valor=t, radicado=d._rad.sum(), glosado=d._glo.sum(), pendiente=d._pen.sum(),
             n_glosadas=int((d._glo > 0).sum()), n_pendientes=int((d._pen > 0).sum()), pct_glosa=d._glo.sum() / t if t else 0,
             dias_radicacion=float(rad.dias_habiles_radicacion.mean()) if len(rad) else None,
             pct_en_plazo=float((rad.dias_habiles_radicacion.between(0, PLAZO_RADICACION)).mean()) if len(rad) else None,
             cuotas_copagos=float((d.cuota + d.copago).sum()), particulares=float(d.loc[d.particular.map(norm) == "SI", "valor_recibo"].sum()),
             dian_no_recibida=int(d.alertas.str.contains("F05").sum()), con_alerta=int(d._ale.sum()), valor_con_alerta=float(d.loc[d._ale == 1, "total"].sum()),
             pacientes=int(d.paciente_id.replace("", np.nan).nunique()), ticket=t / len(d), sedes=int(d.sede.nunique()), terceros=int(d.tercero.nunique()),
             plazo=PLAZO_RADICACION, corte=fecha_corte())
    reglas = []
    for r, txt in REGLAS_FG.items():
        mk = d.alertas.str.contains(r)
        if mk.any(): reglas.append(dict(regla=r, descripcion=txt, facturas=int(mk.sum()), valor=float(d.loc[mk, "total"].sum())))
    pen = d[d._pen > 0]
    dia = d.groupby("fecha_factura").agg(facturas=("factura", "size"), valor=("total", "sum")).reset_index().sort_values("fecha_factura")
    ult5 = dia.tail(5).valor.sum() / t if t else 0
    k["pct_ultimos_5_dias"] = float(ult5)
    return dict(
        kpis=k,
        por_estado=d.groupby("estado_factura").agg(facturas=("factura", "size"), valor=("total", "sum")).reset_index().sort_values("valor", ascending=False).to_dict("records"),
        por_sede=_agr(d, "sede").to_dict("records"),
        por_eps=_agr(d, "tercero").head(20).to_dict("records"),
        por_contrato=_agr(d, "contrato").head(25).to_dict("records"),
        por_ambito=_agr(d, "ambito").to_dict("records"),
        por_regimen=_agr(d, "regimen").to_dict("records"),
        por_tipo_contrato=_agr(d, "tipo_contrato").to_dict("records"),
        por_dia=dia.to_dict("records"),
        oportunidad=_rangos(rad, "dias_habiles_radicacion", [-1e9, -1, 10, 15, PLAZO_RADICACION, 30, 1e9],
                            ["Antes de facturar", "0–10", "11–15", f"16–{PLAZO_RADICACION}", f"{PLAZO_RADICACION + 1}–30", "> 30"]),
        antiguedad_pendientes=_rangos(pen, "antiguedad_habiles", [-1e9, 10, PLAZO_RADICACION, 45, 1e9], ["0–10", f"11–{PLAZO_RADICACION}", f"{PLAZO_RADICACION + 1}–45", "> 45"]),
        reglas=reglas)

def facturadores(mes=None, sede=None, eps=None):
    """Productividad y calidad por usuario que registra la factura (Software 360 · REGISTRADA POR)."""
    d = base(mes, sede, eps)
    if d.empty: return d
    d = _marcas(d)
    g = d.groupby("registrada_por").agg(facturas=("factura", "size"), valor=("total", "sum"), sedes=("sede", "nunique"), glosadas=("_glo", lambda s: int((s > 0).sum())),
                                        valor_glosado=("_glo", "sum"), con_alerta=("_ale", "sum"), dias_radicacion=("dias_habiles_radicacion", "mean")).reset_index()
    g["pct_glosa"] = (g.glosadas / g.facturas).fillna(0); g["pct_con_alerta"] = (g.con_alerta / g.facturas).fillna(0)
    lim = g.pct_glosa.mean() + 2 * g.pct_glosa.std() if len(g) > 2 else 1
    g["estado"] = np.where((g.facturas >= 20) & (g.pct_glosa > max(lim, 0.15)), "ALERTA: GLOSA ATÍPICA", "OK")
    return g.sort_values("valor", ascending=False).reset_index(drop=True)

def _fk(s): return s.astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)

def cruce(mes=None):
    """Encabezado (facturas generadas) ↔ detalle (facturación por ámbito) ↔ RIPS (FEV), factura a factura."""
    w = " WHERE mes=?" if mes else ""; p = (mes,) if mes else ()
    g = df(f"SELECT mes, factura, sede, tercero, contrato, estado_factura, total FROM fac_gen{w}", p)
    if g.empty: return g
    det = df(f"SELECT mes, factura, SUM(valor) valor_detalle, SUM(lineas) lineas FROM factura{w} GROUP BY mes, factura", p)
    rf = df(f"SELECT factura, MAX(valor_fev) valor_rips FROM rips_factura{w} GROUP BY factura", p)
    g["fk"] = _fk(g.factura)
    for t in (det, rf): t["fk"] = _fk(t.factura) if len(t) else pd.Series(dtype=str)
    meses_det = set(det.mes) if len(det) else set()
    m = g.merge(det[["fk", "valor_detalle", "lineas"]].groupby("fk").sum().reset_index() if len(det) else pd.DataFrame(columns=["fk", "valor_detalle", "lineas"]), on="fk", how="left")
    m = m.merge(rf[["fk", "valor_rips"]].drop_duplicates("fk") if len(rf) else pd.DataFrame(columns=["fk", "valor_rips"]), on="fk", how="left")
    m["dif_detalle"] = m.total - m.valor_detalle; m["dif_rips"] = m.total - m.valor_rips
    tol = np.maximum(1.0, 0.001 * m.total)
    est = []
    for r, t in zip(m.itertuples(), tol):
        a = []
        if r.mes in meses_det:
            if pd.isna(r.valor_detalle): a.append("SIN DETALLE POR ÁMBITO")
            elif abs(r.dif_detalle) > t: a.append("ENCABEZADO ≠ DETALLE")
        if len(rf):
            if pd.isna(r.valor_rips): a.append("SIN RIPS")
            elif abs(r.dif_rips) > t: a.append("VALOR RIPS DIFIERE")
        est.append("; ".join(a) or ("OK" if (r.mes in meses_det or len(rf)) else "SIN FUENTES PARA CRUZAR"))
    m["estado"] = est
    cols = ["mes", "factura", "sede", "tercero", "contrato", "estado_factura", "total", "valor_detalle", "dif_detalle", "lineas", "valor_rips", "dif_rips", "estado"]
    return m[cols].sort_values("dif_detalle", key=lambda s: s.abs().fillna(0), ascending=False).reset_index(drop=True)

def recomendaciones(mes=None):
    r = resumen(mes)
    if r.get("vacio"): return []
    k, R = r["kpis"], []
    if k["glosado"]: R.append(("rojo", f"Glosas: {k['n_glosadas']:,} facturas en estado «Glosada» por ${k['glosado']:,.0f} ({k['pct_glosa']:.1%} del valor facturado; el informe de 360 no trae el valor objetado). "
                                      "Priorizar respuesta dentro de los términos del Manual Único de Glosas (Res. 2284/2023) y atacar la causa raíz por EPS y facturador."))
    if k["n_pendientes"]: R.append(("naranja", f"{k['n_pendientes']:,} facturas pendientes por radicar por ${k['pendiente']:,.0f}: programar radicación inmediata para proteger el flujo de caja."))
    if k["pct_en_plazo"] is not None and k["pct_en_plazo"] < 0.95:
        R.append(("naranja", f"Solo el {k['pct_en_plazo']:.1%} de las facturas se radicó dentro de {k['plazo']} días hábiles (promedio {k['dias_radicacion']:.1f}). Fijar cronograma semanal de radicación por EPS."))
    if k["pct_ultimos_5_dias"] > 0.4:
        R.append(("naranja", f"El {k['pct_ultimos_5_dias']:.0%} del valor se facturó en los últimos 5 días con facturación: embalse de cierre. Facturar a diario (egreso → factura ≤ 72 h) reduce errores y glosas."))
    for x in r["reglas"]:
        if x["regla"] in ("F04", "F05", "F06", "F08", "F11"):
            R.append(("rojo" if x["regla"] in ("F05", "F06") else "naranja", f"{x['regla']} – {x['descripcion']}: {x['facturas']:,} facturas (${x['valor']:,.0f}). Corregir en Software 360."))
    ep = [e for e in r["por_eps"] if e["pct_glosa"] > 0.10 and e["glosado"] > 0][:3]
    for e in ep: R.append(("naranja", f"EPS {e['tercero']}: {e['pct_glosa']:.1%} del valor en facturas glosadas (${e['glosado']:,.0f}). Revisar causales con la EPS en mesa de conciliación."))
    return [dict(nivel=n, texto=t, modulo="Facturas generadas") for n, t in R]

"""COMPARATIVO RIPS vs LAS TRES FACTURACIONES DE SOFTWARE 360 (pestaña «⚖️ RIPS vs facturación 360»).

Une, factura por factura, el RIPS (JSON/TXT/Excel) con:
  · la facturación por ámbito (líneas de la carga «1»),
  · el informe de facturas generadas (encabezado, carga «1b»),
  · la facturación generada por actividad (carga «1c»; VALOR FACTURA y suma de líneas),
  · el XML de la factura electrónica (FEV), si se cargó con los RIPS.
Una fuente solo se exige en los meses en que fue cargada (no se marca «no está» una factura de un mes sin esa fuente).
También compara los medicamentos e insumos por código 360 (los CUM del RIPS se llevan a código 360 con la homologación / maestro).
"""
import numpy as np, pandas as pd
from .db import df
from .loaders import cum_norm, mapa_cum

FUENTES = {"rips": "RIPS", "ambito": "Facturación por ámbito", "generada": "Facturas generadas", "actividad": "Facturación por actividad", "xml": "XML (FEV)"}
SIGLA = {"rips": "R", "ambito": "A", "generada": "G", "actividad": "C", "xml": "X"}

def _fk(s): return s.astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)

def _w(mes, extra=""):
    c = [x for x in ([("mes=?" if mes else "")] + ([extra] if extra else [])) if x]
    return (" WHERE " + " AND ".join(c) if c else ""), ((mes,) if mes else ())

def _tablas(mes=None):
    w, p = _w(mes)
    wa, pa = _w(mes, "carga_id IN (SELECT id FROM carga WHERE tipo='facturacion')")
    return dict(
        rips=df(f"SELECT mes, factura, SUM(valor) valor, MAX(eps) eps, MAX(contrato) contrato, MAX(modalidad) modalidad FROM rips{w} GROUP BY mes, factura", p),
        ambito=df(f"SELECT mes, factura, SUM(valor) valor, MAX(eps_nombre) eps, MAX(contrato) contrato, MAX(modalidad) modalidad, MAX(sede) sede FROM factura{wa} GROUP BY mes, factura", pa),
        generada=df(f"SELECT mes, factura, MAX(total) valor, MAX(tercero) eps, MAX(contrato) contrato, MAX(estado_factura) estado_factura, MAX(sede) sede FROM fac_gen{w} GROUP BY mes, factura", p),
        actividad=df(f"SELECT mes, factura, MAX(valor_factura) valor, SUM(valor) valor_lineas, MAX(eps_nombre) eps, MAX(contrato) contrato, MAX(modalidad) modalidad, MAX(sede) sede "
                     f"FROM act_det{w} GROUP BY mes, factura", p),
        xml=df(f"SELECT mes, factura, MAX(valor) valor, MAX(eps) eps, MAX(contrato) contrato, MAX(modalidad) modalidad, MAX(estado_dian) estado_dian FROM fev_xml{w} GROUP BY mes, factura", p))

def _prefijo(s): return s.astype(str).str.upper().str.extract(r"^([A-Z]+)", expand=False).fillna("")

def _global(mod): return mod.fillna("").astype(str).str.upper().str.contains("CAPITA|CÁPITA|PGP|GLOBAL")

def facturas(mes=None):
    """Una fila por factura con el valor en cada fuente, las diferencias frente al RIPS y el estado."""
    T = _tablas(mes)
    if all(t.empty for t in T.values()): return pd.DataFrame()
    meses = {k: set(t.mes) for k, t in T.items()}
    m = None
    for k, t in T.items():
        if t.empty: continue
        t = t.copy(); t["fk"] = _fk(t.factura)
        t = t.groupby("fk", as_index=False).agg({c: ("sum" if c in ("valor", "valor_lineas") else "first") for c in t.columns if c != "fk"})
        t = t.rename(columns={c: f"{c}_{k}" for c in t.columns if c != "fk"})
        m = t if m is None else m.merge(t, on="fk", how="outer")
    for k in FUENTES:
        for c in ("mes", "factura", "valor", "eps", "contrato", "modalidad"):
            if f"{c}_{k}" not in m: m[f"{c}_{k}"] = np.nan
    pri = lambda c: m[[f"{c}_{k}" for k in ("ambito", "actividad", "generada", "xml", "rips")]].bfill(axis=1).iloc[:, 0]
    m["mes"], m["factura"], m["eps"], m["contrato"] = pri("mes"), pri("factura"), pri("eps"), pri("contrato")
    m["modalidad"] = pri("modalidad").fillna("")
    glob = _global(m.modalidad)
    tol = lambda v: np.maximum(1.0, 0.001 * v.abs().fillna(0))
    est = pd.Series([[] for _ in range(len(m))], index=m.index)
    def marca(mask, txt):
        for i in m.index[mask.fillna(False).to_numpy()]: est[i].append(txt)
    hay_r = m.valor_rips.notna()
    # cuota moderadora / copago del RIPS (JSON valorPagoModerador, TXT AC): el valor de la factura en 360 suele ser el neto
    w, p = _w(mes)
    mo = df(f"SELECT factura, SUM(COALESCE(moderadora,0)) moderadora FROM rips_det{w} GROUP BY factura", p)
    m["moderadora"] = m.fk.map(mo.assign(fk=_fk(mo.factura)).groupby("fk").moderadora.sum()).fillna(0.0) if len(mo) else 0.0
    # una factura solo es «SIN RIPS» si ya se cargó el RIPS de su sede (prefijo de la factura) en ese mes
    pr = T["rips"].assign(p=_prefijo(T["rips"].factura)) if len(T["rips"]) else pd.DataFrame(columns=["mes", "p"])
    con_rips = set(zip(pr.mes, pr.p)); m["prefijo"] = _prefijo(m.factura)
    sede_cargada = pd.Series([(a_, b_) in con_rips for a_, b_ in zip(m.mes, m.prefijo)], index=m.index, dtype=bool)
    marca(~hay_r & sede_cargada, "SIN RIPS")
    marca(~hay_r & ~sede_cargada & m.mes.isin(meses["rips"]), "INFO: RIPS DE ESTA SEDE (PREFIJO) NO CARGADO")
    marca(hay_r & m.valor_ambito.isna() & m.mes.isin(meses["ambito"]), "NO ESTÁ EN FACTURACIÓN POR ÁMBITO")
    marca(hay_r & m.valor_generada.isna() & m.mes.isin(meses["generada"]), "NO ESTÁ EN FACTURAS GENERADAS")
    marca(hay_r & m.valor_actividad.isna() & m.mes.isin(meses["actividad"]), "NO ESTÁ EN FACTURACIÓN POR ACTIVIDAD")
    for k, txt in (("ambito", "RIPS ≠ POR ÁMBITO"), ("generada", "RIPS ≠ FACTURA GENERADA"), ("actividad", "RIPS ≠ POR ACTIVIDAD"), ("xml", "RIPS ≠ XML (FEV)")):
        ref = m[f"valor_{k}"]; m[f"dif_{k}"] = ref - m.valor_rips
        difiere = hay_r & ref.notna() & ~glob & ((ref - m.valor_rips).abs() > tol(ref))
        neto = difiere & (m.moderadora > 0) & ((ref - (m.valor_rips - m.moderadora)).abs() <= tol(ref))
        marca(difiere & ~neto, txt); marca(neto, f"OK NETO DE CUOTA MODERADORA ({FUENTES[k].upper()})")
    gen_act = m.valor_generada.notna() & m.valor_actividad.notna() & ((m.valor_generada - m.valor_actividad).abs() > tol(m.valor_generada))
    marca(gen_act, "GENERADA ≠ ACTIVIDAD")
    marca(m.estado_factura_generada.fillna("").astype(str).str.upper().str.contains("ANULAD") & hay_r if "estado_factura_generada" in m else pd.Series(False, index=m.index),
          "ANULADA CON RIPS")
    sin_fuente = ~m[[f"valor_{k}" for k in ("ambito", "generada", "actividad")]].notna().any(axis=1)
    marca(hay_r & sin_fuente, "INFO: SIN FACTURACIÓN 360 CARGADA PARA LA FACTURA")
    m["fuentes"] = ""
    for k in FUENTES: m["fuentes"] += np.where(m[f"valor_{k}"].notna(), SIGLA[k], "")
    m["estado"] = [("; ".join(x) if x else ("OK (CÁPITA/PGP: VALOR NO COMPARABLE)" if g else "OK")) for x, g in zip(est, glob)]
    m["estado"] = m.estado.str.replace(r"^OK NETO DE CUOTA MODERADORA.*$", "OK NETO DE CUOTA MODERADORA", regex=True)
    cols = ["mes", "factura", "eps", "contrato", "modalidad", "fuentes", "valor_rips", "moderadora", "valor_ambito", "valor_generada", "valor_actividad", "valor_lineas_actividad",
            "valor_xml", "dif_ambito", "dif_generada", "dif_actividad", "dif_xml", "estado"]
    for c in cols:
        if c not in m: m[c] = np.nan
    orden = m[["dif_ambito", "dif_generada", "dif_actividad"]].abs().max(axis=1).fillna(0) + np.where(m.estado.str.startswith("INFO"), -1e15, 0) \
        + np.where(m.estado.str.startswith("OK"), -1e14, 0)
    return m.assign(_o=orden)[cols + ["_o"]].sort_values("_o", ascending=False).drop(columns="_o").reset_index(drop=True)

def resumen(mes=None):
    """Por mes y fuente: facturas y valor, cuántas tienen RIPS y la diferencia de valor; más el conteo de estados."""
    f = facturas(mes)
    if f.empty: return dict(vacio=True, filas=[], estados=[], kpis={})
    filas = []
    f = f.assign(_p=_prefijo(f.factura))
    for (mm), g in f.groupby("mes"):
        r = g.valor_rips.notna()
        g = g[g._p.isin(set(g.loc[r, "_p"]))] if r.any() else g          # solo las sedes (prefijos) con RIPS cargado en el mes
        r = g.valor_rips.notna()
        for k, nom in FUENTES.items():
            v = g[f"valor_{k}"]; h = v.notna()
            if not h.any(): continue
            ambas = h & r
            filas.append(dict(mes=mm, fuente=nom, facturas=int(h.sum()), valor=float(v.sum()),
                              con_rips=int(ambas.sum()) if k != "rips" else int(h.sum()), pct_con_rips=float(ambas.sum() / h.sum()) if k != "rips" else 1.0,
                              valor_rips_de_esas=float(g.loc[ambas, "valor_rips"].sum()), dif_vs_rips=float((v[ambas] - g.loc[ambas, "valor_rips"]).sum()) if k != "rips" else 0.0))
    est = f.estado.str.split("; ").explode().value_counts().rename_axis("estado").reset_index(name="facturas")
    k = dict(facturas=len(f), con_rips=int(f.valor_rips.notna().sum()), sin_rips=int(f.estado.str.contains("SIN RIPS").sum()),
             con_diferencia=int(f.estado.str.contains("≠").sum()), ok=int(f.estado.str.startswith("OK").sum()),
             neto_moderadora=int(f.estado.str.contains("CUOTA MODERADORA").sum()), sede_sin_rips=int(f.estado.str.contains("NO CARGADO").sum()),
             no_en_360=int(f.estado.str.contains("NO ESTÁ EN").sum()),
             valor_diferencia=float(f[["dif_ambito", "dif_generada", "dif_actividad"]].abs().max(axis=1)[f.estado.str.contains("≠")].sum()))
    k["sedes_con_rips"] = sorted(set(f.loc[f.valor_rips.notna(), "_p"]))
    return dict(kpis=k, filas=filas, estados=est.to_dict("records"))

def medicamentos(mes=None):
    """Medicamentos e insumos por código 360 en las facturas que tienen RIPS: RIPS (CUM homologado) vs facturación por ámbito vs por actividad.
    Cada fuente de 360 se compara solo contra los RIPS de SUS facturas (así un reporte de todas las sedes no se compara contra el RIPS de una sola)."""
    w, p = _w(mes)
    wr = _w(mes, "servicio IN ('Medicamentos','Otros servicios')")[0]; wc = _w(mes, "clase IN ('MEDICAMENTO','INSUMO','SUPLEMENTO')")[0]
    wa = _w(mes, "carga_id IN (SELECT id FROM carga WHERE tipo='facturacion')")[0]
    r = df(f"SELECT factura, servicio, cups codigo_rips, MAX(descripcion) desc_rips, SUM(cantidad) cant, SUM(valor) valor FROM rips{wr} GROUP BY factura, servicio, cups", p)
    a = df(f"SELECT factura, codigo, MAX(clase) clase, MAX(descripcion) descripcion, SUM(cantidad) cant, SUM(valor) valor FROM med_det{wa} GROUP BY factura, codigo", p)
    c = df(f"SELECT factura, codigo, MAX(clase) clase, MAX(actividad) descripcion, SUM(cantidad) cant, SUM(valor) valor FROM act_det{wc} GROUP BY factura, codigo", p)
    if r.empty: return pd.DataFrame()
    todas_r = set(_fk(df(f"SELECT DISTINCT factura FROM rips{w}", p).factura))
    hm = mapa_cum()
    r["codigo"] = r.codigo_rips.map(lambda x: hm.get(cum_norm(x), x)); r["fk"] = _fk(r.factura)
    # «Otros servicios» del RIPS incluye traslados y estancias: solo cuentan los que son insumos (código 360 de medicamento/insumo o CUM)
    cod_med = set(a.codigo) | set(c.codigo) | set(hm.values())
    es_med = (r.servicio == "Medicamentos") | r.codigo.isin(cod_med) | r.codigo.astype(str).str.upper().str.match(r"^[GD]-\d") | r.codigo_rips.astype(str).str.match(r"^\d{5,}-\d{1,2}$")
    r = r[es_med]
    out, info = [], {}
    for k, t in (("ambito", a), ("actividad", c)):
        if t.empty: continue
        t = t.assign(fk=_fk(t.factura)); comunes = set(t.fk) & todas_r
        t = t[t.fk.isin(comunes)]; rr = r[r.fk.isin(comunes)]
        g = t.groupby("codigo", as_index=False).agg(clase=("clase", "first"), descripcion=("descripcion", "first"), cant=("cant", "sum"), valor=("valor", "sum"))
        gr = rr.groupby("codigo", as_index=False).agg(cant_rips=("cant", "sum"), valor_rips=("valor", "sum"))
        x = g.merge(gr, on="codigo", how="outer").rename(columns={"cant": f"cant_{k}", "valor": f"valor_{k}", "cant_rips": f"cant_rips_{k}", "valor_rips": f"valor_rips_{k}"})
        out.append(x.set_index("codigo")); info[k] = len(comunes)
    if not out: return pd.DataFrame()
    m = out[0] if len(out) == 1 else out[0].combine_first(out[1])
    m = m.reset_index()
    cr = r[r.fk.isin(set().union(*[set(_fk(t.factura)) for t in (a, c) if len(t)]))].groupby("codigo").agg(
        codigo_rips=("codigo_rips", lambda s_: ", ".join(sorted(set(s_)))[:120]), desc_rips=("desc_rips", "first"), cant_rips=("cant", "sum"), valor_rips=("valor", "sum"))
    m = m.merge(cr.reset_index(), on="codigo", how="left")
    m["descripcion"] = m.descripcion.fillna(m.desc_rips)
    for k in ("ambito", "actividad"):
        for col in (f"cant_{k}", f"valor_{k}", f"cant_rips_{k}"):
            if col not in m: m[col] = np.nan
        m[f"dif_cant_{k}"] = (m[f"cant_{k}"].fillna(0) - m[f"cant_rips_{k}"].fillna(0)) if k in info else np.nan
    for col in ("cant_rips", "valor_rips"): m[col] = m[col].fillna(0.0)
    def est(x):
        e = []
        for k, nom in (("ambito", "POR ÁMBITO"), ("actividad", "POR ACTIVIDAD")):
            if k not in info: continue
            q, qr = x[f"cant_{k}"] if pd.notna(x[f"cant_{k}"]) else 0, x[f"cant_rips_{k}"] if pd.notna(x[f"cant_rips_{k}"]) else 0
            if q > 0 and qr == 0: e.append(f"FACTURADO {nom} SIN RIPS")
            elif q == 0 and qr > 0:
                e.append("CUM DEL RIPS SIN HOMOLOGAR" if not str(x.codigo).upper().startswith(("G-", "D-")) and str(x.codigo) in str(x.codigo_rips) else f"EN RIPS, NO FACTURADO {nom}")
            elif abs(q - qr) > 0.001: e.append(f"CANTIDAD ≠ {nom}")
        return "; ".join(dict.fromkeys(e)) or "OK"
    m["estado"] = m.apply(est, axis=1)
    cols = ["codigo", "codigo_rips", "clase", "descripcion", "cant_rips", "cant_ambito", "cant_actividad", "dif_cant_ambito", "dif_cant_actividad",
            "valor_rips", "valor_ambito", "valor_actividad", "estado"]
    return m[cols].sort_values("valor_rips", ascending=False).reset_index(drop=True)

def recomendaciones(mes=None):
    s = resumen(mes); k = s.get("kpis") or {}
    if not k: return []
    out = []
    if k["sin_rips"]: out.append(dict(nivel="rojo", modulo="Facturación / RIPS / costos",
                                      texto=f"{k['sin_rips']:,} facturas de 360 sin RIPS en meses con RIPS cargado: generar y enviar el RIPS antes de radicar (Res. 2275 de 2023)."))
    if k["con_diferencia"]: out.append(dict(nivel="naranja", modulo="Facturación / RIPS / costos",
                                            texto=f"{k['con_diferencia']:,} facturas con valor del RIPS distinto al de 360 (ámbito, generada o actividad): conciliar antes de la radicación para evitar glosas."))
    if k["no_en_360"]: out.append(dict(nivel="naranja", modulo="Facturación / RIPS / costos",
                                       texto=f"{k['no_en_360']:,} facturas del RIPS no aparecen en algún reporte de 360 del mismo mes: revisar anulaciones, notas o filtros del reporte."))
    return out

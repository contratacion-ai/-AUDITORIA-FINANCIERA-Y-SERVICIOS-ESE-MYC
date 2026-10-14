"""Análisis: conciliación Facturación ↔ RIPS ↔ Costos ↔ Notas técnicas, radicación, contratos, cobertura de llaves e informes."""
import numpy as np, pandas as pd
from .db import df, UMBRAL_OK, UMBRAL_ALERTA, NT_EXCESO, NT_BAJA, REGLAS, SEDES
from .loaders import norm

def _w(mes, extra=""):
    cond = (["mes=?"] if mes else []) + ([extra] if extra else [])
    return (" WHERE " + " AND ".join(cond) if cond else ""), ((mes,) if mes else ())

def semaforo(p):
    p = abs(p); return "OK" if p <= UMBRAL_OK else "ALERTA" if p <= UMBRAL_ALERTA else "CRÍTICO"

def _pct(a, b): return (pd.Series(a, dtype=float) / pd.Series(b, dtype=float).replace(0, np.nan)).fillna(0)
def _fk(s): return s.astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)

# ───────── homologación de RIPS contra Software 360 (por factura FEV y por CUPS) ─────────
def _fill(r):
    """Completa contrato/sede/modalidad del RIPS usando la factura FEV de Software 360 cuando faltan o no coinciden."""
    fm = df("SELECT factura, contrato, sede, modalidad FROM factura"); fm["fk"] = _fk(fm.factura)
    fm = fm.drop_duplicates("fk").set_index("fk"); fk = _fk(r.factura)
    known = {norm(c) for c in fm.contrato.unique()}
    for c in ("contrato", "sede", "modalidad"):
        v = fk.map(fm[c]); bad = r[c].fillna("").eq("") | (~r[c].map(norm).isin(known) if c == "contrato" else False)
        r[c] = np.where(bad & v.notna(), v, r[c])
    return r

def _cups_serv():
    f = df("SELECT cups, servicio, SUM(valor) v FROM fact WHERE cups<>'' GROUP BY cups, servicio").sort_values("v").drop_duplicates("cups", keep="last")
    return f.set_index("cups").servicio

def rips_h(mes=None):
    w, p = _w(mes)
    r = df(f"SELECT mes,eps,nit_eps,contrato,cucon,modalidad,sede,servicio tipo_servicio,cups,factura,cantidad,valor FROM rips{w}", p)
    if r.empty: return r
    r = _fill(r)
    hc = df("SELECT codigo_rips, codigo_360 FROM hom_codigo")
    if not hc.empty: r["cups"] = r.cups.map(hc.set_index("codigo_rips").codigo_360).fillna(r.cups)   # CUMS → código interno 360
    h = r.cups.map(_cups_serv())
    r["servicio"] = np.where(h.notna(), h, "SIN HOMOLOGAR · " + r.tipo_servicio)
    return r

def rips_fact_h(mes=None):
    w, p = _w(mes)
    r = df(f"SELECT mes,factura,factura_rips,eps,contrato,cucon,modalidad,sede,valor_fev,servicios,validacion FROM rips_factura{w}", p)
    return _fill(r) if not r.empty else r

# ───────── series mensuales ─────────
def mensual():
    """Serie mensual. Eventos: facturado vs FEV (solo EPS con RIPS cargado). Cápita: valor FEV aparte (no está en el detalle de 360)."""
    f = df("SELECT mes, SUM(valor) facturado, SUM(cantidad) cant_fact, SUM(lineas) lineas FROM fact GROUP BY mes")
    r2 = df("SELECT mes, SUM(cantidad) cant_rips FROM rips GROUP BY mes")
    k = df("SELECT mes, SUM(valor) costos FROM costo GROUP BY mes")
    h = df("SELECT mes, SUM(lineas) lineas_hallazgo, SUM(valor) valor_hallazgo FROM hallazgo WHERE regla NOT IN ('R08','R09') GROUP BY mes")
    fe = df("SELECT mes, eps_nombre eps, modalidad, SUM(valor) v FROM fact GROUP BY 1,2,3"); rf = df("SELECT mes, eps, modalidad, SUM(valor_fev) v FROM rips_factura GROUP BY 1,2,3")
    cap = lambda s: s.map(norm) == "CAPITA"
    if rf.empty: x = pd.DataFrame(columns=["mes", "rips", "fact_evento", "rips_evento", "cap_fev", "cap_360"])
    else:
        pares = set(zip(rf.mes, rf.eps.map(norm), rf.modalidad.map(norm))); fe = fe[[(a, b, c) in pares for a, b, c in zip(fe.mes, fe.eps.map(norm), fe.modalidad.map(norm))]]
        x = pd.DataFrame({"rips": rf.groupby("mes").v.sum(), "rips_evento": rf[~cap(rf.modalidad)].groupby("mes").v.sum(), "cap_fev": rf[cap(rf.modalidad)].groupby("mes").v.sum(),
                          "fact_evento": fe[~cap(fe.modalidad)].groupby("mes").v.sum(), "cap_360": fe[cap(fe.modalidad)].groupby("mes").v.sum()}).reset_index().rename(columns={"index": "mes"})
    m = f.merge(x, on="mes", how="outer").merge(r2, on="mes", how="outer").merge(k, on="mes", how="outer").merge(h, on="mes", how="outer").sort_values("mes").reset_index(drop=True)
    sin_r = m.rips.isna(); m = m.fillna(0)
    m["brecha_fact_rips"] = m.fact_evento - m.rips_evento; m["pct_brecha"] = _pct(m.brecha_fact_rips, m.fact_evento)
    m["margen"] = m.facturado - m.costos; m["pct_margen"] = _pct(m.margen, m.facturado)
    m["estado"] = ["SIN RIPS" if s else ("SOLO CÁPITA (ver eventos)" if fe_ == 0 and re_ == 0 else semaforo(p)) for s, fe_, re_, p in zip(sin_r, m.fact_evento, m.rips_evento, m.pct_brecha)]
    return m

def _est(fa, ri, cf, cr, cap=False):
    if ri == 0 and cr == 0: return "FACTURADO SIN RIPS"
    if fa == 0 and cf == 0: return "CÁPITA: FEV SIN DETALLE EN 360" if cap else "RIPS SIN FACTURA"
    if cap or not (ri > 0 and fa > 0): return semaforo((cf - cr) / (cf or cr))      # cápita (o RIPS sin valor): se compara por EVENTOS
    return semaforo((fa - ri) / fa)

def fact_vs_rips(mes=None, nivel="eps"):
    """Facturación (360) vs RIPS por modalidad. EVENTO se compara por valor (FEV); CÁPITA por eventos (el valor cápita no está en el detalle de 360).
    Solo se incluyen EPS con RIPS cargado."""
    fcol = {"eps": "eps_nombre"}.get(nivel, nivel); w, p = _w(mes)
    r = rips_h(mes)
    if r.empty: return pd.DataFrame()
    r["_lvl"] = r.modalidad; lv = "_lvl" if nivel == "modalidad" else nivel
    f = df(f"SELECT mes, eps_nombre e, modalidad mo, {fcol} k, SUM(valor) facturado, SUM(cantidad) cant_fact FROM fact{w} GROUP BY 1,2,3,4", p)
    pares = set(zip(r.mes, r.eps.map(norm), r.modalidad.map(norm))); f = f[[(a, b, c) in pares for a, b, c in zip(f.mes, f.e.map(norm), f.mo.map(norm))]]
    rc = r.groupby(["mes", "modalidad", lv], as_index=False).agg(cant_rips=("cantidad", "sum"), rips=("valor", "sum")).rename(columns={lv: "k", "modalidad": "mo"})
    if nivel in ("eps", "contrato", "modalidad", "sede"):
        rfh = rips_fact_h(mes); rfh["_lvl"] = rfh.modalidad
        rf = rfh.groupby(["mes", "modalidad", lv], as_index=False).valor_fev.sum().rename(columns={lv: "k", "valor_fev": "rips", "modalidad": "mo"})
        rc = rc.drop(columns="rips").merge(rf, on=["mes", "mo", "k"], how="outer")
    for t in (f, rc): t["k"] = t.k.map(norm) if nivel != "cups" else t.k.astype(str).str.strip(); t["mo"] = t.mo.map(norm).replace("", "SIN DATO")
    m = f.drop(columns="e").groupby(["mes", "mo", "k"], as_index=False).sum().merge(rc.groupby(["mes", "mo", "k"], as_index=False).sum(), on=["mes", "mo", "k"], how="outer").fillna(0)
    m["dif"] = m.facturado - m.rips; m["pct_dif"] = _pct(m.dif, m.facturado.where(m.facturado != 0, m.rips)); m["dif_cant"] = m.cant_fact - m.cant_rips
    m["pct_dif_cant"] = _pct(m.dif_cant, m.cant_fact.where(m.cant_fact != 0, m.cant_rips))
    m["comparado_por"] = np.where(m.mo == "CAPITA", "EVENTOS (cápita)", "VALOR")
    m["estado"] = [_est(*x, cap) for x, cap in zip(zip(m.facturado, m.rips, m.cant_fact, m.cant_rips), m.mo == "CAPITA")]
    return m.rename(columns={"k": nivel, "mo": "modalidad_"}).sort_values("dif", key=abs, ascending=False).reset_index(drop=True)

def tri_eje(mes=None, nivel="servicio"):
    """Facturación vs RIPS vs Costos por servicio (PARAM SERVICIO), sede o CUPS."""
    w, p = _w(mes)
    f = df(f"SELECT {nivel} k, SUM(valor) facturado, SUM(cantidad) cant_fact FROM fact{w} GROUP BY 1", p)
    epr = set(df("SELECT DISTINCT eps FROM rips_factura" + w, p).eps.map(norm))
    fcub = df(f"SELECT {nivel} k, eps_nombre e, SUM(valor) fact_cub FROM fact{w} GROUP BY 1,2", p); fcub = fcub[fcub.e.map(norm).isin(epr)].drop(columns="e")
    c = df(f"SELECT {nivel} k, SUM(valor) costos, SUM(cantidad) cant_costo FROM costo{w} GROUP BY 1", p)
    r = rips_h(mes)
    r = r.groupby(nivel, as_index=False).agg(rips=("valor", "sum"), cant_rips=("cantidad", "sum")).rename(columns={nivel: "k"}) if not r.empty else pd.DataFrame(columns=["k", "rips", "cant_rips"])
    for t in (f, r, c, fcub): t["k"] = t.k.map(norm) if nivel != "cups" else t.k.astype(str).str.strip()
    m = f.groupby("k", as_index=False).sum().merge(fcub.groupby("k", as_index=False).sum(), on="k", how="left")
    for t in (r, c): m = m.merge(t.groupby("k", as_index=False).sum(), on="k", how="outer")
    m = m.fillna(0)
    m["dif_fact_rips"] = m.facturado - m.rips; m["pct_dif"] = _pct(m.dif_fact_rips, m.facturado)
    m["margen"] = m.facturado - m.costos; m["pct_margen"] = _pct(m.margen, m.facturado)
    m["tarifa_fact"] = _pct(m.facturado, m.cant_fact); m["costo_unitario"] = _pct(m.costos, m.cant_costo)
    def alerta(r):
        a = []; hay_r = r.rips > 0 or r.cant_rips > 0
        if r.fact_cub > 0 and not hay_r: a.append("Facturado sin RIPS")
        if hay_r and r.facturado == 0: a.append("RIPS sin facturación")
        if r.facturado > 0 and r.rips > 0 and abs(r.pct_dif) > UMBRAL_ALERTA: a.append("Brecha F-R > 5%")
        if r.facturado > 0 and r.cant_rips > 0 and r.rips == 0 and r.cant_fact > 0 and abs(r.cant_fact - r.cant_rips) / r.cant_fact > UMBRAL_ALERTA: a.append("Eventos F≠R > 5%")
        if r.costos > r.facturado > 0: a.append("Costo > facturado (pérdida)")
        if r.costos > 0 and r.facturado == 0: a.append("Costo sin facturación")
        if r.costo_unitario > r.tarifa_fact > 0: a.append("Costo unitario > tarifa")
        return "; ".join(a) or "OK"
    m["alertas"] = m.apply(alerta, axis=1)
    return m.rename(columns={"k": nivel}).sort_values("facturado", ascending=False).reset_index(drop=True)

def radicacion(mes=None):
    """Factura a factura: Software 360 (FACTURA NRO) vs RIPS (FEV): existencia, valor, contrato, sede y validación XML/JSON."""
    w, p = _w(mes)
    f = df(f"SELECT mes, factura, eps_nombre eps, contrato, modalidad, sede, n_contratos, valor valor_fact, lineas FROM factura{w}", p)
    ra = rips_fact_h(); rs = rips_fact_h(mes)
    f["fk"] = _fk(f.factura)
    if ra.empty:
        f["estado"] = np.where(f.n_contratos > 1, "MULTI CONTRATO", "RIPS NO CARGADO")
        return f.drop(columns="fk").sort_values("valor_fact", ascending=False).reset_index(drop=True)
    ra["fk"] = _fk(ra.factura); rs["fk"] = _fk(rs.factura)
    ra = ra.drop_duplicates("fk").rename(columns={"valor_fev": "valor_rips", "contrato": "contrato_rips", "sede": "sede_rips"})
    m = f.merge(ra[["fk", "factura_rips", "valor_rips", "contrato_rips", "sede_rips", "validacion"]], on="fk", how="left")
    m["dif"] = m.valor_fact - m.valor_rips; tol = np.maximum(1.0, 0.001 * m.valor_fact)
    cd = m.contrato_rips.notna() & (m.contrato_rips.map(norm) != m.contrato.map(norm)); sd = m.sede_rips.notna() & (m.sede_rips.map(norm) != m.sede.map(norm))
    pares = set(zip(ra.eps.map(norm), ra.modalidad.map(norm))); sin_eps = pd.Series([(a, b) not in pares for a, b in zip(m.eps.map(norm), m.modalidad.map(norm))], index=m.index)
    m["estado"] = np.select([sin_eps & m.valor_rips.isna(), m.valor_rips.isna(), m.dif.abs() > tol, cd, sd, m.n_contratos > 1],
                            ["EPS/MODALIDAD SIN RIPS CARGADO", "SIN RIPS", "VALOR DIFIERE", "CONTRATO DISTINTO", "SEDE DISTINTA", "MULTI CONTRATO"], "OK")
    sinf = rs[~rs.fk.isin(set(_fk(df("SELECT factura FROM factura").factura)))].rename(columns={"valor_fev": "valor_rips", "contrato": "contrato_rips", "sede": "sede_rips"})
    sinf = sinf.assign(estado=np.where(sinf.modalidad.map(norm) == "CAPITA", "CÁPITA: FEV GLOBAL (VALIDAR VS CONTRATO)", "RIPS SIN FACTURA EN FACTURACIÓN"), dif=-sinf.valor_rips, eps=sinf.eps, contrato=sinf.contrato_rips, sede=sinf.sede_rips)
    cols = ["mes", "factura", "factura_rips", "eps", "contrato", "modalidad", "sede", "lineas", "valor_fact", "valor_rips", "dif", "validacion", "contrato_rips", "estado"]
    out = pd.concat([m, sinf], ignore_index=True).reindex(columns=cols)
    return out.sort_values("dif", key=abs, ascending=False, na_position="last").reset_index(drop=True)

def hallazgos(mes=None):
    w, p = _w(mes)
    h = df(f"SELECT mes, regla, contrato, SUM(lineas) lineas, SUM(valor) valor FROM hallazgo{w} GROUP BY mes,regla,contrato", p)
    h.insert(2, "descripcion", h.regla.map(REGLAS))
    return h.sort_values("valor", ascending=False).reset_index(drop=True)

def contratos(mes=None):
    """Comportamiento de contratos: modalidad (PARAM CONTRATO), facturado vs RIPS (FEV), ticket, participación y hallazgos."""
    w, p = _w(mes)
    f = df(f"SELECT contrato, modalidad, SUM(valor) facturado, SUM(cantidad) cantidad, SUM(lineas) lineas FROM fact{w} GROUP BY contrato, modalidad", p); f["k"] = f.contrato.map(norm)
    r = rips_fact_h(mes)
    r = r.groupby("contrato", as_index=False).valor_fev.sum().rename(columns={"valor_fev": "rips"}) if not r.empty else pd.DataFrame(columns=["contrato", "rips"])
    r["k"] = r.contrato.map(norm)
    h = df(f"SELECT contrato, SUM(lineas) lineas_hallazgo, SUM(valor) valor_hallazgo FROM hallazgo{w} GROUP BY contrato", p); h["k"] = h.contrato.map(norm)
    m = f.merge(r[["k", "rips"]].groupby("k").sum().reset_index(), on="k", how="left").merge(h[["k", "lineas_hallazgo", "valor_hallazgo"]].groupby("k").sum().reset_index(), on="k", how="left").drop(columns="k")
    sin = m.rips.isna(); m["rips"] = m.rips.fillna(0)
    m["dif_fact_rips"] = m.facturado - m.rips; m["pct_dif"] = _pct(m.dif_fact_rips, m.facturado)
    m["estado"] = [("SIN RIPS" if s else semaforo(x)) for s, x in zip(sin, m.pct_dif)]
    m["pct_participacion"] = _pct(m.facturado, [m.facturado.sum()] * len(m)); m["ticket_linea"] = _pct(m.facturado, m.lineas)
    m["pct_hallazgo"] = _pct(m.valor_hallazgo.fillna(0), m.facturado)
    return m.sort_values("facturado", ascending=False).reset_index(drop=True)

def notas_vs_fact(mes=None):
    """Nota técnica EPS (EPS + modalidad + CUPS) vs facturación, RIPS y costo unitario."""
    n = df("SELECT * FROM nota")
    if n.empty: return n
    w, p = _w(mes)
    f = df(f"SELECT eps_nombre eps, modalidad, cups, SUM(cantidad) cant_fact, SUM(valor) valor_fact FROM fact{w} GROUP BY 1,2,3", p)
    if f.empty: return pd.DataFrame()
    r = rips_h(mes); r = r.groupby(["eps", "modalidad", "cups"], as_index=False).agg(cant_rips=("cantidad", "sum"), valor_rips=("valor", "sum")) if not r.empty else pd.DataFrame(columns=["eps", "modalidad", "cups", "cant_rips", "valor_rips"])
    w3, p3 = _w(mes, "cups<>''"); c = df(f"SELECT cups, SUM(cantidad) cant_costo, SUM(valor) costo FROM costo{w3} GROUP BY cups", p3)
    for t in (n, f, r): t["en"] = t.eps.map(norm); t["mo"] = t.modalidad.map(norm); t["cups"] = t.cups.astype(str).str.strip()
    ks = ["en", "mo", "cups"]; nm = n.drop(columns="modalidad").rename(columns={"eps": "eps_nt"})
    m = nm.merge(f.drop(columns=["eps", "modalidad"]), on=ks, how="left").merge(r.drop(columns=["eps", "modalidad"]), on=ks, how="left").merge(c, on="cups", how="left")
    m["tarifa_fact"] = np.where(m.cant_fact > 0, m.valor_fact / m.cant_fact.replace(0, np.nan), np.nan)
    m["pct_dif_tarifa"] = np.where(m.tarifa > 0, m.tarifa_fact / m.tarifa.replace(0, np.nan) - 1, np.nan)
    ev = np.where(m.cant_rips > 0, m.cant_rips, m.cant_fact)
    m["pct_dif_eventos"] = np.where(m.eventos_mes > 0, ev / m.eventos_mes.replace(0, np.nan) - 1, np.nan)
    m["costo_unitario"] = np.where(m.cant_costo > 0, m.costo / m.cant_costo.replace(0, np.nan), np.nan)
    m["margen_unit"] = np.where(m.costo_unitario.notna(), m.tarifa.where(m.tarifa > 0, m.tarifa_fact) - m.costo_unitario, np.nan)
    def est(r):
        a = []
        if pd.isna(r.valor_fact): return "PACTADO SIN FACTURAR"
        if r.tarifa > 0 and r.pct_dif_tarifa > 0.005: a.append("TARIFA FACTURADA > PACTADA")
        if r.tarifa > 0 and r.pct_dif_tarifa < -0.005: a.append("Tarifa facturada < pactada")
        if r.tope_valor > 0 and r.valor_fact > r.tope_valor: a.append("EXCEDE TOPE VALOR")
        if r.eventos_mes > 0 and r.pct_dif_eventos > NT_EXCESO: a.append("EXCEDE EVENTOS NT")
        if r.eventos_mes > 0 and r.pct_dif_eventos < NT_BAJA: a.append("Baja ejecución vs NT")
        if pd.isna(r.valor_rips) and pd.isna(r.cant_rips): a.append("Sin RIPS")
        if r.margen_unit < 0: a.append("Costo unitario > tarifa")
        return "; ".join(a) or "OK"
    m["estado"] = m.apply(est, axis=1)
    pact = set(zip(m.en, m.mo, m.cups)); ens = set(m.en)
    sn = f[f.en.isin(ens) & ~pd.Series(list(zip(f.en, f.mo, f.cups)), index=f.index).isin(pact)].assign(estado="FACTURADO SIN NOTA TÉCNICA")
    m = pd.concat([m.rename(columns={"eps_nt": "eps"}), sn], ignore_index=True)
    cols = ["eps", "modalidad", "cups", "descripcion", "categoria", "tarifa", "tarifa_fact", "pct_dif_tarifa", "eventos_mes", "cant_fact", "cant_rips", "pct_dif_eventos",
            "tope_valor", "valor_fact", "valor_rips", "costo_unitario", "margen_unit", "estado"]
    m["modalidad"] = m.mo.str.upper() if "mo" in m else ""
    return m.reindex(columns=cols).sort_values("valor_fact", ascending=False, na_position="last").reset_index(drop=True)

# ───────── validación de la homologación: ¿las llaves comunes cruzan? ─────────
def cobertura(mes=None):
    """Para cada llave común mide cuántos valores de una fuente existen en la otra y lista los que no cruzan."""
    w, p = _w(mes); f = lambda col, tab="fact", v=True: df(f"SELECT {col} k, {'SUM(valor)' if v else '0'} v FROM {tab}{w} GROUP BY 1", p)
    ri, rf = rips_h(mes), df(f"SELECT factura, contrato, sede, eps, modalidad, valor_fev FROM rips_factura{w}", p)
    fac = df(f"SELECT factura k, valor v FROM factura{w}", p)
    notas, cost = df("SELECT eps k, 0 v FROM nota GROUP BY 1"), None
    pc, ps = df("SELECT contrato k FROM param_contrato"), df("SELECT servicio k FROM param_servicio")
    filas = []
    def par(llave, a_n, a, b_n, b, normf=norm, info=False):
        if a.empty: return
        a = a.assign(n=a.k.map(normf)).groupby("n", as_index=False).agg(k=("k", "first"), v=("v", "sum"))
        bs = set(b.k.map(normf)) if len(b) else set(); ok = a.n.isin(bs)
        mal = a[~ok].sort_values("v", ascending=False).head(5).k.tolist()
        filas.append(dict(llave=llave, origen=a_n, destino=b_n, valores_origen=len(a), con_cruce=int(ok.sum()), pct_cruce=ok.mean(),
                          pct_valor_cruza=(a.v[ok].sum() / a.v.sum()) if a.v.sum() else ok.mean(), sin_cruce=len(a) - int(ok.sum()),
                          ejemplos_sin_cruce=" | ".join(map(str, mal)), estado=("INFO (cobertura de la carga)" if info and ok.mean() < .98 else "OK" if ok.mean() >= .98 else "ALERTA" if ok.mean() >= .9 else "CRÍTICO")))
    fe, fc, fs, fsv, fcu = f("eps_nombre"), f("contrato"), f("sede"), f("servicio"), f("cups")
    if not ri.empty:
        rv = lambda c: ri.groupby(c, as_index=False).valor.sum().rename(columns={c: "k", "valor": "v"})
        par("EPS", "Facturación", fe, "RIPS", rv("eps"), info=True); par("EPS", "Notas técnicas", notas, "Facturación", fe, info=True); par("EPS", "RIPS", rv("eps"), "Facturación", fe)
        fem = df(f"SELECT eps_nombre || ' | ' || modalidad k, SUM(valor) v FROM fact{w} GROUP BY 1", p); rem = ri.assign(k=ri.eps + " | " + ri.modalidad).groupby("k", as_index=False).valor.sum().rename(columns={"valor": "v"})
        par("EPS + MODALIDAD", "Facturación", fem, "RIPS", rem, info=True)
        par("CONTRATO", "Facturación", fc, "RIPS", rv("contrato"), info=True); par("CONTRATO", "RIPS", rv("contrato"), "Facturación", fc)
        par("SEDE", "RIPS", rv("sede"), "Facturación", fs); par("SEDE", "Facturación", fs, "RIPS", rv("sede"), info=True)
        ak = lambda s: "".join(ch for ch in str(s).upper() if ch.isalnum()); rfe = rf[rf.modalidad.map(norm) != "CAPITA"] if "modalidad" in rf else rf
        fac_r = df(f"SELECT factura k, valor v FROM factura{w}", p)
        par("FACTURA EVENTO (radicación)", "Facturación", fac_r[fac_r.k.map(ak).isin(set(rfe.factura.map(ak)) | set(fac_r.k.map(ak)))] if False else fac_r, "RIPS", rf.rename(columns={"factura": "k", "valor_fev": "v"})[["k", "v"]], ak, info=True)
        par("FACTURA EVENTO (radicación)", "RIPS", rfe.rename(columns={"factura": "k", "valor_fev": "v"})[["k", "v"]], "Facturación", fac, ak)
        par("CUPS", "RIPS", rv("cups"), "Facturación", fcu, lambda s: str(s).strip()); par("CUPS", "Facturación", fcu, "RIPS", rv("cups"), lambda s: str(s).strip(), info=True)
        par("SERVICIO (por CUPS)", "RIPS", rv("servicio").assign(k=lambda d: d.k.str.replace("SIN HOMOLOGAR · ", "SIN HOMOLOGAR ")), "Facturación", fsv)
    par("CONTRATO", "Facturación", fc, "PARAM CONTRATO", pc); par("SERVICIO", "Facturación", fsv, "PARAM SERVICIO", ps)
    c_s, c_v, c_c = f("sede", "costo"), f("servicio", "costo"), f("cups", "costo")
    par("SEDE", "Costos", c_s, "Facturación", fs); par("SERVICIO", "Costos", c_v, "PARAM SERVICIO", ps); par("SERVICIO", "Costos", c_v, "Facturación", fsv)
    par("CUPS", "Costos", c_c[c_c.k != ""], "Facturación", fcu, lambda s: str(s).strip())
    nt = df("SELECT cups k, 0 v FROM nota GROUP BY 1"); par("CUPS", "Notas técnicas", nt, "Facturación", fcu, lambda s: str(s).strip(), info=True)
    return pd.DataFrame(filas)

# ───────── informes parametrizados (hojas INFORME 1–7 del libro de parámetros) ─────────
INFORMES = {"1_general": "INFORME 1 GENERAL (detalle por factura/actividad)", "2_cups": "INFORME 2 CUPS", "3_servicio": "INFORME 3 SERVICIO x Sede",
            "4_contrato_sede": "INFORME 4 CONTRATO x Sede", "5_paciente": "INFORME 5 PACIENTE (NUMERO IDE) x Sede", "6_profesional": "INFORME 6 PROFESIONAL x Sede",
            "7_especialidad": "INFORME 7 ESPECIALIDAD x Sede", "7_agenda": "INFORME 7 AGENDA x Sede", "8_ambito": "Complemento: ÁMBITO x Sede"}
_DIM = {"3": ("servicio", "SERVICIO"), "4": ("contrato", "CONTRATO"), "5": ("paciente", "NUMERO IDE."), "6": ("profesional", "PROFESIONAL"),
        "7e": ("especialidad", "ESPECIALIDAD"), "7a": ("agenda", "AGENDA"), "8": ("ambito", "AMBITO")}

def informe(nombre, mes=None, modalidad=None):
    cond = (["mes=?"] if mes else []) + (["modalidad=?"] if modalidad else []); p = tuple(x for x in (mes, modalidad) if x)
    w = " WHERE " + " AND ".join(cond) if cond else ""
    if nombre == "1_general":
        d = df(f"""SELECT mes, fecha "FECHA DE FACTURACION", servicio SERVICIO, agenda AGENDA, regulado REGULADO, cod_proc "COD. PROCEDIMIENTO", cod_med "COD. MEDICAMENTO",
                   actividad ACTIVIDAD, SUM(cantidad) CANTIDAD, diagnostico "NOMBRE DIAGNÓSTICO", factura "FACTURA NRO", valor_actividad "VALOR ACTIVIDAD", SUM(valor) "VALOR TOTAL",
                   tipo_paciente "TIPO DE PACIENTE", regimen "REGIMEN DEL PACIENTE" FROM linea{w} GROUP BY 2,3,4,5,6,7,8,10,11,12,14,15 ORDER BY fecha""", p)
        return d.drop(columns="mes")
    if nombre == "2_cups":
        return df(f"""SELECT mes MES, servicio SERVICIO, cod_proc "COD. PROCEDIMIENTO", cod_med "COD. MEDICAMENTO", actividad ACTIVIDAD, SUM(cantidad) CANTIDAD,
                      valor_actividad "VALOR ACTIVIDAD", SUM(valor) "VALOR TOTAL", diagnostico "NOMBRE DIAGNÓSTICO" FROM linea{w}
                      GROUP BY 1,2,3,4,5,7,9 ORDER BY 1, "VALOR TOTAL" DESC""", p)
    key = nombre.split("_")[0] + ("e" if "especialidad" in nombre else "a" if "agenda" in nombre else "")
    dim, etiqueta = _DIM[key]; w2 = " WHERE " + " AND ".join(["dim=?"] + cond); 
    t = df(f"SELECT mes, item, sede, valor FROM dim{w2}", (dim,) + p)
    if t.empty: return t
    pv = t.pivot_table(index=["mes", "item"], columns="sede", values="valor", aggfunc="sum", fill_value=0)
    pv = pv.reindex(columns=SEDES + [c for c in pv.columns if c not in SEDES], fill_value=0)
    pv["Total general"] = pv.sum(axis=1); pv.columns.name = None
    return pv.reset_index().rename(columns={"mes": "MES", "item": etiqueta}).sort_values(["MES", "Total general"], ascending=[True, False])

def comparativos(mes=None):
    """Conjuntos de datos para los gráficos comparativos del dashboard."""
    w, p = _w(mes)
    return dict(
        sede=tri_eje(mes, "sede")[["sede", "facturado", "rips", "costos", "margen", "pct_margen"]],
        servicio=tri_eje(mes, "servicio").head(12)[["servicio", "facturado", "rips", "costos", "margen", "pct_margen", "cant_fact", "cant_rips"]],
        contratos=contratos(mes).head(10)[["contrato", "modalidad", "facturado", "rips", "pct_participacion"]],
        modalidad_mes=df("SELECT mes, modalidad, SUM(valor) valor FROM fact GROUP BY 1,2 ORDER BY 1"),
        mensual=mensual())

def comparativo_mensual(nivel="servicio"):
    """Facturación por mes y nivel, con variación absoluta y % del último mes vs el anterior."""
    col = {"servicio": "servicio", "sede": "sede", "contrato": "contrato", "eps": "eps_nombre", "modalidad": "modalidad", "ambito": "ambito"}[nivel]
    f = df(f"SELECT mes, {col} k, SUM(valor) v FROM fact GROUP BY 1,2")
    if f.empty: return f
    pv = f.pivot_table(index="k", columns="mes", values="v", aggfunc="sum", fill_value=0); ms = list(pv.columns); pv["total"] = pv.sum(axis=1)
    if len(ms) >= 2:
        pv["var_abs"] = pv[ms[-1]] - pv[ms[-2]]; pv["pct_var"] = (pv.var_abs / pv[ms[-2]].replace(0, np.nan)).fillna(0)
    pv = pv.sort_values("total", ascending=False).reset_index().rename(columns={"k": nivel}); pv.columns.name = None
    return pv

def recomendaciones(mes=None):
    R = []
    fr = fact_vs_rips(mes, "eps")
    if fr.empty: R.append(("info", "No hay RIPS cargado para el periodo: cargue el consolidado de RIPS para habilitar la conciliación y la validación de radicación."))
    else:
        for r in fr[fr.estado.isin(["CRÍTICO", "ALERTA", "FACTURADO SIN RIPS", "RIPS SIN FACTURA"])].head(3).itertuples():
            det = (f"eventos 360 {r.cant_fact:,.0f} vs RIPS {r.cant_rips:,.0f} (cápita se audita por eventos)" if r.comparado_por.startswith("EVENTOS") else f"diferencia ${r.dif:,.0f}")
            R.append(("rojo" if r.estado == "CRÍTICO" else "naranja", f"EPS {r.eps} · {r.modalidad_}: {det} → {r.estado}. Conciliar antes de radicar."))
    cv = cobertura(mes)
    for r in cv[cv.estado.isin(["ALERTA", "CRÍTICO"])].head(3).itertuples() if not cv.empty else []:
        R.append(("naranja", f"Llave {r.llave}: {r.origen} → {r.destino} solo cruza {r.pct_cruce:.0%} ({r.sin_cruce} valores sin cruce). Homologar nombres/códigos antes de auditar."))
    te = tri_eje(mes)
    for r in te[(te.costos > te.facturado) & (te.facturado > 0)].head(3).itertuples():
        R.append(("naranja", f"Servicio {r.servicio}: costo (${r.costos:,.0f}) supera lo facturado (${r.facturado:,.0f}). Revisar tarifas, productividad y costos unitarios (ABC)."))
    h = hallazgos(mes).groupby(["regla", "descripcion"], as_index=False)[["lineas", "valor"]].sum().sort_values("valor", ascending=False).head(3)
    for r in h.itertuples(): R.append(("naranja", f"{r.regla} – {r.descripcion}: {r.lineas:,.0f} líneas por ${r.valor:,.0f}. Corregir en origen (Software 360) antes del cierre."))
    ra = radicacion(mes)
    if not ra.empty:
        for e in ("SIN RIPS", "VALOR DIFIERE", "CONTRATO DISTINTO", "SEDE DISTINTA", "RIPS SIN FACTURA EN FACTURACIÓN"):
            c = (ra.estado == e).sum()
            if c: R.append(("rojo", f"{c:,} facturas con estado '{e}': riesgo de glosa o radicación incorrecta; depurar y re-radicar."))
    nt = notas_vs_fact(mes)
    if not nt.empty:
        for e in ("TARIFA FACTURADA > PACTADA", "FACTURADO SIN NOTA TÉCNICA", "EXCEDE EVENTOS NT", "EXCEDE TOPE"):
            c = nt.estado.str.contains(e, regex=False).sum()
            if c: R.append(("rojo" if "TARIFA" in e else "naranja", f"{c:,} registros con '{e}' frente a la nota técnica de la EPS: validar tarifa/eventos/tope contractual."))
    return [dict(nivel=n, texto=t) for n, t in R] or [dict(nivel="verde", texto="Sin discrepancias relevantes con los datos cargados.")]

def resumen(mes=None):
    m = mensual(); s = m if not mes else m[m.mes == mes]
    fa, ri, co = s.facturado.sum(), s.rips.sum(), s.costos.sum(); w, p = _w(mes)
    return dict(
        kpis=dict(facturado=fa, rips=ri, costos=co, brecha_fact_rips=s.brecha_fact_rips.sum(), pct_brecha=float(_pct([s.brecha_fact_rips.sum()], [s.fact_evento.sum()])[0]),
                  cap_fev=s.cap_fev.sum(), cap_360=s.cap_360.sum(),
                  margen=fa - co, lineas_hallazgo=int(s.lineas_hallazgo.sum()), valor_hallazgo=s.valor_hallazgo.sum(), lineas=int(s.lineas.sum())),
        mensual=m.to_dict("records"),
        ambito=df(f"SELECT ambito, SUM(valor) valor, SUM(cantidad) cantidad FROM fact{w} GROUP BY ambito ORDER BY valor DESC", p).to_dict("records"),
        recomendaciones=recomendaciones(mes))

# ───────── Resumen desagregado por empresa (EPS) ─────────
def resumen_empresa(mes=None):
    """Una fila por EPS: facturado total, cápita/evento, líneas, facturas, contratos, RIPS (valor FEV), brecha Fact-RIPS y hallazgos.
    Cuadra con `resumen` (misma base `fact`). Los costos no traen EPS, por lo que no se reparten."""
    w, p = _w(mes)
    f = df(f"""SELECT eps_nombre empresa, SUM(valor) facturado, SUM(cantidad) cantidad, SUM(lineas) lineas, COUNT(DISTINCT contrato) contratos,
               SUM(CASE WHEN modalidad='CAPITA' THEN valor ELSE 0 END) fact_capita,
               SUM(CASE WHEN modalidad='EVENTO' THEN valor ELSE 0 END) fact_evento,
               SUM(CASE WHEN modalidad NOT IN ('CAPITA','EVENTO') THEN valor ELSE 0 END) fact_otras
               FROM fact{w} GROUP BY 1""", p)
    if f.empty: return f
    fa = df(f"SELECT eps_nombre empresa, COUNT(DISTINCT factura) facturas FROM factura{w} GROUP BY 1", p)
    r = df(f"SELECT factura, eps, valor_fev FROM rips_factura{w}", p)
    ev = df(f"SELECT factura, eps_nombre empresa, modalidad, valor FROM factura{w}", p)
    m = f.merge(fa, on="empresa", how="left")
    if not r.empty and not ev.empty:
        ev["fk"] = _fk(ev.factura); r["fk"] = _fk(r.factura); rr = r.drop_duplicates("fk").set_index("fk").valor_fev
        ev["fev"] = ev.fk.map(rr)
        ev = ev[(ev.modalidad == "EVENTO") & ev.fev.notna()]
        g = ev.groupby("empresa").agg(fact_evento_con_rips=("valor", "sum"), rips_fev=("fev", "sum")).reset_index()
        m = m.merge(g, on="empresa", how="left")
    for k in ("fact_evento_con_rips", "rips_fev"):
        if k not in m: m[k] = 0.0
    m[["fact_evento_con_rips", "rips_fev"]] = m[["fact_evento_con_rips", "rips_fev"]].fillna(0.0)
    m["brecha_fact_rips"] = m.fact_evento_con_rips - m.rips_fev
    m["pct_brecha"] = _pct(m.brecha_fact_rips, m.fact_evento_con_rips)
    m["estado"] = [("SOLO CÁPITA/OTRAS" if e == 0 else "SIN RIPS" if a == 0 else semaforo(b)) for e, a, b in zip(m.fact_evento, m.rips_fev, m.pct_brecha)]
    h = df(f"SELECT c.eps_nombre empresa, SUM(h.lineas) lineas_hallazgo, SUM(h.valor) valor_hallazgo FROM hallazgo h "
           f"JOIN (SELECT DISTINCT contrato, eps_nombre FROM fact) c ON c.contrato=h.contrato {w.replace('mes', 'h.mes')} GROUP BY 1", p)
    m = m.merge(h, on="empresa", how="left").fillna({"lineas_hallazgo": 0, "valor_hallazgo": 0})
    m["pct_participacion"] = _pct(m.facturado, [m.facturado.sum()] * len(m))
    return m.sort_values("facturado", ascending=False).reset_index(drop=True)

def resumen_empresa_mensual(mes=None):
    """EPS × mes (para la matriz de la pestaña Resumen por empresa)."""
    return df("SELECT eps_nombre empresa, mes, SUM(valor) facturado FROM fact GROUP BY 1,2 ORDER BY 1,2")

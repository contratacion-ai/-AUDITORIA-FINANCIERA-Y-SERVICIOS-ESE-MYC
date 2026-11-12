"""Hoja MEDICAMENTOS, INSUMOS Y SUPLEMENTOS por sede: facturación, clasificación ABC, homogeneidad de precios,
consumo en contratos cápita y cruce con la dispensación/consumo de farmacia.

Fuente principal: facturación por ámbito de Software 360 (líneas cuyo SERVICIO es MEDICAMENTOS, MATERIALES E INSUMOS MEDICOS
o SUPLEMENTOS DIETARIOS, o que traen COD. MEDICAMENTO sin COD. PROCEDIMIENTO; ver `loaders.clase_item`).
Fuente opcional: archivo de farmacia (dispensación / salidas de bodega por sede con costo), tipo de carga «farmacia».
"""
import numpy as np, pandas as pd
from .db import df, SEDES, TOL_PRECIO, TOL_DISPENSACION, ABC_A, ABC_B

CLASES = ("MEDICAMENTO", "INSUMO", "SUPLEMENTO")

def _w(mes=None, sede=None, clase=None, eps=None):
    cond, p = [], []
    for col, v in (("mes", mes), ("sede", sede), ("clase", clase), ("eps_nombre", eps)):
        if v: cond.append(f"{col}=?"); p.append(v)
    return (" WHERE " + " AND ".join(cond) if cond else ""), tuple(p)

def base(mes=None, sede=None, clase=None, eps=None):
    w, p = _w(mes, sede, clase, eps)
    return df(f"SELECT * FROM medins{w}", p)

def _abc(v):
    """Clasificación ABC (Pareto) por valor: A hasta el 80 % acumulado, B hasta el 95 %, C el resto."""
    tot = v.sum()
    if not tot: return pd.Series("C", index=v.index)
    acum = v.sort_values(ascending=False).cumsum() / tot
    prev = acum.shift(fill_value=0)                       # el ítem que cruza el corte queda en la clase de abajo
    return pd.Series(np.select([prev < ABC_A, prev < ABC_B], ["A", "B"], "C"), index=acum.index).reindex(v.index)

def items(mes=None, sede=None, clase=None, eps=None):
    """Una fila por código: cantidad, valor, valor unitario promedio/mín/máx, sedes, EPS, % del total, ABC y alertas."""
    d = base(mes, sede, clase, eps)
    if d.empty: return d
    d["vu_min"] = d.vu_min.replace(0, np.nan)
    g = d.groupby(["clase", "codigo"]).agg(descripcion=("descripcion", "first"), cantidad=("cantidad", "sum"), valor=("valor", "sum"),
                                           lineas=("lineas", "sum"), vu_min=("vu_min", "min"), vu_max=("vu_max", "max"), cant_max_linea=("cant_max", "max"),
                                           lineas_cero=("lineas_cero", "sum"), sedes=("sede", "nunique"), eps=("eps_nombre", "nunique")).reset_index()
    g["valor_unitario"] = (g.valor / g.cantidad.replace(0, np.nan)).fillna(0)
    g["var_precio"] = (g.vu_max / g.vu_min - 1).fillna(0)
    g["pct_valor"] = g.valor / g.valor.sum() if g.valor.sum() else 0
    g["abc"] = _abc(g.valor)
    cap = d[d.modalidad == "CAPITA"].groupby("codigo").valor.sum()
    g["valor_capita"] = g.codigo.map(cap).fillna(0)
    def alerta(r):
        a = []
        if r.codigo == "SIN CODIGO": a.append("SIN CÓDIGO")
        if r.var_precio > TOL_PRECIO: a.append(f"PRECIO NO HOMOGÉNEO (> {TOL_PRECIO:.0%})")
        if r.lineas_cero: a.append("Líneas en cero (validar si es cápita/paquete)")
        if r.cantidad <= 0: a.append("CANTIDAD ≤ 0")
        return "; ".join(a) or "OK"
    g["estado"] = g.apply(alerta, axis=1)
    return g.sort_values("valor", ascending=False).reset_index(drop=True)

def por_sede(mes=None, clase=None, eps=None, metrica="valor"):
    """Ítem × sede (mismo formato que el Informe 3: una columna por sede + Total general)."""
    d = base(mes, None, clase, eps)
    if d.empty: return d
    pv = d.pivot_table(index=["clase", "codigo"], columns="sede", values=metrica, aggfunc="sum", fill_value=0)
    pv = pv.reindex(columns=[s for s in SEDES if s in pv.columns] + [c for c in pv.columns if c not in SEDES])
    pv["Total general"] = pv.sum(axis=1); pv.columns.name = None
    desc = d.groupby("codigo").descripcion.first()
    pv = pv.reset_index(); pv.insert(2, "descripcion", pv.codigo.map(desc))
    return pv.sort_values("Total general", ascending=False).reset_index(drop=True)

def precios_sede(mes=None, clase=None):
    """Valor unitario promedio de cada código por sede y diferencia contra la sede más barata (para homologar tarifas internas)."""
    d = base(mes, None, clase)
    if d.empty: return d
    g = d.groupby(["codigo", "sede"]).agg(valor=("valor", "sum"), cantidad=("cantidad", "sum")).reset_index()
    g["vu"] = g.valor / g.cantidad.replace(0, np.nan)
    pv = g.pivot_table(index="codigo", columns="sede", values="vu", aggfunc="first")
    pv = pv[pv.notna().sum(axis=1) > 1]
    if pv.empty: return pd.DataFrame(columns=["codigo", "descripcion", "vu_min", "vu_max", "var_precio", "estado"])
    out = pv.copy(); out.columns.name = None
    out["vu_min"] = pv.min(axis=1); out["vu_max"] = pv.max(axis=1); out["var_precio"] = out.vu_max / out.vu_min - 1
    out["sede_mas_cara"] = pv.idxmax(axis=1); out["sede_mas_barata"] = pv.idxmin(axis=1)
    out["estado"] = np.where(out.var_precio > TOL_PRECIO, f"PRECIO NO HOMOGÉNEO (> {TOL_PRECIO:.0%})", "OK")
    out = out.reset_index(); out.insert(1, "descripcion", out.codigo.map(d.groupby("codigo").descripcion.first()))
    return out.sort_values("var_precio", ascending=False).reset_index(drop=True)

def por_eps(mes=None, sede=None):
    """Medicamentos e insumos por EPS y modalidad: cuánto se consume dentro de contratos cápita (costo absorbido por la ESE)."""
    d = base(mes, sede)
    if d.empty: return d
    g = d.pivot_table(index=["eps_nombre", "modalidad"], columns="clase", values="valor", aggfunc="sum", fill_value=0).reset_index()
    g.columns.name = None
    for c in CLASES:
        if c not in g: g[c] = 0.0
    g["total"] = g[list(CLASES)].sum(axis=1)
    return g.rename(columns={"eps_nombre": "eps"}).sort_values("total", ascending=False).reset_index(drop=True)

def resumen(mes=None, sede=None, clase=None, eps=None):
    d = base(mes, sede, clase, eps)
    if d.empty: return dict(kpis={}, vacio=True)
    w, p = _w(mes, sede, None, eps)
    fact = df(f"SELECT SUM(valor) v FROM fact{w}", p).v.iloc[0] or 0
    it = items(mes, sede, clase, eps)
    k = dict(valor=d.valor.sum(), cantidad=d.cantidad.sum(), lineas=int(d.lineas.sum()), codigos=int(d.codigo.nunique()),
             medicamentos=d.loc[d.clase == "MEDICAMENTO", "valor"].sum(), insumos=d.loc[d.clase == "INSUMO", "valor"].sum(),
             suplementos=d.loc[d.clase == "SUPLEMENTO", "valor"].sum(), pct_facturado=(d.valor.sum() / fact) if fact else 0,
             valor_capita=d.loc[d.modalidad == "CAPITA", "valor"].sum(), items_a=int((it.abc == "A").sum()),
             alertas_precio=int(it.estado.str.contains("PRECIO").sum()), sin_codigo=float(d.loc[d.codigo == "SIN CODIGO", "valor"].sum()))
    sc = d.groupby(["sede", "clase"]).agg(valor=("valor", "sum"), cantidad=("cantidad", "sum"), codigos=("codigo", "nunique")).reset_index()
    fs = df(f"SELECT sede, SUM(valor) facturado FROM fact{_w(mes, None, None, eps)[0]} GROUP BY sede", _w(mes, None, None, eps)[1])
    ss = d.groupby("sede").agg(valor=("valor", "sum"), codigos=("codigo", "nunique")).reset_index().merge(fs, on="sede", how="left")
    ss["pct_facturado_sede"] = (ss.valor / ss.facturado.replace(0, np.nan)).fillna(0)
    return dict(kpis=k, por_sede_clase=sc.to_dict("records"), por_sede=ss.sort_values("valor", ascending=False).to_dict("records"),
                por_mes=d.groupby(["mes", "clase"]).valor.sum().reset_index().to_dict("records"),
                por_ambito=d.groupby("ambito").valor.sum().reset_index().sort_values("valor", ascending=False).to_dict("records"),
                top=it.head(20)[["clase", "codigo", "descripcion", "cantidad", "valor", "valor_unitario", "abc", "estado"]].to_dict("records"),
                abc=it.groupby("abc").agg(codigos=("codigo", "size"), valor=("valor", "sum")).reset_index().to_dict("records"))

def cruce_farmacia(mes=None, sede=None):
    """Dispensación/consumo de farmacia (costo) vs facturación (precio de venta) por mes, sede y código.

    Los códigos de farmacia (CUM) se llevan al código interno de Software 360 con la tabla de homologación de códigos."""
    w, p = _w(mes, sede)
    f = df(f"SELECT mes, sede, codigo, MAX(descripcion) descripcion, MAX(clase) clase, SUM(cantidad) cant_fact, SUM(valor) valor_fact FROM medins{w} GROUP BY 1,2,3", p)
    fa = df(f"SELECT mes, sede, codigo, MAX(descripcion) desc_farmacia, SUM(cantidad) cant_dispensada, SUM(valor_costo) costo FROM farmacia{w} GROUP BY 1,2,3", p)
    if fa.empty: return pd.DataFrame()
    hc = df("SELECT codigo_rips, codigo_360 FROM hom_codigo")
    if not hc.empty: fa["codigo"] = fa.codigo.map(hc.set_index("codigo_rips").codigo_360).fillna(fa.codigo)
    fa = fa.groupby(["mes", "sede", "codigo"], as_index=False).agg(desc_farmacia=("desc_farmacia", "first"), cant_dispensada=("cant_dispensada", "sum"), costo=("costo", "sum"))
    m = f.merge(fa, on=["mes", "sede", "codigo"], how="outer")
    for c in ("cant_fact", "valor_fact", "cant_dispensada", "costo"): m[c] = m[c].fillna(0.0)
    m["descripcion"] = m.descripcion.fillna(m.desc_farmacia); m["clase"] = m.clase.fillna("")
    m["dif_cantidad"] = m.cant_fact - m.cant_dispensada
    m["pct_dif_cantidad"] = (m.dif_cantidad / m.cant_dispensada.replace(0, np.nan)).fillna(0)
    m["costo_unitario"] = (m.costo / m.cant_dispensada.replace(0, np.nan))
    m["precio_venta"] = (m.valor_fact / m.cant_fact.replace(0, np.nan))
    m["margen"] = m.valor_fact - m.costo_unitario.fillna(0) * m.cant_fact
    m["pct_margen"] = (m.margen / m.valor_fact.replace(0, np.nan)).fillna(0)
    m["valor_no_facturado"] = np.where(m.dif_cantidad < 0, -m.dif_cantidad * m.precio_venta.fillna(m.costo_unitario).fillna(0), 0)
    def est(r):
        a = []
        if r.cant_dispensada > 0 and r.cant_fact == 0: a.append("DISPENSADO SIN FACTURAR")
        elif r.cant_fact > 0 and r.cant_dispensada == 0: a.append("FACTURADO SIN DISPENSACIÓN")
        elif r.cant_dispensada > 0 and r.pct_dif_cantidad < -TOL_DISPENSACION: a.append("FACTURADO < DISPENSADO")
        elif r.cant_dispensada > 0 and r.pct_dif_cantidad > TOL_DISPENSACION: a.append("FACTURADO > DISPENSADO")
        if pd.notna(r.precio_venta) and pd.notna(r.costo_unitario) and r.precio_venta < r.costo_unitario: a.append("PRECIO VENTA < COSTO")
        return "; ".join(a) or "OK"
    m["estado"] = m.apply(est, axis=1)
    cols = ["mes", "sede", "clase", "codigo", "descripcion", "cant_dispensada", "cant_fact", "dif_cantidad", "pct_dif_cantidad", "costo_unitario",
            "precio_venta", "costo", "valor_fact", "margen", "pct_margen", "valor_no_facturado", "estado"]
    return m[cols].sort_values("valor_no_facturado", ascending=False).reset_index(drop=True)

def recomendaciones(mes=None):
    r = resumen(mes)
    if r.get("vacio"): return []
    k, R = r["kpis"], []
    a = r["abc"]; ca = next((x for x in a if x["abc"] == "A"), None)
    if ca: R.append(("info", f"Medicamentos e insumos: {ca['codigos']:,} códigos (clase A) concentran ${ca['valor']:,.0f}. Enfocar control de inventario, "
                             "kardex y negociación de compras en esos ítems."))
    if k["alertas_precio"]: R.append(("naranja", f"{k['alertas_precio']:,} códigos de medicamentos/insumos con valor unitario no homogéneo (> {TOL_PRECIO:.0%}) entre sedes/EPS: "
                                                "unificar el manual tarifario en Software 360."))
    if k["valor_capita"]: R.append(("info", f"${k['valor_capita']:,.0f} en medicamentos/insumos se registraron en contratos CÁPITA: no generan ingreso adicional; "
                                           "monitorear el consumo frente a la UPC pactada."))
    if k["sin_codigo"]: R.append(("naranja", f"${k['sin_codigo']:,.0f} en medicamentos/insumos sin código: no se pueden cruzar con RIPS ni farmacia."))
    cf = cruce_farmacia(mes)
    if not cf.empty:
        nf = cf[cf.estado.str.contains("DISPENSADO SIN FACTURAR|FACTURADO < DISPENSADO")]
        if len(nf): R.append(("rojo", f"{len(nf):,} combinaciones sede-código dispensadas y no facturadas (o facturadas de menos): ${nf.valor_no_facturado.sum():,.0f} "
                                     "de ingreso potencial no cobrado (excluya lo incluido en cápita)."))
        pv = cf[cf.estado.str.contains("PRECIO VENTA < COSTO")]
        if len(pv): R.append(("naranja", f"{len(pv):,} ítems se facturan por debajo de su costo de adquisición: actualizar tarifas."))
    rd = resumen_detalle(mes)
    if rd.get("sin_cum"):
        R.append(("naranja", f"{rd['sin_cum']:,} códigos de medicamentos/insumos de Software 360 no tienen CUM homologado ({rd['pct_valor_sin_cum']:.0%} del valor): "
                             "no se pueden cruzar con el RIPS. Descargue la plantilla de homologación CUM, complétela y cárguela como «códigos»."))
    for e in rd.get("rips_estados", []):
        if e["estado"] in ("MEDICAMENTO FACTURADO SIN RIPS", "CANTIDAD DIFIERE", "CUM DEL RIPS SIN HOMOLOGAR"):
            R.append(("rojo" if "SIN RIPS" in e["estado"] else "naranja", f"Cruce medicamentos 360 ↔ RIPS: {e['n']:,} registros con estado «{e['estado']}». Corregir antes de radicar."))
    return [dict(nivel=n, texto=t, modulo="Medicamentos e insumos") for n, t in R]

# ═════════ v5.1 · detalle por línea (tabla med_det): contrato, código 360, ATC/CUM, usuarios, servicios, RIPS y homologación CUM ═════════
import re
from .loaders import norm

ATC_GRUPOS = {"A": "Tracto alimentario y metabolismo", "B": "Sangre y órganos hematopoyéticos", "C": "Sistema cardiovascular",
              "D": "Dermatológicos", "G": "Genitourinario y hormonas sexuales", "H": "Preparados hormonales sistémicos",
              "J": "Antiinfecciosos para uso sistémico", "L": "Antineoplásicos e inmunomoduladores", "M": "Sistema musculoesquelético",
              "N": "Sistema nervioso", "P": "Antiparasitarios", "R": "Sistema respiratorio", "S": "Órganos de los sentidos", "V": "Varios"}
ATC_RE = re.compile(r"^[A-Z]\d{2}[A-Z]{2}\d{2}$")
CUM_RE = re.compile(r"^(\d{5,}-\d{1,3}|\d{7,})$")      # CUM: expediente-consecutivo (19942561-6) o 7+ dígitos; un CUPS tiene 6

def tipo_codigo(c):
    """Clasifica el COD. MEDICAMENTO de Software 360: ATC (B05CB01), CUM (19942561-6) u OTRO."""
    c = str(c or "").strip().upper()
    return "VACÍO" if not c else "ATC" if ATC_RE.match(c) else "CUM" if CUM_RE.match(c) else "OTRO"

def grupo_atc(c):
    c = str(c or "").strip().upper()
    return f"{c[0]} · {ATC_GRUPOS[c[0]]}" if ATC_RE.match(c) and c[0] in ATC_GRUPOS else "SIN ATC"

NIVELES = {"contrato": ["contrato", "eps_nombre", "modalidad"], "codigo": ["clase", "codigo"], "servicio": ["unidad_funcional", "ambito"],
           "sede": ["sede"], "eps": ["eps_nombre"], "diagnostico": ["diagnostico"], "atc": ["grupo_atc"], "usuario": ["paciente_id"]}

def det(mes=None, sede=None, clase=None, eps=None, contrato=None):
    cond, p = [], []
    for col, v in (("mes", mes), ("sede", sede), ("clase", clase), ("eps_nombre", eps), ("contrato", contrato)):
        if v: cond.append(f"{col}=?"); p.append(v)
    d = df("SELECT * FROM med_det" + (" WHERE " + " AND ".join(cond) if cond else ""), tuple(p))
    if not d.empty:
        d["grupo_atc"] = d.cod_med.map(grupo_atc)
        for c in ("unidad_funcional", "modalidad", "diagnostico", "contrato", "sede"): d[c] = d[c].replace("", "SIN DATO")
    return d

def _hom():
    """codigo_360 → 'CUM1 | CUM2' según la tabla de homologación de códigos (RIPS ↔ Software 360)."""
    h = df("SELECT codigo_rips, codigo_360 FROM hom_codigo")
    return h.groupby("codigo_360").codigo_rips.agg(lambda s: " | ".join(sorted(set(s)))) if len(h) else pd.Series(dtype=str)

def agrupado(nivel="contrato", mes=None, sede=None, clase=None, eps=None, contrato=None):
    """Líneas, cantidad, valor, usuarios únicos, facturas y códigos por contrato, código, servicio, sede, EPS, diagnóstico, grupo ATC o usuario."""
    d = det(mes, sede, clase, eps, contrato)
    if d.empty: return d
    por = NIVELES[nivel]; nz = lambda s: s[s != ""].nunique()
    g = d.groupby(por, dropna=False).agg(lineas=("codigo", "size"), cantidad=("cantidad", "sum"), valor=("valor", "sum"),
                                         usuarios=("paciente_id", nz), facturas=("factura", nz), codigos=("codigo", "nunique"),
                                         contratos=("contrato", "nunique"), sedes=("sede", "nunique")).reset_index()
    g["valor_por_usuario"] = (g.valor / g.usuarios.replace(0, np.nan)).fillna(0)
    g["cantidad_por_usuario"] = (g.cantidad / g.usuarios.replace(0, np.nan)).fillna(0)
    g["pct_valor"] = g.valor / g.valor.sum() if g.valor.sum() else 0
    if nivel == "codigo":
        x = d.groupby("codigo").agg(cod_med=("cod_med", lambda s: " | ".join(sorted(set(s) - {""}))), descripcion=("descripcion", "first"),
                                    vu_min=("valor_actividad", lambda s: s[s > 0].min()), vu_max=("valor_actividad", "max"), cant_max_linea=("cantidad", "max"))
        g = g.merge(x, on="codigo", how="left")
        g["tipo_cod_medicamento"] = g.cod_med.map(lambda c: "VARIOS" if "|" in c else tipo_codigo(c))
        g["grupo_atc"] = g.cod_med.map(lambda c: grupo_atc(c.split(" | ")[0]))
        g["cum_homologado"] = g.codigo.map(_hom()).fillna("")
        g["var_precio"] = (g.vu_max / g.vu_min - 1).fillna(0)
        def est(r):
            a = []
            if not r.cum_homologado: a.append("SIN CUM HOMOLOGADO")
            if r.tipo_cod_medicamento == "VARIOS": a.append("VARIOS ATC/CUM PARA UN CÓDIGO 360")
            if r.var_precio > TOL_PRECIO: a.append(f"PRECIO NO HOMOGÉNEO (> {TOL_PRECIO:.0%})")
            if r.valor <= 0: a.append("Facturado en $0 (validar cápita/paquete)")
            return "; ".join(a) or "OK"
        g["estado"] = g.apply(est, axis=1)
        cols = ["clase", "codigo", "cod_med", "tipo_cod_medicamento", "grupo_atc", "descripcion", "cum_homologado", "lineas", "cantidad", "valor",
                "usuarios", "facturas", "contratos", "sedes", "vu_min", "vu_max", "var_precio", "cant_max_linea", "valor_por_usuario", "pct_valor", "estado"]
        g = g[cols]
    elif nivel == "contrato":
        g["estado"] = np.where(g.modalidad.map(norm) == "CAPITA", "CÁPITA: consumo incluido en la UPC (sin ingreso adicional)", "OK")
    elif nivel == "usuario":
        lim = g.valor.quantile(0.99) if len(g) > 20 else np.inf
        fx = d[d.factura != ""].groupby("paciente_id").factura.agg(lambda s: ", ".join(sorted(set(s))[:4]))
        g["facturas_ejemplo"] = g.paciente_id.map(fx).fillna("")
        g["estado"] = np.where(g.valor > lim, "ALERTA: CONSUMO ATÍPICO (percentil 99)", "OK")
        g = g.rename(columns={"paciente_id": "usuario_anonimizado"})
    trivial = {"contrato": ["contratos"], "sede": ["sedes"], "usuario": ["usuarios", "valor_por_usuario", "cantidad_por_usuario"]}.get(nivel, [])
    return g.drop(columns=[c for c in trivial if c in g]).sort_values("valor", ascending=False).reset_index(drop=True)

def _fk(s): return s.astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)

def cruce_rips(mes=None, sede=None, eps=None, contrato=None):
    """Medicamentos facturados (360) vs RIPS, por factura y código 360 (los CUM del RIPS se llevan al código 360 con la homologación)."""
    d = det(mes, sede, None, eps, contrato)
    w = " WHERE mes=?" if mes else ""; p = (mes,) if mes else ()
    r = df(f"SELECT factura, cups codigo_rips, servicio tipo_servicio, MAX(descripcion) desc_rips, SUM(cantidad) cant_rips, SUM(valor) valor_rips "
           f"FROM rips{w} GROUP BY factura, cups, servicio", p)
    if d.empty or r.empty: return pd.DataFrame()
    h = df("SELECT codigo_rips, codigo_360 FROM hom_codigo"); hm = dict(zip(h.codigo_rips, h.codigo_360))
    cod360 = set(d.codigo)
    r["codigo"] = r.codigo_rips.map(lambda c: hm.get(c, c))
    r["homologado"] = r.codigo.isin(cod360)
    es_med = r.homologado | r.tipo_servicio.map(norm).str.contains("MEDICAMENT|INSUMO") | r.codigo_rips.str.match(CUM_RE.pattern) | r.codigo_rips.str.match(ATC_RE.pattern)
    r = r[es_med]; r["fk"] = _fk(r.factura)
    rf = set(_fk(df(f"SELECT factura FROM rips_factura{w}", p).factura))
    f = d.groupby(["factura", "codigo"]).agg(descripcion=("descripcion", "first"), cod_med=("cod_med", "first"), contrato=("contrato", "first"),
                                             sede=("sede", "first"), cant_fact=("cantidad", "sum"), valor_fact=("valor", "sum")).reset_index()
    f["fk"] = _fk(f.factura)
    if sede or eps or contrato: r = r[r.fk.isin(set(f.fk))]
    m = f.merge(r[["fk", "codigo", "codigo_rips", "desc_rips", "tipo_servicio", "homologado", "cant_rips", "valor_rips"]], on=["fk", "codigo"], how="outer")
    m["factura"] = m.factura.fillna(m.fk)
    for c in ("cant_fact", "valor_fact", "cant_rips", "valor_rips"): m[c] = m[c].fillna(0.0)
    m["dif_cantidad"] = m.cant_fact - m.cant_rips; m["dif_valor"] = m.valor_fact - m.valor_rips
    def est(x):
        if x.cant_fact > 0 and x.fk not in rf: return "FACTURA SIN RIPS"
        if x.cant_rips == 0: return "MEDICAMENTO FACTURADO SIN RIPS"
        if x.cant_fact == 0: return "CUM DEL RIPS SIN HOMOLOGAR" if not x.homologado else "EN RIPS, NO FACTURADO EN 360"
        a = []
        if abs(x.dif_cantidad) > 0.001: a.append("CANTIDAD DIFIERE")
        if x.valor_rips > 0 and abs(x.dif_valor) > max(1, 0.001 * x.valor_fact): a.append("VALOR DIFIERE")
        return "; ".join(a) or "OK"
    m["estado"] = m.apply(est, axis=1)
    cols = ["factura", "contrato", "sede", "codigo", "cod_med", "codigo_rips", "descripcion", "desc_rips", "cant_fact", "cant_rips", "dif_cantidad",
            "valor_fact", "valor_rips", "dif_valor", "estado"]
    return m[cols].sort_values(["estado", "valor_fact"], ascending=[True, False]).reset_index(drop=True)

def homologacion_cum(mes=None):
    """Qué códigos NO homologan: códigos 360 de medicamentos sin CUM asignado y CUM del RIPS sin código 360."""
    g = agrupado("codigo", mes)
    out = []
    if not g.empty:
        a = g.assign(origen="SOFTWARE 360", codigo_rips=g.cum_homologado)
        a["estado"] = np.where(a.cum_homologado == "", np.where(a.tipo_cod_medicamento == "CUM", "SIN CUM HOMOLOGADO (sugerido: COD. MEDICAMENTO)", "SIN CUM HOMOLOGADO"), "OK")
        out.append(a[["origen", "clase", "codigo", "cod_med", "tipo_cod_medicamento", "descripcion", "codigo_rips", "cantidad", "valor", "usuarios", "estado"]])
    w = " WHERE mes=?" if mes else ""; p = (mes,) if mes else ()
    r = df(f"SELECT cups codigo_rips, MAX(servicio) tipo_servicio, MAX(descripcion) descripcion, SUM(cantidad) cantidad, SUM(valor) valor FROM rips{w} GROUP BY cups", p)
    if not r.empty:
        h = set(df("SELECT codigo_rips FROM hom_codigo").codigo_rips); c360 = set(df("SELECT DISTINCT codigo FROM med_det").codigo) | set(df("SELECT DISTINCT cups FROM fact").cups)
        r = r[(r.tipo_servicio.map(norm).str.contains("MEDICAMENT|INSUMO") | r.codigo_rips.str.match(CUM_RE.pattern)) & ~r.codigo_rips.isin(h) & ~r.codigo_rips.isin(c360)]
        if len(r): out.append(r.assign(origen="RIPS", clase="", codigo="", cod_med="", tipo_cod_medicamento=r.codigo_rips.map(tipo_codigo), usuarios=np.nan,
                                       estado="CUM DEL RIPS SIN CÓDIGO 360")[["origen", "clase", "codigo", "cod_med", "tipo_cod_medicamento", "descripcion",
                                                                              "codigo_rips", "cantidad", "valor", "usuarios", "estado"]])
    if not out: return pd.DataFrame()
    return pd.concat(out, ignore_index=True).sort_values(["estado", "valor"], ascending=[False, False]).reset_index(drop=True)

def resumen_detalle(mes=None, sede=None, clase=None, eps=None, contrato=None):
    """KPIs del detalle: usuarios únicos, facturas, contratos, códigos sin CUM y estados del cruce con RIPS."""
    d = det(mes, sede, clase, eps, contrato)
    if d.empty: return {}
    g = agrupado("codigo", mes, sede, clase, eps, contrato); cr = cruce_rips(mes, sede, eps, contrato)
    sin = g[g.cum_homologado == ""]
    return dict(usuarios=int(d.paciente_id.replace("", np.nan).nunique()), facturas=int(d.factura.replace("", np.nan).nunique()),
                contratos=int(d.contrato.nunique()), codigos_360=int(d.codigo.nunique()), codigos_atc=int(d.cod_med.replace("", np.nan).nunique()),
                sin_cum=int(len(sin)), valor_sin_cum=float(sin.valor.sum()), pct_valor_sin_cum=float(sin.valor.sum() / g.valor.sum()) if g.valor.sum() else 0.0,
                rips_estados=(cr.estado.value_counts().rename_axis("estado").reset_index(name="n").to_dict("records") if len(cr) else []),
                por_atc=agrupado("atc", mes, sede, clase, eps, contrato)[["grupo_atc", "valor", "usuarios"]].to_dict("records"),
                por_contrato=agrupado("contrato", mes, sede, clase, eps, contrato).head(12)[["contrato", "modalidad", "valor", "usuarios", "lineas"]].to_dict("records"),
                por_servicio=agrupado("servicio", mes, sede, clase, eps, contrato)[["unidad_funcional", "ambito", "valor", "usuarios"]].to_dict("records"))

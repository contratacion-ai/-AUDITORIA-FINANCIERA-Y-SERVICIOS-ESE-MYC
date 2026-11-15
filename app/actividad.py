"""Pestaña FACTURACIÓN POR ACTIVIDAD (reporte «facturas generadas por actividad» de Software 360, tabla act_det, una fila por línea).

Cruces de cada factura con todas las demás fuentes:
  · el propio VALOR FACTURA del reporte (Σ líneas ≠ valor de la factura),
  · RIPS (JSON/TXT o Excel), XML de la factura electrónica (FEV) y su validación DIAN,
  · informe de facturas generadas (encabezado: estado, glosa, radicación) y facturación por ámbito del mismo mes,
  · radicación (FECHA RADICADO del reporte: días hábiles y plazo),
  · por factura y código contra RIPS / XML, y medicamentos contra el maestro de 360.
"""
import numpy as np, pandas as pd
from .db import df, PLAZO_RADICACION, DIAS_FACTURACION_TARDIA
from . import facturas as F, maestro as MA, rips_fev as RF

def _w(mes=None, sede=None, eps=None, contrato=None, extra=""):
    cond, p = [], []
    for col, v in (("mes", mes), ("sede", sede), ("eps_nombre", eps), ("contrato", contrato)):
        if v: cond.append(f"{col}=?"); p.append(v)
    if extra: cond.append(extra)
    return (" WHERE " + " AND ".join(cond) if cond else ""), tuple(p)

def base(mes=None, sede=None, eps=None, contrato=None):
    w, p = _w(mes, sede, eps, contrato)
    return df(f"SELECT * FROM act_det{w}", p)

def _fk(s): return s.astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)

def resumen(mes=None, sede=None, eps=None, contrato=None):
    d = base(mes, sede, eps, contrato)
    if d.empty: return dict(kpis={}, vacio=True)
    fac = d.groupby("factura").agg(vf=("valor_factura", "max")).reset_index()
    cf = facturas(mes, sede, eps, contrato)
    est = cf.estado.str.split("; ").explode().value_counts().rename_axis("estado").reset_index(name="facturas").to_dict("records") if len(cf) else []
    agr = lambda col, n=15: (d.assign(**{col: d[col].replace("", "SIN DATO")}).groupby(col)
                             .agg(valor=("valor", "sum"), lineas=("codigo", "size"), usuarios=("paciente_id", "nunique"), facturas=("factura", "nunique"))
                             .reset_index().sort_values("valor", ascending=False).head(n).to_dict("records"))
    rad = cf[cf.dias_habiles_radicacion.notna()] if len(cf) else cf
    k = dict(lineas=len(d), facturas=int(d.factura.replace("", np.nan).nunique()), valor=float(d.valor.sum()), valor_facturas=float(fac.vf.sum()),
             usuarios=int(d.paciente_id.replace("", np.nan).nunique()), contratos=int(d.contrato.nunique()), codigos=int(d.codigo.nunique()),
             medicamentos=float(d.loc[d.clase == "MEDICAMENTO", "valor"].sum()), insumos=float(d.loc[d.clase == "INSUMO", "valor"].sum()),
             con_rips=int((cf.valor_rips.notna()).sum()) if len(cf) else 0, con_xml=int(cf.valor_xml.notna().sum()) if len(cf) else 0,
             con_hallazgo=int((cf.estado != "OK").sum()) if len(cf) else 0,
             dias_radicacion=float(rad.dias_habiles_radicacion.mean()) if len(rad) else None,
             pct_en_plazo=float(rad.dias_habiles_radicacion.between(0, PLAZO_RADICACION).mean()) if len(rad) else None,
             sin_radicar=int((cf.fecha_radicado.fillna("") == "").sum()) if len(cf) else 0, plazo=PLAZO_RADICACION)
    return dict(kpis=k, estados=est, por_servicio=agr("servicio"), por_unidad=agr("unidad_funcional"), por_sede=agr("sede"), por_contrato=agr("contrato", 12),
                por_clase=agr("clase"), por_especialidad=agr("especialidad", 12))

def facturas(mes=None, sede=None, eps=None, contrato=None):
    """Una fila por factura con todas las fuentes y el estado del cruce."""
    d = base(mes, sede, eps, contrato)
    if d.empty: return pd.DataFrame()
    a = d.groupby(["mes", "factura"]).agg(contrato=("contrato", "first"), eps=("eps_nombre", "first"), modalidad=("modalidad", "first"), sede=("sede", "first"),
                                          fecha_factura=("fecha_factura", "max"), fecha_radicado=("fecha_radicado", "max"), radicado_eps=("radicado_eps", "max"),
                                          lineas=("codigo", "size"), usuarios=("paciente_id", "nunique"), paciente=("paciente_id", "max"),
                                          valor_lineas=("valor", "sum"), valor_factura=("valor_factura", "max"), n_contratos=("contrato", "nunique")).reset_index()
    a = a[a.factura != ""]
    a["fk"] = _fk(a.factura)
    w, p = (" WHERE mes=?", (mes,)) if mes else ("", ())
    def tabla(sql, cols):
        t = df(sql, p)
        if t.empty: return pd.DataFrame(columns=["fk"] + cols)
        t["fk"] = _fk(t.factura); return t.drop_duplicates("fk")[["fk"] + cols]
    rips = tabla(f"SELECT factura, SUM(valor) valor_rips, COUNT(DISTINCT paciente_id) usuarios_rips, MAX(paciente_id) paciente_rips FROM rips_det{w} GROUP BY factura",
                 ["valor_rips", "usuarios_rips", "paciente_rips"])
    rx = tabla(f"SELECT factura, MAX(valor_fev) valor_rips_excel FROM rips_factura{w} GROUP BY factura", ["valor_rips_excel"])
    xml = tabla(f"SELECT factura, MAX(valor) valor_xml, MAX(estado_dian) estado_dian FROM fev_xml{w} GROUP BY factura", ["valor_xml", "estado_dian"])
    fg = tabla(f"SELECT factura, MAX(total) total_encabezado, MAX(estado_factura) estado_factura FROM fac_gen{w} GROUP BY factura", ["total_encabezado", "estado_factura"])
    amb = tabla(f"SELECT factura, SUM(valor) valor_ambito FROM factura{w}{' AND' if w else ' WHERE'} carga_id IN (SELECT id FROM carga WHERE tipo='facturacion') "
                f"GROUP BY factura", ["valor_ambito"])
    for t in (rips, rx, xml, fg, amb): a = a.merge(t, on="fk", how="left")
    a["valor_rips"] = a.valor_rips.fillna(a.valor_rips_excel)
    a["dias_habiles_radicacion"] = F.dias_habiles(a.fecha_factura.replace("", None), a.fecha_radicado.replace("", None))
    # una fuente solo cuenta como «faltante» en los meses en que se cargó (con «Todos los meses», los RIPS de noviembre no afectan a enero)
    ms = lambda sql: set(df(sql).mes)
    en = lambda meses: a.mes.isin(meses)
    hay = dict(rips=en(ms("SELECT DISTINCT mes FROM rips_det") | ms("SELECT DISTINCT mes FROM rips_factura")), xml=en(ms("SELECT DISTINCT mes FROM fev_xml")),
               fg=en(ms("SELECT DISTINCT mes FROM fac_gen")),
               amb=en(ms("SELECT DISTINCT mes FROM factura WHERE carga_id IN (SELECT id FROM carga WHERE tipo='facturacion')")))
    ref = a.valor_factura.where(a.valor_factura > 0, a.valor_lineas)
    tol = lambda v: np.maximum(1.0, 0.001 * v.fillna(0))
    cap = a.modalidad.fillna("") == "CAPITA"
    reglas = [
        ("Σ LÍNEAS ≠ VALOR FACTURA", (a.valor_factura > 0) & ~cap & ((a.valor_lineas - a.valor_factura).abs() > tol(a.valor_factura))),
        ("SIN RIPS", hay["rips"] & a.valor_rips.isna()),
        ("RIPS ≠ VALOR FACTURA", a.valor_rips.notna() & ~cap & ((ref - a.valor_rips).abs() > tol(ref))),
        ("SIN XML (FEV)", hay["xml"] & a.valor_xml.isna()),
        ("XML (FEV) ≠ VALOR FACTURA", a.valor_xml.notna() & ((ref - a.valor_xml).abs() > tol(ref))),
        ("DIAN NO VALIDADA", a.estado_dian.notna() & (a.estado_dian.fillna("") != "VALIDADO DIAN") & (a.estado_dian.fillna("") != "")),
        ("NO ESTÁ EN FACTURAS GENERADAS", hay["fg"] & a.total_encabezado.isna()),
        ("ENCABEZADO ≠ VALOR FACTURA", a.total_encabezado.notna() & ((ref - a.total_encabezado).abs() > tol(ref))),
        ("GLOSADA", a.estado_factura.fillna("").str.upper().str.contains("GLOSAD")),
        ("POR ÁMBITO ≠ POR ACTIVIDAD", a.valor_ambito.notna() & ((a.valor_lineas - a.valor_ambito).abs() > tol(a.valor_lineas))),
        ("NO ESTÁ EN FACTURACIÓN POR ÁMBITO", hay["amb"] & a.valor_ambito.isna()),
        ("SIN FECHA DE RADICADO", a.fecha_radicado.fillna("") == ""),
        (f"RADICADA DESPUÉS DE {PLAZO_RADICACION} DÍAS HÁBILES", a.dias_habiles_radicacion > PLAZO_RADICACION),
        ("RADICADO ANTES DE LA FACTURA", a.dias_habiles_radicacion < 0),
        ("PACIENTE DIFERENTE AL RIPS", (a.usuarios == 1) & (a.usuarios_rips == 1) & (a.paciente != "") & a.paciente_rips.notna() & (a.paciente != a.paciente_rips)),
        ("FACTURA CON VARIOS CONTRATOS", a.n_contratos > 1),
    ]
    m = pd.DataFrame({k: v.fillna(False).astype(bool) for k, v in reglas})
    a["estado"] = ["; ".join(k for k, x in zip(m.columns, fila) if x) or "OK" for fila in m.values]
    cols = ["mes", "factura", "eps", "contrato", "modalidad", "sede", "fecha_factura", "fecha_radicado", "dias_habiles_radicacion", "lineas", "usuarios",
            "valor_lineas", "valor_factura", "valor_rips", "valor_xml", "estado_dian", "total_encabezado", "estado_factura", "valor_ambito", "estado"]
    return a[cols].sort_values("valor_factura", ascending=False).reset_index(drop=True)

def servicios(mes=None, sede=None, eps=None, contrato=None, tipo=None):
    """Factura + código de la facturación por actividad vs RIPS (y XML), con CUM homologados por el maestro."""
    w, p = _w(mes, sede, eps, contrato)
    l = df(f"SELECT factura, codigo, MAX(actividad) actividad, MAX(servicio) servicio, SUM(cantidad) cant_360, SUM(valor) valor_360 FROM act_det{w} "
           f"GROUP BY factura, codigo", p)
    if l.empty: return pd.DataFrame()
    return RF.cruce_servicios(mes, tipo, lineas=l)

def ambito_vs_actividad(mes=None):
    """Totales por servicio en los dos reportes de 360 del mismo mes (cuadre de la facturación)."""
    w, p = (" WHERE mes=?", (mes,)) if mes else ("", ())
    a = df(f"SELECT mes, servicio, SUM(cantidad) cant_actividad, SUM(valor) valor_actividad, COUNT(DISTINCT factura) facturas_actividad FROM act_det{w} GROUP BY mes, servicio", p)
    b = df(f"SELECT mes, servicio, SUM(cantidad) cant_ambito, SUM(valor) valor_ambito FROM fact{w}{' AND' if w else ' WHERE'} "
           f"carga_id IN (SELECT id FROM carga WHERE tipo='facturacion') GROUP BY mes, servicio", p)
    if a.empty: return a
    if b.empty: return a.assign(estado="INFO: no hay facturación por ámbito cargada para comparar")
    m = a.merge(b, on=["mes", "servicio"], how="outer").fillna({"cant_actividad": 0, "valor_actividad": 0, "cant_ambito": 0, "valor_ambito": 0})
    m["dif_valor"] = m.valor_actividad - m.valor_ambito; m["dif_cantidad"] = m.cant_actividad - m.cant_ambito
    m["estado"] = np.where(m.dif_valor.abs() <= np.maximum(1, 0.001 * m.valor_ambito), "OK", "DIFIERE")
    return m.sort_values("dif_valor", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)

REGLAS_LINEA = {
    "A01": ("Línea con valor total en cero o negativo", lambda d: d.valor <= 0),
    "A02": ("Valor total ≠ valor actividad × cantidad", lambda d: (d.valor_actividad > 0) & (d.cantidad > 0) & ((d.valor_actividad * d.cantidad - d.valor).abs() > 1)),
    "A03": ("Línea sin código (CUPS / medicamento)", lambda d: d.codigo == ""),
    "A04": ("Consulta sin diagnóstico", lambda d: (d.servicio.str.upper().str.contains("CONSULTA")) & (d.diagnostico == "")),
    "A05": (f"Facturación tardía: más de {DIAS_FACTURACION_TARDIA} días entre el servicio y la factura (no aplica a cápita)",
            lambda d: (d.modalidad != "CAPITA") & ((pd.to_datetime(d.fecha_factura, errors="coerce") - pd.to_datetime(d.fecha_servicio, errors="coerce")).dt.days > DIAS_FACTURACION_TARDIA)),
    "A06": ("Línea repetida: misma factura, código, usuario y fecha de servicio (no aplica a cápita)",
            lambda d: (d.modalidad != "CAPITA") & d.duplicated(["factura", "codigo", "paciente_id", "fecha_servicio"], keep=False) & (d.codigo != "")),
    "A07": ("Línea sin número de factura", lambda d: d.factura == ""),
    "A08": ("Cantidad en cero o negativa", lambda d: d.cantidad <= 0),
}

def reglas(mes=None, regla=None, sede=None, eps=None, contrato=None):
    d = base(mes, sede, eps, contrato)
    if d.empty: return pd.DataFrame(columns=["regla", "descripcion", "lineas", "facturas", "valor"])
    if regla:
        x = d[REGLAS_LINEA[regla][1](d)]
        return x[["mes", "factura", "fecha_servicio", "unidad_funcional", "contrato", "sede", "servicio", "codigo", "actividad", "cantidad",
                  "valor_actividad", "valor", "diagnostico", "profesional"]]
    out = []
    for k, (txt, f) in REGLAS_LINEA.items():
        mk = f(d)
        if mk.any(): out.append(dict(regla=k, descripcion=txt, lineas=int(mk.sum()), facturas=int(d.loc[mk, "factura"].nunique()), valor=float(d.loc[mk, "valor"].sum())))
    return pd.DataFrame(out, columns=["regla", "descripcion", "lineas", "facturas", "valor"])

def medicamentos(mes=None):
    return MA.validacion(mes, fuente="act_det")

def detalle(mes=None, sede=None, eps=None, contrato=None, factura=None):
    w, p = _w(mes, sede, eps, contrato, "factura=?" if factura else "")
    return df(f"SELECT mes, factura, fecha_factura, fecha_servicio, unidad_funcional, contrato, eps_nombre, modalidad, sede, servicio, clase, codigo, cod_med, "
              f"actividad, cantidad, valor_actividad, valor, valor_factura, fecha_radicado, ambito, diagnostico, profesional, especialidad, autorizacion "
              f"FROM act_det{w} ORDER BY factura", p + ((factura,) if factura else ()))

def recomendaciones(mes=None):
    cf = facturas(mes)
    if cf.empty: return []
    R = []
    cnt = cf.estado.str.split("; ").explode().value_counts()
    for e, nivel in (("Σ LÍNEAS ≠ VALOR FACTURA", "rojo"), ("RIPS ≠ VALOR FACTURA", "rojo"), ("SIN RIPS", "rojo"), ("XML (FEV) ≠ VALOR FACTURA", "rojo"),
                     ("PACIENTE DIFERENTE AL RIPS", "rojo"), ("SIN FECHA DE RADICADO", "naranja"), (f"RADICADA DESPUÉS DE {PLAZO_RADICACION} DÍAS HÁBILES", "naranja"),
                     ("POR ÁMBITO ≠ POR ACTIVIDAD", "naranja")):
        if cnt.get(e): R.append((nivel, f"Facturación por actividad: {int(cnt[e]):,} facturas con «{e}». Revisar en la pestaña «Facturación por actividad»."))
    return [dict(nivel=n, texto=t, modulo="Facturación por actividad") for n, t in R]

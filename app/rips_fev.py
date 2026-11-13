"""RIPS en sus formatos de envío: JSON (Res. 2275 de 2023 / 1557 de 2023, un archivo por factura electrónica), el XML de la factura
electrónica (FEV, AttachedDocument de la DIAN: valor, fecha, EPS, contrato, modalidad, CUFE, estado DIAN y líneas con CUPS) y TXT planos
(Res. 3374 de 2000: AF, US, AC, AP, AM, AT, AU, AH, AN, CT). Ambos se pueden subir sueltos o dentro de .zip / .rar / .7z.

Cada servicio queda en `rips_det` (paciente con hash) y además se agrega a `rips` / `rips_factura`, de modo que la conciliación
Facturación vs RIPS, la radicación y el cruce de medicamentos funcionan igual que con el consolidado en Excel.
También se leen las respuestas de validación del Ministerio (JSON con CodigoUnicoValidacion / ResultState) para marcar el CUV de cada factura.
"""
import hashlib, html, json, os, re
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np, pandas as pd
from .db import df
from .loaders import norm, num, _ymd

TIPOS_JSON = {"consultas": "Consultas", "procedimientos": "Procedimientos", "urgencias": "Urgencias", "hospitalizacion": "Hospitalización",
              "reciennacidos": "Recién nacidos", "medicamentos": "Medicamentos", "otrosservicios": "Otros servicios"}
PREFIJOS_TXT = {"AF": "Transacciones", "US": "Usuarios", "AC": "Consultas", "AP": "Procedimientos", "AM": "Medicamentos",
                "AT": "Otros servicios", "AU": "Urgencias", "AH": "Hospitalización", "AN": "Recién nacidos", "CT": "Control"}
COLS_DET = ["mes", "factura", "tipo_servicio", "codigo", "descripcion", "cantidad", "valor_unitario", "valor", "paciente_id", "tipo_doc",
            "fecha", "diagnostico", "autorizacion", "origen", "archivo"]

def _s(v):
    """Texto limpio: los generadores de RIPS escriben «None» / «null» en los campos vacíos."""
    t = str(v if v is not None else "").strip()
    return "" if t.lower() in ("none", "null", "nan") else t
def _h(doc): doc = _s(doc); return hashlib.sha1(doc.encode()).hexdigest()[:12] if doc else ""
def _low(d): return {re.sub(r"[^a-z]", "", str(k).lower()): (v if isinstance(v, (dict, list)) else _s(v)) for k, v in d.items()} if isinstance(d, dict) else {}
def _fecha(v):
    s = str(v or "").strip()
    return _ymd(s.split(" ")[0].split("T")[0]) if s else ""

def es_rips(ruta):
    """True si el archivo es un RIPS JSON (o respuesta de validación), el XML de la FEV o un TXT de RIPS (AF/AC/AP… por el nombre)."""
    n = Path(ruta).name.upper()
    if n.endswith(".JSON"): return True
    if n.endswith(".XML"):
        with open(ruta, "rb") as f: cab = f.read(4096)
        return b"AttachedDocument" in cab or b"<Invoice" in cab
    return n.endswith(".TXT") and n[:2] in PREFIJOS_TXT

# ───────── JSON ─────────
def _facturas_json(o):
    """Devuelve las facturas RIPS contenidas en un JSON (objeto, lista o envoltura {'rips': …})."""
    if isinstance(o, list):
        for x in o: yield from _facturas_json(x)
    elif isinstance(o, dict):
        l = _low(o)
        if "numfactura" in l and "usuarios" in l: yield o
        else:
            for v in o.values():
                if isinstance(v, (dict, list)): yield from _facturas_json(v)

def _validaciones_json(o):
    """Respuestas del MinSalud: [(factura, cuv, estado, detalle)]."""
    if isinstance(o, list):
        for x in o: yield from _validaciones_json(x)
    elif isinstance(o, dict):
        l = _low(o)
        if "codigounicovalidacion" in l or "resultstate" in l:
            res = l.get("resultadosvalidacion") or []
            rech = [r for r in res if "RECHAZ" in str(_low(r).get("clase", "")).upper()]
            yield (str(l.get("numfactura") or l.get("numerofactura") or "").strip(), str(l.get("codigounicovalidacion") or "").strip(),
                   "RECHAZADO" if (l.get("resultstate") is False or rech) else "VALIDADO (CUV)",
                   "; ".join(f"{_low(r).get('codigo', '')} {_low(r).get('descripcion', '')}".strip() for r in rech)[:300])
        for v in o.values():
            if isinstance(v, (dict, list)) and not ("codigounicovalidacion" in l or "resultstate" in l): yield from _validaciones_json(v)

def leer_json(ruta):
    with open(ruta, "rb") as f: crudo = f.read()
    for cod in ("utf-8-sig", "cp1252"):
        try: o = json.loads(crudo.decode(cod)); break
        except (UnicodeDecodeError, json.JSONDecodeError): o = None
    if o is None: raise ValueError(f"«{Path(ruta).name}» no es un JSON válido.")
    filas = []
    for fac in _facturas_json(o):
        lf = _low(fac); nf = str(lf.get("numfactura") or "").strip()
        for u in lf.get("usuarios") or []:
            lu = _low(u); pid = _h(lu.get("numdocumentoidentificacion")); td = str(lu.get("tipodocumentoidentificacion") or "")
            serv = _low(lu.get("servicios") or {})
            for clave, tipo in TIPOS_JSON.items():
                for s in serv.get(clave) or []:
                    ls = _low(s)
                    cod = (ls.get("codconsulta") or ls.get("codprocedimiento") or ls.get("codtecnologiasalud") or "")
                    cant = num(ls.get("cantidadmedicamento") or ls.get("cantidados") or 1) or 1.0
                    vu = num(ls.get("vrunitmedicamento") or ls.get("vrunitos") or 0)
                    val = num(ls.get("vrservicio") or 0)
                    fecha = _fecha(ls.get("fechainicioatencion") or ls.get("fechadispensadmon") or ls.get("fechasuministrotecnologia") or ls.get("fechanacimiento"))
                    filas.append(dict(factura=nf, tipo_servicio=tipo, codigo=str(cod).strip(), descripcion=str(ls.get("nomtecnologiasalud") or "").strip(),
                                      cantidad=cant, valor_unitario=vu or (val / cant if cant else 0), valor=val, paciente_id=pid, tipo_doc=td, fecha=fecha,
                                      diagnostico=str(ls.get("coddiagnosticoprincipal") or "").strip(), autorizacion=str(ls.get("numautorizacion") or "").strip(),
                                      origen="JSON", archivo=Path(ruta).name))
            if not any(serv.get(k) for k in TIPOS_JSON):
                filas.append(dict(factura=nf, tipo_servicio="SIN SERVICIOS", codigo="", descripcion="", cantidad=0, valor_unitario=0, valor=0, paciente_id=pid,
                                  tipo_doc=td, fecha="", diagnostico="", autorizacion="", origen="JSON", archivo=Path(ruta).name))
    return filas, list(_validaciones_json(o))

# ───────── XML de la factura electrónica (FEV) ─────────
NIT_ESE = "900034131"
def _tag(x, t):
    return [html.unescape(v).strip() for v in re.findall(rf"<cbc:{t}\b[^>]*>(.*?)</cbc:{t}>", x, re.S)]

def _modalidad(txt):
    n = norm(txt)
    return "CAPITA" if "CAPITA" in n else "EVENTO" if "EVENTO" in n else "PGP" if ("GLOBAL PROSPECTIVO" in n or "PGP" in n) else ""

def leer_xml(ruta):
    """AttachedDocument (o Invoice) de la DIAN → (encabezado, líneas). El Invoice viene en CDATA dentro del AttachedDocument."""
    with open(ruta, encoding="utf-8", errors="replace") as f: x = f.read()
    partes = re.findall(r"<!\[CDATA\[(.*?)\]\]>", x, re.S)
    inv = next((p for p in partes if "<Invoice" in p or "<CreditNote" in p or "<DebitNote" in p), x)
    resp = next((p for p in partes if "ApplicationResponse" in p), "")
    ids = _tag(inv, "ID"); fac = (_tag(x, "ParentDocumentID") or [i for i in ids if re.match(r"^[A-Z]{1,6}\d", i)] or [""])[0]
    nombres, nits = _tag(inv, "RegistrationName"), _tag(inv, "CompanyID")
    cliente = next(((n, i) for n, i in zip(nombres, nits) if i != NIT_ESE), ("", ""))
    sector = lambda k: (re.search(rf"<Name>{k}</Name>\s*<Value[^>]*>(.*?)</Value>", inv, re.S) or [None, ""])[1].strip()
    codigos = _tag(resp, "ResponseCode"); desc = _tag(resp, "Description")
    dian = ("VALIDADO DIAN" if "02" in codigos[:1] else (f"DIAN {codigos[0]}: {desc[0] if desc else ''}" if codigos else ""))
    enc = dict(factura=fac, fecha=(_tag(inv, "IssueDate") or [""])[0], valor=num((_tag(inv, "PayableAmount") or ["0"])[0]), eps=cliente[0], nit=cliente[1],
               contrato=_s(sector("NUMERO_CONTRATO")), modalidad=_modalidad(sector("MODALIDAD_PAGO")), cobertura=_s(sector("COBERTURA_PLAN_BENEFICIOS")),
               cufe=(_tag(inv, "UUID") or [""])[0], estado_dian=dian, archivo=Path(ruta).name)
    lineas = []
    for l in re.findall(r"<cac:(?:Invoice|CreditNote|DebitNote)Line>(.*?)</cac:(?:Invoice|CreditNote|DebitNote)Line>", inv, re.S):
        cod = (re.search(r"<cac:SellersItemIdentification>\s*<cbc:ID[^>]*>(.*?)</cbc:ID>", l, re.S) or
               re.search(r"<cac:StandardItemIdentification>\s*<cbc:ID[^>]*>(.*?)</cbc:ID>", l, re.S))
        lineas.append(dict(factura=fac, codigo=cod.group(1).strip() if cod else "", descripcion=(_tag(l, "Description") or [""])[0],
                           cantidad=num((_tag(l, "InvoicedQuantity") or ["1"])[0]), valor=num((_tag(l, "LineExtensionAmount") or ["0"])[0])))
    return enc, lineas

# ───────── TXT (Res. 3374 de 2000) ─────────
def _lineas_txt(ruta):
    with open(ruta, "rb") as f: crudo = f.read()
    for cod in ("utf-8-sig", "cp1252"):
        try: txt = crudo.decode(cod); break
        except UnicodeDecodeError: continue
    for l in txt.splitlines():
        if l.strip(): yield [x.strip() for x in l.split(",")]

def leer_txt(rutas):
    """Un juego de RIPS TXT (puede venir de varias remisiones): AF da EPS, contrato, fecha y valor neto de cada factura."""
    por = defaultdict(list)
    for r in rutas: por[Path(r).name.upper()[:2]].append(r)
    af = {}
    for r in por.get("AF", []):
        for x in _lineas_txt(r):
            if len(x) >= 17: af[x[4]] = dict(fecha=_ymd(x[5]), cod_eps=x[8], eps=x[9], contrato=x[10], valor_neto=num(x[16]))
    filas = []
    def add(r, tipo, x, cod, desc, cant, vu, val, fecha, dx, aut):
        filas.append(dict(factura=x[0], tipo_servicio=tipo, codigo=cod, descripcion=desc, cantidad=cant or 1.0, valor_unitario=vu, valor=val, paciente_id=_h(x[3]),
                          tipo_doc=x[2], fecha=fecha or af.get(x[0], {}).get("fecha", ""), diagnostico=dx, autorizacion=aut, origen="TXT", archivo=Path(r).name))
    for r in por.get("AC", []):
        for x in _lineas_txt(r):
            if len(x) >= 15: add(r, "Consultas", x, x[6], "", 1.0, num(x[14]), num(x[14]), _ymd(x[4]), x[9], x[5])
    for r in por.get("AP", []):
        for x in _lineas_txt(r):
            if len(x) >= 15: add(r, "Procedimientos", x, x[6], "", 1.0, num(x[14]), num(x[14]), _ymd(x[4]), x[10], x[5])
    for r in por.get("AM", []):
        for x in _lineas_txt(r):
            if len(x) >= 14: add(r, "Medicamentos", x, x[5], x[7], num(x[11]), num(x[12]), num(x[13]), "", "", x[4])
    for r in por.get("AT", []):
        for x in _lineas_txt(r):
            if len(x) >= 11: add(r, "Otros servicios", x, x[6], x[7], num(x[8]), num(x[9]), num(x[10]), "", "", x[4])
    for pref, tipo in (("AU", "Urgencias"), ("AH", "Hospitalización")):
        for r in por.get(pref, []):
            for x in _lineas_txt(r):
                if len(x) >= 5: add(r, tipo, x, "", "", 0.0, 0.0, 0.0, _ymd(x[4] if pref == "AU" else x[5] if len(x) > 5 else ""), "", "")
    if not filas and not af: raise ValueError("No se encontraron archivos RIPS TXT reconocibles (AF, AC, AP, AM, AT, AU, AH…).")
    return filas, af

# ───────── carga ─────────
def procesar(rutas, mes_param=None):
    """Lee JSON y TXT y devuelve (det, rips_agregado, rips_factura, validaciones) como DataFrames."""
    filas, val, af, xml, xlin = [], [], {}, [], []
    txt = [r for r in rutas if Path(r).suffix.lower() == ".txt" and Path(r).name.upper()[:2] in PREFIJOS_TXT]
    for r in rutas:
        if Path(r).suffix.lower() == ".json":
            f, v = leer_json(r); filas += f; val += v
        elif Path(r).suffix.lower() == ".xml":
            try: e, l = leer_xml(r)
            except Exception: continue
            if e["factura"]: xml.append(e); xlin += l
    if txt:
        f, af = leer_txt(txt); filas += f
    if not filas and not val and not xml: raise ValueError("Los archivos no contienen RIPS JSON (numFactura + usuarios), XML de facturas (FEV) ni RIPS TXT.")
    d = pd.DataFrame(filas, columns=[c for c in COLS_DET if c != "mes"]) if filas else pd.DataFrame(columns=[c for c in COLS_DET if c != "mes"])
    # mes de cada factura: el de Software 360 si ya se cargó; si no, AF (TXT) o la fecha más frecuente de los servicios
    f360 = df("SELECT factura, MAX(mes) mes, MAX(eps_nombre) eps, MAX(contrato) contrato, MAX(modalidad) modalidad, MAX(sede) sede FROM factura GROUP BY factura")
    k = lambda s: s.astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    f360["fk"] = k(f360.factura); f360 = f360.drop_duplicates("fk").set_index("fk")
    d["fk"] = k(d.factura)
    fx = pd.DataFrame(xml, columns=["factura", "fecha", "valor", "eps", "nit", "contrato", "modalidad", "cobertura", "cufe", "estado_dian", "archivo"])
    fx["fk"] = k(fx.factura); fx = fx.drop_duplicates("fk", keep="last")
    if d.empty and len(fx):                                            # solo XML: una fila por factura para ubicar mes y totales
        d = pd.DataFrame([dict(factura=f, tipo_servicio="SIN RIPS (SOLO XML)", codigo="", descripcion="", cantidad=0, valor=0, valor_unitario=0, paciente_id="",
                               tipo_doc="", fecha="", diagnostico="", autorizacion="", origen="XML", archivo="") for f in fx.factura])
        d["fk"] = k(d.factura)
    fxi = fx.set_index("fk")
    moda = d[d.fecha != ""].groupby("fk").fecha.agg(lambda s: Counter(x[:7] for x in s).most_common(1)[0][0])
    af_mes = {k(pd.Series([f])).iloc[0]: v["fecha"][:7] for f, v in af.items() if v.get("fecha")}
    # mes = el de la factura (XML de la FEV, Software 360 o AF del TXT); la fecha del servicio solo si no hay otra fuente
    d["mes"] = d.fk.map(fxi.fecha.str[:7].replace("", None)).fillna(d.fk.map(f360.mes)).fillna(d.fk.map(af_mes)).fillna(d.fk.map(moda)).fillna(mes_param or "")
    if (d.mes == "").all(): raise ValueError("No se pudo determinar el mes de los RIPS: indique el mes (AAAA-MM) al cargar.")
    d = d[d.mes != ""]
    af_k = {k(pd.Series([f])).iloc[0]: v for f, v in af.items()}
    def g(col, fuente, xcol):
        v = d.fk.map(lambda x: (af_k.get(x) or {}).get(fuente) or "")
        v = v.where(v != "", d.fk.map(fxi[xcol]).fillna(""))
        return v.where(v != "", d.fk.map(f360[col]).fillna(""))
    d["eps"] = g("eps", "eps", "eps"); d["contrato"] = g("contrato", "contrato", "contrato")
    mx = d.fk.map(fxi.modalidad).fillna(""); d["modalidad"] = mx.where(mx != "", d.fk.map(f360.modalidad).fillna("")); d["sede"] = d.fk.map(f360.sede).fillna("")
    agg = d.groupby(["mes", "eps", "contrato", "modalidad", "sede", "tipo_servicio", "codigo", "factura"], as_index=False).agg(
        cantidad=("cantidad", "sum"), valor=("valor", "sum"), descripcion=("descripcion", "first"))
    vf = d.groupby(["mes", "factura"], as_index=False).agg(eps=("eps", "first"), contrato=("contrato", "first"), modalidad=("modalidad", "first"),
                                                         sede=("sede", "first"), valor_srv=("valor", "sum"), servicios=("cantidad", "sum"), fk=("fk", "first"))
    vf["valor_fev"] = vf.fk.map(fxi.valor).fillna(vf.fk.map(lambda x: (af_k.get(x) or {}).get("valor_neto"))).fillna(vf.valor_srv)
    vmap = {k(pd.Series([f])).iloc[0]: (cuv, est, det) for f, cuv, est, det in val if f}
    vf["validacion"] = vf.fk.map(lambda x: (f"{vmap[x][1]} · CUV {vmap[x][0]}" + (f" · {vmap[x][2]}" if vmap[x][2] else "")) if x in vmap else "")
    vf["validacion"] = vf.validacion.where(vf.validacion != "", vf.fk.map(fxi.estado_dian).fillna(""))
    mes_fk = d.drop_duplicates("fk").set_index("fk").mes
    fx["mes"] = fx.fk.map(mes_fk).fillna(fx.fecha.str[:7])
    xl = pd.DataFrame(xlin, columns=["factura", "codigo", "descripcion", "cantidad", "valor"]); xl["mes"] = k(xl.factura).map(mes_fk).fillna(k(xl.factura).map(fxi.fecha.str[:7]))
    d = d[d.tipo_servicio != "SIN RIPS (SOLO XML)"]; vf = vf[vf.factura.isin(set(d.factura))]      # una FEV sin RIPS no cuenta como RIPS
    return d.drop(columns=["fk"]), agg[agg.tipo_servicio != "SIN RIPS (SOLO XML)"], vf.drop(columns=["fk", "valor_srv"]), val, fx.drop(columns=["fk"]), xl

# ───────── análisis (pestaña «RIPS JSON/TXT vs 360») ─────────
def _w(mes): return (" WHERE mes=?", (mes,)) if mes else ("", ())

def resumen(mes=None):
    w, p = _w(mes)
    d = df(f"SELECT * FROM rips_det{w}", p)
    if d.empty: return dict(kpis={}, vacio=True)
    cf = cruce_facturas(mes); vs = validacion(mes)
    k = dict(facturas=int(d.factura.nunique()), valor=float(d.valor.sum()), usuarios=int(d.paciente_id.replace("", np.nan).nunique()),
             servicios=int(len(d)), json=int((d.origen == "JSON").sum()), txt=int((d.origen == "TXT").sum()),
             xml=int(df(f"SELECT COUNT(*) n FROM fev_xml{w}", p).n.iloc[0]), dian_validadas=int(df(f"SELECT COUNT(*) n FROM fev_xml{w} AND estado_dian='VALIDADO DIAN'" if w else "SELECT COUNT(*) n FROM fev_xml WHERE estado_dian='VALIDADO DIAN'", p).n.iloc[0]),
             facturas_ok=int((cf.estado == "OK").sum()) if len(cf) else 0, facturas_con_diferencia=int((cf.estado != "OK").sum()) if len(cf) else 0,
             valor_diferencia=float(cf.loc[cf.estado.str.contains("DIFIERE|≠"), "dif"].abs().sum()) if len(cf) else 0.0, hallazgos_estructura=int(vs.registros.sum()) if len(vs) else 0)
    return dict(kpis=k, por_tipo=d.groupby("tipo_servicio").agg(servicios=("codigo", "size"), valor=("valor", "sum"), usuarios=("paciente_id", "nunique")).reset_index().to_dict("records"),
                estados=(cf.estado.str.split("; ").explode().value_counts().rename_axis("estado").reset_index(name="facturas").to_dict("records") if len(cf) else []),
                estructura=vs.to_dict("records"))

def _fk(s): return s.astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)

def cruce_facturas(mes=None):
    """Factura a factura: valor 360 (detalle por ámbito/actividad o encabezado) vs valor RIPS; paciente; validación CUV."""
    w, p = _w(mes)
    r = df(f"SELECT mes, factura, SUM(valor) valor_rips, COUNT(DISTINCT paciente_id) usuarios_rips, MAX(paciente_id) paciente_rips "
           f"FROM rips_det{w} GROUP BY mes, factura", p)
    if r.empty: return r
    rf = df(f"SELECT factura, MAX(valor_fev) valor_fev, MAX(validacion) validacion, MAX(contrato) contrato_rips, MAX(eps) eps_rips, MAX(modalidad) modalidad_rips "
            f"FROM rips_factura{w} GROUP BY factura", p)
    f = df(f"SELECT mes, factura, MAX(contrato) contrato, MAX(sede) sede, MAX(modalidad) modalidad, SUM(valor) valor_360, MAX(valor_factura) valor_factura, "
           f"MAX(paciente_id) paciente_360, MAX(n_pacientes) n_pacientes FROM factura{w} GROUP BY mes, factura", p)
    g = df(f"SELECT factura, MAX(total) total_encabezado FROM fac_gen{w} GROUP BY factura", p)
    x = df(f"SELECT factura, MAX(valor) valor_xml, MAX(eps) eps_xml, MAX(estado_dian) estado_dian, MAX(cufe) cufe FROM fev_xml{w} GROUP BY factura", p)
    for t in (r, rf, f, g, x): t["fk"] = _fk(t.factura) if len(t) else pd.Series(dtype=str)
    m = r.merge(rf[["fk", "valor_fev", "validacion", "contrato_rips", "eps_rips", "modalidad_rips"]], on="fk", how="left")
    m = m.merge(f.drop(columns=["factura"]).rename(columns={"mes": "mes_360", "contrato": "contrato_360"}), on="fk", how="outer")
    m = m.merge(g[["fk", "total_encabezado"]], on="fk", how="left")
    m = m.merge(x[["fk", "valor_xml", "eps_xml", "estado_dian", "cufe"]], on="fk", how="outer")
    m["factura"] = m.factura.fillna(m.fk); m["mes"] = m.mes.fillna(m.mes_360)
    m["valor_360_ref"] = m.valor_factura.where(m.valor_factura > 0, m.total_encabezado).fillna(m.valor_360)
    m["modalidad"] = m.modalidad.fillna(m.modalidad_rips).replace("", np.nan)
    m["dif"] = m.valor_360_ref.fillna(m.valor_xml).fillna(0) - m.valor_rips.fillna(0)
    meses_r = set(r.mes); meses_360 = set(df("SELECT DISTINCT mes FROM factura").mes)
    tol = lambda v: max(1.0, 0.001 * (v or 0))
    def est(x):
        a = []
        if pd.notna(x.valor_xml) and pd.notna(x.valor_rips) and abs(x.valor_xml - x.valor_rips) > tol(x.valor_xml): a.append("XML (FEV) ≠ RIPS")
        if pd.notna(x.estado_dian) and x.estado_dian not in ("", "VALIDADO DIAN"): a.append("DIAN NO VALIDADA")
        if pd.isna(x.valor_rips):
            return "; ".join(["SIN RIPS (HAY XML)" if pd.notna(x.valor_xml) else "SIN RIPS" if x.mes in meses_r else "RIPS NO CARGADO PARA EL MES"] + a)
        if pd.isna(x.valor_360):
            return "; ".join(["RIPS SIN FACTURA EN 360" if x.mes in meses_360 else "INFO: FALTA CARGAR LA FACTURACIÓN 360 DEL MES"] + a)
        if x.modalidad != "CAPITA" and abs(x.dif) > tol(x.valor_360_ref): a.append("VALOR DIFIERE")
        if x.n_pacientes == 1 and x.usuarios_rips == 1 and x.paciente_360 and x.paciente_rips and x.paciente_360 != x.paciente_rips: a.append("PACIENTE DIFERENTE")
        if "RECHAZ" in str(x.validacion or ""): a.append("RECHAZADO POR MINSALUD")
        return "; ".join(a) or "OK"
    m["estado"] = m.apply(est, axis=1)
    m.loc[m.valor_360.isna() & m.valor_xml.isna(), "dif"] = np.nan                  # sin 360 ni XML no hay contra qué comparar
    m["contrato"] = m.contrato_360.fillna(m.contrato_rips); m["eps"] = m.eps_xml.fillna(m.eps_rips)
    cols = ["mes", "factura", "eps", "contrato", "sede", "modalidad", "valor_360_ref", "valor_xml", "valor_rips", "dif", "usuarios_rips", "n_pacientes",
            "estado_dian", "validacion", "estado"]
    return m[cols].rename(columns={"valor_360_ref": "valor_360", "n_pacientes": "usuarios_360"}).sort_values("dif", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)

def cruce_servicios(mes=None, tipo=None):
    """Factura + código: cantidad y valor en 360 (líneas) vs RIPS (servicios). Medicamentos/insumos por la homologación CUM ↔ código 360."""
    w, p = _w(mes)
    r = df(f"SELECT factura, tipo_servicio, codigo codigo_rips, MAX(descripcion) desc_rips, SUM(cantidad) cant_rips, SUM(valor) valor_rips FROM rips_det{w} "
           f"GROUP BY factura, tipo_servicio, codigo", p)
    if r.empty: return r
    if tipo: r = r[r.tipo_servicio == tipo]
    l = df(f"SELECT factura, CASE WHEN cod_proc<>'' THEN cod_proc ELSE cod_med END codigo, MAX(actividad) actividad, MAX(servicio) servicio, "
           f"SUM(cantidad) cant_360, SUM(valor) valor_360 FROM linea{w} GROUP BY factura, codigo", p)
    h = df("SELECT codigo_rips, codigo_360 FROM hom_codigo"); hm = dict(zip(h.codigo_rips, h.codigo_360))
    r["codigo"] = r.codigo_rips.map(lambda c: hm.get(c, c))
    r["fk"] = _fk(r.factura); l["fk"] = _fk(l.factura)
    fr = set(r.fk); l = l[l.fk.isin(fr)]                                # solo facturas que están en el RIPS
    m = l.merge(r.drop(columns=["factura"]), on=["fk", "codigo"], how="outer")
    xl = df(f"SELECT factura, codigo, SUM(cantidad) cant_xml, SUM(valor) valor_xml FROM fev_linea{w} GROUP BY factura, codigo", p)
    if len(xl):
        xl["fk"] = _fk(xl.factura); xl["codigo"] = xl.codigo.map(lambda c: hm.get(c, c))
        m = m.merge(xl[xl.fk.isin(fr)].drop(columns=["factura"]), on=["fk", "codigo"], how="outer")
    else: m["cant_xml"] = np.nan; m["valor_xml"] = np.nan
    m["factura"] = m.factura.fillna(m.fk)
    sin360 = m.cant_360.isna() & m.valor_360.isna()
    for c in ("cant_360", "valor_360", "cant_rips", "valor_rips"): m[c] = m[c].fillna(0.0)
    m["dif_cantidad"] = m.cant_360 - m.cant_rips; m["dif_valor"] = m.valor_360 - m.valor_rips
    hay360 = len(l) > 0
    def est(x):
        xa = []
        if pd.notna(x.valor_xml) and (abs((x.cant_xml or 0) - x.cant_rips) > 0.001 or abs(x.valor_xml - x.valor_rips) > max(1.0, 0.001 * x.valor_xml)): xa.append("XML (FEV) ≠ RIPS")
        if pd.isna(x.valor_xml) and len(xl) and x.cant_rips > 0: xa.append("NO ESTÁ EN EL XML")
        if not hay360: return "; ".join(xa) or ("INFO: XML = RIPS · falta cargar la facturación 360 del mes" if pd.notna(x.valor_xml) else "INFO: falta cargar la facturación 360 del mes")
        if x.cant_rips == 0 and x.valor_rips == 0 and pd.isna(x.tipo_servicio): return "; ".join(["FACTURADO EN 360 SIN RIPS"] + xa)
        if x.cant_360 == 0 and x.valor_360 == 0:
            return "CÓDIGO RIPS SIN HOMOLOGAR" if x.tipo_servicio in ("Medicamentos", "Otros servicios") and x.codigo == x.codigo_rips else "EN RIPS, NO FACTURADO EN 360"
        a = []
        if x.tipo_servicio not in ("Urgencias", "Hospitalización") and abs(x.dif_cantidad) > 0.001: a.append("CANTIDAD DIFIERE")
        if x.valor_rips > 0 and abs(x.dif_valor) > max(1.0, 0.001 * x.valor_360): a.append("VALOR DIFIERE")
        return "; ".join(a + xa) or "OK"
    m["estado"] = m.apply(est, axis=1)
    m = m[~((m.tipo_servicio.isin(["Urgencias", "Hospitalización", "Recién nacidos", "SIN SERVICIOS"])) & (m.codigo.fillna("") == ""))]
    if not hay360: m[["cant_360", "valor_360", "dif_cantidad", "dif_valor"]] = np.nan
    cols = ["factura", "tipo_servicio", "servicio", "codigo", "codigo_rips", "actividad", "desc_rips", "cant_360", "cant_xml", "cant_rips", "dif_cantidad",
            "valor_360", "valor_xml", "valor_rips", "dif_valor", "estado"]
    return m[cols].sort_values("dif_valor", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)

REGLAS_RIPS = {
    "RV01": ("Servicio sin código (CUPS/CUM)", lambda d: (d.codigo == "") & ~d.tipo_servicio.isin(["Urgencias", "Hospitalización", "Recién nacidos", "SIN SERVICIOS"])),
    "RV02": ("Servicio con valor en cero (consultas, procedimientos, medicamentos, otros)", lambda d: (d.valor <= 0) & d.tipo_servicio.isin(["Consultas", "Procedimientos", "Medicamentos", "Otros servicios"])),
    "RV03": ("Sin diagnóstico principal (consultas; procedimientos en JSON)", lambda d: (d.diagnostico == "") & ((d.tipo_servicio == "Consultas")
             | ((d.tipo_servicio == "Procedimientos") & (d.origen == "JSON")))),
    "RV04": ("Usuario sin número de documento", lambda d: d.paciente_id == ""),
    "RV05": ("Servicio prestado en un mes distinto al de la factura (facturación tardía)", lambda d: (d.fecha != "") & (d.fecha.str[:7] != d.mes)),
    "RV06": ("Valor ≠ cantidad × valor unitario (medicamentos y otros servicios)", lambda d: d.tipo_servicio.isin(["Medicamentos", "Otros servicios"]) & (d.valor_unitario > 0)
             & ((d.cantidad * d.valor_unitario - d.valor).abs() > 1)),
    "RV07": ("Servicio repetido (misma factura, código, usuario y fecha)", lambda d: d.duplicated(["factura", "tipo_servicio", "codigo", "paciente_id", "fecha"], keep=False) & (d.codigo != "")),
    "RV08": ("Factura con servicios de más de un usuario", lambda d: d.groupby("factura").paciente_id.transform("nunique") > 1),
}

def validacion(mes=None, regla=None):
    """Reglas de estructura del RIPS (RV01–RV08): resumen por regla o detalle de una regla."""
    w, p = _w(mes)
    d = df(f"SELECT * FROM rips_det{w}", p)
    if d.empty: return pd.DataFrame(columns=["regla", "descripcion", "registros", "facturas", "valor"])
    if regla:
        x = d[REGLAS_RIPS[regla][1](d)]
        return x[["mes", "factura", "tipo_servicio", "codigo", "descripcion", "cantidad", "valor_unitario", "valor", "fecha", "diagnostico", "origen", "archivo"]]
    out = []
    for k, (txt, f) in REGLAS_RIPS.items():
        mk = f(d)
        if mk.any(): out.append(dict(regla=k, descripcion=txt, registros=int(mk.sum()), facturas=int(d.loc[mk, "factura"].nunique()), valor=float(d.loc[mk, "valor"].sum())))
    return pd.DataFrame(out, columns=["regla", "descripcion", "registros", "facturas", "valor"])

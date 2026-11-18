"""RIPS en sus formatos de envío: JSON (Res. 2275 de 2023 / 1557 de 2023, un archivo por factura electrónica), el XML de la factura
electrónica (FEV, AttachedDocument de la DIAN: valor, fecha, EPS, contrato, modalidad, CUFE, estado DIAN y líneas con CUPS) y TXT planos
(Res. 3374 de 2000: AF, US, AC, AP, AM, AT, AU, AH, AN, CT). Ambos se pueden subir sueltos o dentro de .zip / .rar / .7z.

Cada servicio queda en `rips_det` (paciente con hash) y además se agrega a `rips` / `rips_factura`, de modo que la conciliación
Facturación vs RIPS, la radicación y el cruce de medicamentos funcionan igual que con el consolidado en Excel.
También se leen las respuestas de validación del Ministerio (JSON con CodigoUnicoValidacion / ResultState) para marcar el CUV de cada factura.
"""
import hashlib, html, json, os, re, shutil, zipfile
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np, pandas as pd
from .db import df
from .loaders import norm, num, _ymd, cum_norm, mapa_cum

TIPOS_JSON = {"consultas": "Consultas", "procedimientos": "Procedimientos", "urgencias": "Urgencias", "hospitalizacion": "Hospitalización",
              "reciennacidos": "Recién nacidos", "medicamentos": "Medicamentos", "otrosservicios": "Otros servicios"}
PREFIJOS_TXT = {"AF": "Transacciones", "US": "Usuarios", "AC": "Consultas", "AP": "Procedimientos", "AM": "Medicamentos",
                "AT": "Otros servicios", "AU": "Urgencias", "AH": "Hospitalización", "AN": "Recién nacidos", "CT": "Control"}
COLS_DET = ["mes", "factura", "tipo_servicio", "codigo", "descripcion", "cantidad", "valor_unitario", "valor", "paciente_id", "tipo_doc",
            "fecha", "diagnostico", "autorizacion", "origen", "archivo", "moderadora"]      # moderadora: cuota moderadora / copago del servicio

def _s(v):
    """Texto limpio: los generadores de RIPS escriben «None» / «null» en los campos vacíos."""
    t = str(v if v is not None else "").strip()
    return "" if t.lower() in ("none", "null", "nan") else t
def _h(doc): doc = _s(doc); return hashlib.sha1(doc.encode()).hexdigest()[:12] if doc else ""
def _low(d): return {re.sub(r"[^a-z]", "", str(k).lower()): (v if isinstance(v, (dict, list)) else _s(v)) for k, v in d.items()} if isinstance(d, dict) else {}
def _fecha(v):
    s = str(v or "").strip()
    return _ymd(s.split(" ")[0].split("T")[0]) if s else ""

# ───────── fuentes: archivos sueltos o archivos dentro de un .zip, leídos SIN descomprimir ─────────
# Los envíos de RIPS de un año pueden pesar varios GB comprimidos (decenas de GB en JSON). Leer cada archivo directamente del .zip evita
# llenar el disco y el límite de rutas de Windows; los JSON grandes se leen usuario por usuario (ijson) y las filas se guardan por lotes.
LIMITE_JSON_MEMORIA = 120 * 1024 * 1024        # JSON más grandes que esto se leen en streaming
LOTE = 50_000
COLS = [c for c in COLS_DET if c != "mes"]

class Fuente:
    """Un archivo RIPS: suelto en disco o dentro de un comprimido. `abrir()` devuelve un archivo binario."""
    def __init__(self, nombre, abrir, tam=0): self.nombre, self.abrir, self.tam = nombre.replace("\\", "/"), abrir, tam
    @property
    def base(self): return self.nombre.rstrip("/").rsplit("/", 1)[-1]
    @classmethod
    def ruta(cls, p): return cls(str(p), lambda: open(p, "rb"), os.path.getsize(p))

def fuentes(ruta, avance=None):
    """Fuentes RIPS de un archivo: un .zip se lee en su lugar (sin extraer); los comprimidos anidados y los .rar/.7z/.tar se extraen."""
    from .formatos import EXT_COMPRIMIDO, extension, extraer, util
    ext = extension(ruta)
    if ext == ".zip":
        # 1) directorio central del zip (lo normal); 2) si está dañado (descargas grandes de Google Drive, descarga incompleta),
        #    recorrido secuencial de los encabezados locales (zipseq), también sin descomprimir al disco
        miembros = None
        try:
            z = zipfile.ZipFile(ruta); miembros = [(m.filename, m.file_size, (lambda m=m: z.open(m))) for m in z.infolist() if not m.is_dir()]
            for m in z.infolist():
                if not m.is_dir() and util(m.filename): z.open(m).close()        # valida el encabezado local de cada miembro
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError, ValueError, NotImplementedError):
            from . import zipseq
            if avance: avance("El índice del .zip está dañado: se recorre el archivo completo para encontrar los RIPS…")
            ent = zipseq.indexar(ruta, avance)
            if not ent: raise ValueError("El .zip está dañado o incompleto y no se encontró ningún archivo. Vuelva a descargarlo.")
            miembros = [(e.nombre, e.usize, (lambda e=e: zipseq.abrir(ruta, e))) for e in ent]
        out, n = [], 0
        for nombre, tam, abrir in miembros:
            partes = [x.strip() for x in nombre.replace("\\", "/").split("/") if x.strip()]
            if not partes or any(x.startswith(("__MACOSX", ".")) for x in partes) or not util(nombre): continue
            e = extension(partes[-1])
            if e in EXT_COMPRIMIDO or e == ".tar.gz":
                n += 1; dst = os.path.join(ruta + "_extraido", f"a{n:04d}{e}"); os.makedirs(os.path.dirname(dst), exist_ok=True)
                with abrir() as s_, open(dst, "wb") as o: shutil.copyfileobj(s_, o, 1 << 20)
                out += [Fuente("/".join(partes) + " › " + f.nombre, f.abrir, f.tam) for f in fuentes(dst)]
            else: out.append(Fuente("/".join(partes), abrir, tam))
        return out
    if ext in EXT_COMPRIMIDO or ext == ".tar.gz":
        from .formatos import nombre_interno
        base = ruta + "_extraido"
        return [Fuente(nombre_interno(r, base), (lambda r=r: open(r, "rb")), os.path.getsize(r)) for r in extraer(ruta, base, avance=avance)]
    return [Fuente.ruta(ruta)]

def es_rips(f):
    """True si el archivo es un RIPS JSON (o respuesta de validación), el XML de la FEV o un TXT de RIPS (AF/AC/AP… por el nombre)."""
    if not isinstance(f, Fuente): f = Fuente.ruta(f)
    n = f.base.upper()
    if n.endswith(".JSON"): return True
    if n.endswith(".XML"):
        with f.abrir() as h: cab = h.read(4096)
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
                   "RECHAZADO" if (str(l.get("resultstate")).lower() == "false" or rech) else "VALIDADO (CUV)",
                   "; ".join(f"{_low(r).get('codigo', '')} {_low(r).get('descripcion', '')}".strip() for r in rech)[:300])
        for v in o.values():
            if isinstance(v, (dict, list)) and not ("codigounicovalidacion" in l or "resultstate" in l): yield from _validaciones_json(v)

def _filas_usuario(nf, u, arch):
    """Servicios de un usuario del JSON → tuplas en el orden de COLS."""
    lu = _low(u); pid = _h(lu.get("numdocumentoidentificacion")); td = str(lu.get("tipodocumentoidentificacion") or "")
    serv = _low(lu.get("servicios") or {}); hay = False
    for clave, tipo in TIPOS_JSON.items():
        for s in serv.get(clave) or []:
            ls = _low(s); hay = True
            cod = (ls.get("codconsulta") or ls.get("codprocedimiento") or ls.get("codtecnologiasalud") or "")
            cant = num(ls.get("cantidadmedicamento") or ls.get("cantidados") or 1) or 1.0
            vu = num(ls.get("vrunitmedicamento") or ls.get("vrunitos") or 0); val = num(ls.get("vrservicio") or 0)
            fecha = _fecha(ls.get("fechainicioatencion") or ls.get("fechadispensadmon") or ls.get("fechasuministrotecnologia") or ls.get("fechanacimiento"))
            yield (nf, tipo, str(cod).strip(), str(ls.get("nomtecnologiasalud") or "").strip(), cant, vu or (val / cant if cant else 0), val, pid, td, fecha,
                   str(ls.get("coddiagnosticoprincipal") or "").strip(), str(ls.get("numautorizacion") or "").strip(), "JSON", arch, num(ls.get("valorpagomoderador") or 0))
    if not hay: yield (nf, "SIN SERVICIOS", "", "", 0.0, 0.0, 0.0, pid, td, "", "", "", "JSON", arch, 0.0)

def _sin_bom(h):
    try:
        if h.peek(3)[:3] == b"\xef\xbb\xbf": h.read(3)
    except AttributeError: pass
    return h

def leer_json(f, val):
    """Filas de un RIPS JSON (generador). Las respuestas de validación (CUV) se agregan a `val`. Los JSON grandes se leen en streaming."""
    if not isinstance(f, Fuente): f = Fuente.ruta(f)
    arch = f.base
    if f.tam <= LIMITE_JSON_MEMORIA:
        with f.abrir() as h: crudo = h.read()
        o = None
        for cod in ("utf-8-sig", "cp1252"):
            try: o = json.loads(crudo.decode(cod)); break
            except (UnicodeDecodeError, json.JSONDecodeError): o = None
        del crudo
        if o is None: raise ValueError(f"«{arch}» no es un JSON válido.")
        val += list(_validaciones_json(o))
        for fac in _facturas_json(o):
            lf = _low(fac); nf = str(lf.get("numfactura") or "").strip()
            for u in lf.get("usuarios") or []: yield from _filas_usuario(nf, u, arch)
        return
    import ijson
    with f.abrir() as h:
        h = _sin_bom(h); ini = h.peek(64).lstrip()[:1] if hasattr(h, "peek") else b"{"
    try:
        if ini == b"[":                                                  # lista de facturas
            with f.abrir() as h:
                for fac in ijson.items(_sin_bom(h), "item", use_float=True):
                    for x in _facturas_json(fac):
                        lf = _low(x); nf = str(lf.get("numfactura") or "").strip()
                        for u in lf.get("usuarios") or []: yield from _filas_usuario(nf, u, arch)
            return
        cab = {}                                                         # una factura: encabezado y luego usuario por usuario
        with f.abrir() as h:
            for pre, ev, v in ijson.parse(_sin_bom(h), use_float=True):
                if "." not in pre and ev in ("string", "number", "null"): cab[re.sub(r"[^a-z]", "", pre.lower())] = v
                if "numfactura" in cab and pre == "usuarios" and ev == "start_array": break
        nf = _s(cab.get("numfactura"))
        with f.abrir() as h:
            for u in ijson.items(_sin_bom(h), "usuarios.item", use_float=True): yield from _filas_usuario(nf, u, arch)
    except ijson.JSONError as e: raise ValueError(f"«{arch}» no es un JSON válido ({e}).")

# ───────── XML de la factura electrónica (FEV) ─────────
NIT_ESE = "900034131"
def _tag(x, t):
    return [html.unescape(v).strip() for v in re.findall(rf"<cbc:{t}\b[^>]*>(.*?)</cbc:{t}>", x, re.S)]

def _modalidad(txt):
    n = norm(txt)
    return "CAPITA" if "CAPITA" in n else "EVENTO" if "EVENTO" in n else "PGP" if ("GLOBAL PROSPECTIVO" in n or "PGP" in n) else ""

def leer_xml(f):
    """AttachedDocument (o Invoice) de la DIAN → (encabezado, líneas). El Invoice viene en CDATA dentro del AttachedDocument."""
    if not isinstance(f, Fuente): f = Fuente.ruta(f)
    with f.abrir() as h: x = h.read().decode("utf-8", errors="replace")
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
               cufe=(_tag(inv, "UUID") or [""])[0], estado_dian=dian, archivo=f.base)
    lineas = []
    for l in re.findall(r"<cac:(?:Invoice|CreditNote|DebitNote)Line>(.*?)</cac:(?:Invoice|CreditNote|DebitNote)Line>", inv, re.S):
        cod = (re.search(r"<cac:SellersItemIdentification>\s*<cbc:ID[^>]*>(.*?)</cbc:ID>", l, re.S) or
               re.search(r"<cac:StandardItemIdentification>\s*<cbc:ID[^>]*>(.*?)</cbc:ID>", l, re.S))
        lineas.append(dict(factura=fac, codigo=cod.group(1).strip() if cod else "", descripcion=(_tag(l, "Description") or [""])[0],
                           cantidad=num((_tag(l, "InvoicedQuantity") or ["1"])[0]), valor=num((_tag(l, "LineExtensionAmount") or ["0"])[0])))
    return enc, lineas

# ───────── TXT (Res. 3374 de 2000) ─────────
def _lineas_txt(f):
    if not isinstance(f, Fuente): f = Fuente.ruta(f)
    with f.abrir() as h:
        for b in h:
            try: l = b.decode("utf-8-sig")
            except UnicodeDecodeError: l = b.decode("cp1252", errors="replace")
            if l.strip(): yield [x.strip() for x in l.rstrip("\r\n").split(",")]

def leer_txt(fs):
    """Un juego de RIPS TXT (puede venir de varias remisiones) → (af, generador de filas). AF da EPS, contrato, fecha y valor neto de cada factura."""
    fs = [f if isinstance(f, Fuente) else Fuente.ruta(f) for f in fs]
    por = defaultdict(list)
    for f in fs: por[f.base.upper()[:2]].append(f)
    af = {}
    for f in por.get("AF", []):
        for x in _lineas_txt(f):
            if len(x) >= 17: af[x[4]] = dict(fecha=_ymd(x[5]), cod_eps=x[8], eps=x[9], contrato=x[10], valor_neto=num(x[16]))
    def filas():
        def t(f, tipo, x, cod, desc, cant, vu, val, fecha, dx, aut, mod=0.0):
            return (x[0], tipo, cod, desc, cant or 1.0, vu, val, _h(x[3]), x[2], fecha or af.get(x[0], {}).get("fecha", ""), dx, aut, "TXT", f.base, mod)
        for f in por.get("AC", []):
            for x in _lineas_txt(f):
                if len(x) >= 15: yield t(f, "Consultas", x, x[6], "", 1.0, num(x[14]), num(x[14]), _ymd(x[4]), x[9], x[5], num(x[15]) if len(x) > 15 else 0.0)
        for f in por.get("AP", []):
            for x in _lineas_txt(f):
                if len(x) >= 15: yield t(f, "Procedimientos", x, x[6], "", 1.0, num(x[14]), num(x[14]), _ymd(x[4]), x[10], x[5])
        for f in por.get("AM", []):
            for x in _lineas_txt(f):
                if len(x) >= 14: yield t(f, "Medicamentos", x, x[5], x[7], num(x[11]), num(x[12]), num(x[13]), "", "", x[4])
        for f in por.get("AT", []):
            for x in _lineas_txt(f):
                if len(x) >= 11: yield t(f, "Otros servicios", x, x[6], x[7], num(x[8]), num(x[9]), num(x[10]), "", "", x[4])
        for pref, tipo in (("AU", "Urgencias"), ("AH", "Hospitalización")):
            for f in por.get(pref, []):
                for x in _lineas_txt(f):
                    if len(x) >= 5: yield (x[0], tipo, "", "", 0.0, 0.0, 0.0, _h(x[3]), x[2], _ymd(x[4] if pref == "AU" else x[5] if len(x) > 5 else ""), "", "", "TXT", f.base, 0.0)
    return af, filas()

# ───────── carga por lotes ─────────
def _fk1(f): return re.sub(r"[^A-Z0-9]", "", str(f).upper())

def cargar(cid, fs, mes_param=None, avance=None, reemplazar=None):
    """Lee las fuentes RIPS y las guarda por lotes (rips_det / rips / rips_factura / fev_* / rips_val*). Devuelve un resumen.
    La memoria usada no depende del tamaño del envío: los servicios se escriben una sola vez en rips_det (con mes vacío) y el mes, la EPS y el contrato de
    cada factura se resuelven con SQL al final."""
    from .db import conn
    av = avance or (lambda t: None)
    fs = [f if isinstance(f, Fuente) else Fuente.ruta(f) for f in fs]
    txt = [f for f in fs if f.base.lower().endswith(".txt") and f.base.upper()[:2] in PREFIJOS_TXT]
    jsons = [f for f in fs if f.base.lower().endswith(".json")]; xmls = [f for f in fs if f.base.lower().endswith(".xml")]
    val, xml, xlin, errores, n, orig = [], [], [], [], 0, Counter()
    for i, f in enumerate(xmls, 1):
        if i % 200 == 0: av(f"Leyendo XML de facturas {i:,} de {len(xmls):,}…")
        try: e, l = leer_xml(f)
        except Exception: continue
        if e["factura"]: xml.append(e); xlin += l
    with conn() as c:
        c.execute("DELETE FROM rips_det WHERE carga_id=?", (cid,)); c.execute("DELETE FROM rips_fi WHERE carga_id=?", (cid,))
    ins = f"INSERT INTO rips_det(carga_id, fk, {', '.join(COLS)}) VALUES(" + ",".join(["?"] * (len(COLS) + 2)) + ")"
    buf = []
    def vaciar():
        nonlocal buf
        if buf:
            with conn() as c: c.executemany(ins, buf)
            buf = []
    def agregar(filas):
        nonlocal n
        for r in filas:
            buf.append((cid, _fk1(r[0]), *r)); n += 1; orig[r[12]] += 1
            if len(buf) >= LOTE:
                vaciar(); av(f"Leyendo RIPS: {n:,} servicios…")
    tot = sum(f.tam for f in jsons) or 1; leido = 0
    for i, f in enumerate(jsons, 1):
        av(f"Leyendo JSON {i:,} de {len(jsons):,} ({leido / tot:.0%} del volumen) · {n:,} servicios…") if i % 25 == 0 or f.tam > 20e6 else None
        try: agregar(leer_json(f, val))
        except (ValueError, RecursionError, MemoryError) as e: errores.append(str(e)[:200])
        leido += f.tam
    af = {}
    if txt:
        av(f"Leyendo {len(txt):,} archivos TXT…"); af, gen = leer_txt(txt); agregar(gen)
    vaciar()
    if not n and not val and not xml:
        raise ValueError("Los archivos no contienen RIPS JSON (numFactura + usuarios), XML de facturas (FEV) ni RIPS TXT." + (f" {errores[0]}" if errores else ""))
    av(f"{n:,} servicios leídos. Ubicando el mes, la EPS y el contrato de cada factura…")
    with conn() as c: c.execute("CREATE INDEX IF NOT EXISTS i_rdet_fk ON rips_det(carga_id, fk, paciente_id)")
    fac = df("SELECT fk, MAX(factura) factura, SUM(valor) valor_srv, SUM(cantidad) servicios, COUNT(*) filas, COUNT(DISTINCT paciente_id) usuarios "
             "FROM rips_det WHERE carga_id=? GROUP BY fk", (cid,))
    moda = df("SELECT fk, substr(fecha,1,7) m, COUNT(*) n FROM rips_det WHERE carga_id=? AND fecha<>'' GROUP BY fk, m", (cid,))
    moda = moda.sort_values(["fk", "n"], ascending=[True, False]).drop_duplicates("fk").set_index("fk").m if len(moda) else pd.Series(dtype=str)
    f360 = df("SELECT factura, MAX(mes) mes, MAX(eps_nombre) eps, MAX(contrato) contrato, MAX(modalidad) modalidad, MAX(sede) sede FROM factura GROUP BY factura")
    f360["fk"] = _fk(f360.factura); f360 = f360.drop_duplicates("fk").set_index("fk")
    fx = pd.DataFrame(xml, columns=["factura", "fecha", "valor", "eps", "nit", "contrato", "modalidad", "cobertura", "cufe", "estado_dian", "archivo"])
    fx["fk"] = _fk(fx.factura); fx = fx.drop_duplicates("fk", keep="last"); fxi = fx.set_index("fk")
    af_k = {_fk1(k): v for k, v in af.items()}
    s = lambda d, k: pd.Series({x: v.get(k, "") for x, v in d.items()}, dtype=object)
    # mes = el de la factura (XML de la FEV, Software 360 o AF del TXT); la fecha más frecuente de los servicios solo si no hay otra fuente
    fac["mes"] = fac.fk.map(fxi.fecha.str[:7].replace("", None)).fillna(fac.fk.map(f360.mes)).fillna(fac.fk.map(s(af_k, "fecha").str[:7].replace("", None))) \
        .fillna(fac.fk.map(moda)).fillna(mes_param or "")
    def g(col, fuente, xcol):
        v = fac.fk.map(s(af_k, fuente)).fillna("")
        v = v.where(v != "", fac.fk.map(fxi[xcol]).fillna(""))
        return v.where(v != "", fac.fk.map(f360[col]).fillna(""))
    fac["eps"] = g("eps", "eps", "eps"); fac["contrato"] = g("contrato", "contrato", "contrato")
    mx = fac.fk.map(fxi.modalidad).fillna(""); fac["modalidad"] = mx.where(mx != "", fac.fk.map(f360.modalidad).fillna("")); fac["sede"] = fac.fk.map(f360.sede).fillna("")
    fac["valor_fev"] = fac.fk.map(fxi.valor).fillna(fac.fk.map(s(af_k, "valor_neto").replace("", None))).fillna(fac.valor_srv)
    vmap = {_fk1(f): (cuv, est, det) for f, cuv, est, det in val if f}
    fac["validacion"] = fac.fk.map(lambda x: (f"{vmap[x][1]} · CUV {vmap[x][0]}" + (f" · {vmap[x][2]}" if vmap[x][2] else "")) if x in vmap else "")
    fac["validacion"] = fac.validacion.where(fac.validacion != "", fac.fk.map(fxi.estado_dian).fillna(""))
    if len(fac) and (fac.mes == "").all(): raise ValueError("No se pudo determinar el mes de los RIPS: indique el mes (AAAA-MM) al cargar.")
    fac = fac[fac.mes != ""].sort_values("fk").reset_index(drop=True)
    fac["lote"] = 0
    mes_fk = fac.set_index("fk").mes
    fx["mes"] = fx.fk.map(mes_fk).fillna(fx.fecha.str[:7])
    xl = pd.DataFrame(xlin, columns=["factura", "codigo", "descripcion", "cantidad", "valor"]); xl["mes"] = _fk(xl.factura).map(mes_fk).fillna(_fk(xl.factura).map(fxi.fecha.str[:7]))
    meses = sorted(set(fac.mes) | set(fx.mes.dropna()) - {""})
    av(f"Guardando {n:,} servicios de {len(fac):,} facturas en {len(meses)} mes(es)…")
    with conn() as c:
        if reemplazar: reemplazar(c, meses)
        fac.assign(carga_id=cid)[["carga_id", "fk", "factura", "mes", "eps", "contrato", "modalidad", "sede", "lote", "usuarios"]].to_sql("rips_fi", c, if_exists="append", index=False)
        c.execute("UPDATE rips_det SET mes=(SELECT f.mes FROM rips_fi f WHERE f.carga_id=rips_det.carga_id AND f.fk=rips_det.fk) WHERE carga_id=?", (cid,))
        c.execute("DELETE FROM rips_det WHERE carga_id=? AND mes IS NULL", (cid,))
        c.execute("""INSERT INTO rips(carga_id, mes, eps, nit_eps, contrato, cucon, modalidad, sede, servicio, cups, factura, cantidad, valor, descripcion)
                     SELECT s.carga_id, f.mes, f.eps, '', f.contrato, '', f.modalidad, f.sede, s.tipo_servicio, s.codigo, f.factura, SUM(s.cantidad), SUM(s.valor), MAX(s.descripcion)
                     FROM rips_det s JOIN rips_fi f ON f.carga_id=s.carga_id AND f.fk=s.fk WHERE s.carga_id=? GROUP BY f.fk, s.tipo_servicio, s.codigo""", (cid,))
        fac.assign(carga_id=cid, factura_rips=fac.factura, cucon="")[["carga_id", "mes", "factura", "factura_rips", "eps", "contrato", "cucon", "modalidad", "sede",
                                                                    "valor_fev", "servicios", "validacion"]].to_sql("rips_factura", c, if_exists="append", index=False)
        fx.assign(carga_id=cid)[["carga_id", "mes", "factura", "fecha", "valor", "eps", "nit", "contrato", "modalidad", "cobertura", "cufe", "estado_dian", "archivo"]] \
            .to_sql("fev_xml", c, if_exists="append", index=False)
        xl.assign(carga_id=cid)[["carga_id", "mes", "factura", "codigo", "descripcion", "cantidad", "valor"]].to_sql("fev_linea", c, if_exists="append", index=False)
    _validar_carga(cid, fac, av)
    with conn() as c:
        c.execute("DELETE FROM rips_fi WHERE carga_id=?", (cid,))
        c.execute("DROP INDEX IF EXISTS i_rdet_fk")                     # solo se usa al cargar: se libera el espacio
    return dict(facturas=len(fac), servicios=int(df("SELECT COUNT(*) n FROM rips_det WHERE carga_id=?", (cid,)).n.iloc[0]), origen=dict(orig),
                xml=len(fx), validaciones=len(val), meses=meses, errores=errores)

DETALLE_MAX = 3000          # filas de ejemplo guardadas por regla y carga (el resumen cuenta todas)

def _validar_carga(cid, fac, av):
    """Reglas RV01–RV10 por lotes de facturas (una factura nunca queda partida entre lotes): guarda totales por mes y regla y un detalle de ejemplo."""
    from .db import conn
    tot, facs, det, nd = defaultdict(lambda: [0, 0.0]), defaultdict(set), [], Counter()
    # lotes de ~200.000 servicios; una factura enorme (cápita de un mes) se parte por usuario: los servicios de un usuario nunca quedan partidos
    g = df("SELECT fk, paciente_id, COUNT(*) n FROM rips_det WHERE carga_id=? GROUP BY fk, paciente_id ORDER BY fk, paciente_id", (cid,))
    g["lote"] = (g.n.cumsum() // 200_000).astype(int)
    with conn() as c:
        c.execute("DELETE FROM rips_lote WHERE carga_id=?", (cid,))
        g.assign(carga_id=cid)[["carga_id", "fk", "paciente_id", "lote"]].to_sql("rips_lote", c, if_exists="append", index=False)
    lotes = sorted(g.lote.unique()); del g
    for j, lote in enumerate(lotes, 1):
        av(f"Validando estructura del RIPS: lote {j} de {len(lotes)}…")
        d = df(f"SELECT f.mes, s.factura, {', '.join('s.' + x for x in COLS if x != 'factura')}, f.modalidad, f.usuarios usuarios_fac FROM rips_lote l "
               "JOIN rips_det s ON s.carga_id=l.carga_id AND s.fk=l.fk AND s.paciente_id=l.paciente_id "
               "JOIN rips_fi f ON f.carga_id=s.carga_id AND f.fk=s.fk WHERE l.carga_id=? AND l.lote=?", (cid, int(lote)))
        if d.empty: continue
        d["glob"] = es_global(d)
        for k, (_, fn) in REGLAS_RIPS.items():
            mk = fn(d)
            if not mk.any(): continue
            x = d[mk]
            for mes, y in x.groupby("mes"):
                t = tot[(mes, k)]; t[0] += len(y); t[1] += float(y.valor.sum()); facs[(mes, k)] |= set(y.factura)
            if nd[k] < DETALLE_MAX:
                y = x.head(DETALLE_MAX - nd[k]); nd[k] += len(y)
                det.append(y.assign(regla=k)[["mes", "regla", "factura", "tipo_servicio", "codigo", "descripcion", "cantidad", "valor_unitario", "valor", "fecha",
                                              "diagnostico", "origen", "archivo"]])
    with conn() as c:
        c.executemany("INSERT INTO rips_val VALUES(?,?,?,?,?,?)", [(cid, m, k, v[0], len(facs[(m, k)]), v[1]) for (m, k), v in tot.items()]
                      + [(cid, m, "_", 0, 0, 0.0) for m in sorted(set(fac.mes))])           # «_»: mes validado (aunque no tenga hallazgos)
        c.execute("DELETE FROM rips_lote WHERE carga_id=?", (cid,))
        if det: pd.concat(det).assign(carga_id=cid).to_sql("rips_val_det", c, if_exists="append", index=False)

def procesar(rutas, mes_param=None, avance=None):
    raise NotImplementedError("Use rips_fev.cargar (carga por lotes).")

# ───────── análisis (pestaña «RIPS JSON/TXT vs 360») ─────────
def _w(mes): return (" WHERE mes=?", (mes,)) if mes else ("", ())

def resumen(mes=None):
    w, p = _w(mes)
    t = df(f"SELECT COUNT(*) servicios, COUNT(DISTINCT factura) facturas, SUM(valor) valor, COUNT(DISTINCT NULLIF(paciente_id,'')) usuarios, "
           f"SUM(origen='JSON') json, SUM(origen='TXT') txt FROM rips_det{w}", p).iloc[0]
    if not t.servicios: return dict(kpis={}, vacio=True)
    cf = cruce_facturas(mes); vs = validacion(mes)
    k = dict(facturas=int(t.facturas), valor=float(t.valor or 0), usuarios=int(t.usuarios), servicios=int(t.servicios), json=int(t.json or 0), txt=int(t.txt or 0),
             xml=int(df(f"SELECT COUNT(*) n FROM fev_xml{w}", p).n.iloc[0]), dian_validadas=int(df(f"SELECT COUNT(*) n FROM fev_xml{w} AND estado_dian='VALIDADO DIAN'" if w else "SELECT COUNT(*) n FROM fev_xml WHERE estado_dian='VALIDADO DIAN'", p).n.iloc[0]),
             facturas_ok=int((cf.estado == "OK").sum()) if len(cf) else 0, facturas_con_diferencia=int(((cf.estado != "OK") & ~cf.estado.str.startswith("INFO")).sum()) if len(cf) else 0, sin_360=int(cf.estado.str.startswith("INFO").sum()) if len(cf) else 0,
             valor_diferencia=float(cf.loc[cf.estado.str.contains("DIFIERE|≠"), "dif"].abs().sum()) if len(cf) else 0.0, hallazgos_estructura=int(vs.registros.sum()) if len(vs) else 0)
    pt = df(f"SELECT tipo_servicio, COUNT(*) servicios, SUM(valor) valor, COUNT(DISTINCT paciente_id) usuarios FROM rips_det{w} GROUP BY tipo_servicio", p)
    return dict(kpis=k, por_tipo=pt.to_dict("records"),
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

def cruce_servicios(mes=None, tipo=None, lineas=None):
    """Factura + código: cantidad y valor en 360 (líneas) vs RIPS (servicios). Medicamentos/insumos por la homologación CUM ↔ código 360.
    `lineas` permite cruzar otra fuente de 360 (p. ej. la facturación por actividad): columnas factura, codigo, actividad, servicio, cant_360, valor_360."""
    w, p = _w(mes)
    r = df(f"SELECT factura, tipo_servicio, codigo codigo_rips, MAX(descripcion) desc_rips, SUM(cantidad) cant_rips, SUM(valor) valor_rips FROM rips_det{w} "
           f"GROUP BY factura, tipo_servicio, codigo", p)
    if r.empty: return r
    if tipo: r = r[r.tipo_servicio == tipo]
    l = lineas.copy() if lineas is not None else df(f"SELECT factura, CASE WHEN cod_proc<>'' THEN cod_proc ELSE cod_med END codigo, MAX(actividad) actividad, "
                                                  f"MAX(servicio) servicio, SUM(cantidad) cant_360, SUM(valor) valor_360 FROM linea{w} GROUP BY factura, codigo", p)
    hm = mapa_cum()
    r["codigo"] = r.codigo_rips.map(lambda c: hm.get(cum_norm(c), c))
    r["fk"] = _fk(r.factura); l["fk"] = _fk(l.factura)
    fr = set(r.fk); l = l[l.fk.isin(fr)]                                # solo facturas que están en el RIPS
    m = l.merge(r.drop(columns=["factura"]), on=["fk", "codigo"], how="outer")
    xl = df(f"SELECT factura, codigo, SUM(cantidad) cant_xml, SUM(valor) valor_xml FROM fev_linea{w} GROUP BY factura, codigo", p)
    if len(xl):
        xl["fk"] = _fk(xl.factura); xl["codigo"] = xl.codigo.map(lambda c: hm.get(cum_norm(c), c))
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

USUARIOS_GLOBAL = 10      # una factura con 10 o más usuarios se trata como global (cápita/PGP) si no se conoce su modalidad

def _usuarios(d):
    """Usuarios distintos de la factura de cada fila (precalculado al cargar cuando la factura se valida por partes)."""
    return d.usuarios_fac if "usuarios_fac" in d else d.groupby("factura").paciente_id.transform("nunique")

def es_global(d):
    """Facturas cápita/PGP: modalidad conocida (XML, AF o 360) o, si no se conoce, factura con muchos usuarios (RIPS mensual de cápita)."""
    mod = (d.modalidad if "modalidad" in d else d.factura.map(df("SELECT factura, MAX(modalidad) modalidad FROM rips_factura GROUP BY factura").set_index("factura").modalidad)).fillna("").astype(str).str.upper()
    usu = _usuarios(d)
    return mod.str.contains("CAPITA|CÁPITA|PGP|GLOBAL") | ((mod == "") & (usu >= USUARIOS_GLOBAL))

REGLAS_RIPS = {
    "RV01": ("Servicio sin código (CUPS/CUM)", lambda d: (d.codigo == "") & ~d.tipo_servicio.isin(["Urgencias", "Hospitalización", "Recién nacidos", "SIN SERVICIOS"])),
    "RV02": ("Servicio con valor en cero (consultas, procedimientos, medicamentos, otros; sin cápita/PGP)", lambda d: (d.valor <= 0) & d.tipo_servicio.isin(["Consultas", "Procedimientos", "Medicamentos", "Otros servicios"]) & ~d.glob),
    "RV03": ("Sin diagnóstico principal (consultas; procedimientos en JSON)", lambda d: (d.diagnostico == "") & ((d.tipo_servicio == "Consultas")
             | ((d.tipo_servicio == "Procedimientos") & (d.origen == "JSON")))),
    "RV04": ("Usuario sin número de documento", lambda d: d.paciente_id == ""),
    "RV05": ("Servicio prestado en un mes anterior al de la factura (facturación tardía)", lambda d: (d.fecha != "") & (d.fecha.str[:7] < d.mes)),
    "RV06": ("Valor ≠ cantidad × valor unitario (medicamentos y otros servicios)", lambda d: d.tipo_servicio.isin(["Medicamentos", "Otros servicios"]) & (d.valor_unitario > 0)
             & ((d.cantidad * d.valor_unitario - d.valor).abs() > 1)),
    "RV07": ("Servicio repetido (misma factura, código, usuario y fecha; sin cápita/PGP)", lambda d: d.duplicated(["factura", "tipo_servicio", "codigo", "paciente_id", "fecha"], keep=False) & (d.codigo != "") & ~d.glob),
    "RV09": ("Servicio con fecha posterior al mes de la factura (fecha errada o servicio facturado antes de prestarse)", lambda d: (d.fecha != "") & (d.fecha.str[:7] > d.mes)),
    "RV10": ("Archivo JSON sin número de factura (numFactura vacío)", lambda d: d.factura.fillna("").str.strip() == ""),
    "RV08": ("Factura de evento con servicios de más de un usuario", lambda d: (_usuarios(d) > 1) & ~d.glob),
}

def validacion(mes=None, regla=None):
    """Reglas de estructura del RIPS (RV01–RV10): resumen por regla o detalle de una regla."""
    w, p = _w(mes)
    cols = ["regla", "descripcion", "registros", "facturas", "valor"]
    if not df(f"SELECT COUNT(*) n FROM rips_det{w}", p).n.iloc[0]: return pd.DataFrame(columns=cols)
    pre = df(f"SELECT COUNT(*) n FROM rips_val{w}", p).n.iloc[0]
    if pre:                                                              # cargas v5.8+: totales calculados al cargar (envíos de millones de servicios)
        if regla:
            return df(f"SELECT mes, factura, tipo_servicio, codigo, descripcion, cantidad, valor_unitario, valor, fecha, diagnostico, origen, archivo FROM rips_val_det"
                      f"{w + ' AND' if w else ' WHERE'} regla=?", (*p, regla))
        v = df(f"SELECT regla, SUM(registros) registros, SUM(facturas) facturas, SUM(valor) valor FROM rips_val{w}{' AND' if w else ' WHERE'} regla<>'_' GROUP BY regla ORDER BY regla", p)
        v.insert(1, "descripcion", v.regla.map(lambda r: REGLAS_RIPS.get(r, ("",))[0]))
        return v[cols]
    d = df(f"SELECT * FROM rips_det{w}", p)
    d["glob"] = es_global(d)
    if regla:
        x = d[REGLAS_RIPS[regla][1](d)]
        return x[["mes", "factura", "tipo_servicio", "codigo", "descripcion", "cantidad", "valor_unitario", "valor", "fecha", "diagnostico", "origen", "archivo"]]
    out = []
    for k, (txt, f) in REGLAS_RIPS.items():
        mk = f(d)
        if mk.any(): out.append(dict(regla=k, descripcion=txt, registros=int(mk.sum()), facturas=int(d.loc[mk, "factura"].nunique()), valor=float(d.loc[mk, "valor"].sum())))
    return pd.DataFrame(out, columns=["regla", "descripcion", "registros", "facturas", "valor"])

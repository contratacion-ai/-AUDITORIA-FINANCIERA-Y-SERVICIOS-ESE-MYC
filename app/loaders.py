"""Extracción: lector en streaming del informe de facturación (Software 360) y lector flexible de RIPS/costos/notas."""
import os, re, zipfile, html, unicodedata, hashlib
from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
import pandas as pd
from .db import SEDES

MESES = {m: i + 1 for i, m in enumerate(
    "ENERO FEBRERO MARZO ABRIL MAYO JUNIO JULIO AGOSTO SEPTIEMBRE OCTUBRE NOVIEMBRE DICIEMBRE".split())}

@lru_cache(maxsize=300000)
def norm(s):
    s = unicodedata.normalize("NFKD", str(s if s is not None else "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", " ", s).strip()

CUPS_RE = re.compile(r"\d{5,6}|[0-9][A-Z]{1,3}\d{2,3}")

def norm_act(txt):
    """Clave canónica de una ACTIVIDAD / descripción de servicio (regla de actividad).

    Quita tildes, signos, el sufijo interno «(1234)» y el «+» final, y colapsa espacios, de modo que
    «CONSULTA DE PRIMERA VEZ POR FISIOTERAPIA(5326)» y «Consulta de primera vez por fisioterapia +» coincidan."""
    t = re.sub(r"\(\s*\d+\s*\)\s*$", "", str(txt if txt is not None else "").strip())
    return norm(t.replace("+", " "))

def parse_mes(x):
    """Devuelve 'AAAA-MM' desde fecha, 'ENERO 2026', '2026-01', '01/2026' o '202601'."""
    if x is None or (isinstance(x, float) and pd.isna(x)): return None
    if hasattr(x, "month") and hasattr(x, "year"): return f"{x.year}-{x.month:02d}"
    s = str(x).strip()
    if s.isdigit() and 30000 < int(s) < 80000: return _ym(s)          # serial de Excel
    for pat, ym in ((r"^(\d{4})[-/ ](\d{1,2})", 1), (r"^(\d{1,2})[-/ ](\d{4})$", 0), (r"^(\d{4})(\d{2})$", 1)):
        m = re.match(pat, s)
        if m:
            y, mo = (m[1], m[2]) if ym else (m[2], m[1])
            if 1 <= int(mo) <= 12: return f"{y}-{int(mo):02d}"
    n = norm(s); y = re.search(r"\b(20\d\d)\b", n)
    for k, v in MESES.items():
        if y and k in n.split(): return f"{y[1]}-{v:02d}"
    return None

def parse_mes_flex(x, anio=None):
    """Como parse_mes, pero acepta '01-ENERO' o 'ENERO' usando el año inferido."""
    m = parse_mes(x)
    if m or not anio: return m
    for p in norm(x).split():
        mo = MESES.get(p) or (int(p) if p.isdigit() and 1 <= int(p) <= 12 else None)
        if mo: return f"{anio}-{mo:02d}"
    return None

def num(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0

# ───────────── Facturación por ámbito: lectura streaming del XML (archivos de cientos de MB) ─────────────
CELL = re.compile(rb'<c r="([A-Z]+)\d+"([^>]*?)(?:/>|>(?:<f[^>]*?(?:/>|>.*?</f>))?(?:<v>([^<]*)</v>)?(?:<is>(.*?)</is>)?</c>)', re.S)

def _shared(z):
    try: data = z.read("xl/sharedStrings.xml").decode("utf8")
    except KeyError: return []
    return [html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", m[1] or "", re.S)))
            for m in re.finditer(r"<si>(.*?)</si>|<si/>", data, re.S)]

def todas_hojas(z):
    """{nombre_hoja: ruta_xml}"""
    wb = z.read("xl/workbook.xml").decode("utf8"); rels = z.read("xl/_rels/workbook.xml.rels").decode("utf8")
    tgt = {re.search(r'Id="([^"]+)"', t)[1]: re.search(r'Target="([^"]+)"', t)[1]
           for t in re.findall(r"<Relationship [^>]*>", rels)}
    out = {}
    for t in re.findall(r"<sheet [^>]*>", wb):
        nm = html.unescape(re.search(r'name="([^"]*)"', t)[1]); p = tgt[re.search(r'r:id="([^"]+)"', t)[1]].lstrip("/")
        out[nm] = p if p.startswith("xl/") else "xl/" + p
    return out

def hojas_mensuales(z):
    """{'2026-01': (nombre_hoja, ruta_xml)} para hojas llamadas 'ENERO 2026', etc."""
    return {parse_mes(n): (n, p) for n, p in todas_hojas(z).items() if parse_mes(n)}

def param_contrato(z, ss):
    """Hoja PARAM CONTRATO → [(contrato, modalidad)]."""
    p = next((p for n, p in todas_hojas(z).items() if norm(n) == "PARAM CONTRATO"), None)
    if not p: return []
    rows = [r for r in iter_rows(z, p, ss) if r]
    hi = next((i for i, r in enumerate(rows) if {"CONTRATO", "MODALIDAD"} <= {norm(v) for v in r.values()}), None)
    if hi is None: return []
    h = {norm(v): k for k, v in rows[hi].items()}
    return [(r[h["CONTRATO"]], r.get(h["MODALIDAD"], "")) for r in rows[hi + 1:] if h["CONTRATO"] in r]

def iter_rows(z, path, ss, cols=None, limit=None):
    rx = CELL if not cols else re.compile(
        CELL.pattern.replace(rb"([A-Z]+)", b"(" + b"|".join(c.encode() for c in cols) + b")", 1), re.S)
    buf, n = b"", 0
    with z.open(path) as f:
        while True:
            chunk = f.read(1 << 23)
            parts = (buf + chunk).split(b"</row>"); buf = parts.pop()
            for p in parts:
                row = {}
                for col, attrs, v, inl in rx.findall(p):
                    if not v:
                        if inl:                                   # t="inlineStr": el texto viene dentro de <is><t>…</t></is>
                            t = html.unescape("".join(x.decode() for x in re.findall(rb"<t[^>]*>(.*?)</t>", inl, re.S)))
                            if t: row[col.decode()] = t
                        continue
                    row[col.decode()] = ss[int(v)] if b't="s"' in attrs else (
                        html.unescape(v.decode()) if b't="str"' in attrs else v.decode())
                yield row; n += 1
                if limit and n > limit: return
            if not chunk: return

@lru_cache(maxsize=None)
def _ym(serial):
    d = date(1899, 12, 30) + timedelta(days=int(float(serial)))
    return f"{d.year}-{d.month:02d}"


@lru_cache(maxsize=None)
def _ymd(valor):
    """'AAAA-MM-DD' desde serial de Excel, 'dd/mm/aaaa' o 'aaaa-mm-dd'. Devuelve '' si no se puede interpretar."""
    try:
        d = date(1899, 12, 30) + timedelta(days=int(float(valor)))
        return f"{d.year}-{d.month:02d}-{d.day:02d}"
    except (ValueError, TypeError, OverflowError):
        t = str(valor).strip()
        m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})", t)
        if m and 1 <= int(m[2]) <= 12: return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
        m = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})", t)
        if m and 1 <= int(m[2]) <= 12: return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}"
        return ""

CAMPOS = dict(contrato="CONTRATO", sede="SEDE", servicio="SERVICIO", ambito="AMBITO", regimen="REGIMEN DEL PACIENTE",
              cups="COD PROCEDIMIENTO", med="COD MEDICAMENTO", cant="CANTIDAD", va="VALOR ACTIVIDAD", vt="VALOR TOTAL",
              factura="FACTURA NRO", fecha="FECHA DE FACTURACION", eps="CODIGO EPS", eps_n="NOMBRE EPS",
              ide="NUMERO IDE", esp="ESPECIALIDAD", agenda="AGENDA", prof="PROFESIONAL", reg="REGULADO",
              diag="NOMBRE DIAGNOSTICO", act="ACTIVIDAD", tipo_pac="TIPO DE PACIENTE")
OPCIONALES = {"med", "eps_n", "ide", "esp", "agenda", "prof", "regimen", "reg", "diag", "act", "tipo_pac"}

def clase_item(servicio, cod_proc="", cod_med=""):
    """Clasifica una línea de facturación como MEDICAMENTO, INSUMO o SUPLEMENTO (None si es un procedimiento).

    Se usa el SERVICIO de PARAM SERVICIO (MEDICAMENTOS, MATERIALES E INSUMOS MEDICOS, SUPLEMENTOS DIETARIOS); si el servicio
    no lo indica pero la línea trae COD. MEDICAMENTO y no trae COD. PROCEDIMIENTO, se clasifica como MEDICAMENTO."""
    s = norm(servicio)
    if "MEDICAMENTO" in s: return "MEDICAMENTO"
    if "INSUMO" in s or "MATERIAL" in s: return "INSUMO"
    if "SUPLEMENTO" in s: return "SUPLEMENTO"
    if (cod_med or "").strip() and not (cod_proc or "").strip(): return "MEDICAMENTO"
    return None

def param_hojas(z, ss):
    """Lee PARAM CONTRATO (contrato, modalidad, sede, servicio) y PARAM SERVICIO desde el libro."""
    hs = {norm(n): p for n, p in todas_hojas(z).items()}
    def tabla(nombre, claves):
        p = hs.get(nombre)
        if not p: return []
        rows = [r for r in iter_rows(z, p, ss) if r]
        hi = next((i for i, r in enumerate(rows) if claves[0] in {norm(v) for v in r.values()}), None)
        if hi is None: return []
        h = {norm(v): k for k, v in rows[hi].items()}
        return [tuple((r.get(h[c]) or "").strip() if c in h else "" for c in claves)
                for r in rows[hi + 1:] if h[claves[0]] in r]
    contratos = [(c, norm(m), sd, sv) for c, m, sd, sv in tabla("PARAM CONTRATO", ["CONTRATO", "MODALIDAD", "SEDE", "SERVICIO"])]
    return contratos, [x[0] for x in tabla("PARAM SERVICIO", ["SERVICIO"])]

def procesar_hoja(z, path, ss, mes, param, limit=None):
    """Agrega una hoja mensual (≈220 mil líneas) sin cargarla completa.
    param = {'contratos': {norm(contrato): modalidad}, 'servicios': {norm(servicio)}}"""
    hdr, hi = None, 0          # el reporte puede traer filas de título antes de los encabezados (p. ej. junio)
    for i, r in enumerate(iter_rows(z, path, ss, limit=25)):
        if {"CONTRATO", "VALOR TOTAL"} <= {norm(v) for v in r.values()}: hdr, hi = r, i; break
    if hdr is None: raise ValueError("No se encontró la fila de encabezados (CONTRATO, VALOR TOTAL…) en las primeras 25 filas de la hoja")
    h = {norm(v): k for k, v in hdr.items()}
    c = {k: h.get(v) for k, v in CAMPOS.items()}
    falta = [CAMPOS[k] for k, v in c.items() if v is None and k not in OPCIONALES]
    if falta: raise ValueError(f"Columnas obligatorias no encontradas en la hoja: {', '.join(falta)}")
    cols = sorted({v for v in c.values() if v})
    pc, ps = param["contratos"], param["servicios"]
    mask = os.environ.get("AUDIT_ENMASCARAR_IDE") == "1"
    fact = defaultdict(lambda: [0.0, 0.0, 0]); dim = defaultdict(lambda: [0.0, 0.0]); lin = defaultdict(lambda: [0.0, 0.0])
    fac, hal = {}, defaultdict(lambda: [0, 0.0])
    # Medicamentos / insumos / suplementos: [cantidad, valor, líneas, vu_min, vu_max, cant_max, líneas en cero]
    med, mdesc = defaultdict(lambda: [0.0, 0.0, 0, float("inf"), 0.0, 0.0, 0]), {}
    # Regla de actividad: ACTIVIDAD normalizada → {código: nº de líneas}. Se usa para identificar el CUPS de las líneas que
    # llegan sin código (ver docstring de `identificar_cups`). `huerf` acumula esas líneas hasta conocer el mapa completo.
    amap, atxt, huerf = defaultdict(lambda: defaultdict(int)), {}, defaultdict(lambda: [0.0, 0.0, 0])
    n = 0
    for idx, r in enumerate(iter_rows(z, path, ss, cols, limit)):
        if idx <= hi: continue                     # títulos del reporte + fila de encabezados
        n += 1; g = r.get
        cont = g(c["contrato"], "SIN CONTRATO"); sede = g(c["sede"], "SIN SEDE"); serv = g(c["servicio"], "SIN SERVICIO")
        amb = g(c["ambito"], "SIN AMBITO"); eps = g(c["eps"], ""); vt = num(g(c["vt"])); va = num(g(c["va"])); cant = num(g(c["cant"]))
        code = (g(c["cups"]) or g(c["med"]) or "").strip(); fn = (g(c["factura"]) or "").strip()
        modal = pc.get(norm(cont), "SIN PARAMETRIZAR") if pc else ""
        fl = []
        if not fn: fl.append("R01")
        if vt <= 0: fl.append("R02")
        if va and cant and abs(va * cant - vt) > 1: fl.append("R03")
        if cant <= 0: fl.append("R04")
        if not code: fl.append("R05")
        f = g(c["fecha"])
        if f and _ymd(f)[:7] != mes: fl.append("R06")
        if pc and modal == "SIN PARAMETRIZAR": fl.append("R08")
        if ps and norm(serv) not in ps: fl.append("R09")
        for x in fl: a = hal[(cont, x)]; a[0] += 1; a[1] += vt
        epsn = g(c["eps_n"], ""); act = norm_act(g(c["act"]))
        kf = (eps, epsn, cont, modal, sede, serv, amb, g(c["regimen"], ""))
        if code:
            es_cups = bool(CUPS_RE.fullmatch(code))               # CUPS oficial (6 dígitos / 5DS…); G-/D-/ATC = código interno de 360
            if act and es_cups: amap[act][code] += 1; atxt.setdefault(act, g(c["act"]))
            a = fact[kf + (code, "CUPS" if es_cups else "INTERNO 360")]
        else:
            a = huerf[kf + (act,)]
        a[0] += cant; a[1] += vt; a[2] += 1
        clase = clase_item(serv, g(c["cups"]), g(c["med"]))
        if clase:
            cm = code or "SIN CODIGO"; mdesc.setdefault(cm, (g(c["act"]) or "").strip())
            a = med[(clase, sede, epsn, cont, modal, serv, amb, cm)]
            a[0] += cant; a[1] += vt; a[2] += 1; a[5] = max(a[5], cant)
            if va > 0: a[3] = min(a[3], va); a[4] = max(a[4], va)
            if vt <= 0: a[6] += 1
        a = lin[(_ymd(f) if f else "", serv, g(c["agenda"], ""), g(c["reg"], ""), g(c["cups"], ""), g(c["med"], ""), g(c["act"], ""),
                 g(c["diag"], ""), fn, va, g(c["tipo_pac"], ""), g(c["regimen"], ""), modal)]; a[0] += cant; a[1] += vt
        ide = g(c["ide"])
        if ide and mask: ide = hashlib.sha1(ide.encode()).hexdigest()[:10]
        for d, it in (("servicio", serv), ("contrato", cont), ("ambito", amb), ("profesional", g(c["prof"], "SIN DATO")),
                      ("especialidad", g(c["esp"], "SIN DATO")), ("agenda", g(c["agenda"], "SIN DATO")), ("paciente", ide or "SIN DATO")):
            a = dim[(d, it, sede, modal)]; a[0] += cant; a[1] += vt
        if fn:
            x = fac.get(fn)
            if x is None: fac[fn] = [eps, epsn, cont, modal, sede, vt, 1, {cont}]
            else: x[5] += vt; x[6] += 1; x[7].add(cont)
    for fn, x in fac.items():
        if len(x[7]) > 1: a = hal[("VARIOS", "R07")]; a[0] += x[6]; a[1] += x[5]
    mejor = {a: max(d, key=d.get) for a, d in amap.items()}            # código más frecuente por actividad
    previo = param.get("actividad", {})                                  # mapa de meses ya cargados (respaldo)
    for k, v in huerf.items():
        act, code = k[-1], (mejor.get(k[-1]) or previo.get(k[-1]) if k[-1] else "")
        a = fact[k[:-1] + ((code, "ACTIVIDAD") if code else ("", "SIN IDENTIFICAR"))]
        a[0] += v[0]; a[1] += v[1]; a[2] += v[2]
    reglas = [(a, atxt.get(a, ""), code, nl) for a, d in amap.items() for code, nl in d.items()]
    medins = [(*k[:8], mdesc.get(k[7], ""), v[0], v[1], v[2], (v[3] if v[3] != float("inf") else 0.0), v[4], v[5], v[6]) for k, v in med.items()]
    return dict(fact=fact, dim=dim, lin=lin, fac=fac, hal=hal, filas=n, reglas=reglas, medins=medins)

# ───────────── RIPS / costos / notas técnicas: formato homologado + lectura del consolidado nativo ─────────────
ALIAS = {
    "rips": dict(mes=["MES", "PERIODO"], eps=["EPS", "NOMBRE EPS", "CLIENTE FEV", "ENTIDAD", "PAGADOR"], nit_eps=["NIT EPS", "NIT CLIENTE FEV"],
                 contrato=["CONTRATO DESCRIPCION FEV", "CONTRATO"], cucon=["CONTRATO CUCON", "CUCON"], modalidad=["MODALIDAD", "MODALIDAD FEV"],
                 sede=["NOMBRE SEDE", "SEDE", "SEDE FEV", "SEDE PRESTADOR"], servicio=["TIPO DE SERVICIO", "TIPO SERVICIO", "SERVICIO"],
                 cups=["CODIGO CUPS CUMS", "CODIGO CUPS", "CODIGO CUMS", "CUPS", "CODIGO", "COD PROCEDIMIENTO"],
                 factura=["FACTURA FEV", "FACTURA XML", "FACTURA", "FACTURA NRO", "NUMERO FACTURA"], factura_rips=["FACTURA JSON", "FACTURA RIPS"],
                 cantidad=["CANTIDAD"], valor=["VALOR DEL SERVICIO", "VALOR SERVICIO", "VALOR"],
                 valor_fev=["VALOR TOTAL FEV", "VALOR FACTURA FEV", "VALOR FACTURA", "VALOR TOTAL FACTURA"],
                 validacion=["VALIDACION XML JSON", "VALIDACION"], fecha_egreso=["FECHA EGRESO FEV"],
                 descripcion=["DESCRIPCION DE SERVICIO", "DESCRIPCION SERVICIO", "DESCRIPCION DEL SERVICIO", "ACTIVIDAD"]),
    "costos": dict(mes=["MES", "PERIODO"], sede=["SEDE", "UNIDAD"], servicio=["SERVICIO", "CENTRO DE COSTO", "CENTRO COSTO", "UNIDAD FUNCIONAL", "AREA"],
                   cups=["CUPS", "CODIGO CUPS", "COD PROCEDIMIENTO"], concepto=["CONCEPTO", "RUBRO", "CUENTA"],
                   cantidad=["CANTIDAD", "PRODUCCION", "VOLUMEN"], costo_unitario=["COSTO UNITARIO", "VALOR UNITARIO"],
                   valor=["VALOR COSTO TOTAL", "COSTO TOTAL", "VALOR COSTO", "COSTO", "VALOR", "TOTAL"],
                   tipo_costo=["TIPO COSTO", "TIPO DE COSTO", "CLASIFICACION"]),
    "notas": dict(eps=["EPS", "ENTIDAD", "NOMBRE EPS", "EMPRESA"], modalidad=["MODALIDAD", "MODALIDD"], contrato=["CONTRATO"],
                  cups=["CUPS", "COD CUPS", "CODIGO CUPS", "CODIGO", "COD PROCEDIMIENTO", "CODIGOCUPS"],
                  descripcion=["DESCRIPCION", "DESCRIPCION SERVICIO O PROCEDIMIENTO", "ACTIVIDAD", "DESCRIPCIONCUPS"],
                  categoria=["AGRUPADOR", "GRUPO", "CATEGORIA", "SERVICIO"],
                  tarifa=["TARIFA", "TARIFA PACTADA", "VALOR UNITARIO", "VALOR", "VALOR TOTAL", "TARIFA UVB"],
                  costo_unitario=["COSTOS", "COSTO UNITARIO", "COSTO"],
                  eventos_mes=["EVENTOS MES", "EVENTOS", "EVENTOS MENSUALES", "CANTIDAD PACTADA", "FRECUENCIA", "TOTAL SERVICIOS NOTA TECNICA", "TOTAL ACTIVIDADES MES"],
                  tope_valor=["TOPE VALOR", "VALOR MENSUAL", "TECHO"], vigencia=["VIGENCIA"]),
}
ALIAS["farmacia"] = dict(mes=["MES", "PERIODO", "FECHA"], sede=["SEDE", "BODEGA", "FARMACIA", "PUNTO DE DISPENSACION"],
                        codigo=["CODIGO SOFTWARE360", "CODIGO 360", "COD MEDICAMENTO", "CODIGO", "CUM", "CUMS", "CODIGO PRODUCTO"],
                        descripcion=["DESCRIPCION", "PRODUCTO", "NOMBRE", "MEDICAMENTO"], clase=["TIPO", "CLASE", "TIPO PRODUCTO", "GRUPO"],
                        concepto=["CONCEPTO", "MOVIMIENTO", "TIPO MOVIMIENTO"], cantidad=["CANTIDAD DISPENSADA", "CANTIDAD", "UNIDADES", "SALIDAS"],
                        costo_unitario=["COSTO UNITARIO", "COSTO PROMEDIO", "VALOR UNITARIO"], valor_costo=["VALOR COSTO", "COSTO TOTAL", "VALOR TOTAL", "VALOR"])
ALIAS["codigos"] = dict(codigo_rips=["CODIGO RIPS", "CODIGO CUMS", "CODIGO CUPS CUMS", "CUMS"], codigo_360=["CODIGO SOFTWARE360", "CODIGO 360", "CODIGO SOFTWARE 360"],
                       descripcion=["DESCRIPCION"])
NUMERICAS = {"cantidad", "valor", "tarifa", "eventos_mes", "tope_valor", "costo_unitario", "valor_fev", "valor_costo"}
ALIAS_COSTOS_CUPS_NUM = {"costo_unitario"}

def canon_eps(txt, alias):
    nt = norm(txt)
    for a, e in alias:
        if norm(a) in nt: return e
    return ""

_SN = {norm(x): x for x in SEDES}

def canon_sede(x):
    """Homologa nombres de sede abreviados (p. ej. 'CENTRO DE SALUD PANAMA') al nombre de Software 360."""
    n = norm(x)
    if not n or n in _SN: return _SN.get(n, "")
    c = [v for k, v in _SN.items() if k.startswith(n) or n.startswith(k)]
    return c[0] if len(c) == 1 else str(x).strip()

def _num(s):
    s = s.fillna("").astype(str).str.strip().str.replace(r"[$\s]", "", regex=True)
    both = s.str.contains(r"\.") & s.str.contains(",")
    s = s.where(~both, s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
    s = s.where(~(s.str.contains(",") & ~both), s.str.replace(",", ".", regex=False))
    return pd.to_numeric(s, errors="coerce").fillna(0.0)

def _extraer(raw, al):
    best = (0, 0)
    for i in range(min(15, len(raw))):
        vals = {norm(v) for v in raw.iloc[i]}
        hits = sum(any(norm(a) in vals for a in lst) for lst in al.values())
        if hits > best[0]: best = (hits, i)
    if best[0] < 2: return None
    hdr = []
    for j, v in enumerate(raw.iloc[best[1]]):
        h = norm(v); hdr.append(h if h not in hdr else f"{h}_{j}")
    body = raw.iloc[best[1] + 1:].copy(); body.columns = hdr
    out = pd.DataFrame(index=body.index)
    for k, lst in al.items():
        for a in lst:
            if norm(a) in hdr: out[k] = body[norm(a)]; break
    return out

def _colidx(l):
    n = 0
    for ch in l: n = n * 26 + ord(ch) - 64
    return n

def hojas_df(path, filtro=None):
    """Hojas como DataFrames sin encabezado. xlsx se lee en streaming (rápido y con poca memoria)."""
    low = path.lower()
    if low.endswith(".csv"):
        return {"csv": pd.read_csv(path, sep=None, engine="python", dtype=str, header=None, encoding_errors="replace")}
    if low.endswith(".xls"):
        return {n: d for n, d in pd.read_excel(path, sheet_name=None, header=None, dtype=str).items()}
    z = zipfile.ZipFile(path); ss = _shared(z); hs = todas_hojas(z); out = {}
    for n, p in hs.items():
        if filtro and not filtro(n, list(hs)): continue
        rows = list(iter_rows(z, p, ss))
        if rows:
            d = pd.DataFrame(rows); d = d[sorted(d.columns, key=_colidx)]; d.columns = range(d.shape[1]); out[n] = d
    return out

def _solo_consolidado(n, todos):
    """Si el libro RIPS trae hojas 'Consolidado CUPS/CUMS', se usan solo esas (evita duplicar con Detalle/Resúmenes)."""
    return not any(norm(x).startswith("CONSOLIDADO") for x in todos) or norm(n).startswith("CONSOLIDADO")

def leer_tabla(path, tipo, mes_param=None, eps_alias=()):
    """Lee RIPS/costos/notas (formato homologado o consolidado nativo) y devuelve un DataFrame con columnas canónicas."""
    al = ALIAS[tipo]
    partes, vistas = [], []
    for nombre, raw in hojas_df(path, _solo_consolidado if tipo == "rips" else None).items():
        t = _extraer(raw, al)
        if t is None: continue
        vistas.append(sorted(t.columns))
        ok = {"rips": "eps" in t and ("factura" in t or "cups" in t), "costos": "valor" in t or "costo_unitario" in t,
              "notas": "cups" in t, "codigos": "codigo_rips" in t and "codigo_360" in t,
              "farmacia": "codigo" in t and ("cantidad" in t or "valor_costo" in t)}[tipo]
        if not ok: continue
        for k in al:
            if k not in t: t[k] = (1.0 if k == "cantidad" and tipo == "rips" else 0.0) if k in NUMERICAS else ""
        for k in NUMERICAS & set(t.columns): t[k] = _num(t[k])
        for k in set(t.columns) - NUMERICAS: t[k] = t[k].fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        if tipo == "notas":
            if not t.eps.astype(bool).any(): t["eps"] = canon_eps(nombre, eps_alias)
            else: t["eps"] = t.eps.map(lambda x: canon_eps(x, eps_alias) or x)
            t["eps"] = t.eps.where(t.eps != "", canon_eps(nombre, eps_alias))
            t["modalidad"] = t.modalidad.map(norm)
            t.loc[t.modalidad == "", "modalidad"] = next((m for m in ("CAPITA", "EVENTO", "PGP") if m in norm(nombre)), "")
            t = t[(t.cups != "") & (t.eps != "")]
        elif tipo == "codigos":
            t = t[(t.codigo_rips != "") & (t.codigo_360 != "")]
        else:
            if tipo == "rips":
                t["eps"] = t.eps.map(lambda x: canon_eps(x, eps_alias) or x)
                t["modalidad"] = t.modalidad.map(norm)
                t.loc[t.contrato == "", "contrato"] = t.cucon
            elif tipo == "farmacia":
                t["valor_costo"] = t.valor_costo.where(t.valor_costo != 0, t.costo_unitario * t.cantidad)
                t["clase"] = [clase_item(x) or ("MEDICAMENTO" if not x else str(x).upper()) for x in t.clase]
            else:
                t["valor"] = t.valor.where(t.valor != 0, t.costo_unitario * t.cantidad)
            t["sede"] = t.sede.map(canon_sede)
            anio = None
            if "fecha_egreso" in t:
                y = t.fecha_egreso.str.extract(r"(20\d\d)")[0].dropna(); anio = y.mode()[0] if len(y) else None
            anio = anio or (mes_param or "")[:4] or (re.search(r"20\d\d", nombre) or [None])[0] or str(date.today().year)
            t["mes"] = t.mes.map(lambda x: parse_mes_flex(x, anio))
            dflt = parse_mes(nombre) or mes_param
            if dflt: t["mes"] = t.mes.fillna(dflt)
            if tipo != "costos": t = t[t.mes.notna()]
            if tipo == "farmacia": t = t[t.codigo != ""]      # costos: las filas sin mes se reportan como ERROR en la validación
        partes.append(t)
    if not partes or sum(len(p) for p in partes) == 0:
        raise ValueError(f"No se reconocieron columnas de {tipo.upper()} (o no se pudo determinar mes/EPS). Use la plantilla homologada. "
                         f"Columnas esperadas: {sorted(al)}. Columnas detectadas por hoja: {vistas or 'ninguna'}")
    return pd.concat(partes, ignore_index=True)

def leer_rips(path, mes_param=None, eps_alias=(), mapa_actividad=None):
    """→ (servicios agregados, facturas del RIPS con valor FEV).

    Regla de actividad: si una línea del RIPS llega sin CUPS pero con descripción, el CUPS se toma del mapa
    actividad→CUPS aprendido de la facturación (``mapa_actividad``)."""
    t = leer_tabla(path, "rips", mes_param, eps_alias)
    if mapa_actividad:
        sin = t.cups == ""
        t.loc[sin, "cups"] = t.loc[sin, "descripcion"].map(lambda x: mapa_actividad.get(norm_act(x), ""))
    keys = ["mes", "eps", "nit_eps", "contrato", "cucon", "modalidad", "sede", "servicio", "cups", "factura"]
    srv = t.groupby(keys, as_index=False).agg(cantidad=("cantidad", "sum"), valor=("valor", "sum"), descripcion=("descripcion", "first"))
    fac = t[t.factura != ""].groupby(["mes", "factura"], as_index=False).agg(
        factura_rips=("factura_rips", "first"), eps=("eps", "first"), contrato=("contrato", "first"), cucon=("cucon", "first"),
        modalidad=("modalidad", "first"), sede=("sede", "first"), valor_fev=("valor_fev", "max"), valor_srv=("valor", "sum"),
        servicios=("cantidad", "sum"), validacion=("validacion", "first"))
    fac["valor_fev"] = fac.valor_fev.where(fac.valor_fev > 0, fac.valor_srv)
    return srv, fac.drop(columns="valor_srv")


# ───────────── v5 · Informe de FACTURAS GENERADAS de Software 360 (una fila por factura) ─────────────
ALIAS["facturas"] = dict(
    factura=["NUMERO FACTURA"], ingreso=["NRO INGRESO"], ambito=["GENERADA EN"], estado_factura=["ESTADO FACTURA"], estado_auditoria=["ESTADO AUDITORIA"],
    fecha_factura=["FECHA DE FACTURA", "FECHA FACTURA"], subtotal=["SUBTOTAL"], cuota=["VALOR CUOTA"], copago=["VALOR COPAGO"], bono=["VALOR BONO"],
    iva=["IVA"], total=["TOTAL", "VALOR TOTAL"], descuento=["VALOR DESCUENTO"], particular=["PARTICULAR"], registrada_por=["REGISTRADA POR"],
    fecha_anulacion=["FECHA DE ANULACION"], motivo_anulacion=["MOTIVO DE ANULACION"], tipo_ide=["TIPO IDE"], paciente_id=["NUMERO IDE"],
    fecha_ingreso=["FECHA INGRESO"], fecha_egreso=["FECHA EGRESO"], recibo_caja=["RECIBO CAJA NRO"], valor_recibo=["VALOR"],
    tipo_contrato=["TIPO CONTRATO"], tercero=["TERCERO", "EPS", "ENTIDAD"], nit=["NIT"], contrato=["CONTRATO"], regimen=["REGIMEN DEL CONTRATO"],
    fecha_radicado=["FECHA RADICADO"], radicado_eps=["NRO RADICADO EPS"], radicado_actual=["RADICADO ACTUAL"], valor_excedente=["VALOR EXCENDENTE", "VALOR EXCEDENTE"],
    sede=["SEDE"], estado_dian=["ESTADO ENVIO DIAN"], cufe=["CUFE"], clase_contrato=["CLASE CONTRATO"], clase_atencion=["CLASE DE ATENCION"])
FG_NUM = {"subtotal", "cuota", "copago", "bono", "iva", "total", "descuento", "valor_recibo", "valor_excedente"}
FG_FECHAS = {"fecha_factura", "fecha_anulacion", "fecha_ingreso", "fecha_egreso", "fecha_radicado"}

def _fechas(s):
    """Serie de fechas 'AAAA-MM-DD' desde serial de Excel, 'aaaa-mm-dd[ hh:mm:ss]' o 'dd/mm/aaaa'. Vacío si no se puede leer."""
    return s.fillna("").astype(str).str.strip().map(lambda v: _ymd(v.split(" ")[0]) if v else "")

def leer_facturas_generadas(path, mes_param=None):
    """Lee el «INFORME DE FACTURAS GENERADAS» de Software 360 (títulos arriba, encabezado en la fila ~7, una fila por factura).

    Privacidad: no se guarda el nombre del paciente; el número de identificación se reemplaza por un hash corto
    (permite contar pacientes únicos y reingresos sin exponer el documento)."""
    al = ALIAS["facturas"]; partes = []
    for nombre, raw in hojas_df(path).items():
        t = _extraer(raw, al)
        if t is None or "factura" not in t or "estado_factura" not in t: continue
        for k in al:
            if k not in t: t[k] = 0.0 if k in FG_NUM else ""
        for k in FG_NUM: t[k] = _num(t[k])
        for k in set(al) - FG_NUM - FG_FECHAS: t[k] = t[k].fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        for k in FG_FECHAS: t[k] = _fechas(t[k])
        t = t[t.factura != ""]
        t["paciente_id"] = t.paciente_id.map(lambda x: hashlib.sha1(x.encode()).hexdigest()[:12] if x else "")
        t["cufe"] = t.cufe.map(lambda x: "SI" if x else "NO")          # solo se conserva si la factura tiene CUFE
        t["sede"] = t.sede.map(canon_sede)
        t["mes"] = t.fecha_factura.str[:7].where(t.fecha_factura != "", mes_param or "")
        partes.append(t[t.mes != ""])
    if not partes or not sum(len(p) for p in partes):
        raise ValueError("No se reconoció el informe de FACTURAS GENERADAS (se esperan columnas NUMERO FACTURA, ESTADO FACTURA, FECHA DE FACTURA, TOTAL, SEDE…).")
    return pd.concat(partes, ignore_index=True).drop_duplicates("factura", keep="last")

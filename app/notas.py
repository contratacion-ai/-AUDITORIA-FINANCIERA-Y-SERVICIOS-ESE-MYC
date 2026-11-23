"""NOTAS TÉCNICAS DE LAS EPS EN SUS FORMATOS NATIVOS (sin pasarlas a la plantilla).

Formatos reconocidos (se detectan por las columnas de cada hoja):
  · SANITAS cápita por municipio («FORMATO CAPITA TAME»…): encabezado con CONTRATANTE, CONSECUTIVO DEL CONTRATO, MUNICIPIO DE COBERTURA,
    POBLACIÓN PROYECTADA y RÉGIMEN; tabla Agrupador · CUPS · DESCRIPCIÓN · Frecuencia anual · Actividades año · Actividades mes ·
    Promedio CxE (tarifa) · Valor Mes. Se omiten los subtotales («Total …») y el texto del acuerdo que va debajo.
  · NUEVA EPS «Servicios_Estudio» (nota de la EPS vs propuesta de la ESE): CodigoCUPS · DescripcionCUPS · Grupo · FrecuenciaUso ·
    Regimen · Eventos (de la EPS, por mes) · Valor (tarifa) · Eventos_Propuestos_ESE. Los medicamentos traen el CUM en «Homologo».
  · COOSALUD matriz PYM (Res. 3280): encabezado con NOMBRE DE LA EPS, NÚMERO DEL CONTRATO, RÉGIMEN, meses de vigencia y población;
    tabla Clave · Población · GRUPO · CONTRATADO (SI/NO) · TOTAL ACTIVIDADES · NÚMERO DE ACTIVIDADES AL MES · Código CUPS (varios por
    celda) · Valor unitario · Valor total. Una actividad con varios CUPS se guarda una vez por CUPS con el mismo GRUPO: la ejecución
    se compara sumando todos los CUPS del grupo.
  · COOSALUD «NT_SERVICIOS» (listado de CUPS incluidos en la cápita, sin metas): queda como cobertura (eventos 0).
Cualquier otro libro se lee con la plantilla homologada (loaders.leer_tabla).
"""
import re
from pathlib import Path
import pandas as pd
from .loaders import canon_eps, hojas_df, norm, num

# municipio de cobertura de cada sede de la ESE (para cruzar las notas técnicas por municipio con lo facturado por sede)
MUNICIPIO_SEDE = {"HOSPITAL SAN ANTONIO": "TAME", "HOSPITAL SAN FRANCISCO": "FORTUL", "HOSPITAL SAN LORENZO": "ARAUQUITA",
                  "HOSPITAL SAN RICARDO PAMPURI": "ARAUQUITA", "CENTRO DE SALUD PANAMA DE ARAUCA": "ARAUQUITA",
                  "HOSPITAL SAN JOSE": "CRAVO NORTE", "HOSPITAL SAN JUAN DE DIOS": "PUERTO RONDON"}
COLS = ["eps", "modalidad", "contrato", "regimen", "municipio", "poblacion", "cups", "descripcion", "categoria", "grupo", "clave", "tarifa",
        "eventos_mes", "eventos_propuesta", "tope_valor", "vigencia", "formato", "hoja"]
_COD = re.compile(r"^[0-9A-Z][0-9A-Z\-]{3,11}$")

def municipio(txt):
    n = norm(txt).replace("PTO ", "PUERTO ")
    return "PUERTO RONDON" if "RONDON" in n else n

def _modalidad(*txts):
    n = " ".join(norm(t) for t in txts)
    return "EVENTO" if "EVENTO" in n else "PGP" if " PGP" in f" {n}" else "CAPITA"

def _regimen(txt):
    n = norm(txt); s, c = "SUBSIDIADO" in n, "CONTRIBUTIVO" in n
    return "CONTRIBUTIVO - SUBSIDIADO" if s and c else "SUBSIDIADO" if s else "CONTRIBUTIVO" if c else ""

def _celdas(raw):
    return [[("" if v is None else str(v).strip()) for v in r] for r in raw.itertuples(index=False)]

def _meta(filas, hasta, etiquetas):
    """Valor a la derecha de una etiqueta («CONTRATANTE:», «NÚMERO DEL CONTRATO…») en las filas de encabezado."""
    out = {}
    for r in filas[:hasta]:
        for i, c in enumerate(r):
            n = norm(c)
            for k, prefs in etiquetas.items():
                if k not in out and any(n.startswith(p) for p in prefs):
                    out[k] = next((x for x in r[i + 1:] if x and norm(x) not in ("DESDE", "HASTA")), "")
    return out

def _col(h, *nombres, empieza=False):
    hs = [norm(x) for x in h]
    for nm in nombres:
        for i, x in enumerate(hs):
            if (x.startswith(nm) if empieza else x == nm): return i
    return None

def _sanitas(f, hoja, archivo, alias):
    hi = next((i for i, r in enumerate(f[:40]) if {"CUPS", "ACTIVIDADES MES"} <= {norm(x) for x in r}), None)
    if hi is None: return None
    h = f[hi]; ix = {k: _col(h, *v) for k, v in dict(agr=["AGRUPADOR"], cups=["CUPS"], cnt=["CODIGO NT"], desc=["DESCRIPCION"], frec=["FRECUENCIA ANUAL"],
                                                      mes=["ACTIVIDADES MES"], tar=["PROMEDIO CXE", "TARIFA"], val=["VALOR MES"]).items()}
    m = _meta(f, hi, dict(eps=["CONTRATANTE"], contrato=["CONSECUTIVO DEL CONTRA"], mun=["MUNICIPIO DE COBERTURA", "MUNICIPIO"], pob=["POBLACION PROYECTADA"],
                          reg=["REGIMEN"], desde=["CONTRATISTA"]))
    eps = canon_eps(m.get("eps", ""), alias) or canon_eps(archivo, alias) or m.get("eps", "")
    filas = []
    for r in f[hi + 1:]:
        g = lambda k: r[ix[k]] if ix[k] is not None and ix[k] < len(r) else ""
        cups = re.sub(r"\.0$", "", g("cups")); agr = g("agr")
        if not cups or not _COD.match(cups.upper()) or norm(agr).startswith("TOTAL"): continue
        filas.append(dict(cups=cups, descripcion=g("desc"), categoria=agr, tarifa=num(g("tar")), eventos_mes=num(g("mes")), tope_valor=num(g("val")),
                          grupo="", eventos_propuesta=0.0))
    if not filas: return None
    d = pd.DataFrame(filas)
    return d.assign(eps=eps, modalidad=_modalidad(hoja, archivo), contrato=m.get("contrato", "").replace(" ", ""), regimen=_regimen(m.get("reg", "")),
                    municipio=municipio(m.get("mun", "")), poblacion=num(m.get("pob", 0)), vigencia="", formato="SANITAS CÁPITA POR MUNICIPIO")

def _nueva_eps(f, hoja, archivo, alias):
    hi = next((i for i, r in enumerate(f[:10]) if {"CODIGOCUPS", "EVENTOS", "VALOR"} <= {norm(x) for x in r}), None)
    if hi is None: return None
    h = f[hi]; ix = {k: _col(h, *v) for k, v in dict(hom=["HOMOLOGO"], cups=["CODIGOCUPS"], desc=["DESCRIPCIONCUPS"], grupo=["GRUPO"], sub=["SUBGRUPO"],
                                                      reg=["REGIMEN"], ev=["EVENTOS"], val=["VALOR"], prop=["EVENTOS PROPUESTOS ESE"], zon=["ZONAL"]).items()}
    filas = []
    for r in f[hi + 1:]:
        g = lambda k: r[ix[k]] if ix[k] is not None and ix[k] < len(r) else ""
        cups = re.sub(r"\.0$", "", g("cups"))
        if not cups or not _COD.match(cups.upper()) or not g("desc"): continue           # sin código o fila de totales
        filas.append(dict(cups=cups, descripcion=g("desc"), categoria=g("grupo"), grupo="", clave=g("hom") if g("hom") and g("hom") != cups else "",
                          tarifa=num(g("val")), eventos_mes=num(g("ev")), eventos_propuesta=num(g("prop")), regimen=_regimen(g("reg")),
                          municipio=""))                                 # «ZONAL ARAUCA»: la nota cubre todos los municipios de la ESE
    if not filas: return None
    d = pd.DataFrame(filas); d["tope_valor"] = d.tarifa * d.eventos_mes
    eps = canon_eps(archivo, alias) or canon_eps(hoja, alias) or "NUEVA EMPRESA PROMOTORA DE SALUD"
    return d.assign(eps=eps, modalidad=_modalidad(archivo.replace("NOTA_TECNICA", "")), contrato="", poblacion=0.0, vigencia="",
                    formato="NUEVA EPS ESTUDIO DE SERVICIOS (EPS vs PROPUESTA ESE)")

def _coosalud_pym(f, hoja, archivo, alias):
    hi = next((i for i, r in enumerate(f[:60]) if "CLAVE" in {norm(x) for x in r} and any(norm(x).startswith("CODIGO CUPS") for x in r)), None)
    if hi is None: return None
    h, h0 = f[hi], (f[hi - 1] if hi else [])
    ix = dict(pob=_col(h, "POBACION", "POBLACION"), grupo=_col(h, "GRUPO"), si=_col(h, "CONTRATADO", empieza=True),
              tot=_col(h, "TOTAL ACTIVIDADES", empieza=True), mes=_col(h, "NUMERO DE ACTIVIDADES AL MES", empieza=True), cod=_col(h, "CODIGO CUPS", empieza=True),
              vu=_col(h0, "VALOR UNITARIO", empieza=True), vt=_col(h0, "VALOR TOTAL", empieza=True), clave=_col(h, "CLAVE"))
    m = _meta(f, hi, dict(eps=["NOMBRE DE LA EPS"], contrato=["NUMERO DEL CONTRATO"], reg=["REGIMEN"], meses=["NUMERO DE MESES DE VIG"],
                          mun=["NOMBRE DEL DEPARTAMENT", "NOMBRE DEL MUNICIPIO"], pob=["TOTAL POBLACION ASIGNA"], ini=["FECHA INICIO VIGENCIA"],
                          fin=["FECHA FINALIZACION"]))
    meses = num(m.get("meses", 0)) or 12.0
    filas = []
    for r in f[hi + 1:]:
        g = lambda k: r[ix[k]] if ix[k] is not None and ix[k] < len(r) else ""
        if norm(g("si")) != "SI": continue
        cods = [c for c in re.split(r"[\s,;/]+", g("cod").upper()) if _COD.match(c) and any(ch.isdigit() for ch in c)]
        if not cods: continue
        ev = num(g("mes")) or num(g("tot")) / meses
        for c in dict.fromkeys(cods):
            filas.append(dict(cups=c, descripcion=g("grupo"), categoria=g("pob"), grupo="", clave=f"PYM {g('clave')}",
                              tarifa=num(g("vu")), eventos_mes=ev, eventos_propuesta=0.0, tope_valor=num(g("vt")) / meses))
    if not filas: return None
    eps = canon_eps(m.get("eps", ""), alias) or canon_eps(archivo, alias) or m.get("eps", "")
    vig = " a ".join(x[:10] for x in (m.get("ini", ""), m.get("fin", "")) if x)
    return pd.DataFrame(filas).assign(eps=eps, modalidad="CAPITA", contrato=m.get("contrato", ""), regimen=_regimen(m.get("reg", "")),
                                      municipio=municipio(m.get("mun", "")), poblacion=num(m.get("pob", 0)), vigencia=vig, formato="COOSALUD MATRIZ PYM (RES. 3280)")

def _coosalud_servicios(f, hoja, archivo, alias):
    hi = next((i for i, r in enumerate(f[:5]) if {"CODIGOCUPS", "MODULOS"} <= {norm(x) for x in r}), None)
    if hi is None: return None
    h = f[hi]; ix = {k: _col(h, v) for k, v in dict(cups="CODIGOCUPS", desc="DESCRIPCIONCUPS", mod="MODULOS", agr="AGRUPADOR", reg="REGIMEN",
                                                     mun="MUNICIPIO", afi="TOTAL AFI").items()}
    filas = []
    for r in f[hi + 1:]:
        g = lambda k: r[ix[k]] if ix[k] is not None and ix[k] < len(r) else ""
        c = re.sub(r"\.0$", "", g("cups"))
        if c: filas.append(dict(cups=c, descripcion=g("desc"), categoria=g("agr") or g("mod"), grupo="", tarifa=0.0, eventos_mes=0.0, eventos_propuesta=0.0,
                                tope_valor=0.0, regimen=_regimen(g("reg")), municipio=municipio(g("mun")), poblacion=num(g("afi"))))
    if not filas: return None
    return pd.DataFrame(filas).assign(eps=canon_eps(archivo, alias) or canon_eps(hoja, alias), modalidad="CAPITA", contrato="", vigencia="",
                                      formato="COOSALUD SERVICIOS INCLUIDOS EN CÁPITA (SIN METAS)")

LECTORES = (_sanitas, _nueva_eps, _coosalud_pym, _coosalud_servicios)

def _bloques(d):
    """Matriz PYM: una actividad trae varios CUPS y un mismo CUPS está en varias actividades (por curso de vida). Las actividades que
    comparten algún CUPS forman un BLOQUE: su meta es la suma de las metas de las actividades y su ejecución la suma de todos sus CUPS
    (así no se cuenta dos veces). `grupo` queda con el bloque («PYM bloque 3: 890201, 890301…»); filas sin bloque, grupo vacío."""
    pym = d.clave.str.startswith("PYM ")
    if not pym.any(): return d
    padre = {}
    def raiz(x):
        while padre.setdefault(x, x) != x: x = padre[x]
        return x
    for (_, cl), g in d[pym].groupby(["contrato", "clave"]):
        nodos = [("C", c) for c in g.cups] + [("K", cl)]
        for n in nodos[1:]: padre[raiz(n)] = raiz(nodos[0])
    comp = {}
    for i, r in d[pym].iterrows():
        comp.setdefault((r.contrato, raiz(("K", r.clave))), []).append(i)
    for (_, _), idx in comp.items():
        sub = d.loc[idx]; cups = list(dict.fromkeys(sub.cups))
        if sub.clave.nunique() == 1 and len(cups) == 1: continue        # actividad de un solo CUPS: se compara directo
        d.loc[idx, "grupo"] = f"PYM bloque {sub.clave.iloc[0][4:]}: " + ", ".join(cups[:8]) + ("…" if len(cups) > 8 else "")
    return d

def leer(path, archivo, alias=()):
    """DataFrame con COLS si el libro tiene algún formato nativo reconocido; None si no (se usa la plantilla homologada)."""
    partes = []
    for hoja, raw in hojas_df(path).items():
        f = _celdas(raw)
        for lector in LECTORES:
            d = lector(f, hoja, Path(archivo).stem.upper().replace("_", " "), alias)
            if d is not None and len(d): partes.append(d.assign(hoja=hoja)); break
    if not partes: return None
    d = pd.concat(partes, ignore_index=True)
    if "clave" not in d: d["clave"] = ""
    d["clave"] = d.clave.fillna(""); d["grupo"] = d.grupo.fillna("")
    d = _bloques(d)
    # COOSALUD: el listado de servicios hereda contrato/municipio de la matriz PYM del mismo libro
    pym = d[d.formato.str.startswith("COOSALUD MATRIZ")]
    if len(pym):
        sv = d.formato.str.startswith("COOSALUD SERVICIOS")
        d.loc[sv & (d.contrato == ""), "contrato"] = pym.contrato.iloc[0]
        d.loc[sv & (d.municipio == ""), "municipio"] = pym.municipio.iloc[0]
    for c in COLS:
        if c not in d: d[c] = "" if c not in ("tarifa", "eventos_mes", "eventos_propuesta", "tope_valor", "poblacion") else 0.0
    d["eps"] = d.eps.fillna("").replace("", "SIN EPS")
    return d[COLS]

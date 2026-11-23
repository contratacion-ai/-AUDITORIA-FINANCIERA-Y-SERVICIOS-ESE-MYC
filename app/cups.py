"""CUPS: informe mes a mes por empresa, cruce RIPS ↔ facturación por ámbito ↔ facturación por actividad, y ejecución de la nota técnica.

Fuentes (todas llevadas a: mes, eps, contrato, familia, regimen, sede, municipio, cups, descripcion, cantidad, valor):
  · ambito    → líneas de la facturación por ámbito (tabla fact, solo cargas «1»)
  · actividad → facturación generada por actividad (act_det, carga «1c»)
  · rips      → RIPS JSON/TXT/Excel (tabla rips); EPS, contrato y sede que falten se completan desde la factura en 360.
familia: CAPITA si el contrato es cápita/PGP o su nombre dice CAPITA, PYM, GLOBAL, ASISTENCIAL o PGP (así nombra 360 los contratos de
cápita de las EPS de la ESE); EVENTO en otro caso. regimen: SUBSIDIADO / CONTRIBUTIVO según el nombre del contrato.
municipio: de la sede (notas.MUNICIPIO_SEDE), para cruzar las notas técnicas que son por municipio.
"""
import re
import numpy as np, pandas as pd
from .db import df, NT_EXCESO
from .loaders import canon_eps, norm
from .notas import MUNICIPIO_SEDE

FUENTES = {"actividad": "Facturación por actividad", "ambito": "Facturación por ámbito", "rips": "RIPS"}
BAJA_NT = 0.5                      # ejecución mensual < 50 % de la meta de la nota técnica → baja ejecución

def fuente_default():
    """Facturación por actividad si está cargada; si no, por ámbito; si no, RIPS."""
    if df("SELECT COUNT(*) n FROM act_det").n.iloc[0]: return "actividad"
    if df("SELECT COUNT(*) n FROM fact WHERE carga_id IN (SELECT id FROM carga WHERE tipo='facturacion')").n.iloc[0]: return "ambito"
    return "rips"

def _w(mes, extra=""):
    c = [x for x in (("mes=?" if mes else ""), extra) if x]
    return (" WHERE " + " AND ".join(c) if c else ""), ((mes,) if mes else ())

def _alias():
    return [tuple(r) for r in df("SELECT alias, eps FROM hom_eps").values.tolist()]

def familia(modalidad, contrato):
    m, c = norm(modalidad), norm(contrato)
    return "CAPITA" if m in ("CAPITA", "PGP") or re.search(r"CAPITA|PYM|GLOBAL|ASISTENCIAL|PGP", c) else "EVENTO"

def regimen(contrato):
    c = norm(contrato)
    return "SUBSIDIADO" if "SUBSIDIADO" in c else "CONTRIBUTIVO" if "CONTRIBUTIVO" in c else ""

def _completar(d):
    al = _alias()
    canon = {e: (canon_eps(e, al) or e) for e in d.eps.dropna().unique()}
    d["eps"] = d.eps.map(canon).fillna("").replace("", "SIN EPS / PARTICULAR")
    fm = {}
    for mo, co in d[["modalidad", "contrato"]].drop_duplicates().itertuples(index=False): fm[(mo, co)] = (familia(mo, co), regimen(co))
    k = list(zip(d.modalidad, d.contrato))
    d["familia"] = [fm[x][0] for x in k]; d["regimen"] = [fm[x][1] for x in k]
    d["municipio"] = d.sede.map(MUNICIPIO_SEDE).fillna("")
    d["cups"] = d.cups.fillna("").astype(str).str.strip()
    return d

def base(mes=None, fuentes=("actividad", "ambito", "rips")):
    """Tabla larga de todas las fuentes, agregada por mes, EPS, contrato, sede y CUPS."""
    partes = []
    w, p = _w(mes)
    if "ambito" in fuentes:
        wa, pa = _w(mes, "carga_id IN (SELECT id FROM carga WHERE tipo='facturacion')")
        a = df(f"SELECT mes, eps_nombre eps, contrato, modalidad, sede, cups, SUM(cantidad) cantidad, SUM(valor) valor FROM fact{wa} "
               "GROUP BY mes, eps_nombre, contrato, modalidad, sede, cups", pa)
        partes.append(a.assign(fuente="ambito"))
    if "actividad" in fuentes:
        c = df(f"SELECT mes, eps_nombre eps, contrato, modalidad, sede, codigo cups, SUM(cantidad) cantidad, SUM(valor) valor FROM act_det{w} "
               "GROUP BY mes, eps_nombre, contrato, modalidad, sede, codigo", p)
        partes.append(c.assign(fuente="actividad"))
    if "rips" in fuentes:
        r = df(f"SELECT mes, factura, eps, contrato, modalidad, sede, cups, SUM(cantidad) cantidad, SUM(valor) valor FROM rips{w} "
               "GROUP BY mes, factura, eps, contrato, modalidad, sede, cups", p)
        if len(r):
            # EPS / contrato / sede de la factura en 360 cuando el RIPS no los trae (JSON sin XML)
            f = df(f"SELECT factura, MAX(eps_nombre) eps, MAX(contrato) contrato, MAX(modalidad) modalidad, MAX(sede) sede FROM act_det{w} GROUP BY factura", p)
            f = pd.concat([f, df(f"SELECT factura, MAX(eps_nombre) eps, MAX(contrato) contrato, MAX(modalidad) modalidad, MAX(sede) sede FROM factura{w} GROUP BY factura", p)])
            fk = lambda s: s.astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
            f = f.assign(k=fk(f.factura)).drop_duplicates("k").set_index("k")
            k = fk(r.factura)
            for col in ("eps", "contrato", "modalidad", "sede"):
                v = r[col].fillna("").astype(str)
                r[col] = v.where(v != "", k.map(f[col]).fillna(""))
            # sede por el prefijo de la factura (FHJD, FHSF…) cuando la factura no está en 360
            pref = f.assign(p=f.index.str.extract(r"^([A-Z]+)", expand=False).values).groupby("p").sede.agg(lambda s: s.mode().iloc[0] if len(s.mode()) else "")
            rp = r.factura.astype(str).str.upper().str.extract(r"^([A-Z]+)", expand=False)
            r["sede"] = r.sede.where(r.sede != "", rp.map(pref).fillna(""))
            r = r.groupby(["mes", "eps", "contrato", "modalidad", "sede", "cups"], as_index=False)[["cantidad", "valor"]].sum()
        partes.append(r.assign(fuente="rips"))
    d = pd.concat([x for x in partes if len(x)], ignore_index=True) if any(len(x) for x in partes) else pd.DataFrame(
        columns=["mes", "eps", "contrato", "modalidad", "sede", "cups", "cantidad", "valor", "fuente"])
    for col in ("eps", "contrato", "modalidad", "sede"): d[col] = d[col].fillna("").astype(str)
    return _completar(d)

def descripciones():
    d = pd.concat([df("SELECT codigo cups, actividad d FROM act_det GROUP BY codigo, actividad"),
                   df("SELECT cod_proc cups, actividad d FROM linea WHERE cod_proc<>'' GROUP BY cod_proc, actividad"),
                   df("SELECT cups, descripcion d FROM rips WHERE COALESCE(descripcion,'')<>'' GROUP BY cups, descripcion"),
                   df("SELECT cups, descripcion d FROM nota GROUP BY cups, descripcion")])
    d = d[d.d.fillna("") != ""]
    return d.drop_duplicates("cups").set_index("cups").d

NIVELES = {"eps": ["eps"], "contrato": ["eps", "contrato"], "municipio": ["eps", "municipio"], "sede": ["eps", "sede"], "familia": ["eps", "familia", "regimen"]}

def mes_a_mes(fuente=None, metrica="cantidad", nivel="eps", eps=None, familia_=None):
    fuente = fuente or fuente_default()
    """Informe de CUPS mes a mes por empresa: filas = (nivel, CUPS), columnas = meses, total, promedio mensual y meta de la nota técnica."""
    d = base(None, (fuente,))
    if eps: d = d[d.eps == eps]
    if familia_: d = d[d.familia == familia_]
    if d.empty: return pd.DataFrame()
    keys = NIVELES.get(nivel, ["eps"]) + ["cups"]
    pv = d.pivot_table(index=keys, columns="mes", values=metrica, aggfunc="sum", fill_value=0.0)
    meses = list(pv.columns); pv = pv.reset_index()
    pv.insert(len(keys), "descripcion", pv.cups.map(descripciones()).fillna(""))
    pv["total"] = pv[meses].sum(axis=1); pv["meses_con_dato"] = (pv[meses] != 0).sum(axis=1)
    pv["promedio_mes"] = pv.total / max(len(meses), 1)
    if metrica == "cantidad" and nivel in ("eps", "municipio"):
        nt = metas_cups()
        if len(nt):
            k = ["eps", "cups"] + (["municipio"] if nivel == "municipio" else [])
            g = nt.groupby(k, as_index=False).agg(nt_eventos_mes=("eventos_mes", "sum"), nt_tarifa=("tarifa", "max"))
            pv = pv.merge(g, on=k, how="left")
            pv["pct_ejecucion_nt"] = np.where(pv.nt_eventos_mes > 0, pv.promedio_mes / pv.nt_eventos_mes.replace(0, np.nan), np.nan)
    pv = pv.sort_values(["eps", "total"], ascending=[True, False]).reset_index(drop=True)
    if metrica == "cantidad":                                         # columnas de cantidades (no se muestran como dinero)
        pv = pv.rename(columns={**{m: f"cant_{m}" for m in meses}, "total": "cant_total", "promedio_mes": "cant_prom_mes"})
    return pv

def metas_cups():
    """Metas de la nota técnica por CUPS (solo CUPS que no forman bloque PYM: los bloques se miden en ejecucion_nt)."""
    n = df("SELECT eps, contrato, regimen, municipio, cups, eventos_mes, tarifa, COALESCE(grupo,'') grupo FROM nota")
    return n[(n.grupo == "") & (n.eventos_mes > 0)] if len(n) else n

def cruce(mes=None):
    """Por mes, EPS, sede y CUPS: cantidad y valor en RIPS, facturación por ámbito y por actividad, con diferencias y estado.
    Solo se exige una fuente en los meses en que fue cargada, y el RIPS solo en las sedes que tienen RIPS ese mes."""
    d = base(mes)
    if d.empty: return pd.DataFrame()
    k = ["mes", "eps", "familia", "sede", "cups"]
    pv = d.pivot_table(index=k, columns="fuente", values=["cantidad", "valor"], aggfunc="sum")
    pv.columns = [f"{a}_{b}" for a, b in pv.columns]; pv = pv.reset_index()
    for f in FUENTES:
        for m in ("cantidad", "valor"):
            if f"{m}_{f}" not in pv: pv[f"{m}_{f}"] = np.nan
    pv.insert(5, "descripcion", pv.cups.map(descripciones()).fillna(""))
    cargado = {f: set(d.loc[d.fuente == f, "mes"]) for f in FUENTES}
    sede_rips = set(zip(d.loc[d.fuente == "rips", "mes"], d.loc[d.fuente == "rips", "sede"]))
    pv["dif_cant_rips_actividad"] = pv.cantidad_rips.fillna(0) - pv.cantidad_actividad.fillna(0)
    pv["dif_cant_rips_ambito"] = pv.cantidad_rips.fillna(0) - pv.cantidad_ambito.fillna(0)
    est = []
    for r in pv.itertuples(index=False):
        a, ok_r = [], (r.mes, r.sede) in sede_rips
        hay = {f: getattr(r, f"cantidad_{f}") for f in FUENTES}
        fact = [f for f in ("actividad", "ambito") if r.mes in cargado[f]]
        if ok_r:
            if pd.isna(hay["rips"]) and any(pd.notna(hay[f]) for f in fact): a.append("FACTURADO SIN RIPS")
            for f in fact:
                if pd.notna(hay["rips"]) and pd.isna(hay[f]): a.append(f"EN RIPS, NO EN {FUENTES[f].upper()}")
                elif pd.notna(hay["rips"]) and abs(hay["rips"] - hay[f]) > 0.001: a.append(f"CANTIDAD RIPS ≠ {f.upper()}")
        if pd.notna(hay["actividad"]) and pd.notna(hay["ambito"]) and abs(hay["actividad"] - hay["ambito"]) > 0.001: a.append("ÁMBITO ≠ ACTIVIDAD")
        if not a and not ok_r and r.mes in cargado["rips"]: a.append("INFO: RIPS DE ESTA SEDE NO CARGADO")
        if not a and not cargado["rips"]: a.append("INFO: SIN RIPS CARGADO")
        est.append("; ".join(dict.fromkeys(a)) or "OK")
    pv["estado"] = est
    cols = k[:-1] + ["cups", "descripcion", "cantidad_rips", "cantidad_ambito", "cantidad_actividad", "dif_cant_rips_ambito", "dif_cant_rips_actividad",
                     "valor_rips", "valor_ambito", "valor_actividad", "estado"]
    orden = pv.dif_cant_rips_actividad.abs() + pv.dif_cant_rips_ambito.abs() - np.where(pv.estado.str.startswith(("OK", "INFO")), 1e12, 0)
    return pv.assign(_o=orden).sort_values("_o", ascending=False)[cols].reset_index(drop=True)

def ejecucion_nt(fuente=None, meses_sel=None):
    fuente = fuente or fuente_default()
    """Nota técnica (cápita) vs ejecución mes a mes: una fila por CUPS o bloque PYM de cada nota (EPS · contrato · municipio · régimen).
    Ejecución = cantidades de la fuente elegida en contratos de la familia CÁPITA de esa EPS, del mismo régimen y municipio."""
    n = df("SELECT * FROM nota")
    if n.empty: return pd.DataFrame()
    for c in ("regimen", "municipio", "grupo", "clave", "formato", "contrato"): n[c] = n[c].fillna("") if c in n else ""
    if "eventos_propuesta" not in n: n["eventos_propuesta"] = 0.0
    d = base(None, (fuente,)); d = d[d.familia == "CAPITA"]
    n["unidad"] = np.where(n.grupo != "", n.grupo, n.cups)
    # qué contratos de cápita de 360 ejecutan cada nota: la matriz PYM → contratos «PYM»; el listado asistencial → los demás; el resto → todos
    n["linea"] = np.where(n.formato.str.contains("PYM"), "PYM", np.where(n.formato.str.contains("SERVICIOS INCLUIDOS"), "ASISTENCIAL", ""))
    salida = []
    for (eps, contrato, reg, mun, linea), b in n.groupby(["eps", "contrato", "regimen", "municipio", "linea"]):
        # meta por unidad: cada actividad (clave) cuenta una vez aunque tenga varios CUPS
        meta = b.drop_duplicates(["unidad", "clave", "cups"] if (b.clave == "").all() else ["unidad", "clave"])
        u = meta.groupby("unidad", as_index=False).agg(descripcion=("descripcion", "first"), categoria=("categoria", "first"), eventos_mes=("eventos_mes", "sum"),
                                                       eventos_propuesta=("eventos_propuesta", "sum"), tarifa=("tarifa", "max"), tope_valor=("tope_valor", "sum"),
                                                       formato=("formato", "first"))
        mapa = b[["unidad", "cups"]].drop_duplicates()
        x = d[d.eps == eps]
        if reg and "-" not in reg: x = x[(x.regimen == reg) | (x.regimen == "")]
        if mun: x = x[x.municipio == mun]
        if linea: x = x[x.contrato.map(norm).str.contains("PYM") == (linea == "PYM")]
        e = x.merge(mapa, on="cups").groupby(["unidad", "mes"], as_index=False)[["cantidad", "valor"]].sum()
        pv = e.pivot_table(index="unidad", columns="mes", values="cantidad", aggfunc="sum", fill_value=0.0) if len(e) else pd.DataFrame()
        u = u.merge(pv.reset_index(), on="unidad", how="left") if len(pv) else u
        u = u.assign(eps=eps, contrato=contrato + (f" ({linea})" if linea else ""), regimen=reg, municipio=mun)
        u["valor_ejecutado"] = u.unidad.map(e.groupby("unidad").valor.sum()).fillna(0.0)
        # CUPS facturados en la cápita de esa EPS/municipio que no están en la nota técnica
        sn = x[~x.cups.isin(set(mapa.cups))].groupby("cups", as_index=False).agg(valor_ejecutado=("valor", "sum"))
        if len(sn):
            ps = x[~x.cups.isin(set(mapa.cups))].pivot_table(index="cups", columns="mes", values="cantidad", aggfunc="sum", fill_value=0.0).reset_index()
            sn = sn.merge(ps, on="cups").rename(columns={"cups": "unidad"}).assign(eps=eps, contrato=contrato + (f" ({linea})" if linea else ""), regimen=reg, municipio=mun, eventos_mes=0.0,
                                                                                   formato="FACTURADO EN CÁPITA SIN NOTA TÉCNICA")
            u = pd.concat([u, sn], ignore_index=True)
        salida.append(u)
    out = pd.concat(salida, ignore_index=True)
    meses = sorted(c for c in out.columns if re.fullmatch(r"\d{4}-\d{2}", str(c)))
    if meses_sel: meses = [m for m in meses if m in meses_sel]
    for m in meses: out[m] = out[m].fillna(0.0)
    out["promedio_mes"] = out[meses].mean(axis=1) if meses else 0.0
    out["pct_ejecucion"] = np.where(out.eventos_mes > 0, out.promedio_mes / out.eventos_mes.replace(0, np.nan), np.nan)
    out["descripcion"] = out.descripcion.fillna(out.unidad.map(descripciones())).fillna("")
    def est(r):
        if r.formato == "FACTURADO EN CÁPITA SIN NOTA TÉCNICA": return "FACTURADO SIN NOTA TÉCNICA"
        if not meses: return "INFO: SIN FACTURACIÓN CARGADA"
        if not r.eventos_mes: return "INCLUIDO EN CÁPITA (SIN META)" if r.promedio_mes else "INCLUIDO EN CÁPITA (SIN META) · SIN EJECUCIÓN"
        if r.promedio_mes == 0: return "SIN EJECUCIÓN"
        if r.pct_ejecucion > 1 + NT_EXCESO: return "EXCEDE LA NOTA TÉCNICA"
        if r.pct_ejecucion < BAJA_NT: return "BAJA EJECUCIÓN"
        return "OK"
    out["estado"] = out.apply(est, axis=1)
    cols = ["eps", "contrato", "regimen", "municipio", "unidad", "descripcion", "categoria", "eventos_mes", "eventos_propuesta", "tarifa"] + meses + \
           ["promedio_mes", "pct_ejecucion", "valor_ejecutado", "tope_valor", "estado", "formato"]
    for c in cols:
        if c not in out: out[c] = np.nan
    out = out[cols].rename(columns={"unidad": "cups_o_bloque", "promedio_mes": "cant_prom_mes", **{m: f"cant_{m}" for m in meses}})
    return out.sort_values(["eps", "municipio", "eventos_mes"], ascending=[True, True, False]).reset_index(drop=True)

def resumen_nt(fuente=None):
    e = ejecucion_nt(fuente)
    if e.empty: return dict(vacio=True, filas=[], estados=[])
    g = e.groupby(["eps", "contrato", "regimen", "municipio"], as_index=False).agg(
        cups=("cups_o_bloque", "size"), con_meta=("eventos_mes", lambda s: int((s > 0).sum())), meta_eventos_mes=("eventos_mes", "sum"),
        ejecutado_prom_mes=("cant_prom_mes", "sum"), valor_ejecutado=("valor_ejecutado", "sum"), tope_mes=("tope_valor", "sum"),
        excede=("estado", lambda s: int((s == "EXCEDE LA NOTA TÉCNICA").sum())), baja=("estado", lambda s: int(s.isin(["BAJA EJECUCIÓN", "SIN EJECUCIÓN"]).sum())),
        sin_nota=("estado", lambda s: int((s == "FACTURADO SIN NOTA TÉCNICA").sum())))
    est = e.estado.value_counts().rename_axis("estado").reset_index(name="n")
    return dict(filas=g.to_dict("records"), estados=est.to_dict("records"))

def recomendaciones():
    r = resumen_nt()
    if r.get("vacio"): return []
    out = []
    for x in r["filas"]:
        lugar = f"{x['eps'].split(' ')[0] if not x['eps'].startswith('ENTIDAD') else 'SANITAS'} {x['municipio'] or x['regimen']}".strip()
        if x["baja"]: out.append(dict(nivel="naranja", modulo="Facturación / RIPS / costos",
                                      texto=f"Nota técnica {lugar}: {x['baja']} CUPS/bloques con baja o nula ejecución frente a la meta mensual: riesgo de descuento por "
                                            "incumplimiento de metas de cápita/PYM; revisar demanda inducida y registro (RIPS) de esas actividades."))
        if x["excede"]: out.append(dict(nivel="info", modulo="Facturación / RIPS / costos",
                                        texto=f"Nota técnica {lugar}: {x['excede']} CUPS/bloques por encima de la meta (+{NT_EXCESO:.0%}): soporte para renegociar la frecuencia de uso."))
        if x["sin_nota"]: out.append(dict(nivel="info", modulo="Facturación / RIPS / costos",
                                          texto=f"Nota técnica {lugar}: {x['sin_nota']} CUPS facturados en cápita que no están en la nota técnica: verificar si están incluidos o son cobrables por evento."))
    return out

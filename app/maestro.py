"""Maestro de medicamentos de Software 360 con CUM (carga «maestro_med»): homologación automática CUM ↔ código 360 y calidad del maestro.

Cómo se relaciona con la facturación: el código que factura 360 (G-0052) NO se puede unir por número con la columna «Código» del maestro
(la numeración se corre a partir de ~150 y los códigos de 5 dígitos son otra serie). Se une por el NOMBRE GENÉRICO: la ACTIVIDAD
facturada es el «Genérico» del maestro (98 % del valor coincide exacto). Un genérico puede tener varias marcas, cada una con su CUM: todos
quedan homologados al código facturado, porque el RIPS puede reportar el CUM de la marca dispensada.
Coincidencia aproximada (≥ 95 % de similitud, p. ej. «SOLUCIN» vs «SOLUCION») se usa pero queda marcada para validar.
"""
import difflib
import numpy as np, pandas as pd
from .db import conn, df
from .loaders import ALIAS, norm, cum_norm, hojas_df, _extraer

ALIAS["maestro_med"] = dict(codigo=["CODIGO"], nombre=["NOMBRE"], generico=["GENERICO"], unidad=["UNIDAD"], forma=["FORMA FARM", "FORMA FARMACEUTICA"],
                            presentacion=["PRESENTACION"], concentracion=["CONCENTRACION"], marca=["MARCA"],
                            cum_original=["COD CUM ORIGINAL", "COD CUM", "CUM", "CODIGO CUM"], invima=["COD INVIMA", "REGISTRO INVIMA", "INVIMA"],
                            cum=["CUM RIPS NORMALIZADO", "CUM RIPS", "CUM NORMALIZADO"], estado_cum=["ESTADO CUM"], estado_invima=["ESTADO INVIMA"],
                            principio=["PRINCIPIO ACTIVO"], alerta=["ALERTA CUM"], observacion=["OBSERVACION REVISION", "OBSERVACION"])
COLS = list(ALIAS["maestro_med"])
UMBRAL = 0.95

def leer(path):
    """Hoja del maestro (la que trae Código, Genérico y CUM) → DataFrame con columnas canónicas y CUM normalizado."""
    mejor = None
    for nombre, raw in hojas_df(path).items():
        t = _extraer(raw, ALIAS["maestro_med"])
        if t is None or not {"codigo", "generico"} <= set(t.columns) or not ({"cum", "cum_original"} & set(t.columns)): continue
        if mejor is None or len(t.columns) > len(mejor.columns): mejor = t
    if mejor is None: raise ValueError("No se reconoció el maestro de medicamentos: se esperan columnas Código, Genérico y Cod CUM (o CUM_RIPS).")
    t = mejor
    for c in COLS:
        if c not in t: t[c] = ""
        t[c] = t[c].fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    t = t[(t.codigo != "") & (t.generico != "")]
    t["cum"] = [cum_norm(a or b) for a, b in zip(t.cum, t.cum_original)]
    t.loc[~t.cum.str.fullmatch(r"\d+(-\d+)?") | t.cum.isin(["0"]), "cum"] = ""          # «NO APLICA», «0», vacío → sin CUM
    t["gnorm"] = t.generico.map(norm)
    return t.reset_index(drop=True)

def _facturados():
    """Códigos de medicamentos (no insumos D-) facturados en 360 con su descripción y valor."""
    d = df("SELECT codigo, MAX(descripcion) descripcion, MAX(clase) clase, SUM(valor) valor, SUM(lineas) lineas FROM medins WHERE codigo NOT LIKE 'D-%' "
           "AND clase <> 'INSUMO' AND codigo <> 'SIN CODIGO' GROUP BY codigo")
    d["gnorm"] = d.descripcion.map(norm)
    return d

def emparejar(fact=None, maestro=None):
    """Código 360 facturado → genérico del maestro: (codigo, generico_maestro, tipo EXACTO|APROXIMADO|SIN COINCIDENCIA, similitud)."""
    fact = _facturados() if fact is None else fact
    maestro = df("SELECT * FROM maestro_med") if maestro is None else maestro
    if fact.empty or maestro.empty: return pd.DataFrame(columns=["codigo", "gnorm_maestro", "tipo", "similitud"])
    gs = sorted(set(maestro.gnorm)); out = []
    for c, g in zip(fact.codigo, fact.gnorm):
        if g in gs: out.append((c, g, "EXACTO", 1.0)); continue
        cand = difflib.get_close_matches(g, gs, n=1, cutoff=UMBRAL)
        out.append((c, cand[0], "APROXIMADO", round(difflib.SequenceMatcher(None, g, cand[0]).ratio(), 3)) if cand else (c, "", "SIN COINCIDENCIA", 0.0))
    return pd.DataFrame(out, columns=["codigo", "gnorm_maestro", "tipo", "similitud"])

def homologar_auto():
    """Llena hom_codigo (fuente «maestro») con todos los CUM del genérico de cada código facturado. Las homologaciones manuales no se tocan.
    Un CUM que resulta para dos códigos 360 se asigna al de mayor valor facturado y se informa como conflicto."""
    maestro = df("SELECT * FROM maestro_med")
    fact = _facturados()
    if maestro.empty or fact.empty: return dict(homologados=0, cums=0, conflictos=0)
    e = emparejar(fact, maestro)
    e = e[e.tipo != "SIN COINCIDENCIA"].merge(fact[["codigo", "descripcion", "valor"]], on="codigo")
    m = maestro[maestro.cum != ""][["gnorm", "cum"]]
    x = e.merge(m, left_on="gnorm_maestro", right_on="gnorm").sort_values("valor", ascending=False)
    conflictos = int(x.groupby("cum").codigo.nunique().gt(1).sum())
    x = x.drop_duplicates("cum")
    with conn() as c:
        manuales = {r[0] for r in c.execute("SELECT codigo_rips FROM hom_codigo WHERE COALESCE(fuente,'manual')='manual'")}
        c.execute("DELETE FROM hom_codigo WHERE fuente LIKE 'maestro%'")
        filas = [(r.cum, r.codigo, r.descripcion, f"maestro:{r.tipo}") for r in x.itertuples() if r.cum not in manuales]
        c.executemany("INSERT OR REPLACE INTO hom_codigo(codigo_rips, codigo_360, descripcion, fuente) VALUES(?,?,?,?)", filas)
    return dict(homologados=int(x.codigo.nunique()), cums=len(filas), conflictos=conflictos)

PRIORIDAD = ["SIN COINCIDENCIA EN EL MAESTRO", "EN EL MAESTRO SIN CUM (no se puede reportar en el RIPS)", "CUM COMPARTIDO CON OTRO GENÉRICO",
             "SIN REGISTRO INVIMA / VITAL", "CUM SIN CONSECUTIVO", "COINCIDENCIA APROXIMADA (validar el nombre en 360)"]

def validacion(mes=None):
    """Lo facturado (medicamentos e insumos) frente al maestro: coincidencia, CUM disponibles, estado CUM/INVIMA y alertas."""
    w, p = (" WHERE mes=?", (mes,)) if mes else ("", ())
    f = df(f"SELECT codigo, MAX(descripcion) descripcion, MAX(clase) clase, SUM(cantidad) cantidad, SUM(valor) valor, SUM(lineas) lineas FROM medins{w} GROUP BY codigo", p)
    maestro = df("SELECT * FROM maestro_med")
    if f.empty or maestro.empty: return pd.DataFrame()
    f["gnorm"] = f.descripcion.map(norm)
    med = f[(f.clase != "INSUMO") & ~f.codigo.str.startswith("D-") & (f.codigo != "SIN CODIGO")]
    e = emparejar(med, maestro).set_index("codigo")
    g = maestro.groupby("gnorm").agg(generico_maestro=("generico", "first"), codigos_maestro=("codigo", lambda s: ", ".join(sorted(set(s)))),
                                     marcas=("marca", lambda s: ", ".join(sorted(set(s) - {""}))), cums=("cum", lambda s: " | ".join(sorted(set(s) - {""}))),
                                     n_cum=("cum", lambda s: len(set(s) - {""})), sin_cum=("cum", lambda s: int((s == "").sum())),
                                     estados_cum=("estado_cum", lambda s: ", ".join(sorted(set(s) - {""}))),
                                     estados_invima=("estado_invima", lambda s: ", ".join(sorted(set(s) - {""}))),
                                     alertas=("alerta", lambda s: "; ".join(sorted(set(s) - {""}))))
    f["tipo_coincidencia"] = f.codigo.map(e.tipo).fillna("")
    f["similitud"] = f.codigo.map(e.similitud)
    f = f.merge(g, left_on=f.codigo.map(e.gnorm_maestro).fillna(""), right_index=True, how="left").drop(columns=["key_0"], errors="ignore")
    def est(r):
        if r.clase == "INSUMO" or str(r.codigo).startswith("D-"): return "INFO: INSUMO (fuera del maestro de medicamentos)"
        if r.tipo_coincidencia in ("", "SIN COINCIDENCIA"): return PRIORIDAD[0]
        a = []
        if not r.n_cum: a.append(PRIORIDAD[1])
        if "OTRO GENERICO" in str(r.alertas or "").upper(): a.append(PRIORIDAD[2])
        if "SIN REGISTRO" in str(r.estados_invima or "").upper(): a.append(PRIORIDAD[3])
        if "SIN CONSECUTIVO" in str(r.estados_cum or "").upper(): a.append(PRIORIDAD[4])
        if r.tipo_coincidencia == "APROXIMADO": a.append(PRIORIDAD[5])
        return "; ".join(a) or "OK"
    f["estado"] = f.apply(est, axis=1)
    cols = ["clase", "codigo", "descripcion", "cantidad", "valor", "lineas", "tipo_coincidencia", "similitud", "generico_maestro", "codigos_maestro",
            "marcas", "n_cum", "cums", "estados_cum", "estados_invima", "alertas", "estado"]
    return f.reindex(columns=cols).sort_values("valor", ascending=False).reset_index(drop=True)

def calidad():
    """Indicadores del maestro (estado CUM / INVIMA / alertas) y listas para corregir en Software 360."""
    m = df("SELECT * FROM maestro_med")
    if m.empty: return dict(kpis={}, vacio=True)
    cnt = lambda c: m[c].replace("", "SIN DATO").value_counts().rename_axis("estado").reset_index(name="productos").to_dict("records")
    comp = m[m.cum != ""].groupby("cum").agg(veces=("codigo", "size"), genericos=("gnorm", "nunique"), codigos=("codigo", lambda s: ", ".join(s)),
                                            nombres=("nombre", lambda s: " | ".join(s))).reset_index()
    comp = comp[comp.veces > 1].assign(tipo=lambda d: np.where(d.genericos > 1, "DISTINTO GENÉRICO (error de parametrización)", "MISMO GENÉRICO (unificar código)"))
    return dict(kpis=dict(productos=len(m), con_cum=int((m.cum != "").sum()), sin_cum=int((m.cum == "").sum()), genericos=int(m.gnorm.nunique()),
                          cum_compartido_otro_generico=int((comp.genericos > 1).sum()), observaciones=int((m.observacion != "").sum())),
                estado_cum=cnt("estado_cum"), estado_invima=cnt("estado_invima"),
                compartidos=comp.sort_values(["genericos", "veces"], ascending=False).to_dict("records"),
                observaciones=m[m.observacion != ""][["codigo", "nombre", "generico", "marca", "cum", "observacion"]].to_dict("records"))

def rips_vs_maestro(mes=None):
    """CUM reportados en los RIPS (JSON/TXT y Excel) frente al maestro: si existen, su genérico y su estado."""
    w, p = (" WHERE mes=?", (mes,)) if mes else ("", ())
    r = pd.concat([df(f"SELECT codigo cum_rips, MAX(descripcion) desc_rips, SUM(cantidad) cantidad, SUM(valor) valor, COUNT(DISTINCT factura) facturas FROM rips_det{w} "
                      f"{'AND' if w else 'WHERE'} tipo_servicio='Medicamentos' GROUP BY codigo", p),
                   df(f"SELECT cups cum_rips, MAX(descripcion) desc_rips, SUM(cantidad) cantidad, SUM(valor) valor, COUNT(DISTINCT factura) facturas FROM rips{w} "
                      f"{'AND' if w else 'WHERE'} servicio LIKE '%edicament%' GROUP BY cups", p)]).drop_duplicates("cum_rips")
    m = df("SELECT * FROM maestro_med")
    if r.empty or m.empty: return pd.DataFrame()
    r["cum"] = r.cum_rips.map(cum_norm)
    g = m[m.cum != ""].groupby("cum").agg(codigos_maestro=("codigo", lambda s: ", ".join(s)), generico=("generico", "first"), marca=("marca", "first"),
                                          estado_cum=("estado_cum", "first"), estado_invima=("estado_invima", "first"), alerta=("alerta", "first"))
    h = df("SELECT codigo_rips, codigo_360 FROM hom_codigo"); hm = {cum_norm(a): b for a, b in zip(h.codigo_rips, h.codigo_360)}
    x = r.merge(g, left_on="cum", right_index=True, how="left"); x["codigo_360"] = x.cum.map(hm).fillna("")
    x["estado"] = np.select([x.generico.isna(), x.codigo_360 == "", x.estado_invima.fillna("").str.contains("SIN REGISTRO")],
                            ["CUM DEL RIPS NO ESTÁ EN EL MAESTRO", "EN EL MAESTRO, SIN CÓDIGO 360 FACTURADO", "SIN REGISTRO INVIMA / VITAL"], "OK")
    return x.sort_values("valor", ascending=False).reset_index(drop=True)

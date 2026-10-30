"""Clasificación CAPITA / EVENTO y asignación de grupo y subgrupo, con trazabilidad.

Precedencia (columna ``regla_modalidad``):

1. ``CATALOGO_OFERTADO``     – el código está en MARCACION CUPS CAPITA con estado OFERTADO  -> CAPITA
2. ``CATALOGO_NO_OFERTADO``  – está en el catálogo con estado NO OFERTADO                  -> EVENTO
3. ``NOTA_TECNICA``          – no está en el catálogo pero sí en la Nota Técnica CAPITA      -> CAPITA
4. ``MODALIDAD_DECLARADA``   – carpeta/contrato de la remisión dice CAPITA, EVENTO o PGP
5. ``POR_DEFECTO``           – sin marca: EVENTO (SIN MARCA), como en el dashboard

El resultado de esa precedencia queda en ``modalidad_catalogo`` (lo que *debería* ser según el
contrato). La columna ``modalidad`` es la **modalidad efectiva** usada en los cálculos: la declarada
por la remisión (carpeta o contrato: así se facturó) cuando existe, y si no, la del catálogo
(``fuente_modalidad`` = DECLARADA / CLASIFICADA). Las diferencias entre ambas se reportan como
discrepancias: un servicio OFERTADO facturado en una remisión de EVENTO (posible doble pago) o uno
NO OFERTADO en una remisión de CÁPITA.

Grupo (columna ``regla_grupo``): catálogo -> Nota Técnica -> reglas del PARSER del dashboard
(``classifyGroup``) -> heurística por tipo de archivo RIPS y rango CUPS (marcada HEURISTICA).
"""
from __future__ import annotations

import re

import pandas as pd

from .constantes import norm


def grupo_parser(codigo: str | None, descripcion: str | None, grupo_marcacion: str | None) -> str:
    """Réplica de ``classifyGroup`` del dashboard CAPITA·EVENTO."""
    a, c = norm(descripcion), str(codigo or "")
    if "TRASLADO" in a:
        return "TRASLADOS"
    if grupo_marcacion:
        return "URGENCIAS" if grupo_marcacion == "URGENCIAS CON OBSERVACION" else grupo_marcacion
    if re.match(r"^(G-|D-|MD)", c):
        return "MEDICAMENTOS E INSUMOS"
    if "INTERNACI" in a:
        return "INTERNACION BASICA"
    if "DERECHOS DE SALA" in a:
        return "PROCEDIMIENTOS"
    if re.search(r"ANTIGENO|SARS|DENGUE|HEPATITIS|VIH|SEROLOG|HEMOGRAMA", a):
        return "LABORATORIO"
    return "OTROS"


def grupo_heuristico(tipo_archivo: str, codigo: str | None, finalidad, tipo_at: str | None) -> tuple[str, str]:
    """(grupo, subgrupo) cuando no hay catálogo. SUPUESTO documentado en docs/REGLAS_CLASIFICACION.md."""
    c = str(codigo or "")
    fin = str(finalidad or "").zfill(2) if str(finalidad or "").isdigit() else str(finalidad or "")
    if tipo_archivo == "AM":
        return "MEDICAMENTOS", "Medicamentos (AM)"
    if tipo_archivo == "AT":
        return {"TRASLADOS": ("TRASLADOS", "Traslados"), "ESTANCIAS": ("INTERNACION BASICA", "Estancias"),
                "HONORARIOS": ("PROCEDIMIENTOS", "Honorarios"),
                "MATERIALES E INSUMOS": ("MEDICAMENTOS E INSUMOS", "Materiales e insumos")}.get(
            tipo_at or "", ("OTROS", "Otros servicios"))
    if tipo_archivo == "AU":
        return "URGENCIAS", "Observación de urgencias"
    if tipo_archivo == "AH":
        return "INTERNACION BASICA", "Hospitalización"
    if tipo_archivo == "AN":
        return "ATENCION PARTO Y RN", "Recién nacido"
    if c in {"890203", "890303", "890703"}:
        return "ODONTOLOGIA", "Consulta odontológica"
    if c.startswith("8907"):
        return "URGENCIAS", "Consulta de urgencias"
    if c.startswith(("8902", "8903", "8904")) and tipo_archivo == "AC":
        return "CONSULTA MEDICA ESPECIALIZADA", "Consulta especializada"
    if tipo_archivo == "AC":
        if fin in {"01", "02", "03", "04", "05", "06", "07", "08", "09"}:
            return "PROMOCION Y MANTENIMIENTO DE LA SALUD", "Consulta PyMS"
        return "MEDICINA GENERAL", "Consulta medicina general"
    if c.startswith("90"):
        return "LABORATORIO", "Laboratorio clínico"
    if c.startswith(("87", "88")):
        return "IMAGENOLOGIA", "Imágenes diagnósticas"
    if c.startswith(("23", "24", "9970", "9971")):
        return "ODONTOLOGIA", "Procedimientos odontológicos"
    if c.startswith(("990", "993")) or fin in {"3", "4", "03", "04"}:
        return "PROMOCION Y MANTENIMIENTO DE LA SALUD", "Protección específica / detección temprana"
    if c.startswith("93"):
        return "APOYO TERAPEUTICO", "Terapias"
    if c.startswith("73"):
        return "ATENCION PARTO Y RN", "Atención del parto"
    if c.startswith(("601", "S31")):
        return "TRASLADOS", "Traslados"
    return "PROCEDIMIENTOS", "Procedimientos"


def _indice(df: pd.DataFrame | None, col_cod="codigo") -> dict[str, dict]:
    if df is None or df.empty:
        return {}
    out = {}
    for r in df.to_dict("records"):
        for k in (r.get(col_cod), r.get("homologo")):
            if k is not None and not (isinstance(k, float) and pd.isna(k)):
                out.setdefault(str(k).strip(), r)
    return out


def clasificar(servicios: pd.DataFrame, catalogo: pd.DataFrame | None, nota_tecnica: pd.DataFrame | None,
               defecto: str = "EVENTO") -> pd.DataFrame:
    s = servicios.copy()
    cat = _indice(catalogo)
    nt = _indice(nota_tecnica)
    mods, reglas, grupos, subgrupos, rgrupo, estados, en_nt, descs = [], [], [], [], [], [], [], []
    for r in s.itertuples(index=False):
        cod = r.codigo if isinstance(r.codigo, str) else None
        # Medicamentos del catálogo NT pueden venir con sufijo '-n' del CUM: se prueba sin él.
        k_cat = cat.get(cod) or (cat.get(cod.split("-")[0]) if cod and r.tipo_archivo == "AM" else None)
        k_nt = nt.get(cod) or (nt.get(cod.split("-")[0]) if cod and r.tipo_archivo == "AM" else None)
        estado = norm(k_cat.get("estado")) if k_cat else ""
        if r.es_episodio:
            mod, regla = (r.modalidad_declarada or defecto), "EPISODIO"
        elif estado == "OFERTADO":
            mod, regla = "CAPITA", "CATALOGO_OFERTADO"
        elif estado == "NO OFERTADO":
            mod, regla = "EVENTO", "CATALOGO_NO_OFERTADO"
        elif k_nt:
            mod, regla = "CAPITA", "NOTA_TECNICA"
        elif isinstance(r.modalidad_declarada, str) and r.modalidad_declarada:
            mod, regla = r.modalidad_declarada, "MODALIDAD_DECLARADA"
        else:
            mod, regla = defecto, "POR_DEFECTO"
        g_cat = k_cat.get("grupo") if k_cat else None
        g_nt = k_nt.get("grupo") if k_nt else None
        g_cat = None if g_cat is None or (isinstance(g_cat, float) and pd.isna(g_cat)) else g_cat
        g_nt = None if g_nt is None or (isinstance(g_nt, float) and pd.isna(g_nt)) else g_nt
        desc = r.descripcion if isinstance(r.descripcion, str) else (
            (k_cat or {}).get("descripcion") or (k_nt or {}).get("descripcion"))
        if isinstance(desc, float):
            desc = None
        g_h, sub_h = grupo_heuristico(r.tipo_archivo, cod, r.finalidad, r.tipo_servicio_at)
        if g_cat or g_nt:
            g = grupo_parser(cod, desc, g_cat or g_nt)
            rg = "CATALOGO" if g_cat else "NOTA_TECNICA"
        elif r.tipo_archivo in ("AM", "AU", "AH", "AN") or (r.tipo_archivo == "AT" and r.tipo_servicio_at):
            g, rg = g_h, "TIPO_ARCHIVO_RIPS"
        else:
            g = grupo_parser(cod, desc, None)
            rg = "REGLA_DASHBOARD"
            if g == "OTROS":
                g, rg = g_h, "HEURISTICA"
        sub = (k_cat or {}).get("subgrupo")
        mods.append(mod)
        reglas.append(regla)
        grupos.append(g)
        subgrupos.append(sub if isinstance(sub, str) and sub else sub_h)
        rgrupo.append(rg)
        estados.append(estado or "SIN MARCA")
        en_nt.append(bool(k_nt))
        descs.append(desc)
    s["modalidad_catalogo"] = mods
    s["regla_modalidad"] = reglas
    dec = s["modalidad_declarada"].where(s["modalidad_declarada"].isin(["CAPITA", "EVENTO", "PGP"]))
    s["modalidad"] = dec.fillna(s["modalidad_catalogo"])
    s["fuente_modalidad"] = dec.notna().map({True: "DECLARADA", False: "CLASIFICADA"})
    s["estado_catalogo"] = estados
    s["en_nota_tecnica"] = en_nt
    s["grupo"] = grupos
    s["subgrupo"] = subgrupos
    s["regla_grupo"] = rgrupo
    s["descripcion"] = descs
    s["discrepancia_modalidad"] = [
        (dec == "EVENTO" and est == "OFERTADO") or (dec == "CAPITA" and est == "NO OFERTADO")
        for dec, est in zip(s["modalidad_declarada"], s["estado_catalogo"])]
    return s

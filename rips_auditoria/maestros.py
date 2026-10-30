"""Maestros locales: catálogo de marcación, Nota Técnica, tarifario de evento, facturación por
ámbito y despacho de medicamentos.

Se guardan en ``datos/maestros/<nombre>.csv`` (nunca en git) con un ``.meta.json`` que registra
fuente, fecha de carga y si la estructura está **pendiente de confirmación**.

Formatos aceptados por maestro:
* Plantilla CSV/XLSX con las columnas de ``PLANTILLAS`` (ver ``config/plantillas/``).
* El libro ``INFORME DE SERVICIOS 2026 BASE DE DATOS ANALISIS.xlsx`` (hojas MARCACION CUPS CAPITA,
  NT COSTOS, NOTA TECNICA CAPITA NEPS), leído con la misma lógica del PARSER del dashboard.
"""
from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from .config import DIR_CONFIG, DIR_DATOS
from .constantes import ALIAS_GRUPO, CONTROL_NT_MUNICIPIO, MESES, norm

PLANTILLAS: dict[str, list[str]] = {
    "catalogo": ["codigo", "homologo", "descripcion", "grupo", "subgrupo", "estado", "tarifa", "uvb"],
    "nota_tecnica": ["codigo", "descripcion", "grupo", "tipo_codigo", "municipio", "mes", "frecuencia_prevista",
                     "costo_unitario"],
    "tarifario": ["codigo", "descripcion", "tarifa", "vigencia_desde", "vigencia_hasta", "manual_referencia",
                  "observacion"],
    "facturacion": ["periodo", "hospital", "municipio", "ambito", "modalidad", "num_factura", "codigo",
                    "descripcion", "cantidad", "valor"],
    "despacho_medicamentos": ["periodo", "unidades", "valor"],
}
# Estructuras no entregadas en los artefactos: se aceptan, pero se marcan como pendientes.
PENDIENTES = {
    "tarifario": "Formato exacto del tarifario pactado de evento no disponible en los artefactos",
    "facturacion": "Formato del reporte de facturación por ámbito no disponible en los artefactos",
}
DIR_MAESTROS = DIR_DATOS / "maestros"


# ----------------------------------------------------------------------------- almacenamiento
def guardar(nombre: str, df: pd.DataFrame, fuente: str, notas: list[str] | None = None) -> None:
    DIR_MAESTROS.mkdir(parents=True, exist_ok=True)
    df.to_csv(DIR_MAESTROS / f"{nombre}.csv", index=False, encoding="utf-8")
    meta = {"nombre": nombre, "fuente": fuente, "cargado": datetime.now().isoformat(timespec="seconds"),
            "filas": int(len(df)), "pendiente_confirmacion": PENDIENTES.get(nombre), "notas": notas or []}
    (DIR_MAESTROS / f"{nombre}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def leer(nombre: str, usar_semilla: bool = True) -> pd.DataFrame | None:
    p = DIR_MAESTROS / f"{nombre}.csv"
    if p.exists():
        return pd.read_csv(p, dtype={"codigo": str, "homologo": str, "periodo": str, "num_factura": str})
    if usar_semilla:
        from .referencias.semillas import SEMILLAS
        if nombre in SEMILLAS:
            return SEMILLAS[nombre]()
    return None


def estado() -> list[dict]:
    from .referencias.semillas import SEMILLAS
    out = []
    for nombre in PLANTILLAS:
        p = DIR_MAESTROS / f"{nombre}.meta.json"
        if p.exists():
            out.append(json.loads(p.read_text(encoding="utf-8")))
        elif nombre in SEMILLAS:
            out.append({"nombre": nombre, "fuente": "semilla parcial extraída de los dashboards de referencia",
                        "pendiente_confirmacion": "Cargue el maestro completo (INFORME DE SERVICIOS o plantilla)"})
        else:
            out.append({"nombre": nombre, "fuente": None, "pendiente_confirmacion": PENDIENTES.get(nombre)})
    return out


# ----------------------------------------------------------------------------- utilidades
def _num(v) -> float:
    if v is None or v == "" or (isinstance(v, float) and pd.isna(v)):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(" ", "")
    if "," in s and "." in s:      # 1.234,56
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _cod(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return s or None


def _grupo(g) -> str:
    g = norm(g)
    return ALIAS_GRUPO.get(g, g)


def _hojas(datos: bytes) -> dict[str, list[list]]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(datos), read_only=True, data_only=True)
    return {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}


def _hoja(hojas: dict, prefijo: str):
    for n, filas in hojas.items():
        if norm(n).startswith(norm(prefijo)):
            return n, filas
    return None, None


def _tabla_plantilla(nombre: str, datos: bytes, archivo: str) -> pd.DataFrame:
    if archivo.lower().endswith((".xlsx", ".xlsm")):
        df = pd.read_excel(io.BytesIO(datos), dtype=object)
    else:
        txt = datos.decode("utf-8-sig", errors="replace")
        sep = ";" if txt.split("\n", 1)[0].count(";") > txt.split("\n", 1)[0].count(",") else ","
        df = pd.read_csv(io.StringIO(txt), sep=sep, dtype=object)
    df.columns = [norm(c).lower().replace(" ", "_") for c in df.columns]
    faltan = [c for c in ("codigo",) if c in PLANTILLAS[nombre] and c not in df.columns]
    if faltan:
        raise ValueError(f"Faltan columnas {faltan}; use la plantilla config/plantillas/{nombre}.csv")
    for c in PLANTILLAS[nombre]:
        if c not in df.columns:
            df[c] = None
    return df[PLANTILLAS[nombre] + [c for c in df.columns if c not in PLANTILLAS[nombre]]]


# ----------------------------------------------------------------------------- informe de servicios
def leer_marcacion(filas: list[list]) -> pd.DataFrame:
    """Hoja MARCACION CUPS CAPITA. Columnas por encabezado; si no se reconocen, posiciones del
    PARSER del dashboard (B=código, E=grupo, H=estado)."""
    cab = [norm(c) for c in (filas[0] or [])]

    def col(*pref, defecto=None):
        for p in pref:
            for i, c in enumerate(cab):
                if c.startswith(p) or p in c:
                    return i
        return defecto
    i_cod = col("CODIGO CUPS", "CUPS", "CODIGO", defecto=1)
    i_hom = col("HOMOLOG")
    i_des = col("DESCRIP", "NOMBRE")
    i_gr = col("GRUPO", defecto=4)
    i_sub = col("SUBGRUPO", "SUB GRUPO")
    i_est = col("ESTADO", "OFERTADO", "MARCACION", defecto=7)
    i_tar = col("TARIFA")
    i_uvb = col("UVB")
    if i_sub == i_gr:
        i_sub = None
    g = lambda r, i: r[i] if i is not None and i < len(r) else None
    reg = []
    for r in filas[1:]:
        if not r or g(r, i_cod) is None:
            continue
        reg.append({"codigo": _cod(g(r, i_cod)), "homologo": _cod(g(r, i_hom)), "descripcion": g(r, i_des),
                    "grupo": _grupo(g(r, i_gr)), "subgrupo": g(r, i_sub), "estado": norm(g(r, i_est)),
                    "tarifa": _num(g(r, i_tar)) if i_tar is not None else None,
                    "uvb": _num(g(r, i_uvb)) if i_uvb is not None else None})
    return pd.DataFrame(reg, columns=PLANTILLAS["catalogo"])


def leer_nt_costos(filas: list[list]) -> tuple[pd.DataFrame, dict]:
    """Hoja NT COSTOS, misma lógica que ``PARSER.parseInforme``: encabezado con CODIGOCUPS, dos
    corridas de columnas de meses (1ª = frecuencia RIPS, 2ª = costo). Costo unitario = costo/frecuencia."""
    hi = next(i for i, r in enumerate(filas) if r and any(norm(c) == "CODIGOCUPS" for c in r))
    h = [norm(c) for c in filas[hi]]
    runs, cur = [], None
    for i, c in enumerate(h):
        if c in MESES:
            # una corrida termina al interrumpirse las columnas o al repetirse un mes
            if cur and cur[-1] == i - 1 and c not in {h[j] for j in cur}:
                cur.append(i)
            else:
                cur = [i]
                runs.append(cur)
    r_idx, c_idx = runs[0], runs[1]
    n_m = len(r_idx)
    i_cod = h.index("CODIGOCUPS")
    i_des = next((i for i, c in enumerate(h) if c.startswith("DESCRIPCION")), None)
    i_gr = h.index("GRUPO")
    reg = []
    for r in filas[hi + 1:]:
        if not r or r[i_cod] is None:
            continue
        for m in range(n_m):
            q, c = _num(r[r_idx[m]]), _num(r[c_idx[m]])
            reg.append({"codigo": _cod(r[i_cod]), "descripcion": r[i_des] if i_des is not None else None,
                        "grupo": _grupo(r[i_gr]), "grupo_original": norm(r[i_gr]), "mes": m + 1,
                        "frecuencia_rips": q, "costo": c, "costo_unitario": c / q if q else None})
    df = pd.DataFrame(reg)
    return df, {"meses": n_m}


def leer_nt_municipio(filas: list[list]) -> tuple[pd.DataFrame, list[str]]:
    """Hoja NOTA TECNICA CAPITA NEPS (frecuencias previstas por servicio y municipio).

    PENDIENTE DE CONFIRMACIÓN: no se dispone del diseño exacto. Se busca la fila de encabezado
    que tenga una columna de código y al menos dos municipios de la red; cada municipio es una
    columna de frecuencia mensual prevista. Se comparan los totales con los de control.
    """
    munis = list(CONTROL_NT_MUNICIPIO)
    notas = []
    for hi, r in enumerate(filas[:40]):
        h = [norm(c) for c in (r or [])]
        cols = {m: next((i for i, c in enumerate(h) if c.startswith(m)), None) for m in munis}
        if sum(v is not None for v in cols.values()) >= 2 and any("CUPS" in c or "CODIGO" in c for c in h):
            i_cod = next(i for i, c in enumerate(h) if "CUPS" in c or "CODIGO" in c)
            i_des = next((i for i, c in enumerate(h) if c.startswith("DESCRIP") or "NOMBRE" in c), None)
            i_gr = next((i for i, c in enumerate(h) if c.startswith("GRUPO")), None)
            reg = []
            for fila in filas[hi + 1:]:
                if not fila or fila[i_cod] is None or norm(fila[i_cod]).startswith("TOTAL"):
                    continue
                for m, i in cols.items():
                    if i is not None and i < len(fila):
                        reg.append({"codigo": _cod(fila[i_cod]), "descripcion": fila[i_des] if i_des is not None else None,
                                    "grupo": _grupo(fila[i_gr]) if i_gr is not None else None, "municipio": m,
                                    "frecuencia_prevista": _num(fila[i])})
            df = pd.DataFrame(reg)
            tot = df.groupby("municipio")["frecuencia_prevista"].sum()
            for m, ctrl in CONTROL_NT_MUNICIPIO.items():
                v = float(tot.get(m, 0))
                notas.append(f"NT {m}: {v:,.2f} (control {ctrl:,.2f}) {'OK' if abs(v - ctrl) < 0.5 else 'DIFERENTE'}")
            return df, notas
    return pd.DataFrame(), ["No se encontró encabezado con código y municipios en NOTA TECNICA CAPITA NEPS"]


def cargar_informe(datos: bytes) -> dict[str, tuple[pd.DataFrame, list[str]]]:
    """Extrae del libro INFORME DE SERVICIOS los maestros que contiene."""
    hojas = _hojas(datos)
    out: dict[str, tuple[pd.DataFrame, list[str]]] = {}
    n, f = _hoja(hojas, "MARCACION")
    if f:
        out["catalogo"] = (leer_marcacion(f), [f"Hoja {n}"])
    n, f = _hoja(hojas, "NT COSTOS")
    nt_cost = None
    if f:
        nt_cost, info = leer_nt_costos(f)
        out["nt_costos"] = (nt_cost, [f"Hoja {n}: {info['meses']} meses"])
    n, f = _hoja(hojas, "NOTA TECNICA CAPITA")
    if f:
        df, notas = leer_nt_municipio(f)
        out["nt_municipio"] = (df, [f"Hoja {n}"] + notas)
    # Nota técnica unificada: costo unitario de NT COSTOS + frecuencia prevista por municipio.
    if nt_cost is not None and not nt_cost.empty:
        cu = (nt_cost.groupby(["codigo"]).agg(descripcion=("descripcion", "first"), grupo=("grupo", "first"),
                                              costo=("costo", "sum"), frec=("frecuencia_rips", "sum")).reset_index())
        cu["costo_unitario"] = cu["costo"] / cu["frec"].where(cu["frec"] > 0)
        nt = cu.assign(tipo_codigo=cu["grupo"].map(lambda g: "CUMS" if g == "MEDICAMENTOS" else "CUPS"),
                       municipio="TODOS", mes=None, frecuencia_prevista=None)
        if "nt_municipio" in out and not out["nt_municipio"][0].empty:
            fm = out["nt_municipio"][0]
            nt = fm.merge(cu[["codigo", "costo_unitario", "descripcion", "grupo"]], on="codigo", how="outer",
                          suffixes=("", "_c"))
            nt["descripcion"] = nt["descripcion"].fillna(nt.pop("descripcion_c"))
            nt["grupo"] = nt["grupo"].fillna(nt.pop("grupo_c"))
            nt["municipio"] = nt["municipio"].fillna("TODOS")
            nt["tipo_codigo"] = nt["grupo"].map(lambda g: "CUMS" if g == "MEDICAMENTOS" else "CUPS")
            nt["mes"] = None
        out["nota_tecnica"] = (nt[PLANTILLAS["nota_tecnica"]],
                               ["Costo unitario = costo NT COSTOS / frecuencia RIPS (acumulado del periodo)"])
    return out


def cargar(nombre: str, datos: bytes, archivo: str, guardar_local: bool = True) -> dict:
    """Carga un maestro desde plantilla o desde el informe de servicios. Devuelve resumen."""
    if nombre == "informe":
        res = cargar_informe(datos)
        if guardar_local:
            for k, (df, notas) in res.items():
                guardar(k, df, archivo, notas)
        return {k: {"filas": len(df), "notas": notas} for k, (df, notas) in res.items()}
    if nombre not in PLANTILLAS:
        raise ValueError(f"Maestro desconocido: {nombre}")
    df = _tabla_plantilla(nombre, datos, archivo)
    df["codigo"] = df["codigo"].map(_cod) if "codigo" in df else None
    for c in ("tarifa", "costo_unitario", "frecuencia_prevista", "cantidad", "valor", "unidades", "uvb"):
        if c in df.columns:
            df[c] = df[c].map(lambda v: _num(v) if v is not None and not (isinstance(v, float) and pd.isna(v)) else None)
    if "grupo" in df.columns:
        df["grupo"] = df["grupo"].map(lambda g: _grupo(g) if g is not None and not pd.isna(g) else None)
    if "estado" in df.columns:
        df["estado"] = df["estado"].map(norm)
    if "municipio" in df.columns:
        df["municipio"] = df["municipio"].map(lambda m: norm(m) or "TODOS")
    if guardar_local:
        guardar(nombre, df, archivo)
    return {nombre: {"filas": len(df), "notas": [PENDIENTES[nombre]] if nombre in PENDIENTES else []}}


def escribir_plantillas(destino: Path) -> None:
    destino.mkdir(parents=True, exist_ok=True)
    for n, cols in PLANTILLAS.items():
        pd.DataFrame(columns=cols).to_csv(destino / f"{n}.csv", index=False)

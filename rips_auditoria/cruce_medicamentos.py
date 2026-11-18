"""Cruce de los medicamentos facturados en RIPS contra el portafolio de farmacia.

Insumo: el libro **CRUCE_PORTAFOLIO_FARMACIA_vs_MAESTRO.xlsx** (portafolio de
farmacia cruzado con el maestro de medicamentos del sistema). Supuestos de
formato, tomados de ese libro:

* Hoja ``PORTAFOLIO_CRUZADO`` (o la primera hoja con una columna «CUM»): una
  fila por presentación con ``CUM normalizado`` (o ``CUM farmacia``),
  ``Código Salud 360``, ``Principio activo``, ``Concentración``, ``Forma farm.``,
  ``Laboratorio``, ``Precio compra``, ``Precio a usar``, ``Alerta precio``,
  ``RESULTADO CRUCE`` (con el maestro), ``Código(s) maestro`` y
  ``Sugerencia / acción``. Las columnas que falten se dejan vacías.
* Hoja ``PARAMETROS``: «Margen mínimo aceptable» (fracción, p. ej. 0,10).
* Hojas ``RESUMEN_EJECUTIVO``, ``NO_ENCONTRADOS``, ``MAESTRO_SIN_PORTAFOLIO`` e
  ``INSUMOS_ANALISIS``: se guardan tal cual para consultarlas en la pantalla.
* El CUM se compara **normalizado**: sin espacios ni tabulaciones, en
  mayúsculas y sin ceros a la izquierda en el expediente ni en el consecutivo
  (``019942561-06`` = ``19942561-6``), igual que el libro.
* Si un CUM aparece varias veces en el portafolio se usa la primera fila.

Cruce con los RIPS (medicamentos del acumulado, ``TIPO_SERVICIO`` =
Medicamentos): por CUM se suman cantidad y valor, y se compara el **valor
unitario facturado** (valor ÷ cantidad) con el portafolio:

* ``NO ESTÁ EN PORTAFOLIO``: el CUM facturado no existe en el portafolio.
* ``FACTURADO BAJO COSTO``: valor unitario < precio de compra.
* ``MARGEN BAJO``: valor unitario sobre el costo pero con margen menor al
  mínimo de PARAMETROS.
* ``FACTURADO BAJO PRECIO`` / ``FACTURADO SOBRE PRECIO``: diferencia con el
  «Precio a usar» mayor que la tolerancia (``cruce_medicamentos.tolerancia_pct``).
* ``OK`` en otro caso; ``SIN PRECIO EN PORTAFOLIO`` si no hay precio.

El impacto es (precio a usar − valor unitario) × cantidad cuando se factura
por debajo. Supuesto: el RIPS factura en la misma unidad que el portafolio
(unidad mínima de dispensación); si no, la diferencia lo mostrará como alerta.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from pathlib import Path

import pandas as pd

from .catalogo_cups import carpeta_catalogos

CARPETA = "farmacia"
ARCHIVO_PORTAFOLIO = "portafolio.csv"
ARCHIVO_PARAMETROS = "parametros.csv"
HOJAS_EXTRA = {"RESUMEN_EJECUTIVO": "resumen.csv", "NO_ENCONTRADOS": "no_encontrados.csv",
               "MAESTRO_SIN_PORTAFOLIO": "maestro_sin_portafolio.csv", "INSUMOS_ANALISIS": "insumos.csv"}

# columna estándar -> textos que la identifican en el libro (nombre normalizado)
COLUMNAS = {
    "cum": ["CUM NORMALIZADO", "CUM FARMACIA", "CUM"],
    "codigo_salud360": ["CODIGO SALUD 360"],
    "principio_activo": ["PRINCIPIO ACTIVO"],
    "concentracion": ["CONCENTRACION"],
    "forma": ["FORMA FARM"],
    "laboratorio": ["LABORATORIO"],
    "precio_regulado": ["PRECIO REGULADO"],
    "precio_compra": ["PRECIO COMPRA"],
    "precio_a_usar": ["PRECIO A USAR", "PRECIO VENTA SUGERIDO", "PRECIO VENTA"],
    "alerta_precio_farmacia": ["ALERTA PRECIO"],
    "resultado_cruce_maestro": ["RESULTADO CRUCE"],
    "codigo_maestro": ["CODIGO S MAESTRO", "CODIGO MAESTRO"],
    "sugerencia_farmacia": ["SUGERENCIA ACCION", "SUGERENCIA"],
}
DEFAULTS = {"tolerancia_pct": 5.0, "margen_minimo": 0.10}
ESTADOS_ALERTA = ("NO ESTÁ EN PORTAFOLIO", "FACTURADO BAJO COSTO", "MARGEN BAJO", "FACTURADO BAJO PRECIO",
                  "FACTURADO SOBRE PRECIO")


def _n(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", " ", t).strip()


def normalizar_cum(valor) -> str:
    """'\\t019942561-06 ' -> '19942561-6' (como el «CUM normalizado» del libro)."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    t = re.sub(r"\s+", "", str(valor)).upper()
    if t in ("", "NAN", "VND", "NONE"):
        return ""
    partes = [p.lstrip("0") or "0" if p.isdigit() else p for p in t.split("-")]
    return "-".join(partes)


def _carpeta() -> Path:
    return carpeta_catalogos() / CARPETA


def _escribir(df: pd.DataFrame, nombre: str) -> None:
    c = _carpeta()
    c.mkdir(parents=True, exist_ok=True)
    tmp = (c / nombre).with_suffix(".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8")
    tmp.replace(c / nombre)


def _leer(nombre: str) -> pd.DataFrame | None:
    ruta = _carpeta() / nombre
    return pd.read_csv(ruta, dtype=str, keep_default_na=False, encoding="utf-8") if ruta.exists() else None


def _hoja_portafolio(hojas: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if "PORTAFOLIO_CRUZADO" in hojas:
        return hojas["PORTAFOLIO_CRUZADO"]
    for df in hojas.values():
        if any("CUM" in _n(c).split() for c in df.columns):
            return df
    raise ValueError("El libro no tiene la hoja PORTAFOLIO_CRUZADO ni una hoja con columna CUM.")


def cargar_cruce(ruta: str | Path) -> dict:
    """Lee el libro del cruce de farmacia y lo guarda normalizado en ``catalogos/farmacia``."""
    ruta = Path(ruta)
    if ruta.suffix.lower() == ".csv":
        hojas = {"PORTAFOLIO_CRUZADO": pd.read_csv(ruta, dtype=object, sep=None, engine="python")}
    else:
        hojas = pd.read_excel(ruta, sheet_name=None, dtype=object, engine="pyxlsb" if ruta.suffix.lower() == ".xlsb" else None)
    origen = _hoja_portafolio(hojas)
    nombres = {c: _n(c) for c in origen.columns}
    port = pd.DataFrame(index=origen.index)
    for destino, claves in COLUMNAS.items():
        col = next((c for k in claves for c, n in nombres.items() if n == k), None)
        port[destino] = origen[col] if col is not None else ""
    port["cum"] = port["cum"].map(normalizar_cum)
    if port["cum"].eq("").all() and "CUM FARMACIA" in nombres.values():
        col = next(c for c, n in nombres.items() if n == "CUM FARMACIA")
        port["cum"] = origen[col].map(normalizar_cum)
    for c in ("precio_compra", "precio_a_usar"):
        port[c] = pd.to_numeric(port[c], errors="coerce")
    for c in port.columns:
        if c not in ("precio_compra", "precio_a_usar"):
            port[c] = port[c].fillna("").astype(str).str.strip()
    sin_cum = int((port["cum"] == "").sum())
    con = port[port["cum"] != ""]
    repetidos = int(con["cum"].duplicated().sum())
    port = con.drop_duplicates("cum")
    _escribir(port, ARCHIVO_PORTAFOLIO)

    params = dict(DEFAULTS)
    if "PARAMETROS" in hojas:
        for fila in hojas["PARAMETROS"].itertuples(index=False):
            texto = _n(fila[0]) if len(fila) else ""
            if "MARGEN MINIMO" in texto and len(fila) > 1 and pd.notna(pd.to_numeric(fila[1], errors="coerce")):
                params["margen_minimo"] = float(fila[1])
    _escribir(pd.DataFrame([params]), ARCHIVO_PARAMETROS)
    guardadas = []
    for hoja, nombre in HOJAS_EXTRA.items():
        if hoja in hojas:
            extra = hojas[hoja]
            if sum(str(c).startswith("Unnamed") for c in extra.columns) >= len(extra.columns) - 1:
                # hoja con un título en la primera fila (p. ej. RESUMEN_EJECUTIVO): el título pasa a ser una fila más
                extra = pd.concat([pd.DataFrame([list(extra.columns)], columns=extra.columns), extra], ignore_index=True)
                extra = extra.mask(extra.astype(str).apply(lambda s: s.str.startswith("Unnamed")))
                extra.columns = (["Concepto", "Valor", "%"] + [f"Columna {i}" for i in range(4, len(extra.columns) + 1)])[:len(extra.columns)]
                if "%" in extra.columns:  # 0.9246 -> "92,5%"
                    frac = pd.to_numeric(extra["%"], errors="coerce")
                    extra["%"] = extra["%"].where(frac.isna(), (frac * 100).round(1).astype(str).str.replace(".", ",") + "%")
            _escribir(extra.dropna(how="all").fillna("").astype(str), nombre)
            guardadas.append(hoja)
    return {"cum_portafolio": len(port), "sin_cum": sin_cum, "cum_repetidos": repetidos,
            "margen_minimo": params["margen_minimo"], "hojas": guardadas, "archivo": ruta.name}


def estado() -> dict | None:
    p = _leer(ARCHIVO_PORTAFOLIO)
    if p is None:
        return None
    ruta = _carpeta() / ARCHIVO_PORTAFOLIO
    return {"cum_portafolio": len(p), "actualizado": dt.datetime.fromtimestamp(ruta.stat().st_mtime).isoformat(timespec="seconds"),
            "hojas": [h for h, n in HOJAS_EXTRA.items() if (_carpeta() / n).exists()]}


def descripciones() -> dict[str, str]:
    """CUM -> «principio activo concentración forma» para completar descripciones de RIPS."""
    p = _leer(ARCHIVO_PORTAFOLIO)
    if p is None:
        return {}
    texto = (p["principio_activo"] + " " + p["concentracion"] + " " + p["forma"]).str.replace(r"\s+", " ", regex=True).str.strip()
    return {c: t for c, t in zip(p["cum"], texto, strict=False) if t}


def _parametros(cfg: dict | None) -> dict:
    guardados = _leer(ARCHIVO_PARAMETROS)
    p = dict(DEFAULTS)
    if guardados is not None and len(guardados):
        p.update({k: float(v) for k, v in guardados.iloc[0].items() if str(v).strip()})
    p.update({k: float(v) for k, v in ((cfg or {}).get("cruce_medicamentos") or {}).items() if v is not None})
    return p


def cruzar(d: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """Medicamentos facturados en los RIPS (``d``: filas homologadas filtradas)
    por CUM, con los datos del portafolio y el estado del cruce, de mayor a menor valor."""
    port = _leer(ARCHIVO_PORTAFOLIO)
    med = d[d["TIPO_SERVICIO"] == "Medicamentos"]
    cols_salida = ["CUM", "descripcion_rips", "facturas", "sedes", "cantidad", "valor", "valor_unitario_rips",
                   "estado_cruce", "impacto", *[c for c in COLUMNAS if c != "cum"], "margen_rips_pct"]
    if med.empty:
        return pd.DataFrame(columns=cols_salida)
    med = med.assign(_cum=med["CODIGO_CUPS_CUMS"].map(normalizar_cum))
    g = med.groupby("_cum").agg(CUM=("CODIGO_CUPS_CUMS", "first"), descripcion_rips=("DESCRIPCION_SERVICIO", "first"),
                                facturas=("_FAC", "nunique") if "_FAC" in med else ("FACTURA_FEV", "nunique"),
                                sedes=("SEDE", "nunique"), cantidad=("CANTIDAD", "sum"), valor=("VALOR_SERVICIO", "sum"))
    g["valor_unitario_rips"] = (g["valor"] / g["cantidad"].where(g["cantidad"] > 0)).round(2)
    if port is None:
        port = pd.DataFrame(columns=list(COLUMNAS))
    port = port.set_index("cum")
    for c in COLUMNAS:
        if c != "cum":
            g[c] = g.index.map(port[c]) if c in port else ""
    for c in ("precio_compra", "precio_a_usar"):
        g[c] = pd.to_numeric(g[c], errors="coerce")
    p = _parametros(cfg)
    tol = p["tolerancia_pct"] / 100
    vu, compra, precio = g["valor_unitario_rips"], g["precio_compra"], g["precio_a_usar"]
    g["margen_rips_pct"] = ((vu - compra) / vu.where(vu > 0) * 100).round(1)
    en_port = g.index.isin(port.index)
    estado = pd.Series("OK", index=g.index)
    estado[precio.isna() & compra.isna()] = "SIN PRECIO EN PORTAFOLIO"
    estado[precio.notna() & (vu > precio * (1 + tol))] = "FACTURADO SOBRE PRECIO"
    estado[precio.notna() & (vu < precio * (1 - tol))] = "FACTURADO BAJO PRECIO"
    estado[compra.notna() & (vu >= compra) & (g["margen_rips_pct"] < p["margen_minimo"] * 100)] = "MARGEN BAJO"
    estado[compra.notna() & (vu < compra)] = "FACTURADO BAJO COSTO"
    estado[~en_port] = "NO ESTÁ EN PORTAFOLIO"
    g["estado_cruce"] = estado
    referencia = precio.fillna(compra)
    g["impacto"] = ((referencia - vu).clip(lower=0) * g["cantidad"]).where(en_port).round(0)
    g = g.sort_values("valor", ascending=False)
    return g.reset_index(drop=True)[cols_salida]


def no_facturados(d: pd.DataFrame) -> pd.DataFrame:
    """Productos del portafolio que no aparecen facturados en los RIPS del filtro."""
    port = _leer(ARCHIVO_PORTAFOLIO)
    if port is None:
        return pd.DataFrame(columns=list(COLUMNAS))
    facturados = set(d.loc[d["TIPO_SERVICIO"] == "Medicamentos", "CODIGO_CUPS_CUMS"].map(normalizar_cum))
    return port[~port["cum"].isin(facturados)].reset_index(drop=True)


def hoja(nombre: str) -> pd.DataFrame:
    """Hoja del libro original guardada (resumen, no_encontrados, maestro_sin_portafolio, insumos)."""
    archivo = {"resumen": "resumen.csv", "no_encontrados": "no_encontrados.csv",
               "maestro_sin_portafolio": "maestro_sin_portafolio.csv", "insumos": "insumos.csv"}[nombre]
    df = _leer(archivo)
    return df if df is not None else pd.DataFrame()


def resumen(d: pd.DataFrame, cfg: dict | None = None) -> dict:
    c = cruzar(d, cfg)
    if c.empty:
        return {"cum_facturados": 0, "cargado": estado() is not None}
    cuenta = c["estado_cruce"].value_counts().to_dict()
    en = c["estado_cruce"] != "NO ESTÁ EN PORTAFOLIO"
    return {
        "cargado": estado() is not None, "cum_facturados": len(c), "valor_medicamentos": float(c["valor"].sum()),
        "en_portafolio": int(en.sum()), "pct_en_portafolio": round(float(en.mean()) * 100, 1),
        "valor_fuera_portafolio": float(c.loc[~en, "valor"].sum()), "por_estado": cuenta,
        "impacto_bajo_precio": float(c["impacto"].fillna(0).sum()),
        "portafolio_no_facturado": len(no_facturados(d)),
    }


def alertas(d: pd.DataFrame, cfg: dict | None = None, maximo: int = 30) -> list[dict]:
    """Alertas para el tablero (sólo si el cruce de farmacia está cargado)."""
    if estado() is None:
        return []
    c = cruzar(d, cfg)
    out = []
    bajo = c[c["estado_cruce"] == "FACTURADO BAJO COSTO"].sort_values("impacto", ascending=False)
    for _, r in bajo.head(maximo).iterrows():
        out.append({"nivel": "ALTA", "categoria": "Medicamento bajo costo", "sede": "", "mes": "",
                    "mensaje": f"CUM {r['CUM']} ({r['principio_activo'] or r['descripcion_rips']}): facturado a "
                               f"$ {r['valor_unitario_rips']:,.0f} por unidad con costo de $ {r['precio_compra']:,.0f}".replace(",", "."),
                    "accion": "Corregir la tarifa del medicamento en facturación y revisar el precio de venta en farmacia.",
                    "valor": float(r["impacto"] or 0)})
    fuera = c[c["estado_cruce"] == "NO ESTÁ EN PORTAFOLIO"]
    if len(fuera):
        out.append({"nivel": "MEDIA", "categoria": "Medicamento fuera de portafolio", "sede": "", "mes": "",
                    "mensaje": f"{len(fuera)} CUM facturados en RIPS no están en el portafolio de farmacia "
                               f"($ {fuera['valor'].sum():,.0f}).".replace(",", "."),
                    "accion": "Verificar el CUM facturado (expediente/consecutivo) o incluirlo en el portafolio y el maestro.",
                    "valor": float(fuera["valor"].sum())})
    return out


__all__ = ["alertas", "cargar_cruce", "cruzar", "descripciones", "estado", "hoja", "no_facturados", "normalizar_cum",
           "resumen"]

"""Catálogos de CUPS para enriquecer el informe «RIPS por CUPS» del tablero.

Dos insumos que carga el usuario y que se guardan normalizados en
``<datos>/catalogos/`` (no hay que volver a cargarlos en cada sesión):

1. **Archivo complementario** (Excel .xlsx/.xlsb o CSV): una fila por código
   con columnas propias de la ESE. Supuestos de formato:

   * La columna del código se llama ``CODIGO_CUPS_CUMS`` (la del informe que
     exporta el tablero) o contiene «CUPS», «CUM» o «CODIGO» en el nombre.
   * Los nombres de columna se comparan sin tildes, mayúsculas ni espacios
     sobrantes (`` cantidad `` = ``CANTIDAD``).
   * Las columnas que ya calcula el informe (registros, cantidad, valor,
     facturas, valor_fev, participacion_pct, valor_unitario, posicion, sedes)
     **no se duplican**: el informe conserva sus cifras vivas.
   * Columnas de plantilla que se **calculan** con los datos cargados (las
     cifras de un archivo exportado quedan viejas al cambiar filtros o meses):
     «promedio mes» (o «pormedio mes»): valor ÷ meses del filtro; una columna
     por EPS (``nueva eps``, ``sanitas``, ``coosalud``, ``fomag``… según
     ``catalogo_cups.columnas_eps`` en configuracion.yaml) y ``otras`` (las
     demás EPS); ``evento``, ``capita`` y ``pgp`` por modalidad. La medida de
     esas columnas es el valor de servicios (``catalogo_cups.medida``).
   * Cualquier otra columna se une tal cual por código.
   * CSV: separador «;» o «,» (se detecta) y UTF-8 con o sin BOM o ANSI.

2. **Tarifario del Manual UVB** (p. ej. *Tarifa_calculada_Manuales_2026_UVB*,
   hoja única con ``MAPIISS``, ``DESCRIPCION`` y ``SOAT-2026-UVB``). Supuestos:

   * Código = primera columna cuyo nombre contiene «MAPIISS», «CUPS» o
     «CODIGO»; descripción = la que contiene «DESCRIP»; tarifa = la que
     contiene «UVB», «SOAT» o «TARIFA».
   * Excel guarda como número los códigos numéricos y pierde los ceros a la
     izquierda (``010101`` → ``10101``): los códigos sólo numéricos de menos
     de 6 dígitos se completan con ceros a 6.
   * Filas sin descripción se descartan; si un código se repite, gana la
     primera fila con descripción.

**Descripciones persistentes** (``descripciones_cups.csv``): para cada
código del acumulado sin descripción se busca, en orden, la descripción que
traen los propios RIPS (AM/AT), la del archivo complementario y la del
tarifario UVB. El resultado se guarda con su fuente y el informe sólo lee
ese archivo; se actualiza al cargar un catálogo y después de cada cargue.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import unicodedata
from pathlib import Path

import pandas as pd

from .config import carpeta_datos

log = logging.getLogger(__name__)

ARCHIVO_COMPLEMENTO = "complemento_cups.csv"
ARCHIVO_TARIFARIO = "tarifario_uvb.csv"
ARCHIVO_DESCRIPCIONES = "descripciones_cups.csv"
COL_CODIGO = "CODIGO_CUPS_CUMS"

# Columnas que el informe ya calcula: no se toman del archivo complementario
PROPIAS_INFORME = {"CODIGO CUPS CUMS", "DESCRIPCION", "REGISTROS", "CANTIDAD", "VALOR", "FACTURAS", "VALOR FEV",
                   "PARTICIPACION PCT", "VALOR UNITARIO", "POSICION", "SEDES"}
PROMEDIO_MES = {"PROMEDIO MES", "PORMEDIO MES", "PROMEDIO MENSUAL", "PROMEDIO"}
MODALIDADES = {"EVENTO": "EVENTO", "CAPITA": "CAPITA", "CÁPITA": "CAPITA", "PGP": "PGP"}

DEFAULTS = {
    "medida": "valor",  # valor | cantidad: medida de las columnas calculadas (promedio, EPS, modalidad)
    # nombre de columna (normalizado) -> textos que identifican la EPS en el acumulado
    "columnas_eps": {
        "NUEVA EPS": ["NUEVA EPS", "NUEVA EMPRESA PROMOTORA"],
        "SANITAS": ["SANITAS"],
        "COOSALUD": ["COOSALUD"],
        "FOMAG": ["FOMAG", "FIDUPREVISORA", "FIDUCIARIA LA PREVISORA", "MAGISTERIO"],
        "SALUD TOTAL": ["SALUD TOTAL"],
        "ASMET": ["ASMET"],
        "MUTUAL SER": ["MUTUAL SER"],
        "CAPRESOCA": ["CAPRESOCA"],
    },
    "columna_otras": "OTRAS",
}

_cache: dict = {}


def normalizar_nombre(texto) -> str:
    """'  Valor_Unitario ' -> 'VALOR UNITARIO' (sin tildes, mayúsculas, espacios simples)."""
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", " ", t).strip()


def normalizar_codigo(valor) -> str:
    """Código CUPS/CUM como texto: sin espacios, en mayúsculas y con los ceros que Excel quitó."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    t = str(valor).strip().upper()
    if re.fullmatch(r"\d+\.0", t):
        t = t[:-2]
    if t.isdigit() and len(t) < 6:
        t = t.zfill(6)
    return t


def carpeta_catalogos() -> Path:
    return carpeta_datos() / "catalogos"


def _leer_tabla(ruta: str | Path) -> pd.DataFrame:
    """Lee CSV, XLSX o XLSB como texto (todas las columnas)."""
    ruta = Path(ruta)
    ext = ruta.suffix.lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(ruta, dtype=object)
    if ext == ".xlsb":
        return pd.read_excel(ruta, dtype=object, engine="pyxlsb")
    crudo = ruta.read_bytes()
    for cod in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texto = crudo.decode(cod)
            break
        except UnicodeDecodeError:
            continue
    primera = texto.splitlines()[0] if texto else ""
    sep = ";" if primera.count(";") >= primera.count(",") else ","
    from io import StringIO

    return pd.read_csv(StringIO(texto), sep=sep, dtype=str, keep_default_na=False)


def _columna(df: pd.DataFrame, *claves: str) -> str | None:
    for c in df.columns:
        n = normalizar_nombre(c)
        if any(k in n for k in claves):
            return c
    return None


def _guardar(df: pd.DataFrame, nombre: str) -> Path:
    carpeta = carpeta_catalogos()
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / nombre
    tmp = ruta.with_suffix(".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8")
    tmp.replace(ruta)
    _cache.clear()
    return ruta


def _leer_guardado(nombre: str) -> pd.DataFrame | None:
    ruta = carpeta_catalogos() / nombre
    if not ruta.exists():
        return None
    clave = (str(ruta), ruta.stat().st_mtime_ns)
    if clave not in _cache:
        _cache[clave] = pd.read_csv(ruta, dtype=str, keep_default_na=False, encoding="utf-8")
    return _cache[clave]


# ---------------------------------------------------------------------------
# Carga de catálogos
# ---------------------------------------------------------------------------

def cargar_complemento(ruta: str | Path) -> dict:
    """Lee el archivo complementario, lo normaliza por código y lo guarda."""
    df = _leer_tabla(ruta)
    col = COL_CODIGO if COL_CODIGO in df.columns else _columna(df, "CODIGO CUPS", "CUPS", "CUM", "CODIGO")
    if col is None:
        raise ValueError("El archivo no tiene una columna de código (CODIGO_CUPS_CUMS, CUPS, CUM o CODIGO).")
    df.columns = [str(c).strip() for c in df.columns]
    col = str(col).strip()
    df = df.rename(columns={col: COL_CODIGO})
    df[COL_CODIGO] = df[COL_CODIGO].map(normalizar_codigo)
    df = df[df[COL_CODIGO] != ""]
    repetidos = int(df[COL_CODIGO].duplicated().sum())
    df = df.drop_duplicates(COL_CODIGO).fillna("")
    df = df.astype(str)
    _guardar(df, ARCHIVO_COMPLEMENTO)
    return {"codigos": len(df), "columnas": list(df.columns), "codigos_repetidos": repetidos,
            "archivo": Path(ruta).name, "descripciones": actualizar_descripciones()}


def cargar_tarifario(ruta: str | Path) -> dict:
    """Lee el tarifario UVB (código, descripción, tarifa) y lo guarda normalizado."""
    df = _leer_tabla(ruta)
    c_cod = _columna(df, "MAPIISS", "CUPS", "CODIGO")
    c_desc = _columna(df, "DESCRIP")
    c_tar = _columna(df, "UVB", "SOAT", "TARIFA", "VALOR")
    if c_cod is None or c_desc is None:
        raise ValueError("El tarifario debe tener una columna de código (MAPIISS/CUPS/CODIGO) y una de DESCRIPCION.")
    t = pd.DataFrame({
        "codigo": df[c_cod].map(normalizar_codigo),
        "descripcion": df[c_desc].fillna("").astype(str).str.strip(),
        "tarifa": pd.to_numeric(df[c_tar], errors="coerce") if c_tar is not None else float("nan"),
    })
    t = t[(t["codigo"] != "") & (t["descripcion"] != "")]
    repetidos = int(t["codigo"].duplicated().sum())
    t = t.drop_duplicates("codigo")
    t["columna_tarifa"] = str(c_tar or "")
    _guardar(t, ARCHIVO_TARIFARIO)
    return {"codigos": len(t), "codigos_repetidos": repetidos, "columna_tarifa": str(c_tar or ""),
            "archivo": Path(ruta).name, "descripciones": actualizar_descripciones()}


def estado() -> dict:
    """Qué catálogos hay guardados (para la pantalla del informe enriquecido)."""
    out = {}
    for clave, nombre in (("complemento", ARCHIVO_COMPLEMENTO), ("tarifario", ARCHIVO_TARIFARIO),
                          ("descripciones", ARCHIVO_DESCRIPCIONES)):
        ruta = carpeta_catalogos() / nombre
        d = _leer_guardado(nombre)
        out[clave] = None if d is None else {
            "registros": len(d), "actualizado": dt.datetime.fromtimestamp(ruta.stat().st_mtime).isoformat(timespec="seconds"),
            **({"columnas": [c for c in d.columns if c != COL_CODIGO]} if clave == "complemento" else {}),
            **({"por_fuente": d["fuente"].value_counts().to_dict()} if clave == "descripciones" and len(d) else {}),
        }
    return out


# ---------------------------------------------------------------------------
# Descripciones persistentes
# ---------------------------------------------------------------------------

def descripciones() -> dict[str, str]:
    """Código -> descripción guardada (lectura barata; no recalcula)."""
    d = _leer_guardado(ARCHIVO_DESCRIPCIONES)
    return {} if d is None else dict(zip(d["codigo"], d["descripcion"], strict=False))


def actualizar_descripciones(df: pd.DataFrame | None = None) -> dict:
    """Completa y guarda la descripción de los códigos del acumulado que no la
    traen. ``df``: filas homologadas (``tablero.cargar``); si es None se leen.
    Devuelve cuántos códigos se completaron por fuente y cuántos siguen sin descripción."""
    if df is None:
        from . import tablero

        df = tablero.cargar(tablero.carpeta_acumulado())[0]
    previas = _leer_guardado(ARCHIVO_DESCRIPCIONES)
    previas = previas if previas is not None else pd.DataFrame(columns=["codigo", "descripcion", "fuente", "actualizado"])
    if df.empty:
        return {"completadas": {}, "sin_descripcion": 0}
    cod = df["CODIGO_CUPS_CUMS"].map(normalizar_codigo)
    propia = (df.assign(_c=cod)[df["DESCRIPCION_SERVICIO"].astype(str).str.strip() != ""]
              .groupby("_c")["DESCRIPCION_SERVICIO"].first())
    codigos = sorted(set(cod) - {"", "(SIN CODIGO)", "(SIN CÓDIGO)"})
    faltan = [c for c in codigos if c not in propia.index]

    fuentes: list[tuple[str, dict]] = []
    comp = _leer_guardado(ARCHIVO_COMPLEMENTO)
    if comp is not None:
        c_desc = next((c for c in comp.columns if normalizar_nombre(c) in ("DESCRIPCION", "DESCRIPCION SERVICIO")), None)
        if c_desc:
            fuentes.append(("archivo complementario", dict(zip(comp[COL_CODIGO], comp[c_desc], strict=False))))
    tar = _leer_guardado(ARCHIVO_TARIFARIO)
    if tar is not None:
        fuentes.append(("tarifario UVB", dict(zip(tar["codigo"], tar["descripcion"], strict=False))))

    ya = dict(zip(previas["codigo"], zip(previas["descripcion"], previas["fuente"], strict=False), strict=False))
    ahora = dt.datetime.now().isoformat(timespec="seconds")
    filas, cuenta = [], {}
    for c in faltan:
        hallada = next(((f, m[c].strip()) for f, m in fuentes if str(m.get(c, "")).strip()), None)
        if hallada:
            fuente, texto = hallada
            if ya.get(c) != (texto, fuente):
                cuenta[fuente] = cuenta.get(fuente, 0) + 1
            filas.append({"codigo": c, "descripcion": texto, "fuente": fuente, "actualizado": ahora})
        elif c in ya:  # se conserva lo guardado aunque hoy no haya catálogo
            filas.append({"codigo": c, "descripcion": ya[c][0], "fuente": ya[c][1], "actualizado": ahora})
    # También se guardan las de códigos que hoy no están en el acumulado (otros meses/sedes ya limpiados)
    otros = previas[~previas["codigo"].isin(faltan)]
    total = pd.concat([otros, pd.DataFrame(filas, columns=previas.columns)], ignore_index=True)
    total = total.drop_duplicates("codigo", keep="last").sort_values("codigo")
    _guardar(total, ARCHIVO_DESCRIPCIONES)
    sin = [c for c in faltan if not any(str(m.get(c, "")).strip() for _, m in fuentes) and c not in ya]
    return {"completadas": cuenta, "sin_descripcion": len(sin), "guardadas": len(total)}


def completar(descripcion: pd.Series, codigos: pd.Series) -> pd.Series:
    """Rellena las descripciones vacías con las guardadas (no toca las que ya hay)."""
    guardadas = descripciones()
    if not guardadas:
        return descripcion
    vacia = descripcion.fillna("").astype(str).str.strip() == ""
    return descripcion.where(~vacia, codigos.map(normalizar_codigo).map(guardadas).fillna(""))


# ---------------------------------------------------------------------------
# Informe enriquecido
# ---------------------------------------------------------------------------

def _cfg(cfg: dict | None) -> dict:
    if cfg is None:
        from .config import cargar_configuracion

        cfg = cargar_configuracion()
    c = {**DEFAULTS, **{k: v for k, v in (cfg.get("catalogo_cups") or {}).items() if v is not None}}
    c["columnas_eps"] = {normalizar_nombre(k): [normalizar_nombre(x) for x in v]
                         for k, v in (c.get("columnas_eps") or {}).items()}
    return c


def _grupo_eps(eps: pd.Series, columnas_eps: dict[str, list[str]]) -> pd.Series:
    """EPS del acumulado -> columna de la plantilla (o '' si no está en ninguna)."""
    n = eps.map(normalizar_nombre)
    salida = pd.Series("", index=eps.index)
    for col, textos in columnas_eps.items():
        coincide = n.map(lambda x, t=textos: any(f" {y} " in f" {x} " for y in t if y))
        salida = salida.where((salida != "") | ~coincide, col)
    return salida


def enriquecer(g: pd.DataFrame, d: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """Agrega al informe por CUPS (``g``, ya con ``descripcion``) los campos nuevos.

    No cambia ni reordena las columnas existentes: completa ``descripcion``
    vacía con las guardadas y agrega al final las columnas del archivo
    complementario (calculadas o unidas por código), la tarifa del tarifario
    UVB y la fuente de la descripción. ``d`` son las filas filtradas."""
    if g.empty or COL_CODIGO not in g.columns:
        return g
    c = _cfg(cfg)
    g = g.copy()
    clave = g[COL_CODIGO].map(normalizar_codigo)
    g["descripcion"] = completar(g["descripcion"], clave)
    propias = set(d.loc[d["DESCRIPCION_SERVICIO"].astype(str).str.strip() != "", "CODIGO_CUPS_CUMS"].map(normalizar_codigo))
    desc = _leer_guardado(ARCHIVO_DESCRIPCIONES)
    fuentes = {} if desc is None else dict(zip(desc["codigo"], desc["fuente"], strict=False))
    fuente = clave.map(lambda x: "RIPS" if x in propias else fuentes.get(x, ""))

    medida = "CANTIDAD" if c["medida"] == "cantidad" else "VALOR_SERVICIO"
    dd = d.assign(_c=d["CODIGO_CUPS_CUMS"].map(normalizar_codigo))
    comp = _leer_guardado(ARCHIVO_COMPLEMENTO)
    if comp is not None:
        porcod = comp.set_index(COL_CODIGO)
        meses = max(1, d["MES"].nunique())
        # sólo las EPS que tienen columna en el archivo; el resto suma en «otras»
        presentes = {normalizar_nombre(x) for x in comp.columns}
        grupo_eps = _grupo_eps(dd["EPS"], {k: v for k, v in c["columnas_eps"].items() if k in presentes})
        otras = normalizar_nombre(c["columna_otras"])
        for col in comp.columns:
            n = normalizar_nombre(col)
            if col == COL_CODIGO or n in PROPIAS_INFORME or col in g.columns:
                continue
            if n in PROMEDIO_MES:
                serie = dd.groupby("_c")[medida].sum() / meses
            elif n in c["columnas_eps"]:
                serie = dd[grupo_eps == n].groupby("_c")[medida].sum()
            elif n == otras:
                serie = dd[grupo_eps == ""].groupby("_c")[medida].sum()
            elif n in MODALIDADES:
                serie = dd[dd["MODALIDAD"].map(normalizar_nombre) == MODALIDADES[n]].groupby("_c")[medida].sum()
            else:
                g[col] = clave.map(porcod[col]).fillna("")
                continue
            g[col] = clave.map(serie).fillna(0.0).round(2)
    tar = _leer_guardado(ARCHIVO_TARIFARIO)
    if tar is not None:
        nombre = (tar["columna_tarifa"].iloc[0] if len(tar) and tar["columna_tarifa"].iloc[0] else "tarifa") + " (tarifario)"
        g[nombre] = clave.map(dict(zip(tar["codigo"], pd.to_numeric(tar["tarifa"], errors="coerce"), strict=False)))
    g["fuente_descripcion"] = fuente
    return g


__all__ = ["actualizar_descripciones", "cargar_complemento", "cargar_tarifario", "completar", "descripciones", "enriquecer",
           "estado", "normalizar_codigo"]

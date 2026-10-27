"""Tablero de análisis de los RIPS cargados (por sede, mes, empresa, servicio y CUPS).

Fuente de datos
---------------
El tablero lee el **mismo acumulado homologado que se envía al aplicativo de
Auditoría ESE MyC** (``<datos>/acumulado_aplicativo/<AAAA-MM>.csv``, columnas
de ``aplicativo.COLUMNAS``). Así, lo que se analiza aquí es exactamente lo que
el aplicativo recibe en «Cargas › RIPS»: mismas sedes, EPS, contratos,
modalidad, códigos CUPS/CUM, cantidades y valores. Cada cargue reemplaza en
el acumulado las sedes que trae, de modo que el tablero siempre muestra la
última versión de cada sede-mes.

Además, cada cargue guarda en ``indicadores.csv`` (misma carpeta) un registro
por sede-mes con lo que la plantilla homologada no conserva: usuarios únicos
(US), registros por archivo (AC, AP, AU, AH, AN, AM, AT), remisiones, valor
neto del AF, facturas con diferencia y errores/advertencias de validación.
Con ellos se calculan la **frecuencia de uso** (atenciones / usuarios) y las
alertas de calidad. Si un acumulado es anterior a esta versión y no tiene
indicadores, el tablero funciona igual sin esas cifras.

Decisiones
----------
* ``CANTIDAD`` es la frecuencia de uso del servicio (AC y AP cuentan 1 por
  registro; AM y AT su cantidad), ``VALOR_SERVICIO`` el valor del servicio.
* El valor facturado (FEV) es un valor por factura: se suma una vez por
  factura (sede + mes + número) y sólo se informa en agrupaciones a nivel de
  factura (sede, mes, EPS, contrato, modalidad); por servicio o CUPS se usa el
  valor de los servicios.
* Las alertas usan umbrales configurables (sección ``tablero`` de
  configuracion.yaml) y cada una trae una acción recomendada.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd
import xlsxwriter

from . import catalogo_cups
from .aplicativo import COLUMNAS, _norm
from .config import carpeta_datos

log = logging.getLogger(__name__)

ARCHIVO_INDICADORES = "indicadores.csv"
TIPOS_ATENCION = ["AC", "AP", "AU", "AH", "AN", "AM", "AT"]
COLUMNAS_INDICADORES = (["MES", "SEDE", "remisiones", "facturas", "usuarios"] + [f"reg_{t}" for t in TIPOS_ATENCION]
                        + ["valor_neto_af", "valor_servicios_rips", "facturas_con_diferencia", "errores",
                           "advertencias", "actualizado"])

# Dimensiones del tablero: nombre en la API -> columna del acumulado
DIMENSIONES = {
    "sede": "SEDE", "mes": "MES", "eps": "EPS", "servicio": "TIPO_SERVICIO", "cups": "CODIGO_CUPS_CUMS",
    "contrato": "CONTRATO", "modalidad": "MODALIDAD", "validacion": "ESTADO",
}
ETIQUETAS = {
    "sede": "Sede (hospital)", "mes": "Mes", "eps": "Empresa (EPS)", "servicio": "Tipo de servicio",
    "cups": "CUPS / CUM", "contrato": "Contrato", "modalidad": "Modalidad", "validacion": "Validación",
}
# Columnas constantes dentro de una factura: en ellas el valor FEV se puede sumar.
NIVEL_FACTURA = {"SEDE", "MES", "EPS", "CONTRATO", "MODALIDAD", "ESTADO", "NIT_EPS", "CUCON"}
MEDIDAS = {"valor": "Valor servicios", "cantidad": "Cantidad (frecuencia de uso)", "facturas": "Facturas",
           "registros": "Registros", "valor_fev": "Valor facturado (FEV)"}

DEFAULTS = {
    "variacion_mes_pct": 30,  # variación mes a mes (valor o cantidad) que genera alerta
    "variacion_alta_pct": 60,  # ... y que la vuelve de nivel ALTA
    "concentracion_pct": 60,  # una EPS o un CUPS con más de este % del valor de una sede
    "factor_valor_unitario": 3,  # valor unitario de un CUPS > factor × mediana de las sedes
    "minimo_cantidad_cups": 5,  # usos mínimos de un CUPS para comparar su valor unitario
    "frecuencia_variacion_pct": 30,  # variación de atenciones por usuario de un mes a otro
    "maximo_alertas": 300,
}

_cache: dict = {"clave": None, "datos": None}


# ---------------------------------------------------------------------------
# Carga del acumulado
# ---------------------------------------------------------------------------

def carpeta_acumulado(cfg: dict | None = None) -> Path:
    app = (cfg or {}).get("aplicativo_auditoria") or {}
    return Path(app.get("carpeta_acumulado") or carpeta_datos() / "acumulado_aplicativo")


def _archivos_mes(carpeta: Path) -> list[Path]:
    return sorted(p for p in carpeta.glob("*.csv") if re.fullmatch(r"\d{4}-\d{2}\.csv", p.name))


def cargar(carpeta: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(filas homologadas de todos los meses, indicadores por sede-mes). Se
    guarda en memoria mientras los archivos no cambien."""
    carpeta = Path(carpeta)
    archivos = _archivos_mes(carpeta) if carpeta.exists() else []
    ind_ruta = carpeta / ARCHIVO_INDICADORES
    firma = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in archivos + (
        [ind_ruta] if ind_ruta.exists() else []))
    clave = (str(carpeta), firma)
    if _cache["clave"] == clave:
        return _cache["datos"]
    partes = [pd.read_csv(p, dtype=str, keep_default_na=False, encoding="utf-8") for p in archivos]
    df = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLUMNAS)
    df = df.reindex(columns=COLUMNAS, fill_value="")
    for c in ("CANTIDAD", "VALOR_SERVICIO", "VALOR_FACTURA_FEV"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    for c in ("EPS", "CONTRATO", "MODALIDAD", "SEDE", "TIPO_SERVICIO", "CODIGO_CUPS_CUMS"):
        df[c] = df[c].astype(str).str.strip()
    df["TIPO_SERVICIO"] = df["TIPO_SERVICIO"].replace("", "Factura sin servicios")
    df["CODIGO_CUPS_CUMS"] = df["CODIGO_CUPS_CUMS"].replace("", "(sin código)")
    df["MODALIDAD"] = df["MODALIDAD"].replace("", "SIN MODALIDAD")
    df["ESTADO"] = df["VALIDACION"].astype(str).str.split(" · ").str[0].replace("", "SIN VALIDACIÓN")
    df["_FAC"] = df["SEDE"] + "|" + df["MES"] + "|" + df["FACTURA_FEV"]
    ind = (pd.read_csv(ind_ruta, dtype={"MES": str, "SEDE": str}, keep_default_na=False, encoding="utf-8")
           if ind_ruta.exists() else pd.DataFrame(columns=COLUMNAS_INDICADORES))
    ind = ind.reindex(columns=COLUMNAS_INDICADORES)
    for c in COLUMNAS_INDICADORES[2:-1]:
        ind[c] = pd.to_numeric(ind[c], errors="coerce").fillna(0)
    _cache.update(clave=clave, datos=(df, ind))
    return df, ind


def actualizar_indicadores(resultado, carpeta: str | Path, hospital: str | None = None,
                           periodo: str | None = None) -> Path | None:
    """Guarda (reemplazando sede-mes) los indicadores de cada unidad del resultado."""
    resumen = resultado.resumen_unidades()
    if resumen.empty:
        return None
    if hospital:
        resumen = resumen[resumen["hospital"] == hospital]
    if periodo:
        resumen = resumen[resumen["periodo"] == periodo]
    resumen = resumen[resumen["periodo"].astype(str).str.fullmatch(r"\d{4}-\d{2}")]
    if resumen.empty:
        return None
    nuevo = pd.DataFrame({
        "MES": resumen["periodo"].astype(str),
        "SEDE": resumen["hospital"].map(lambda h: _norm(h) or "SIN SEDE"),
        "remisiones": resumen["remisiones"], "facturas": resumen["facturas"], "usuarios": resumen["usuarios_unicos"],
        **{f"reg_{t}": resumen.get(f"reg_{t}", 0) for t in TIPOS_ATENCION},
        "valor_neto_af": resumen["valor_neto_facturado_af"], "valor_servicios_rips": resumen["valor_servicios_rips"],
        "facturas_con_diferencia": resumen["facturas_con_diferencia"],
        "errores": resumen.get("inc_error", 0), "advertencias": resumen.get("inc_advertencia", 0),
        "actualizado": dt.datetime.now().isoformat(timespec="seconds"),
    }).fillna(0)
    # Varias unidades pueden llevar a la misma sede normalizada: se suman.
    nuevo = nuevo.groupby(["MES", "SEDE"], as_index=False).agg(
        {**{c: "sum" for c in COLUMNAS_INDICADORES[2:-1]}, "actualizado": "max"})
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / ARCHIVO_INDICADORES
    if ruta.exists():
        previo = pd.read_csv(ruta, dtype={"MES": str, "SEDE": str}, keep_default_na=False, encoding="utf-8")
        llaves = set(zip(nuevo["MES"], nuevo["SEDE"]))
        previo = previo[[(m, s) not in llaves for m, s in zip(previo["MES"], previo["SEDE"])]]
        nuevo = pd.concat([previo, nuevo], ignore_index=True)
    nuevo = nuevo.reindex(columns=COLUMNAS_INDICADORES).sort_values(["MES", "SEDE"])
    tmp = ruta.with_suffix(".tmp")
    nuevo.to_csv(tmp, index=False, encoding="utf-8")
    tmp.replace(ruta)
    return ruta


# ---------------------------------------------------------------------------
# Filtros y agregaciones
# ---------------------------------------------------------------------------

def _lista(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    return [x.strip() for x in v if x and str(x).strip()]


def filtrar(df: pd.DataFrame, filtros: dict | None) -> pd.DataFrame:
    f = filtros or {}
    m = pd.Series(True, index=df.index)
    if f.get("desde"):
        m &= df["MES"] >= f["desde"]
    if f.get("hasta"):
        m &= df["MES"] <= f["hasta"]
    for nombre in ("sede", "eps", "servicio", "contrato", "modalidad", "validacion"):
        valores = _lista(f.get(nombre))
        if valores:
            m &= df[DIMENSIONES[nombre]].isin(valores)
    texto = str(f.get("cups") or "").strip().upper()
    if texto:
        m &= (df["CODIGO_CUPS_CUMS"].str.upper().str.contains(texto, regex=False)
              | df["DESCRIPCION_SERVICIO"].str.upper().str.contains(texto, regex=False))
    return df[m]


def filtrar_indicadores(ind: pd.DataFrame, filtros: dict | None) -> pd.DataFrame:
    f = filtros or {}
    m = pd.Series(True, index=ind.index)
    if f.get("desde"):
        m &= ind["MES"] >= f["desde"]
    if f.get("hasta"):
        m &= ind["MES"] <= f["hasta"]
    if _lista(f.get("sede")):
        m &= ind["SEDE"].isin(_lista(f.get("sede")))
    return ind[m]


def agrupar(df: pd.DataFrame, dims: list[str], orden: str = "valor") -> pd.DataFrame:
    """Agregado por columnas ``dims``: registros, cantidad, valor, facturas,
    valor FEV (si aplica), participación y valor unitario, de mayor a menor."""
    cols = dims + ["registros", "cantidad", "valor", "facturas", "valor_fev", "participacion_pct",
                   "valor_unitario", "posicion"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    g = df.groupby(dims, sort=False).agg(registros=("VALOR_SERVICIO", "size"), cantidad=("CANTIDAD", "sum"),
                                         valor=("VALOR_SERVICIO", "sum"), facturas=("_FAC", "nunique"))
    if set(dims) <= NIVEL_FACTURA:
        fac = df.groupby("_FAC").agg(VALOR_FACTURA_FEV=("VALOR_FACTURA_FEV", "max"),
                                     **{d: (d, "first") for d in dims})
        g["valor_fev"] = fac.groupby(dims)["VALOR_FACTURA_FEV"].sum()
    else:
        g["valor_fev"] = np.nan
    g = g.reset_index()
    total = g["valor"].sum()
    g["participacion_pct"] = (g["valor"] / total * 100).round(2) if total else 0.0
    g["valor_unitario"] = (g["valor"] / g["cantidad"].where(g["cantidad"] != 0)).round(0)
    llave = orden if orden in g.columns else "valor"
    asc = llave in dims
    g = g.sort_values([llave] + ([] if asc else dims), ascending=[asc] + ([] if asc else [True] * len(dims)))
    g["posicion"] = range(1, len(g) + 1)
    return g[cols].reset_index(drop=True)


def _medida(df: pd.DataFrame, dims: list[str], medida: str) -> pd.Series:
    if medida == "cantidad":
        return df.groupby(dims)["CANTIDAD"].sum()
    if medida == "facturas":
        return df.groupby(dims)["_FAC"].nunique()
    if medida == "registros":
        return df.groupby(dims).size()
    if medida == "valor_fev":
        fac = df.groupby(["_FAC"] + [d for d in dims if d in NIVEL_FACTURA], as_index=False)["VALOR_FACTURA_FEV"].max()
        return fac.groupby(dims)["VALOR_FACTURA_FEV"].sum()
    return df.groupby(dims)["VALOR_SERVICIO"].sum()


def comparativo(df: pd.DataFrame, filas: str = "sede", columnas: str = "mes", medida: str = "valor",
                limite: int = 500) -> dict:
    """Tabla cruzada (``filas`` × ``columnas``) de una medida, de mayor a menor,
    con total, participación y, si las columnas son meses, la variación del
    último mes frente al anterior."""
    if filas not in DIMENSIONES or columnas not in DIMENSIONES or filas == columnas:
        raise ValueError("Elija dos dimensiones distintas: " + ", ".join(DIMENSIONES))
    if medida not in MEDIDAS:
        raise ValueError("Medida no válida: " + ", ".join(MEDIDAS))
    if medida == "valor_fev" and not {DIMENSIONES[filas], DIMENSIONES[columnas]} <= NIVEL_FACTURA:
        medida = "valor"  # el valor FEV no se reparte por servicio o CUPS
    f, c = DIMENSIONES[filas], DIMENSIONES[columnas]
    base = {"filas": filas, "columnas": columnas, "medida": medida, "etiqueta_medida": MEDIDAS[medida],
            "etiqueta_filas": ETIQUETAS[filas], "etiqueta_columnas": ETIQUETAS[columnas]}
    if df.empty:
        return {**base, "cols": [], "datos": [], "total_filas": 0, "totales": {}}
    s = _medida(df, [f, c], medida).rename("v").reset_index()
    tabla = s.pivot_table(index=f, columns=c, values="v", aggfunc="sum", fill_value=0)
    if c == "MES":
        tabla = tabla.reindex(sorted(tabla.columns), axis=1)
    else:
        tabla = tabla[tabla.sum().sort_values(ascending=False).index]
    tabla["TOTAL"] = _medida(df, [f], medida).reindex(tabla.index).fillna(0) if medida in (
        "facturas", "valor_fev") else tabla.sum(axis=1)
    tabla = tabla.sort_values("TOTAL", ascending=False)
    total = float(tabla["TOTAL"].sum())
    tabla["PARTICIPACION_PCT"] = (tabla["TOTAL"] / total * 100).round(2) if total else 0.0
    meses = [x for x in tabla.columns if x not in ("TOTAL", "PARTICIPACION_PCT")]
    if c == "MES" and len(meses) >= 2:
        ant, ult = tabla[meses[-2]], tabla[meses[-1]]
        tabla["VARIACION_PCT"] = ((ult - ant) / ant.where(ant != 0) * 100).round(1)
    cols = [str(x) for x in tabla.columns]
    tabla.columns = cols
    tabla.index.name = "fila"
    out = tabla.reset_index()
    totales = {k: float(tabla[k].sum()) for k in cols if k not in ("PARTICIPACION_PCT", "VARIACION_PCT")}
    if medida in ("facturas", "valor_fev"):
        totales["TOTAL"] = float(df["_FAC"].nunique() if medida == "facturas"
                                 else df.groupby("_FAC")["VALOR_FACTURA_FEV"].max().sum())
    return {**base, "cols": cols, "datos": _registros(out.head(limite)), "total_filas": int(len(out)),
            "totales": totales}


def _registros(df: pd.DataFrame) -> list[dict]:
    """Filas JSON-seguras (NaN -> None, numpy -> tipos de Python)."""
    d = df.astype(object).where(df.notna(), None)
    return [{k: (v.item() if hasattr(v, "item") else v) for k, v in fila.items()} for fila in d.to_dict("records")]


# ---------------------------------------------------------------------------
# Indicadores de uso, alertas y análisis
# ---------------------------------------------------------------------------

def frecuencias(df: pd.DataFrame, ind: pd.DataFrame) -> pd.DataFrame:
    """Por sede y mes: usos (cantidad), registros, valor y, con indicadores,
    usuarios, atenciones y atenciones por usuario (frecuencia de uso)."""
    if df.empty and ind.empty:
        return pd.DataFrame(columns=["SEDE", "MES", "cantidad", "registros", "valor", "usuarios", "atenciones",
                                     "frecuencia_uso", "valor_por_usuario"])
    g = df.groupby(["SEDE", "MES"]).agg(cantidad=("CANTIDAD", "sum"), registros=("CANTIDAD", "size"),
                                         valor=("VALOR_SERVICIO", "sum"))
    if not ind.empty:
        i = ind.set_index(["SEDE", "MES"])
        i = i.assign(atenciones=i[[f"reg_{t}" for t in TIPOS_ATENCION]].sum(axis=1))[["usuarios", "atenciones"]]
        g = g.join(i, how="outer")
    else:
        g["usuarios"], g["atenciones"] = np.nan, np.nan
    g = g.reset_index()
    for c in ("cantidad", "registros", "valor"):
        g[c] = g[c].fillna(0)
    u = g["usuarios"].where(g["usuarios"] > 0)
    g["frecuencia_uso"] = (g["atenciones"] / u).round(2)
    g["valor_por_usuario"] = (g["valor"] / u).round(0)
    return g.sort_values(["SEDE", "MES"]).reset_index(drop=True)


def _pesos(v: float) -> str:
    return "$ " + f"{v:,.0f}".replace(",", ".")


def _ent(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".")


def _dec(v: float, n: int = 1) -> str:
    return f"{v:.{n}f}".replace(".", ",")


def _pct(v: float) -> str:
    return f"{v:+.1f}%".replace(".", ",")


def _meses_rango(meses: list[str]) -> list[str]:
    if not meses:
        return []
    ini, fin = pd.Period(min(meses), "M"), pd.Period(max(meses), "M")
    return [str(p) for p in pd.period_range(ini, fin, freq="M")]


def alertas(df: pd.DataFrame, ind: pd.DataFrame, params: dict | None = None) -> list[dict]:
    """Hallazgos priorizados (CRÍTICA, ALTA, MEDIA, INFORMATIVA), cada uno con acción recomendada."""
    p = {**DEFAULTS, **(params or {})}
    out: list[dict] = []

    def agregar(nivel, categoria, mensaje, accion, sede="", mes="", valor=None):
        out.append({"nivel": nivel, "categoria": categoria, "sede": sede, "mes": mes, "mensaje": mensaje,
                    "accion": accion, "valor": None if valor is None else float(valor)})

    if df.empty:
        return out
    meses = _meses_rango(df["MES"].unique().tolist())
    sm = df.groupby(["SEDE", "MES"]).agg(valor=("VALOR_SERVICIO", "sum"), cantidad=("CANTIDAD", "sum"))

    # 1) Meses sin RIPS dentro del período de cada sede
    for sede, g in sm.groupby(level=0):
        tiene = set(g.index.get_level_values(1))
        faltan = [m for m in meses if m not in tiene and min(tiene) <= m <= max(meses)]
        for m in faltan:
            agregar("CRÍTICA", "Sin RIPS", f"{sede} no tiene RIPS cargados para {m}.",
                    "Solicitar a la sede los RIPS del mes y cargarlos antes de radicar o auditar.", sede, m)

    # 2) Variación mes a mes por sede (valor y cantidad)
    for sede, g in sm.groupby(level=0):
        g = g.droplevel(0)
        g = g.reindex([m for m in meses if m >= g.index.min()])
        for medida, nombre in (("valor", "valor de servicios"), ("cantidad", "cantidad de servicios")):
            serie = g[medida]
            for i in range(1, len(serie)):
                ant, act = serie.iloc[i - 1], serie.iloc[i]
                if pd.isna(ant) or pd.isna(act) or ant <= 0:
                    continue
                var = (act - ant) / ant * 100
                if abs(var) >= p["variacion_mes_pct"]:
                    nivel = "ALTA" if abs(var) >= p["variacion_alta_pct"] else "MEDIA"
                    texto = (f"{_pesos(ant)} → {_pesos(act)}" if medida == "valor"
                             else f"{_ent(ant)} → {_ent(act)}")
                    agregar(nivel, "Variación mensual",
                            f"{sede}: el {nombre} {'subió' if var > 0 else 'bajó'} {_pct(var)} en {serie.index[i]} "
                            f"frente a {serie.index[i - 1]} ({texto}).",
                            "Verificar con la sede si hubo cambio de contratación, cierre de servicios, "
                            "subregistro o RIPS incompletos." if var < 0 else
                            "Verificar soporte de la mayor producción (contratos nuevos, campañas, "
                            "duplicidad de registros) antes de radicar.", sede, serie.index[i], var)

    # 3) Facturas con diferencia AF vs. servicios y servicios sin factura en AF
    fac = df.groupby("_FAC").agg(SEDE=("SEDE", "first"), MES=("MES", "first"), ESTADO=("ESTADO", "first"),
                                 fev=("VALOR_FACTURA_FEV", "max"), valor=("VALOR_SERVICIO", "sum"))
    for (sede, mes), g in fac[fac["ESTADO"] == "DIFERENCIA"].groupby(["SEDE", "MES"]):
        dif = float((g["fev"] - g["valor"]).abs().sum())
        nivel = "ALTA" if len(g) >= 10 or dif >= 10_000_000 else "MEDIA"
        agregar(nivel, "Conciliación AF vs. servicios",
                f"{sede} {mes}: {len(g)} factura(s) cuyo valor neto del AF no coincide con sus servicios "
                f"(diferencia acumulada {_pesos(dif)}).",
                "Revisar con facturación si son facturas globales (cápita/PGP) o corregir valores en RIPS "
                "antes de radicar; el aplicativo de auditoría las marcará en el cruce.", sede, mes, dif)
    for (sede, mes), g in fac[fac["ESTADO"].str.startswith("FACTURA NO ESTÁ EN AF")].groupby(["SEDE", "MES"]):
        agregar("ALTA", "Servicios sin factura", f"{sede} {mes}: {len(g)} factura(s) con servicios que no están en el AF "
                f"({_pesos(g['valor'].sum())}).", "Completar el archivo AF o corregir el número de factura en los "
                "archivos de servicios.", sede, mes, g["valor"].sum())
    sin_srv = fac[df.assign(_s=df["TIPO_SERVICIO"] == "Factura sin servicios").groupby("_FAC")["_s"].all()]
    for (sede, mes), g in sin_srv.groupby(["SEDE", "MES"]):
        agregar("MEDIA", "Facturas sin servicios", f"{sede} {mes}: {len(g)} factura(s) del AF sin servicios en "
                f"AC/AP/AM/AT (FEV {_pesos(g['fev'].sum())}).", "Solicitar los archivos de servicios de esas "
                "facturas o confirmar que corresponden a servicios sin detalle (AU/AH/AN).", sede, mes, g["fev"].sum())

    # 4) Modalidad inferida y valores en cero
    inferida = df[df["VALIDACION"].str.contains("MODALIDAD INFERIDA", regex=False)]
    if len(inferida):
        for sede, g in inferida.groupby("SEDE"):
            agregar("INFORMATIVA", "Modalidad inferida", f"{sede}: {g['_FAC'].nunique()} factura(s) con modalidad "
                    "deducida (el nombre del contrato no dice EVENTO/CÁPITA/PGP).", "Cargar PARAM CONTRATO en el "
                    "aplicativo de auditoría o definir 'contratos' en configuracion.yaml.", sede)
    ceros = df[(df["VALOR_SERVICIO"] <= 0) & (df["TIPO_SERVICIO"] != "Factura sin servicios")]
    for sede, g in ceros.groupby("SEDE"):
        agregar("MEDIA", "Servicios en cero", f"{sede}: {len(g)} registro(s) de servicios con valor cero o negativo.",
                "Validar si son servicios incluidos en paquete/cápita o un error de digitación.", sede)

    # 5) Concentración por EPS en cada sede
    for sede, g in df.groupby("SEDE"):
        tot = g["VALOR_SERVICIO"].sum()
        if tot <= 0:
            continue
        eps = g.groupby("EPS")["VALOR_SERVICIO"].sum().sort_values(ascending=False)
        part = eps.iloc[0] / tot * 100
        if part >= p["concentracion_pct"] and len(eps) > 1:
            agregar("INFORMATIVA", "Concentración", f"{sede}: {eps.index[0]} concentra el {_dec(part)}% del valor de servicios.", "Riesgo de dependencia de un pagador: priorizar su "
                    "radicación oportuna y seguimiento de glosas.", sede, valor=part)

    # 6) Valor unitario atípico del mismo CUPS entre sedes
    srv = df[(df["CODIGO_CUPS_CUMS"] != "(sin código)") & (df["CANTIDAD"] > 0)]
    if not srv.empty and srv["SEDE"].nunique() > 1:
        cu = srv.groupby(["CODIGO_CUPS_CUMS", "SEDE"]).agg(valor=("VALOR_SERVICIO", "sum"), cantidad=("CANTIDAD", "sum"))
        cu = cu[cu["cantidad"] >= p["minimo_cantidad_cups"]]
        cu["unitario"] = cu["valor"] / cu["cantidad"]
        mediana = cu.groupby(level=0)["unitario"].transform("median")
        n = cu.groupby(level=0)["unitario"].transform("size")
        raros = cu[(n >= 3) & (mediana > 0) & (cu["unitario"] > mediana * p["factor_valor_unitario"])]
        for (cups, sede), r in raros.sort_values("valor", ascending=False).head(50).iterrows():
            med = mediana.loc[(cups, sede)]
            agregar("MEDIA", "Valor unitario atípico", f"{sede}: el código {cups} tiene valor unitario "
                    f"{_pesos(r['unitario'])}, {_dec(r['unitario'] / med)} veces la mediana de las sedes "
                    f"({_pesos(med)}).", "Revisar tarifa pactada y unidades facturadas del "
                    "código en esa sede.", sede, valor=r["unitario"])

    # 7) Calidad de la validación y frecuencia de uso (indicadores)
    if not ind.empty:
        for _, r in ind[ind["errores"] > 0].iterrows():
            agregar("ALTA" if r["errores"] >= 100 else "MEDIA", "Errores de estructura",
                    f"{r['SEDE']} {r['MES']}: {_ent(r['errores'])} error(es) de validación Res. 3374.",
                    "Abrir la hoja Incidencias del Excel del mes y corregir los RIPS en origen antes de radicar.",
                    r["SEDE"], r["MES"], r["errores"])
        fr = frecuencias(df, ind).dropna(subset=["frecuencia_uso"])
        for sede, g in fr.groupby("SEDE"):
            g = g.sort_values("MES")
            for i in range(1, len(g)):
                ant, act = g["frecuencia_uso"].iloc[i - 1], g["frecuencia_uso"].iloc[i]
                if ant > 0 and abs(act - ant) / ant * 100 >= p["frecuencia_variacion_pct"]:
                    agregar("MEDIA", "Frecuencia de uso", f"{sede} {g['MES'].iloc[i]}: atenciones por usuario "
                            f"pasaron de {_dec(ant, 2)} a {_dec(act, 2)}.",
                            "Analizar demanda inducida, reconsultas o cambios en la población asignada.",
                            sede, g["MES"].iloc[i], act)

    orden = {"CRÍTICA": 0, "ALTA": 1, "MEDIA": 2, "INFORMATIVA": 3}
    out.sort(key=lambda a: (orden[a["nivel"]], -abs(a["valor"] or 0)))
    return out[: p["maximo_alertas"]]


def analisis(df: pd.DataFrame, ind: pd.DataFrame, lista_alertas: list[dict]) -> dict:
    """Lectura automática de las cifras y recomendaciones para la gerencia."""
    hallazgos: list[str] = []
    recomendaciones: list[str] = []
    if df.empty:
        return {"hallazgos": ["No hay RIPS cargados para el filtro elegido."], "recomendaciones": []}
    tot = df["VALOR_SERVICIO"].sum()
    meses = sorted(df["MES"].unique())
    hallazgos.append(f"Se analizan {_ent(len(df))} registros homologados de {df['SEDE'].nunique()} sede(s), "
                     f"{df['EPS'].nunique()} empresa(s) y {len(meses)} mes(es) ({meses[0]} a {meses[-1]}), "
                     f"por {_pesos(tot)} en servicios y {_ent(df['_FAC'].nunique())} facturas.")
    pm = df.groupby("MES")["VALOR_SERVICIO"].sum()
    if len(pm) >= 2:
        var = (pm.iloc[-1] - pm.iloc[-2]) / pm.iloc[-2] * 100 if pm.iloc[-2] else 0
        hallazgos.append(f"El último mes ({pm.index[-1]}) {'creció' if var >= 0 else 'cayó'} {_pct(var)} frente a "
                         f"{pm.index[-2]}; el mes de mayor valor fue {pm.idxmax()} ({_pesos(pm.max())}) y el de menor "
                         f"{pm.idxmin()} ({_pesos(pm.min())}).")
    elif len(pm) == 1:
        hallazgos.append(f"Sólo hay un mes en el filtro ({pm.index[0]}): cargue más meses para ver tendencias.")
    for dim, nombre in (("SEDE", "la sede"), ("EPS", "la empresa"), ("TIPO_SERVICIO", "el tipo de servicio")):
        s = df.groupby(dim)["VALOR_SERVICIO"].sum().sort_values(ascending=False)
        if len(s) > 1 and tot:
            hallazgos.append(f"Mayor valor: {nombre} {s.index[0]} con {_pesos(s.iloc[0])} "
                             f"({_dec(s.iloc[0] / tot * 100)}% del total).")
    cups = df[df["CODIGO_CUPS_CUMS"] != "(sin código)"].groupby("CODIGO_CUPS_CUMS").agg(
        cantidad=("CANTIDAD", "sum"), valor=("VALOR_SERVICIO", "sum"))
    if len(cups):
        top_c, top_v = cups["cantidad"].idxmax(), cups["valor"].idxmax()
        hallazgos.append(f"El código más frecuente es {top_c} ({_ent(cups.loc[top_c, 'cantidad'])} usos) y el de mayor "
                         f"valor {top_v} ({_pesos(cups.loc[top_v, 'valor'])}).")
        top10 = cups["valor"].nlargest(10).sum() / cups["valor"].sum() * 100 if cups["valor"].sum() else 0
        hallazgos.append(f"Los 10 códigos de mayor valor concentran el {_dec(top10)}% "
                         "del valor de servicios.")
    fr = frecuencias(df, ind).dropna(subset=["frecuencia_uso"])
    if fr["SEDE"].nunique() > 1:
        f = fr.groupby("SEDE").apply(lambda g: g["atenciones"].sum() / g["usuarios"].sum() if g["usuarios"].sum() else 0,
                                     include_groups=False).sort_values(ascending=False)
        hallazgos.append(f"Frecuencia de uso (atenciones por usuario): mayor en {f.index[0]} "
                         f"({_dec(f.iloc[0], 2)}) y menor en {f.index[-1]} ({_dec(f.iloc[-1], 2)}).")
    dif = df[df["ESTADO"] == "DIFERENCIA"]["_FAC"].nunique()
    if dif:
        hallazgos.append(f"{_ent(dif)} factura(s) no concilian el valor del AF con sus servicios.")

    cuenta = pd.Series([a["categoria"] for a in lista_alertas]).value_counts()
    acciones = {
        "Sin RIPS": "Completar los RIPS faltantes por sede y mes (ver alertas CRÍTICAS) antes del cierre de facturación.",
        "Conciliación AF vs. servicios": "Conciliar con facturación las facturas con diferencia; si son globales "
                                          "(cápita/PGP) registrar la modalidad correcta en PARAM CONTRATO.",
        "Servicios sin factura": "Corregir números de factura o completar el AF para no perder valor en la radicación.",
        "Variación mensual": "Revisar con cada sede las variaciones mensuales fuertes: pueden indicar subregistro, "
                             "duplicidad o cambios de contratación.",
        "Errores de estructura": "Corregir en origen los errores de estructura (Res. 3374) para evitar devoluciones.",
        "Valor unitario atípico": "Auditar tarifas de los códigos con valor unitario atípico frente a las demás sedes.",
        "Frecuencia de uso": "Analizar con la coordinación médica los cambios en atenciones por usuario.",
        "Concentración": "Priorizar seguimiento de cartera y glosas con las EPS que concentran el valor.",
        "Modalidad inferida": "Cargar PARAM CONTRATO en el aplicativo de auditoría para homologar contratos y modalidad.",
        "Facturas sin servicios": "Solicitar el detalle de servicios de las facturas que sólo están en el AF.",
        "Servicios en cero": "Validar los servicios en cero (paquetes o errores) antes del cruce con Software 360.",
    }
    for cat in cuenta.index:
        if cat in acciones:
            recomendaciones.append(f"{acciones[cat]} ({cuenta[cat]} alerta(s)).")
    recomendaciones.append("Enviar los meses revisados al aplicativo de Auditoría de Facturación y revisar su "
                           "«validación del cruce» (RIPS ↔ Software 360).")
    return {"hallazgos": hallazgos, "recomendaciones": recomendaciones}


# ---------------------------------------------------------------------------
# Tablero completo
# ---------------------------------------------------------------------------

def opciones(df: pd.DataFrame) -> dict:
    return {
        "meses": sorted(df["MES"].unique().tolist()),
        **{n: sorted(df[DIMENSIONES[n]].unique().tolist()) for n in
           ("sede", "eps", "servicio", "contrato", "modalidad", "validacion")},
        "dimensiones": ETIQUETAS, "medidas": MEDIDAS,
    }


def tablero(df: pd.DataFrame, ind: pd.DataFrame, filtros: dict | None = None, params: dict | None = None) -> dict:
    d = filtrar(df, filtros)
    i = filtrar_indicadores(ind, filtros)
    pm = agrupar(d, ["MES"]).sort_values("MES")
    if len(pm) >= 2:
        pm["variacion_valor_pct"] = (pm["valor"].pct_change() * 100).round(1)
        pm["variacion_cantidad_pct"] = (pm["cantidad"].pct_change() * 100).round(1)
    fr = frecuencias(d, i)
    lista = alertas(d, i, params)
    usuarios = float(i["usuarios"].sum()) if len(i) else None
    atenciones = float(i[[f"reg_{t}" for t in TIPOS_ATENCION]].to_numpy().sum()) if len(i) else None
    fac = d.groupby("_FAC").agg(fev=("VALOR_FACTURA_FEV", "max"), est=("ESTADO", "first")) if len(d) else None
    kpis = {
        "valor_servicios": float(d["VALOR_SERVICIO"].sum()), "valor_fev": float(fac["fev"].sum()) if fac is not None else 0,
        "facturas": int(len(fac)) if fac is not None else 0, "registros": int(len(d)),
        "cantidad": float(d["CANTIDAD"].sum()), "sedes": int(d["SEDE"].nunique()), "eps": int(d["EPS"].nunique()),
        "cups": int(d["CODIGO_CUPS_CUMS"].nunique()), "meses": int(d["MES"].nunique()),
        "usuarios": usuarios, "atenciones": atenciones,
        "frecuencia_uso": round(atenciones / usuarios, 2) if usuarios else None,
        "facturas_diferencia": int((fac["est"] == "DIFERENCIA").sum()) if fac is not None else 0,
        "alertas_criticas": sum(a["nivel"] == "CRÍTICA" for a in lista),
        "alertas_altas": sum(a["nivel"] == "ALTA" for a in lista),
        "alertas": len(lista),
        "variacion_valor_pct": float(pm["variacion_valor_pct"].iloc[-1]) if len(pm) >= 2 and
        pd.notna(pm["variacion_valor_pct"].iloc[-1]) else None,
        "ultimo_mes": pm["MES"].iloc[-1] if len(pm) else None,
    }
    sede_mes = comparativo(d, "sede", "mes", "valor") if len(d) else None
    return {
        "kpis": kpis,
        "por_mes": _registros(pm),
        "por_sede": _registros(agrupar(d, ["SEDE"])),
        "por_eps": _registros(agrupar(d, ["EPS"])),
        "por_servicio": _registros(agrupar(d, ["TIPO_SERVICIO"])),
        "por_modalidad": _registros(agrupar(d, ["MODALIDAD"])),
        "por_contrato": _registros(agrupar(d, ["CONTRATO"]).head(30)),
        "por_validacion": _registros(agrupar(d, ["ESTADO"])),
        "cups_valor": _registros(_con_descripcion(d, agrupar(d, ["CODIGO_CUPS_CUMS"])).head(20)),
        "cups_cantidad": _registros(_con_descripcion(d, agrupar(d, ["CODIGO_CUPS_CUMS"], "cantidad")).head(20)),
        "sede_mes": sede_mes,
        "sede_mes_cantidad": comparativo(d, "sede", "mes", "cantidad") if len(d) else None,
        "servicio_mes": comparativo(d, "servicio", "mes", "cantidad") if len(d) else None,
        "frecuencias": _registros(fr),
        "alertas": lista,
        "analisis": analisis(d, i, lista),
        "con_indicadores": bool(len(i)),
    }


def _con_descripcion(d: pd.DataFrame, g: pd.DataFrame) -> pd.DataFrame:
    if g.empty:
        return g.assign(descripcion="")
    desc = (d[d["DESCRIPCION_SERVICIO"] != ""].groupby("CODIGO_CUPS_CUMS")["DESCRIPCION_SERVICIO"].first())
    sedes = d.groupby("CODIGO_CUPS_CUMS")["SEDE"].nunique().rename("sedes")
    g = g.join(sedes, on="CODIGO_CUPS_CUMS")
    g.insert(1, "descripcion", g["CODIGO_CUPS_CUMS"].map(desc).fillna(""))
    # Códigos sin descripción en los RIPS: la guardada desde el Excel o el tarifario UVB
    g["descripcion"] = catalogo_cups.completar(g["descripcion"], g["CODIGO_CUPS_CUMS"])
    return g


# Tablas completas (paginadas en la API y descargables)
TABLAS = {
    "consolidado": "Consolidado RIPS homologado (todas las filas)",
    "sedes": "Resumen por sede", "meses": "Resumen por mes", "eps": "RIPS por empresa (EPS)",
    "servicio": "RIPS por tipo de servicio", "cups": "RIPS por CUPS / CUM", "contrato": "RIPS por contrato",
    "modalidad": "RIPS por modalidad", "sede_mes": "Sede por mes", "eps_sede": "Empresa por sede",
    "cups_sede": "CUPS por sede", "cups_mes": "CUPS por mes", "frecuencias": "Frecuencia de uso por sede y mes",
    "indicadores": "Indicadores de cargue por sede y mes", "alertas": "Alertas",
    "cups_enriquecido": "RIPS por CUPS enriquecido (archivo complementario y tarifario UVB)",
}


def tabla(df: pd.DataFrame, ind: pd.DataFrame, nombre: str, filtros: dict | None = None,
          params: dict | None = None) -> pd.DataFrame:
    d = filtrar(df, filtros)
    if nombre == "consolidado":
        return d[COLUMNAS].sort_values(["MES", "SEDE", "EPS", "FACTURA_FEV"]).reset_index(drop=True)
    if nombre == "sedes":
        g = agrupar(d, ["SEDE"])
        fr = frecuencias(d, filtrar_indicadores(ind, filtros)).groupby("SEDE")[["usuarios", "atenciones"]].sum(min_count=1)
        g = g.join(fr, on="SEDE")
        g["frecuencia_uso"] = (g["atenciones"] / g["usuarios"].where(g["usuarios"] > 0)).round(2)
        g["eps"] = g["SEDE"].map(d.groupby("SEDE")["EPS"].nunique())
        g["meses"] = g["SEDE"].map(d.groupby("SEDE")["MES"].nunique())
        return g
    if nombre == "meses":
        g = agrupar(d, ["MES"]).sort_values("MES")
        g["variacion_valor_pct"] = (g["valor"].pct_change() * 100).round(1)
        return g.reset_index(drop=True)
    simples = {"eps": ["EPS"], "servicio": ["TIPO_SERVICIO"], "contrato": ["CONTRATO", "MODALIDAD"],
               "modalidad": ["MODALIDAD"], "sede_mes": ["SEDE", "MES"], "eps_sede": ["EPS", "SEDE"],
               "cups_sede": ["CODIGO_CUPS_CUMS", "SEDE"], "cups_mes": ["CODIGO_CUPS_CUMS", "MES"]}
    if nombre in ("cups", "cups_enriquecido"):
        # mismo informe de siempre + los campos del archivo complementario y del tarifario al final
        return catalogo_cups.enriquecer(_con_descripcion(d, agrupar(d, ["CODIGO_CUPS_CUMS"])), d)
    if nombre in simples:
        g = agrupar(d, simples[nombre])
        return _con_descripcion(d, g) if nombre.startswith("cups") else g
    if nombre == "frecuencias":
        return frecuencias(d, filtrar_indicadores(ind, filtros))
    if nombre == "indicadores":
        return filtrar_indicadores(ind, filtros).reset_index(drop=True)
    if nombre == "alertas":
        return pd.DataFrame(alertas(d, filtrar_indicadores(ind, filtros), params),
                            columns=["nivel", "categoria", "sede", "mes", "mensaje", "accion", "valor"])
    raise KeyError(nombre)


def buscar_y_ordenar(t: pd.DataFrame, buscar: str = "", orden: str | None = None, desc: bool = True) -> pd.DataFrame:
    if buscar:
        b = buscar.strip().upper()
        texto = t.select_dtypes(exclude="number").astype(str).agg(" ".join, axis=1).str.upper()
        t = t[texto.str.contains(b, regex=False)]
    if orden and orden in t.columns:
        t = t.sort_values(orden, ascending=not desc, kind="stable")
    return t


# ---------------------------------------------------------------------------
# Informe Excel para la gerencia
# ---------------------------------------------------------------------------

MAX_FILAS_HOJA = 1_000_000


def exportar_excel(df: pd.DataFrame, ind: pd.DataFrame, ruta: str | Path, filtros: dict | None = None,
                   params: dict | None = None, institucion: str = "ESE Moreno y Clavijo") -> Path:
    """Libro con el tablero: resumen y análisis, alertas, comparativos y el consolidado completo."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    t = tablero(df, ind, filtros, params)
    wb = xlsxwriter.Workbook(str(ruta), {"constant_memory": True, "strings_to_numbers": False,
                                         "strings_to_formulas": False, "strings_to_urls": False,
                                         "nan_inf_to_errors": True})
    f_tit = wb.add_format({"bold": True, "font_size": 16, "font_color": "#0E6B16"})
    f_sub = wb.add_format({"italic": True, "font_color": "#5F6B63"})
    f_enc = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#18A020", "border": 1,
                           "text_wrap": True, "valign": "vcenter"})
    f_sec = wb.add_format({"bold": True, "font_size": 12, "font_color": "#A01038"})
    f_num = wb.add_format({"num_format": "#,##0"})
    f_dec = wb.add_format({"num_format": "#,##0.00"})
    f_pes = wb.add_format({"num_format": "$ #,##0"})
    f_txt = wb.add_format({"text_wrap": True, "valign": "top"})
    niveles = {n: wb.add_format({"bold": True, "font_color": c, "bg_color": b}) for n, c, b in (
        ("CRÍTICA", "#9C0006", "#FFC7CE"), ("ALTA", "#9C5700", "#FFEB9C"), ("MEDIA", "#5F4B00", "#FFF6D5"),
        ("INFORMATIVA", "#0E6B16", "#E8F6E9"))}
    pesos = {"valor", "valor_fev", "valor_unitario", "VALOR_SERVICIO", "VALOR_FACTURA_FEV", "valor_por_usuario",
             "valor_neto_af", "valor_servicios_rips"}

    def hoja(nombre: str, tabla_df: pd.DataFrame, titulo: str) -> tuple:
        ws = wb.add_worksheet(nombre[:31])
        ws.write_string(0, 0, titulo, f_tit)
        ws.write_string(1, 0, f"{institucion} · generado {dt.datetime.now():%Y-%m-%d %H:%M} · {len(tabla_df):,} filas", f_sub)
        cols = list(tabla_df.columns)
        for j, c in enumerate(cols):
            ws.write_string(3, j, str(c), f_enc)
            ws.set_column(j, j, 34 if tabla_df[c].dtype == object else 16)
        for i, fila in enumerate(tabla_df.itertuples(index=False, name=None), start=4):
            for j, (c, v) in enumerate(zip(cols, fila)):
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    continue
                if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool):
                    fmt = f_pes if str(c) in pesos or str(c)[:4].isdigit() and "valor" in titulo.lower() else (
                        f_dec if "pct" in str(c).lower() or "frecuencia" in str(c) else f_num)
                    ws.write_number(i, j, float(v), fmt)
                elif c == "nivel" and v in niveles:
                    ws.write_string(i, j, str(v), niveles[v])
                else:
                    ws.write_string(i, j, str(v))
        ws.freeze_panes(4, 0)
        if cols:
            ws.autofilter(3, 0, max(4, 3 + len(tabla_df)), len(cols) - 1)
        return ws, len(tabla_df)

    # Resumen ejecutivo
    ws = wb.add_worksheet("Resumen")
    ws.set_column(0, 0, 44)
    ws.set_column(1, 1, 110)
    ws.write_string(0, 0, f"{institucion} · Tablero de análisis de RIPS", f_tit)
    filtro_txt = ", ".join(f"{k}={v}" for k, v in (filtros or {}).items() if v) or "sin filtros"
    ws.write_string(1, 0, f"Generado {dt.datetime.now():%Y-%m-%d %H:%M} · filtros: {filtro_txt}", f_sub)
    k = t["kpis"]
    filas_kpi = [("Valor de servicios", k["valor_servicios"], f_pes), ("Valor facturado (FEV)", k["valor_fev"], f_pes),
                 ("Facturas", k["facturas"], f_num), ("Registros homologados", k["registros"], f_num),
                 ("Cantidad de servicios (usos)", k["cantidad"], f_num), ("Sedes", k["sedes"], f_num),
                 ("Empresas (EPS)", k["eps"], f_num), ("Códigos CUPS/CUM", k["cups"], f_num),
                 ("Meses", k["meses"], f_num), ("Usuarios (suma sede-mes)", k["usuarios"], f_num),
                 ("Atenciones", k["atenciones"], f_num), ("Atenciones por usuario", k["frecuencia_uso"], f_dec),
                 ("Facturas con diferencia AF", k["facturas_diferencia"], f_num),
                 ("Alertas (críticas / altas / total)", f"{k['alertas_criticas']} / {k['alertas_altas']} / {k['alertas']}", None)]
    ws.write_string(3, 0, "Indicadores", f_sec)
    for i, (n, v, fmt) in enumerate(filas_kpi, start=4):
        ws.write_string(i, 0, n)
        if v is None:
            ws.write_string(i, 1, "sin indicadores de cargue")
        elif fmt is None:
            ws.write_string(i, 1, str(v))
        else:
            ws.write_number(i, 1, float(v), fmt)
    fila = 5 + len(filas_kpi)
    ws.write_string(fila, 0, "Análisis de los datos", f_sec)
    for h in t["analisis"]["hallazgos"]:
        fila += 1
        ws.write_string(fila, 0, "•")
        ws.write_string(fila, 1, h, f_txt)
    fila += 2
    ws.write_string(fila, 0, "Acciones recomendadas", f_sec)
    for n, r in enumerate(t["analisis"]["recomendaciones"], start=1):
        fila += 1
        ws.write_string(fila, 0, f"{n}.")
        ws.write_string(fila, 1, r, f_txt)

    hoja("Alertas", tabla(df, ind, "alertas", filtros, params), "Alertas priorizadas y acciones")
    wm, nm = hoja("Por mes", tabla(df, ind, "meses", filtros), "RIPS por mes")
    _, ns = hoja("Resumen sedes", tabla(df, ind, "sedes", filtros), "Resumen por sede (de mayor a menor valor)")
    for nombre, medida, titulo in (("Sede x mes valor", "valor", "Comparativo sede por mes · valor de servicios"),
                                   ("Sede x mes cantidad", "cantidad", "Comparativo sede por mes · cantidad"),
                                   ("EPS x mes valor", "valor", "Comparativo empresa por mes · valor"),
                                   ("Servicio x mes cantidad", "cantidad", "Frecuencia de uso por servicio y mes")):
        filas_dim = {"Sede": "sede", "EPS ": "eps", "Serv": "servicio"}[nombre[:4]]
        c = comparativo(filtrar(df, filtros), filas_dim, "mes", medida, limite=10**9)
        hoja(nombre, pd.DataFrame(c["datos"]).rename(columns={"fila": ETIQUETAS[filas_dim]}), titulo)
    hoja("Frecuencias", tabla(df, ind, "frecuencias", filtros), "Frecuencia de uso por sede y mes")
    hoja("RIPS por EPS", tabla(df, ind, "eps", filtros), "RIPS por empresa (de mayor a menor)")
    hoja("EPS x sede", tabla(df, ind, "eps_sede", filtros), "Empresa por sede")
    hoja("RIPS por servicio", tabla(df, ind, "servicio", filtros), "RIPS por tipo de servicio")
    hoja("RIPS por CUPS", tabla(df, ind, "cups", filtros), "RIPS por CUPS / CUM (de mayor a menor valor)")
    hoja("CUPS x sede", tabla(df, ind, "cups_sede", filtros), "CUPS por sede")
    hoja("RIPS por contrato", tabla(df, ind, "contrato", filtros), "RIPS por contrato y modalidad")
    hoja("Indicadores cargue", tabla(df, ind, "indicadores", filtros), "Indicadores de cargue por sede y mes")
    cons = tabla(df, ind, "consolidado", filtros)
    for n, ini in enumerate(range(0, max(len(cons), 1), MAX_FILAS_HOJA), start=1):
        hoja("CONSOLIDADO RIPS" + ("" if n == 1 else f" {n}"), cons.iloc[ini:ini + MAX_FILAS_HOJA],
             "Consolidado RIPS homologado (mismo formato del aplicativo de auditoría)")

    # Gráficos nativos de Excel en el resumen (una medida por gráfico)
    if nm:
        g = wb.add_chart({"type": "line"})
        g.add_series({"name": "Valor de servicios", "categories": ["Por mes", 4, 0, 3 + nm, 0],
                      "values": ["Por mes", 4, 3, 3 + nm, 3], "line": {"color": "#2a78d6", "width": 2},
                      "marker": {"type": "circle", "size": 6, "fill": {"color": "#2a78d6"}, "border": {"color": "#FFFFFF"}}})
        g.set_title({"name": "Valor de servicios por mes"})
        g.set_legend({"none": True})
        g.set_y_axis({"num_format": "$ #,##0", "major_gridlines": {"visible": True, "line": {"color": "#E5E5E5"}}})
        ws.insert_chart(fila + 3, 1, g, {"x_scale": 1.5, "y_scale": 1.1})
    if ns:
        g = wb.add_chart({"type": "bar"})
        g.add_series({"name": "Valor de servicios", "categories": ["Resumen sedes", 4, 0, 3 + min(ns, 15), 0],
                      "values": ["Resumen sedes", 4, 3, 3 + min(ns, 15), 3], "fill": {"color": "#2a78d6"}, "gap": 60})
        g.set_title({"name": "Valor por sede"})
        g.set_legend({"none": True})
        g.set_y_axis({"reverse": True})
        g.set_x_axis({"num_format": "$ #,##0", "major_gridlines": {"visible": True, "line": {"color": "#E5E5E5"}}})
        ws.insert_chart(fila + 21, 1, g, {"x_scale": 1.5, "y_scale": 1.2})
    wb.close()
    return ruta


__all__ = ["cargar", "actualizar_indicadores", "tablero", "tabla", "comparativo", "alertas", "analisis",
           "exportar_excel", "carpeta_acumulado", "opciones", "TABLAS", "DIMENSIONES", "MEDIDAS"]

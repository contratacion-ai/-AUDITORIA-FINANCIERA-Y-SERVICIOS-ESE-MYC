"""Separación y deduplicación de registros que llegan repetidos en varios formatos.

Problema: un mismo envío puede llegar como TXT, como JSON (Res. 2275 o consolidado) y como
XLSX consolidado dentro de la misma carga. Sumarlos duplicaría frecuencias y valores.

Regla (multiconjunto por máximo):

1. Para cada tipo de archivo se calcula una **llave de contenido** con los campos que
   identifican el hecho clínico/financiero (factura, paciente, código, fecha, cantidad, valor),
   en forma canónica (fechas dd/mm/aaaa, números con 2 decimales, textos sin espacios).
2. Se cuenta cuántas veces aparece cada llave **dentro de cada archivo físico de origen** (columna ``origen``: el TXT, el JSON o el XLSX
   cargado; no la ruta original que un consolidado declara).
3. Se conserva, por llave, el número de repeticiones del archivo que más tiene (máximo, no
   suma). Así, dos líneas idénticas legítimas dentro de un mismo archivo se mantienen (y quedan
   marcadas como posible duplicado), pero la misma información repetida en otro archivo/formato
   se elimina.
4. Ante empate se prefiere el formato con mayor prioridad (TXT > JSON_2275 > CONSOLIDADO_JSON >
   CONSOLIDADO_XLSX), por ser el más cercano al soporte original; luego la remisión más pequeña
   (la carpeta específica por EPS antes que una carpeta de "RIPS unificados") y luego el orden
   alfabético de la ruta (el mes más antiguo).
"""
from __future__ import annotations

import pandas as pd

PRIORIDAD_FORMATO = {"TXT": 0, "JSON_2275": 1, "CONSOLIDADO_JSON": 2, "CONSOLIDADO_XLSX": 3}

LLAVES = {
    "CT": ["cod_prestador", "codigo_archivo", "total_registros"],
    "AF": ["num_id_prestador", "num_factura", "valor_neto"],
    "US": ["tipo_id_usuario", "num_id_usuario", "periodo", "hospital"],
    "AD": ["num_factura", "cod_concepto", "cantidad", "valor_total"],
    "AC": ["num_factura", "tipo_id_usuario", "num_id_usuario", "fecha_consulta", "cod_consulta", "valor_consulta"],
    "AP": ["num_factura", "tipo_id_usuario", "num_id_usuario", "fecha_procedimiento", "cod_procedimiento", "valor"],
    "AU": ["num_factura", "tipo_id_usuario", "num_id_usuario", "fecha_ingreso", "hora_ingreso"],
    "AH": ["num_factura", "tipo_id_usuario", "num_id_usuario", "fecha_ingreso", "hora_ingreso"],
    "AN": ["num_factura", "num_id_madre", "fecha_nacimiento", "hora_nacimiento"],
    "AM": ["num_factura", "tipo_id_usuario", "num_id_usuario", "cod_medicamento", "num_unidades", "valor_total"],
    "AT": ["num_factura", "tipo_id_usuario", "num_id_usuario", "cod_servicio", "cantidad", "valor_total"],
}


def _canon(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, (int, float)):
        return f"{float(v):.2f}"
    s = str(v).strip().upper()
    try:
        return f"{float(s):.2f}" if s.replace(".", "", 1).replace("-", "", 1).isdigit() and "." in s else s
    except ValueError:
        return s


def llave(df: pd.DataFrame, tipo: str) -> pd.Series:
    cols = [c for c in LLAVES.get(tipo, []) if c in df.columns]
    if not cols:
        return pd.Series([""] * len(df), index=df.index)
    partes = [df[c].map(_canon) for c in cols]
    k = partes[0]
    for p in partes[1:]:
        k = k + "|" + p
    return k


def deduplicar(df: pd.DataFrame, tipo: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve (registros conservados, registros eliminados por repetirse en otra fuente)."""
    if df.empty:
        return df, df.iloc[0:0]
    d = df.copy()
    d["_llave"] = llave(d, tipo)
    d["_ord"] = d.groupby(["_llave", "origen"]).cumcount()
    d["_prio"] = d["formato"].map(PRIORIDAD_FORMATO).fillna(9)
    # tamaño de la remisión de origen: ante empate se prefiere la remisión más específica
    # (p. ej. la carpeta por EPS sobre una carpeta "RIPS UNIFICADOS" que la contiene).
    d["_tam"] = d.groupby("envio_id")["_llave"].transform("size") if "envio_id" in d else 0
    cont = d.groupby(["_llave", "origen"]).agg(n=("_ord", "size"), prio=("_prio", "min"),
                                               tam=("_tam", "min")).reset_index()
    cont = cont.sort_values(["_llave", "n", "prio", "tam", "origen"], ascending=[True, False, True, True, True])
    ganador = cont.drop_duplicates("_llave").set_index("_llave")["origen"]
    conservar = d["origen"].values == d["_llave"].map(ganador).values
    elim = d[~conservar].copy()
    elim["motivo"] = "REGISTRO_REPETIDO_ENTRE_FUENTES"
    elim["conservado_en"] = elim["_llave"].map(ganador)
    out = d[conservar].drop(columns=["_llave", "_ord", "_prio", "_tam"])
    elim = elim.drop(columns=["_ord", "_prio", "_tam"]).rename(columns={"_llave": "llave"})
    return out.reset_index(drop=True), elim.reset_index(drop=True)

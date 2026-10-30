"""Tabla única de servicios a partir de los archivos RIPS de un lote.

Cada fila es una línea de servicio con: CUPS/CUMS, descripción, cantidad (frecuencia),
valor unitario, valor total, fecha, mes, municipio/hospital, EPS, paciente seudonimizado y
ámbito. Los registros de episodio (AU, AH, AN) entran con ``es_episodio=True``: cuentan para
asistencias y pacientes, pero no para frecuencias ni valores (su valor se factura en AP/AT).
"""
from __future__ import annotations

import pandas as pd

from .anonimizacion import columna_paciente
from .config import contratos_modalidad, red_prestadores
from .ingesta.lote import Lote

AMBITO_TXT = {"1": "AMBULATORIO", "2": "HOSPITALARIO", "3": "URGENCIAS"}
AT_TIPO = {"1": "MATERIALES E INSUMOS", "2": "TRASLADOS", "3": "ESTANCIAS", "4": "HONORARIOS"}

COLUMNAS = ["tipo_archivo", "num_factura", "cod_prestador", "paciente", "fecha", "periodo", "hospital", "municipio",
            "envio_id", "carpeta_envio", "modalidad_declarada", "cod_entidad", "num_contrato", "codigo", "tipo_codigo",
            "descripcion", "cantidad", "valor_unitario", "valor_total", "ambito", "finalidad", "dx_principal",
            "tipo_servicio_at", "es_episodio", "formato", "archivo", "linea"]


def _f(df: pd.DataFrame, col: str):
    return df[col] if col in df.columns else pd.Series([None] * len(df), index=df.index, dtype=object)


def _bloque(df: pd.DataFrame, tipo: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=COLUMNAS)
    o = pd.DataFrame(index=df.index)
    o["tipo_archivo"] = tipo
    for c in ("num_factura", "cod_prestador", "periodo", "hospital", "municipio", "envio_id", "carpeta_envio",
              "modalidad_declarada", "formato", "archivo", "linea"):
        o[c] = _f(df, c)
    if tipo == "AN":
        o["paciente"] = columna_paciente(df, "tipo_id_madre", "num_id_madre")
    else:
        o["paciente"] = columna_paciente(df)
    o["es_episodio"] = tipo in ("AU", "AH", "AN")
    o["ambito"] = {"AC": "AMBULATORIO", "AU": "URGENCIAS", "AH": "HOSPITALARIO", "AN": "HOSPITALARIO"}.get(tipo)
    o["finalidad"] = _f(df, "finalidad")
    o["tipo_servicio_at"] = None
    num = lambda s: pd.to_numeric(s, errors="coerce")
    if tipo == "AC":
        o["fecha"], o["codigo"], o["tipo_codigo"] = df["fecha_consulta"], df["cod_consulta"], "CUPS"
        o["cantidad"] = 1.0
        o["valor_total"] = num(df["valor_consulta"])
        o["valor_unitario"] = o["valor_total"]
        o["dx_principal"] = df["dx_principal"]
        o["descripcion"] = None
    elif tipo == "AP":
        o["fecha"], o["codigo"], o["tipo_codigo"] = df["fecha_procedimiento"], df["cod_procedimiento"], "CUPS"
        o["cantidad"] = 1.0
        o["valor_total"] = num(df["valor"])
        o["valor_unitario"] = o["valor_total"]
        o["ambito"] = df["ambito"].astype(str).map(AMBITO_TXT)
        o["dx_principal"] = df["dx_principal"]
        o["descripcion"] = None
    elif tipo == "AM":
        o["fecha"] = _f(df, "fecha_servicio")
        o["codigo"], o["tipo_codigo"] = df["cod_medicamento"], "CUMS"
        o["cantidad"] = num(df["num_unidades"])
        o["valor_unitario"], o["valor_total"] = num(df["valor_unitario"]), num(df["valor_total"])
        o["descripcion"] = df["nombre_generico"]
        o["dx_principal"] = None
    elif tipo == "AT":
        o["fecha"] = _f(df, "fecha_servicio")
        o["codigo"] = df["cod_servicio"].where(df["cod_servicio"].notna(), df["nombre_servicio"])
        o["tipo_codigo"] = "OTRO"
        o["cantidad"] = num(df["cantidad"])
        o["valor_unitario"], o["valor_total"] = num(df["valor_unitario"]), num(df["valor_total"])
        o["descripcion"] = df["nombre_servicio"]
        o["tipo_servicio_at"] = df["tipo_servicio"].astype(str).map(AT_TIPO)
        o["dx_principal"] = None
    elif tipo == "AD":
        o["fecha"] = None
        o["codigo"], o["tipo_codigo"] = "AD" + df["cod_concepto"].astype(str), "CONCEPTO"
        o["cantidad"] = num(df["cantidad"])
        o["valor_unitario"], o["valor_total"] = num(df["valor_unitario"]), num(df["valor_total"])
        o["descripcion"], o["dx_principal"] = None, None
    else:  # AU / AH / AN: episodios sin valor propio
        fcol = {"AU": "fecha_ingreso", "AH": "fecha_ingreso", "AN": "fecha_nacimiento"}[tipo]
        o["fecha"] = df[fcol]
        o["codigo"], o["tipo_codigo"], o["descripcion"] = None, None, {"AU": "Urgencias con observación",
                                                                        "AH": "Hospitalización",
                                                                        "AN": "Recién nacido"}[tipo]
        o["cantidad"], o["valor_unitario"], o["valor_total"] = 0.0, 0.0, 0.0
        o["dx_principal"] = _f(df, "dx_salida" if tipo == "AU" else "dx_egreso" if tipo == "AH" else "dx_recien_nacido")
    return o


def construir(lote: Lote) -> pd.DataFrame:
    partes = [_bloque(lote.tablas[t], t) for t in ("AC", "AP", "AM", "AT", "AD", "AU", "AH", "AN")]
    s = pd.concat([p for p in partes if not p.empty], ignore_index=True) if any(not p.empty for p in partes) \
        else pd.DataFrame(columns=COLUMNAS)
    af = lote.tablas["AF"]
    if not af.empty and not s.empty:
        m = af[["envio_id", "num_factura", "cod_entidad", "num_contrato", "cod_entidad_envio"]].drop_duplicates(
            ["envio_id", "num_factura"])
        s = s.merge(m, on=["envio_id", "num_factura"], how="left")
        s["cod_entidad"] = s["cod_entidad"].where(s["cod_entidad"].notna(), s["cod_entidad_envio"])
        s = s.drop(columns=["cod_entidad_envio"])
    else:
        s["cod_entidad"], s["num_contrato"] = None, None
    mapa = contratos_modalidad()
    if mapa:   # el contrato de la factura define la modalidad aunque la carpeta no la diga
        por_contrato = s["num_contrato"].astype(str).str.strip().str.upper().map(mapa)
        s["modalidad_declarada"] = por_contrato.where(por_contrato.notna(), s["modalidad_declarada"])
    s["codigo"] = s["codigo"].map(lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip())
    s["fecha"] = s["fecha"].astype(object)
    # Sin fecha propia (AM/AT en TXT): se usa el periodo como mes de atención.
    s["mes"] = s["periodo"]
    eps = {e.upper() for e in red_prestadores().get("eps_objetivo", [])}
    s["eps_objetivo"] = s["cod_entidad"].astype(str).str.upper().isin(eps)
    for c in ("cantidad", "valor_unitario", "valor_total"):
        s[c] = pd.to_numeric(s[c], errors="coerce").fillna(0.0)
    return s[COLUMNAS + ["mes", "eps_objetivo"]]

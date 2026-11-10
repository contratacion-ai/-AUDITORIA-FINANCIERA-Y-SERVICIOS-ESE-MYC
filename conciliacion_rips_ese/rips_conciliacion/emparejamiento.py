"""Criterio de emparejamiento facturación ↔ RIPS (propuesta para validar con la ESE).

CRITERIO PROPUESTO: llave compuesta jerárquica, agregada.

  Nivel 1 – Factura:   ``factura_clave`` (número de factura normalizado:
                        mayúsculas y solo letras/dígitos; ``FE-0012`` = ``FE0012``).
  Nivel 2 – Código:    ``codigo_clave`` (CUPS / CUMS / código de servicio,
                        mayúsculas sin espacios; homologable con una tabla de
                        equivalencias).
  Comparación:         dentro de cada par (factura, código) se SUMAN las
                        cantidades y los valores de cada fuente y se comparan
                        los totales.

POR QUÉ NO EL NÚMERO DE LÍNEA:
  * Los RIPS 3374 no tienen número de línea de factura; en los JSON 2275 el
    ``consecutivo`` reinicia por usuario y por tipo de servicio.
  * Los sistemas de facturación parten o agrupan líneas (p. ej. el mismo
    medicamento dispensado dos veces en la misma factura), de modo que el
    orden y el número de líneas no son estables entre fuentes.

POR QUÉ AGREGAR (y no emparejar línea a línea):
  * La factura es el documento que se radica y glosa; el código es lo que se
    audita. Una diferencia en (factura, código) es exactamente lo que el
    auditor necesita conocer, sin depender del orden de las líneas.
  * Es determinístico: no hay emparejamientos "probables" ni estimaciones.

SUPUESTOS:
  * El número de factura de la facturación y el de RIPS (``Número de la
    factura`` en 3374, ``numFactura`` en 2275) identifican el mismo documento
    después de la normalización (opcionalmente ignorando ceros a la izquierda).
  * Ambos sistemas usan la misma codificación (CUPS/CUMS). Si no, se usa la
    tabla de equivalencias.
  * El valor comparado es el valor bruto del servicio: ``Valor de la
    consulta``, ``Valor del procedimiento``, ``Valor total de medicamento``,
    ``Valor total del material e insumo`` (3374) o ``vrServicio`` (2275).

CASOS NO RESUELTOS (se reportan, no se fuerzan):
  * Factura presente en una sola fuente  -> ``FACTURA_NO_REPORTADA_EN_RIPS`` /
    ``FACTURA_NO_ESTA_EN_FACTURACION``.
  * Código distinto para el mismo servicio (homologación, CUMS con/sin
    sufijo de presentación) -> aparece como ``CODIGO_NO_REPORTADO_EN_RIPS`` y
    ``CODIGO_NO_ESTA_EN_FACTURACION`` en la misma factura; se resuelve con la
    tabla de equivalencias. El reporte marca estos pares como
    ``posible_homologacion`` cuando la raíz del código coincide.
  * Mismo código con distinto paciente dentro de una factura colectiva: se
    concilia por total del código, no por paciente.
  * Servicios sin valor en RIPS (urgencias AU, hospitalización AH, recién
    nacidos AN) no generan líneas conciliables; se cuentan en el reporte general.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pandas as pd

from .decimales import CERO

LLAVE = ["factura_clave", "codigo_clave"]

COINCIDE = "COINCIDE"
DIF_VALOR = "DIFERENCIA_VALOR"
DIF_CANTIDAD = "DIFERENCIA_CANTIDAD"
DIF_AMBOS = "DIFERENCIA_VALOR_Y_CANTIDAD"
SOLO_FACT_COD = "CODIGO_NO_REPORTADO_EN_RIPS"
SOLO_FACT_FAC = "FACTURA_NO_REPORTADA_EN_RIPS"
SOLO_RIPS_COD = "CODIGO_NO_ESTA_EN_FACTURACION"
SOLO_RIPS_FAC = "FACTURA_NO_ESTA_EN_FACTURACION"

ESTADOS = [
    COINCIDE,
    DIF_VALOR,
    DIF_CANTIDAD,
    DIF_AMBOS,
    SOLO_FACT_COD,
    SOLO_FACT_FAC,
    SOLO_RIPS_COD,
    SOLO_RIPS_FAC,
]

ATRIBUTOS = [
    "factura",
    "codigo",
    "campo_codigo",
    "categoria",
    "tipo_codigo",
    "servicio",
    "descripcion",
    "sede",
    "hospital",
    "mes",
    "ambito",
    "archivo",
    "ubicacion",
]


def agregar_por_llave(lineas: pd.DataFrame) -> pd.DataFrame:
    if lineas.empty:
        return pd.DataFrame(
            columns=LLAVE + ATRIBUTOS + ["cantidad", "valor", "n_lineas"]
        )
    g = lineas.groupby(LLAVE, sort=False, dropna=False)
    agregado = g[ATRIBUTOS].first()
    agregado["cantidad"] = g["cantidad"].sum()
    agregado["valor"] = g["valor"].sum()
    agregado["n_lineas"] = g.size()
    return agregado.reset_index()


def _raiz(codigo: str) -> str:
    return re.split(r"[-_.]", codigo or "")[0]


def emparejar(
    fact: pd.DataFrame,
    rips: pd.DataFrame,
    facturas_rips: set[str],
    tolerancia_valor: Decimal = CERO,
    tolerancia_cantidad: Decimal = CERO,
) -> pd.DataFrame:
    """Devuelve el detalle de conciliación por (factura, código)."""
    a = agregar_por_llave(fact)
    b = agregar_por_llave(rips)
    detalle = a.merge(
        b, on=LLAVE, how="outer", suffixes=("_fact", "_rips"), indicator=True
    )
    for c in ("cantidad_fact", "valor_fact", "cantidad_rips", "valor_rips"):
        detalle[c] = [v if isinstance(v, Decimal) else CERO for v in detalle[c]]
    for c in ("n_lineas_fact", "n_lineas_rips"):
        detalle[c] = detalle[c].fillna(0).astype(int)

    facturas_fact = set(fact["factura_clave"]) if not fact.empty else set()
    todas_rips = set(facturas_rips) | (
        set(rips["factura_clave"]) if not rips.empty else set()
    )

    detalle["dif_cantidad"] = [
        x - y for x, y in zip(detalle["cantidad_fact"], detalle["cantidad_rips"])
    ]
    detalle["dif_valor"] = [
        x - y for x, y in zip(detalle["valor_fact"], detalle["valor_rips"])
    ]

    estados = []
    for presencia, fclave, dv, dc in zip(
        detalle["_merge"],
        detalle["factura_clave"],
        detalle["dif_valor"],
        detalle["dif_cantidad"],
    ):
        if presencia == "left_only":
            estados.append(SOLO_FACT_COD if fclave in todas_rips else SOLO_FACT_FAC)
        elif presencia == "right_only":
            estados.append(SOLO_RIPS_COD if fclave in facturas_fact else SOLO_RIPS_FAC)
        else:
            hay_v = abs(dv) > tolerancia_valor
            hay_c = abs(dc) > tolerancia_cantidad
            estados.append(
                DIF_AMBOS
                if hay_v and hay_c
                else DIF_VALOR if hay_v else DIF_CANTIDAD if hay_c else COINCIDE
            )
    detalle["estado"] = estados

    # Atributos consolidados (prefiere facturación; RIPS si no existe)
    for attr in (
        "factura",
        "codigo",
        "categoria",
        "tipo_codigo",
        "servicio",
        "descripcion",
        "sede",
        "hospital",
        "mes",
    ):
        detalle[attr] = detalle[f"{attr}_fact"].where(
            detalle[f"{attr}_fact"].notna(), detalle[f"{attr}_rips"]
        )
    detalle["categoria"] = detalle["categoria_rips"].where(
        detalle["categoria_rips"].notna(), detalle["categoria"]
    )
    ambos = detalle["_merge"] == "both"
    detalle["inconsistencia_sede_mes"] = ambos & (
        (detalle["sede_fact"] != detalle["sede_rips"])
        | (detalle["mes_fact"] != detalle["mes_rips"])
    )

    # Posible homologación: en la misma factura hay un código solo en facturación y otro
    # solo en RIPS con la misma raíz (p. ej. CUMS 19912345-01 vs 19912345-1).
    detalle["posible_homologacion"] = ""
    solo_f = detalle[detalle["estado"] == SOLO_FACT_COD]
    solo_r = detalle[detalle["estado"] == SOLO_RIPS_COD]
    if not solo_f.empty and not solo_r.empty:
        raices_r = {}
        for idx, f, c in zip(
            solo_r.index, solo_r["factura_clave"], solo_r["codigo_clave"]
        ):
            raices_r.setdefault((f, _raiz(c)), []).append((idx, c))
        for idx, f, c in zip(
            solo_f.index, solo_f["factura_clave"], solo_f["codigo_clave"]
        ):
            for idx_r, c_r in raices_r.get((f, _raiz(c)), []):
                detalle.at[idx, "posible_homologacion"] = c_r
                detalle.at[idx_r, "posible_homologacion"] = c

    columnas = [
        "estado",
        "factura",
        "factura_clave",
        "mes",
        "sede",
        "hospital",
        "categoria",
        "servicio",
        "tipo_codigo",
        "codigo",
        "codigo_clave",
        "descripcion",
        "cantidad_fact",
        "cantidad_rips",
        "dif_cantidad",
        "valor_fact",
        "valor_rips",
        "dif_valor",
        "n_lineas_fact",
        "n_lineas_rips",
        "campo_codigo_fact",
        "campo_codigo_rips",
        "codigo_fact",
        "codigo_rips",
        "sede_fact",
        "sede_rips",
        "hospital_fact",
        "hospital_rips",
        "mes_fact",
        "mes_rips",
        "ambito_fact",
        "ambito_rips",
        "archivo_fact",
        "ubicacion_fact",
        "archivo_rips",
        "ubicacion_rips",
        "inconsistencia_sede_mes",
        "posible_homologacion",
    ]
    orden = {e: i for i, e in enumerate(ESTADOS)}
    detalle["_orden"] = detalle["estado"].map(orden)
    detalle = detalle.sort_values(
        ["_orden", "factura_clave", "codigo_clave"], kind="stable"
    )
    return detalle[columnas].reset_index(drop=True)

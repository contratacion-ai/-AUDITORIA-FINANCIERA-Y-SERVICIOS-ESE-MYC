"""Orquestación de la conciliación y cálculo de los análisis."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pandas as pd

from .decimales import CERO
from .emparejamiento import COINCIDE, ESTADOS, emparejar
from .esquemas import CATEGORIAS_FARMACIA
from .facturacion import leer_catalogo_sedes, leer_equivalencias, leer_facturacion
from .lectores import leer_rips
from .modelos import ADVERTENCIA, ERROR, ResultadoCarga
from .modelos import Incidencia
from .normalizacion import (
    asegurar_decimales,
    clasificar_facturacion,
    completar_hospital_desde_facturacion,
    lineas_facturacion,
    lineas_rips,
)


@dataclass
class ConfiguracionConciliacion:
    separador_decimal: str = "auto"  # separador decimal de la facturación
    mapeo_facturacion: dict = field(
        default_factory=dict
    )  # campo canónico -> columna(s)
    ignorar_ceros_factura: bool = False  # FE000123 == FE123
    tolerancia_valor: Decimal = CERO  # por defecto exacto (sin redondeo)
    tolerancia_cantidad: Decimal = CERO
    top_n: int = 20  # tamaño de los rankings de consumo


def _nombre_entrada(entrada) -> str:
    if isinstance(entrada, tuple):
        return str(entrada[0])
    return Path(str(entrada)).name


def _entrada(e) -> tuple:
    """Normaliza una entrada: ruta | (nombre, bytes) | {"entrada":..., "sede":..., ...}."""
    if isinstance(e, dict):
        return (
            e["entrada"],
            e.get("sede") or None,
            e.get("hospital") or None,
            e.get("ambito") or None,
        )
    return e, None, None, None


@dataclass
class ResultadoConciliacion:
    configuracion: ConfiguracionConciliacion
    carga_rips: ResultadoCarga
    carga_facturacion: ResultadoCarga
    lineas_rips: pd.DataFrame
    lineas_facturacion: pd.DataFrame
    detalle: pd.DataFrame
    facturas: pd.DataFrame
    cruce_codigos: pd.DataFrame
    cruce_codigos_sede: pd.DataFrame
    otros_codigos: pd.DataFrame
    comparativos: dict[str, pd.DataFrame]
    consumo: dict[str, pd.DataFrame]
    incidencias: pd.DataFrame
    archivos: pd.DataFrame
    resumen: list[tuple[str, str, object]]

    @property
    def diferencias(self) -> pd.DataFrame:
        return self.detalle[self.detalle["estado"] != COINCIDE]


def conciliar(
    rips,
    facturacion,
    configuracion: ConfiguracionConciliacion | None = None,
    catalogo_sedes=None,
    equivalencias=None,
    avance=None,
) -> ResultadoConciliacion:
    """Ejecuta la conciliación completa.

    ``rips`` y ``facturacion``: listas de rutas, tuplas ``(nombre, bytes)`` o
    dicts ``{"entrada": ..., "sede": ..., "hospital": ..., "ambito": ...}``.
    ``avance(mensaje, fraccion)``: función opcional para informar el progreso (0 a 1).
    """
    avisar = avance or (lambda mensaje, fraccion: None)
    from . import progreso

    token = progreso.establecer(avisar)
    try:
        return _conciliar(rips, facturacion, configuracion, catalogo_sedes, equivalencias, avisar)
    finally:
        progreso.restablecer(token)


def _conciliar(rips, facturacion, configuracion, catalogo_sedes, equivalencias, avisar) -> ResultadoConciliacion:
    cfg = configuracion or ConfiguracionConciliacion()
    rips = rips if isinstance(rips, list) else [rips]
    facturacion = facturacion if isinstance(facturacion, list) else [facturacion]

    carga_rips = ResultadoCarga(archivo="RIPS")
    total_archivos = max(len(rips) + len(facturacion), 1)
    for i, e in enumerate(rips):
        entrada, sede, hospital, _ = _entrada(e)
        avisar(f"Leyendo RIPS: {_nombre_entrada(entrada)}", 0.05 + 0.45 * i / total_archivos)
        carga_rips.fusionar(leer_rips(entrada, sede=sede, hospital=hospital))
    versiones = sorted(
        {a["version"] for a in carga_rips.archivos if a.get("tipo") != "IGNORADO"}
    )
    carga_rips.version = " + ".join(versiones) if versiones else "SIN ARCHIVOS"

    import gc

    incidencias_extra = []
    catalogo = {}
    if catalogo_sedes is not None:
        catalogo, inc = leer_catalogo_sedes(catalogo_sedes)
        incidencias_extra.extend(inc)
    tabla_equivalencias = leer_equivalencias(equivalencias) if equivalencias is not None else {}

    # RIPS: a líneas y se liberan los registros crudos ANTES de leer la facturación (menos memoria)
    avisar(f"Preparando líneas de RIPS: {carga_rips.total_registros:,} registros".replace(",", "."), 0.3)
    l_rips, facturas_rips, otros, inc = lineas_rips(carga_rips, catalogo, cfg.ignorar_ceros_factura)
    incidencias_extra.extend(inc)
    carga_rips.liberar()
    gc.collect()

    carga_fact = ResultadoCarga(archivo="FACTURACION", version="Facturación")
    for i, e in enumerate(facturacion):
        entrada, sede, hospital, ambito = _entrada(e)
        avisar(f"Leyendo facturación: {_nombre_entrada(entrada)}",
               0.05 + 0.45 * (len(rips) + i) / total_archivos)
        carga_fact.fusionar(leer_facturacion(entrada, cfg.mapeo_facturacion, cfg.separador_decimal,
                                             sede=sede, hospital=hospital, ambito=ambito))

    avisar(f"Preparando líneas de facturación: {carga_fact.conteo('facturacion'):,} filas".replace(",", "."), 0.5)
    l_fact = lineas_facturacion(carga_fact, catalogo, tabla_equivalencias, cfg.ignorar_ceros_factura)
    carga_fact.liberar()
    gc.collect()
    l_fact = clasificar_facturacion(l_fact, l_rips)
    for sede in completar_hospital_desde_facturacion(l_rips, facturas_rips, l_fact):
        incidencias_extra.append(
            Incidencia(
                "RIPS",
                ADVERTENCIA,
                f"Hospital de la sede '{sede}' tomado de la "
                "facturación (RIPS no lo trae y no hay catálogo de sedes)",
            )
        )
    asegurar_decimales(l_rips)
    asegurar_decimales(l_fact)

    claves_facturas_rips = (
        set(facturas_rips["factura_clave"]) if not facturas_rips.empty else set()
    )
    avisar("Emparejando facturación con RIPS por factura y código", 0.6)
    detalle = emparejar(
        l_fact,
        l_rips,
        claves_facturas_rips,
        cfg.tolerancia_valor,
        cfg.tolerancia_cantidad,
    )

    incidencias = pd.DataFrame(
        [
            i.as_dict()
            for i in carga_rips.incidencias + carga_fact.incidencias + incidencias_extra
        ],
        columns=["archivo", "severidad", "mensaje", "linea", "campo", "valor"],
    )
    archivos = pd.DataFrame(carga_rips.archivos + carga_fact.archivos)

    avisar("Calculando comparativos, cruce de códigos y consumo", 0.7)
    facturas = _comparar_facturas(l_fact, l_rips, facturas_rips)
    cruce, cruce_sede = _cruce_codigos(l_fact, l_rips)
    comparativos = {
        "Sede-Hospital-Mes": _comparar(l_fact, l_rips, ["sede", "hospital", "mes"]),
        "Por sede": _comparar(l_fact, l_rips, ["sede"]),
        "Por hospital": _comparar(l_fact, l_rips, ["hospital"]),
        "Por mes": _comparar(l_fact, l_rips, ["mes"]),
        "Por servicio": _comparar(l_fact, l_rips, ["categoria"]),
        "Hospital-Servicio": _comparar(l_fact, l_rips, ["hospital", "categoria"]),
    }
    consumo = _consumo(l_fact, l_rips, cfg.top_n)
    otros_codigos = _resumen_otros_codigos(otros)

    resultado = ResultadoConciliacion(
        configuracion=cfg,
        carga_rips=carga_rips,
        carga_facturacion=carga_fact,
        lineas_rips=l_rips,
        lineas_facturacion=l_fact,
        detalle=detalle,
        facturas=facturas,
        cruce_codigos=cruce,
        cruce_codigos_sede=cruce_sede,
        otros_codigos=otros_codigos,
        comparativos=comparativos,
        consumo=consumo,
        incidencias=incidencias,
        archivos=archivos,
        resumen=[],
    )
    resultado.resumen = _resumen_general(resultado)
    return resultado


# ---------------------------------------------------------------------------
# Análisis
# ---------------------------------------------------------------------------


def _sumas(lineas: pd.DataFrame, por: list[str], sufijo: str) -> pd.DataFrame:
    if lineas.empty:
        return pd.DataFrame(
            columns=por
            + [
                f"cantidad_{sufijo}",
                f"valor_{sufijo}",
                f"facturas_{sufijo}",
                f"lineas_{sufijo}",
            ]
        )
    g = lineas.groupby(por, dropna=False, sort=True)
    return pd.DataFrame(
        {
            f"cantidad_{sufijo}": g["cantidad"].sum(),
            f"valor_{sufijo}": g["valor"].sum(),
            f"facturas_{sufijo}": g["factura_clave"].nunique(),
            f"lineas_{sufijo}": g.size(),
        }
    ).reset_index()


def _completar(df: pd.DataFrame) -> pd.DataFrame:
    for c in df.columns:
        if c.startswith(("cantidad_", "valor_")):
            df[c] = [v if isinstance(v, Decimal) else CERO for v in df[c]]
        elif c.startswith(("facturas_", "lineas_", "codigos_")):
            df[c] = df[c].fillna(0).astype(int)
    return df


def _comparar(fact: pd.DataFrame, rips: pd.DataFrame, por: list[str]) -> pd.DataFrame:
    """Facturado vs reportado en RIPS agrupando cada fuente por sus propios atributos."""
    df = _sumas(fact, por, "fact").merge(_sumas(rips, por, "rips"), on=por, how="outer")
    df = _completar(df)
    df["dif_cantidad"] = [
        a - b for a, b in zip(df["cantidad_fact"], df["cantidad_rips"])
    ]
    df["dif_valor"] = [a - b for a, b in zip(df["valor_fact"], df["valor_rips"])]
    df["pct_dif_valor"] = [
        (d / a * 100) if a else None for d, a in zip(df["dif_valor"], df["valor_fact"])
    ]
    df["estado"] = [
        "COINCIDE" if (dv == 0 and dc == 0) else "DIFERENCIA"
        for dv, dc in zip(df["dif_valor"], df["dif_cantidad"])
    ]
    return df.sort_values(por).reset_index(drop=True)


def _comparar_facturas(
    fact: pd.DataFrame, rips: pd.DataFrame, facturas_rips: pd.DataFrame
) -> pd.DataFrame:
    por = ["factura_clave"]
    a = _sumas(fact, por, "fact")
    if not fact.empty:
        attrs = fact.groupby("factura_clave").agg(
            factura_fact=("factura", "first"),
            sede_fact=("sede", "first"),
            hospital_fact=("hospital", "first"),
            mes_fact=("mes", "first"),
        )
        a = a.merge(attrs.reset_index(), on="factura_clave", how="left")
    b = _sumas(rips, por, "rips")
    if not rips.empty:
        attrs = rips.groupby("factura_clave").agg(
            factura_rips=("factura", "first"),
            sede_rips_det=("sede", "first"),
            hospital_rips_det=("hospital", "first"),
            mes_rips=("mes", "first"),
        )
        b = b.merge(attrs.reset_index(), on="factura_clave", how="left")
    df = a.merge(b, on="factura_clave", how="outer")
    if not facturas_rips.empty:
        df = df.merge(
            facturas_rips.drop(columns=["factura"], errors="ignore"),
            on="factura_clave",
            how="outer",
        )
    df = _completar(df)
    for c in ("AF_valor_neto", "AF_copago", "AF_comision", "AF_descuentos"):
        if c in df.columns:
            df[c] = [v if isinstance(v, Decimal) else None for v in df[c]]
    for c in (
        "factura_fact",
        "factura_rips",
        "sede_fact",
        "hospital_fact",
        "mes_fact",
        "mes_rips",
        "sede_rips",
        "hospital_rips",
        "sede_rips_det",
        "hospital_rips_det",
        "AF_valor_neto",
        "AF_copago",
        "AF_comision",
        "AF_descuentos",
        "archivo_rips",
    ):
        if c not in df.columns:
            df[c] = None
    df["factura"] = df["factura_fact"].where(
        df["factura_fact"].notna(), df["factura_rips"]
    )
    df["sede"] = df["sede_fact"].where(
        df["sede_fact"].notna(),
        df["sede_rips_det"].where(df["sede_rips_det"].notna(), df["sede_rips"]),
    )
    df["hospital"] = df["hospital_fact"].where(
        df["hospital_fact"].notna(),
        df["hospital_rips_det"].where(
            df["hospital_rips_det"].notna(), df["hospital_rips"]
        ),
    )
    df["mes"] = df["mes_fact"].where(df["mes_fact"].notna(), df["mes_rips"])
    df["dif_valor"] = [x - y for x, y in zip(df["valor_fact"], df["valor_rips"])]
    df["dif_cantidad"] = [
        x - y for x, y in zip(df["cantidad_fact"], df["cantidad_rips"])
    ]

    # Consistencia interna RIPS 3374: detalle - copago - comisión - descuentos = neto AF
    def dec(v):
        return v if isinstance(v, Decimal) else CERO

    df["neto_calculado_detalle_rips"] = [
        (vr - dec(cop) - dec(com) - dec(des)) if isinstance(neto, Decimal) else None
        for neto, vr, cop, com, des in zip(df["AF_valor_neto"], df["valor_rips"], df["AF_copago"],
                                           df["AF_comision"], df["AF_descuentos"])
    ]
    df["dif_AF_neto_vs_detalle"] = [
        (n - c) if isinstance(n, Decimal) and isinstance(c, Decimal) else None
        for n, c in zip(df["AF_valor_neto"], df["neto_calculado_detalle_rips"])
    ]
    en_fact = df["lineas_fact"] > 0
    en_rips = (df["lineas_rips"] > 0) | df["archivo_rips"].notna()
    estados = []
    for f, r, dv, dc in zip(en_fact, en_rips, df["dif_valor"], df["dif_cantidad"]):
        if f and not r:
            estados.append("FACTURA_NO_REPORTADA_EN_RIPS")
        elif r and not f:
            estados.append("FACTURA_NO_ESTA_EN_FACTURACION")
        elif dv == 0 and dc == 0:
            estados.append("COINCIDE")
        else:
            estados.append("DIFERENCIA")
    df["estado"] = estados
    columnas = [
        "estado",
        "factura",
        "factura_clave",
        "mes",
        "sede",
        "hospital",
        "cantidad_fact",
        "cantidad_rips",
        "dif_cantidad",
        "valor_fact",
        "valor_rips",
        "dif_valor",
        "lineas_fact",
        "lineas_rips",
        "AF_valor_neto",
        "AF_copago",
        "AF_comision",
        "AF_descuentos",
        "neto_calculado_detalle_rips",
        "dif_AF_neto_vs_detalle",
        "archivo_rips",
    ]
    return df[columnas].sort_values(["estado", "factura_clave"]).reset_index(drop=True)


def _cruce_codigos(
    fact: pd.DataFrame, rips: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    def por_codigo(lineas, sufijo, extra):
        por = extra + ["codigo_clave"]
        if lineas.empty:
            return pd.DataFrame(
                columns=por
                + [
                    f"codigo_{sufijo}",
                    f"categoria_{sufijo}",
                    f"descripcion_{sufijo}",
                    f"campo_codigo_{sufijo}",
                    f"cantidad_{sufijo}",
                    f"valor_{sufijo}",
                    f"facturas_{sufijo}",
                ]
            )
        g = lineas.groupby(por, dropna=False)
        return pd.DataFrame(
            {
                f"codigo_{sufijo}": g["codigo"].first(),
                f"categoria_{sufijo}": g["categoria"].first(),
                f"tipo_codigo_{sufijo}": g["tipo_codigo"].first(),
                f"descripcion_{sufijo}": g["descripcion"].first(),
                f"campo_codigo_{sufijo}": g["campo_codigo"].agg(
                    lambda s: " | ".join(sorted({str(x) for x in s.dropna()}))
                ),
                f"cantidad_{sufijo}": g["cantidad"].sum(),
                f"valor_{sufijo}": g["valor"].sum(),
                f"facturas_{sufijo}": g["factura_clave"].nunique(),
            }
        ).reset_index()

    def cruzar(extra):
        df = por_codigo(fact, "fact", extra).merge(
            por_codigo(rips, "rips", extra),
            on=extra + ["codigo_clave"],
            how="outer",
            indicator=True,
        )
        df = _completar(df)
        df["resultado"] = (
            df["_merge"]
            .map(
                {
                    "both": "EN_AMBOS",
                    "left_only": "SOLO_EN_FACTURACION",
                    "right_only": "SOLO_EN_RIPS",
                }
            )
            .astype(str)
        )
        df["codigo"] = df["codigo_fact"].where(
            df["codigo_fact"].notna(), df["codigo_rips"]
        )
        df["categoria"] = df["categoria_rips"].where(
            df["categoria_rips"].notna(), df.get("categoria_fact")
        )
        df["tipo_codigo"] = df.get("tipo_codigo_rips").where(
            df.get("tipo_codigo_rips").notna(), df.get("tipo_codigo_fact")
        )
        df["descripcion"] = df["descripcion_fact"].where(
            df["descripcion_fact"].notna(), df["descripcion_rips"]
        )
        df["dif_cantidad"] = [
            a - b for a, b in zip(df["cantidad_fact"], df["cantidad_rips"])
        ]
        df["dif_valor"] = [a - b for a, b in zip(df["valor_fact"], df["valor_rips"])]
        columnas = (
            ["resultado"]
            + extra
            + [
                "codigo",
                "codigo_clave",
                "tipo_codigo",
                "categoria",
                "descripcion",
                "campo_codigo_fact",
                "campo_codigo_rips",
                "facturas_fact",
                "facturas_rips",
                "cantidad_fact",
                "cantidad_rips",
                "dif_cantidad",
                "valor_fact",
                "valor_rips",
                "dif_valor",
            ]
        )
        orden = {"SOLO_EN_FACTURACION": 0, "SOLO_EN_RIPS": 1, "EN_AMBOS": 2}
        df["_o"] = df["resultado"].map(orden)
        return df.sort_values(["_o"] + extra + ["codigo_clave"])[columnas].reset_index(
            drop=True
        )

    return cruzar([]), cruzar(["sede"])


def _consumo(
    fact: pd.DataFrame, rips: pd.DataFrame, top_n: int
) -> dict[str, pd.DataFrame]:
    """Cantidades y valores por código, por servicio, por hospital y por farmacia, con ranking."""

    def tabla(por, filtro=None):
        f = fact if filtro is None else fact[filtro(fact)] if not fact.empty else fact
        r = rips if filtro is None else rips[filtro(rips)] if not rips.empty else rips
        claves = por + ["codigo_clave"]
        df = _sumas(f, claves, "fact").merge(
            _sumas(r, claves, "rips"), on=claves, how="outer"
        )
        df = _completar(df)
        columnas_nombre = ["codigo_clave", "codigo", "descripcion", "tipo_codigo"]
        partes = [x[columnas_nombre] for x in (f, r) if not x.empty]
        nombres = pd.concat(partes) if partes else None
        if nombres is not None and not nombres.empty:
            nombres = (
                nombres.dropna(subset=["codigo_clave"])
                .groupby("codigo_clave")
                .first()
                .reset_index()
            )
            df = df.merge(nombres, on="codigo_clave", how="left")
        else:
            df["codigo"] = df["descripcion"] = df["tipo_codigo"] = None
        df["dif_valor"] = [a - b for a, b in zip(df["valor_fact"], df["valor_rips"])]
        df["dif_cantidad"] = [
            a - b for a, b in zip(df["cantidad_fact"], df["cantidad_rips"])
        ]
        # Ranking por valor facturado (o reportado en RIPS si no fue facturado) dentro del grupo
        df["_valor_rank"] = [
            a if a else b for a, b in zip(df["valor_fact"], df["valor_rips"])
        ]
        df = df.sort_values(
            por + ["_valor_rank"], ascending=[True] * len(por) + [False], kind="stable"
        )
        df["ranking"] = (
            df.groupby(por, dropna=False).cumcount() + 1
            if por
            else range(1, len(df) + 1)
        )
        df["top"] = df["ranking"] <= top_n
        columnas = por + [
            "ranking",
            "top",
            "codigo",
            "codigo_clave",
            "tipo_codigo",
            "descripcion",
            "cantidad_fact",
            "cantidad_rips",
            "dif_cantidad",
            "valor_fact",
            "valor_rips",
            "dif_valor",
            "facturas_fact",
            "facturas_rips",
        ]
        return df[columnas].reset_index(drop=True)

    es_farmacia = lambda d: d["categoria"].isin(CATEGORIAS_FARMACIA)  # noqa: E731
    return {
        "Consumo por código": tabla([]),
        "Consumo por servicio": tabla(["categoria"]),
        "Consumo por hospital": tabla(["hospital", "categoria"]),
        "Consumo farmacias": tabla(["hospital", "farmacia"], es_farmacia),
    }


def _resumen_otros_codigos(otros: pd.DataFrame) -> pd.DataFrame:
    if otros.empty:
        return pd.DataFrame(
            columns=["campo", "codigo", "registros", "facturas", "sedes"]
        )
    g = otros.groupby(["campo", "codigo"])
    df = pd.DataFrame(
        {
            "registros": g.size(),
            "facturas": g["factura"].nunique(),
            "sedes": g["sede"].agg(lambda s: ", ".join(sorted(set(map(str, s))))),
        }
    ).reset_index()
    return df.sort_values(["campo", "registros"], ascending=[True, False]).reset_index(
        drop=True
    )


def _resumen_general(r: ResultadoConciliacion) -> list[tuple[str, str, object]]:
    """Reporte general de revisión de archivos (nivel 1)."""
    filas: list[tuple[str, str, object]] = []
    sec = "1. Archivos"
    filas.append((sec, "Versión(es) de RIPS detectada(s)", r.carga_rips.version))
    no_det = [
        a["archivo"]
        for a in r.carga_rips.archivos
        if a.get("version") == "NO DETERMINADA"
    ]
    filas.append(
        (
            sec,
            "Archivos RIPS leídos",
            len([a for a in r.carga_rips.archivos if a.get("tipo") != "IGNORADO"]),
        )
    )
    filas.append((sec, "Archivos con versión NO DETERMINADA", len(no_det)))
    for a in no_det[:20]:
        filas.append((sec, "  · Sin versión", a))
    for tipo, cantidad in sorted(r.carga_rips.conteos_por_tipo().items()):
        filas.append((sec, f"Registros RIPS válidos tipo {tipo}", cantidad))
    filas.append(
        (
            sec,
            "Registros de facturación válidos",
            r.carga_facturacion.conteo("facturacion"),
        )
    )
    sec = "2. Calidad de datos"
    inc = r.incidencias
    filas.append(
        (
            sec,
            "Errores (registros excluidos o archivos no procesados)",
            int((inc["severidad"] == ERROR).sum()),
        )
    )
    filas.append((sec, "Advertencias", int((inc["severidad"] == ADVERTENCIA).sum())))
    sec = "3. Facturas"
    est = r.facturas["estado"].value_counts() if not r.facturas.empty else {}
    filas.append(
        (
            sec,
            "Facturas en facturación",
            int((r.facturas["lineas_fact"] > 0).sum()) if not r.facturas.empty else 0,
        )
    )
    filas.append(
        (
            sec,
            "Facturas en RIPS",
            (
                int(
                    (
                        (r.facturas["lineas_rips"] > 0)
                        | r.facturas["archivo_rips"].notna()
                    ).sum()
                )
                if not r.facturas.empty
                else 0
            ),
        )
    )
    for e in (
        "COINCIDE",
        "DIFERENCIA",
        "FACTURA_NO_REPORTADA_EN_RIPS",
        "FACTURA_NO_ESTA_EN_FACTURACION",
    ):
        filas.append((sec, f"Facturas {e}", int(est.get(e, 0)) if len(est) else 0))
    sec = "4. Valores"
    total_f = sum(r.lineas_facturacion["valor"], CERO)
    total_r = sum(r.lineas_rips["valor"], CERO)
    filas.append((sec, "Valor total facturado", total_f))
    filas.append((sec, "Valor total reportado en RIPS (detalle)", total_r))
    filas.append((sec, "Diferencia (facturado - RIPS)", total_f - total_r))
    filas.append(
        (sec, "Cantidad total facturada", sum(r.lineas_facturacion["cantidad"], CERO))
    )
    filas.append(
        (sec, "Cantidad total reportada en RIPS", sum(r.lineas_rips["cantidad"], CERO))
    )
    sec = "5. Detalle factura-código"
    conteo = r.detalle["estado"].value_counts()
    for e in ESTADOS:
        sub = r.detalle[r.detalle["estado"] == e]
        filas.append((sec, f"{e} (pares factura-código)", int(conteo.get(e, 0))))
        if e != COINCIDE and not sub.empty:
            filas.append(
                (
                    sec,
                    f"  · Valor neto de la diferencia {e}",
                    sum(sub["dif_valor"], CERO),
                )
            )
    sec = "6. Cruce de códigos"
    cc = r.cruce_codigos["resultado"].value_counts()
    for e in ("SOLO_EN_FACTURACION", "SOLO_EN_RIPS", "EN_AMBOS"):
        filas.append((sec, f"Códigos {e}", int(cc.get(e, 0))))
    return filas

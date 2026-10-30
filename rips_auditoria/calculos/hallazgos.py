"""Hallazgos de auditoría consolidados, con severidad, valor en riesgo y acción recomendada."""
from __future__ import annotations

import pandas as pd

COLUMNAS = ["tipo", "severidad", "periodo", "municipio", "hospital", "codigo", "descripcion", "cantidad",
            "valor_en_riesgo", "detalle", "accion_recomendada"]


def _f(**kw) -> dict:
    return {k: kw.get(k) for k in COLUMNAS}


def consolidar(servicios: pd.DataFrame, nt_cruce: pd.DataFrame, tarifas: pd.DataFrame,
               facturacion: pd.DataFrame, incidencias: pd.DataFrame, conc_facturas: pd.DataFrame,
               riesgo: pd.DataFrame | None = None) -> pd.DataFrame:
    h: list[dict] = []
    if nt_cruce is not None and not nt_cruce.empty:
        for r in nt_cruce[nt_cruce.estado == "SOBREPASO"].itertuples():
            h.append(_f(tipo="SOBREPASO_FRECUENCIA_NT", severidad="ALTA", periodo=r.mes,
                        municipio=getattr(r, "municipio", None), codigo=r.codigo, descripcion=r.descripcion,
                        cantidad=r.desviacion, valor_en_riesgo=getattr(r, "valor_exceso", None),
                        detalle=f"RIPS {r.frecuencia_rips:,.0f} vs NT {r.frecuencia_prevista:,.2f} (+{r.desviacion_pct:.1f} %)",
                        accion_recomendada="Revisar pertinencia y demanda inducida; soporte para ajuste de la NT"))
    s = servicios[~servicios["es_episodio"] & servicios["eps_objetivo"]]
    no_of = s[(s["modalidad_declarada"] == "CAPITA") & (s["estado_catalogo"] == "NO OFERTADO")]
    for (per, mun, cod), g in no_of.groupby(["mes", "municipio", "codigo"], dropna=False):
        h.append(_f(tipo="NO_OFERTADO_EN_CAPITA", severidad="ALTA", periodo=per, municipio=mun, codigo=cod,
                    descripcion=g["descripcion"].iloc[0], cantidad=g["cantidad"].sum(),
                    valor_en_riesgo=g["valor_total"].sum(),
                    detalle="Servicio NO OFERTADO registrado en remisión de cápita",
                    accion_recomendada="Facturar por evento con autorización o retirar del RIPS de cápita"))
    of_ev = s[(s["modalidad_declarada"] == "EVENTO") & (s["estado_catalogo"] == "OFERTADO")]
    for (per, mun, cod), g in of_ev.groupby(["mes", "municipio", "codigo"], dropna=False):
        h.append(_f(tipo="OFERTADO_CAPITA_FACTURADO_EVENTO", severidad="MEDIA", periodo=per, municipio=mun, codigo=cod,
                    descripcion=g["descripcion"].iloc[0], cantidad=g["cantidad"].sum(),
                    valor_en_riesgo=g["valor_total"].sum(),
                    detalle="Código marcado OFERTADO en cápita facturado por evento: riesgo de glosa por doble pago",
                    accion_recomendada="Verificar contrato/autorización; si está en cápita, no facturar por evento"))
    if tarifas is not None and not tarifas.empty and "estado" in tarifas:
        for r in tarifas[tarifas.estado.isin(["SOBRE_TARIFA", "BAJO_TARIFA", "SIN_TARIFA"])].itertuples():
            sev = "ALTA" if r.estado == "SOBRE_TARIFA" else "MEDIA"
            riesgo_v = getattr(r, "dif_valor_total", None)
            h.append(_f(tipo=f"TARIFA_{r.estado}", severidad=sev, periodo=r.mes, codigo=r.codigo,
                        descripcion=r.descripcion, cantidad=r.cantidad,
                        valor_en_riesgo=riesgo_v if r.estado == "SOBRE_TARIFA" else (r.valor_facturado if r.estado == "SIN_TARIFA" else None),
                        detalle=(f"VU facturado {r.valor_unitario_facturado:,.0f} vs pactado {r.tarifa_pactada:,.0f}"
                                 if r.estado != "SIN_TARIFA" else "Servicio EVENTO sin tarifa pactada"),
                        accion_recomendada="Ajustar al tarifario pactado" if r.estado != "SIN_TARIFA"
                        else "Incluir en el anexo tarifario o soportar con manual de referencia"))
    if facturacion is not None and not facturacion.empty:
        for r in facturacion[facturacion.estado.isin(["FACTURADO_SIN_RIPS", "RIPS_SIN_FACTURA", "DIFERENCIA"])].itertuples():
            h.append(_f(tipo=r.estado, severidad="ALTA" if r.estado == "FACTURADO_SIN_RIPS" else "MEDIA",
                        periodo=r.periodo, municipio=getattr(r, "municipio", None), codigo=r.codigo,
                        descripcion=r.descripcion, cantidad=r.dif_cantidad,
                        valor_en_riesgo=r.glosa_potencial or abs(r.dif_valor),
                        detalle=f"Facturado {r.valor_facturado:,.0f} vs RIPS {r.valor_rips:,.0f}",
                        accion_recomendada="Glosa potencial: completar soporte RIPS" if r.estado != "RIPS_SIN_FACTURA"
                        else "Servicio prestado sin facturar: radicar"))
    if conc_facturas is not None and not conc_facturas.empty:
        for r in conc_facturas[conc_facturas.estado == "DIFERENCIA"].itertuples():
            h.append(_f(tipo="FACTURA_NO_CONCILIA_RIPS", severidad="MEDIA", periodo=r.periodo, municipio=r.municipio,
                        hospital=r.hospital, codigo=r.num_factura, valor_en_riesgo=abs(r.diferencia),
                        detalle=f"Neto AF {float(r.valor_neto or 0):,.0f} vs servicios {r.valor_neto_esperado:,.0f}",
                        accion_recomendada="Corregir AF o servicios antes de radicar"))
    dup = incidencias[incidencias.regla.isin(["REGISTRO_DUPLICADO", "REMISION_DUPLICADA", "FACTURA_DUPLICADA",
                                               "FACTURA_DUPLICADA_ENTRE_REMISIONES"])] if not incidencias.empty else incidencias
    for (regla, per, hosp), g in dup.groupby(["regla", "periodo", "hospital"], dropna=False) if not dup.empty else []:
        h.append(_f(tipo=regla, severidad="ALTA" if "FACTURA" in regla else "MEDIA", periodo=per, hospital=hosp,
                    cantidad=len(g), detalle="; ".join(g["mensaje"].astype(str).head(3)),
                    accion_recomendada="Depurar duplicados antes de radicar y de consolidar frecuencias"))
    if riesgo is not None and not riesgo.empty and riesgo.attrs.get("exceso_tope"):
        if riesgo.attrs["exceso_tope"] > 0:
            h.append(_f(tipo="TRASLADOS_SOBRE_TOPE", severidad="ALTA",
                        valor_en_riesgo=riesgo.attrs["exceso_tope"],
                        detalle=f"Traslados/mes {riesgo.attrs['traslados_mes']:,.0f} vs tope {riesgo.attrs['tope_mensual']:,.0f}",
                        accion_recomendada="Activar cláusula de tope y auditoría de pertinencia de traslados"))
    df = pd.DataFrame(h, columns=COLUMNAS)
    orden = {"ALTA": 0, "MEDIA": 1, "BAJA": 2}
    return df.sort_values(["severidad", "valor_en_riesgo"], key=lambda c: c.map(orden) if c.name == "severidad" else -c.fillna(0)).reset_index(drop=True)

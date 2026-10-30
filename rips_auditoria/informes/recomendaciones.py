"""Recomendaciones gerenciales generadas a partir del resultado (reglas explícitas, no IA)."""
from __future__ import annotations

from ..analisis import Resultado


def _m(v: float) -> str:
    return f"${v / 1e6:,.1f} M".replace(",", "X").replace(".", ",").replace("X", ".")


def generar(r: Resultado) -> list[tuple[str, str]]:
    t, ind = r.tablas, r.indicadores
    k = ind["capita"]
    rec: list[tuple[str, str]] = []
    h = t.get("hallazgos")
    if h is not None and not h.empty:
        of = h[h.tipo == "OFERTADO_CAPITA_FACTURADO_EVENTO"]
        if not of.empty:
            rec.append(("Conciliar servicios ofertados en cápita facturados por evento",
                        f"{of['codigo'].nunique()} códigos OFERTADOS suman {_m(of['valor_en_riesgo'].sum())} facturados por "
                        "evento. Validar autorizaciones y contrato antes de radicar para evitar glosa por doble pago."))
        sob = h[h.tipo == "SOBREPASO_FRECUENCIA_NT"]
        if not sob.empty:
            rec.append(("Soportar ajuste de la Nota Técnica",
                        f"{sob['codigo'].nunique()} servicios superan la frecuencia prevista. Usar el cruce NT vs RIPS como "
                        "evidencia técnica para renegociar frecuencias (o auditar demanda inducida)."))
        dup = h[h.tipo.str.contains("DUPLICAD")]
        if not dup.empty:
            rec.append(("Depurar duplicados antes de radicar",
                        f"{int(dup['cantidad'].fillna(0).sum())} registros/remisiones duplicados detectados. Corregir para no "
                        "sobreestimar frecuencias ni exponerse a glosas."))
        tar = h[h.tipo.str.startswith("TARIFA_SOBRE")]
        if not tar.empty:
            rec.append(("Ajustar tarifas al tarifario pactado",
                        f"{len(tar)} combinaciones código-mes facturadas sobre tarifa; valor en riesgo {_m(tar['valor_en_riesgo'].fillna(0).sum())}."))
    if k["crecimiento_pct"] > 10 and k["promedio_reciente"] > 0:
        rec.append(("Revisar el crecimiento del costo CAPITA",
                    f"El costo CAPITA del periodo reciente ({_m(k['promedio_reciente'])}/mes) creció "
                    f"{k['crecimiento_pct']:.1f} % frente al inicial."))
    ej = ind.get("ejecucion_nt_pct")
    if ej is not None:
        rec.append(("Seguimiento de frecuencias Nota Técnica Nueva EPS",
                    f"Los RIPS de cápita cargados ejecutan el {ej:.1f} % de la frecuencia prevista; "
                    f"{ind.get('codigos_sobreejecucion', 0)} servicio(s) por municipio superan lo previsto "
                    "(hojas Frecuencias NT)."))
    if ind.get("participacion_traslados", 0) > 0.05:
        rec.append(("Traslados por evento con tope contractual",
                    f"Los traslados son {ind['participacion_traslados'] * 100:.1f} % del EVENTO. Mantenerlos por evento con "
                    "tope mensual y auditoría de pertinencia (dato de tope pendiente de confirmar)."))
    if r.advertencias:
        rec.append(("Completar maestros pendientes", " ".join(r.advertencias)))
    return rec

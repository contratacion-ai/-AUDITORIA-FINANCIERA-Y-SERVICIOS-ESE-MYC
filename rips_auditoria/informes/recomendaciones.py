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
        rec.append(("Renegociar la cápita con el run-rate reciente",
                    f"El costo CAPITA del periodo reciente ({_m(k['promedio_reciente'])}/mes) creció "
                    f"{k['crecimiento_pct']:.1f} % frente al inicial. El escenario 3 (demanda real) es la base técnica."))
    mig = t.get("migrables")
    if mig is not None and not mig.empty:
        rec.append(("Migrar servicios predecibles de EVENTO a CÁPITA",
                    f"{len(mig)} servicios con frecuencia estable suman {_m(ind['migrables_mes'])}/mes a tarifa evento "
                    "(escenario 2: cápita ampliada)."))
    if ind.get("participacion_traslados", 0) > 0.05:
        rec.append(("Traslados fuera del PGP con tope contractual",
                    f"Los traslados son {ind['participacion_traslados'] * 100:.1f} % del EVENTO. Mantenerlos por evento con "
                    "tope mensual y auditoría de pertinencia (dato de tope pendiente de confirmar)."))
    c = ind.get("corredor_evento") or {}
    if c.get("run_rate"):
        rec.append(("Proponer PGP-EVENTO con corredor de ajuste",
                    f"Run-rate EVENTO {_m(c['run_rate'])}/mes con corredor ±{c['cv_pct']:.1f} % "
                    f"({_m(c['limite_inferior'])} – {_m(c['limite_superior'])})."))
    if r.advertencias:
        rec.append(("Completar maestros pendientes", " ".join(r.advertencias)))
    return rec

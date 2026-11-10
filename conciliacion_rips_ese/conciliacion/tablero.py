"""Cálculos del tablero interactivo (agregados exactos en Python con Decimal)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Q

FILTROS = ("sede", "hospital", "mes", "estado", "categoria", "tipo_codigo")


def filtrar(qs, params):
    for campo in FILTROS:
        valor = params.get(campo)
        if valor:
            qs = qs.filter(**{campo: valor})
    if params.get("factura"):
        qs = qs.filter(factura__icontains=params["factura"].strip())
    if params.get("codigo"):
        texto = params["codigo"].strip()
        qs = qs.filter(Q(codigo__icontains=texto) | Q(descripcion__icontains=texto))
    if params.get("solo_diferencias", "1") == "1":
        qs = qs.exclude(estado="COINCIDE")
    return qs


def opciones(qs_base):
    return {campo: sorted(v for v in qs_base.order_by(campo).values_list(campo, flat=True).distinct() if v) for campo in FILTROS}


def agregados(qs) -> dict:
    """KPIs y series para las gráficas."""
    cero = Decimal(0)
    tot = {"valor_fact": cero, "valor_rips": cero, "dif_valor": cero, "dif_abs": cero, "filas": 0, "facturas": set()}
    por_sede = defaultdict(lambda: [cero, cero])
    por_mes = defaultdict(lambda: [cero, cero])
    por_hospital = defaultdict(lambda: [cero, cero])
    por_estado = defaultdict(lambda: [0, cero])
    por_codigo = defaultdict(lambda: [cero, "", ""])
    por_categoria = defaultdict(lambda: [cero, cero])

    campos = ("sede", "hospital", "mes", "estado", "categoria", "codigo", "descripcion", "factura",
              "valor_fact", "valor_rips", "dif_valor")
    for sede, hospital, mes, estado, categoria, codigo, descripcion, factura, vf, vr, dv in qs.values_list(*campos).iterator():
        tot["valor_fact"] += vf
        tot["valor_rips"] += vr
        tot["dif_valor"] += dv
        tot["dif_abs"] += abs(dv)
        tot["filas"] += 1
        tot["facturas"].add(factura)
        for mapa, clave in ((por_sede, sede), (por_mes, mes), (por_hospital, hospital), (por_categoria, categoria or "—")):
            mapa[clave][0] += vf
            mapa[clave][1] += vr
        por_estado[estado][0] += 1
        por_estado[estado][1] += dv
        c = por_codigo[codigo]
        c[0] += abs(dv)
        c[1] = c[1] or (descripcion or "")
        c[2] = categoria or ""

    def serie(mapa, ordenar_por_clave=True):
        items = sorted(mapa.items()) if ordenar_por_clave else sorted(mapa.items(), key=lambda kv: -(kv[1][0]))
        return {
            "etiquetas": [k for k, _ in items],
            "facturado": [str(v[0]) for _, v in items],
            "rips": [str(v[1]) for _, v in items],
        }

    top_codigos = sorted(por_codigo.items(), key=lambda kv: kv[1][0], reverse=True)[:15]
    return {
        "kpi": {
            "valor_fact": tot["valor_fact"],
            "valor_rips": tot["valor_rips"],
            "dif_valor": tot["dif_valor"],
            "dif_abs": tot["dif_abs"],
            "filas": tot["filas"],
            "facturas": len(tot["facturas"]),
        },
        "graficas": {
            "sede": serie(por_sede),
            "mes": serie(por_mes),
            "hospital": serie(por_hospital),
            "categoria": serie(por_categoria),
            "estado": {
                "etiquetas": list(sorted(por_estado)),
                "conteo": [por_estado[e][0] for e in sorted(por_estado)],
                "valor": [str(por_estado[e][1]) for e in sorted(por_estado)],
            },
            "codigos": {
                "etiquetas": [f"{k} {v[1][:30]}".strip() for k, v in top_codigos],
                "valor": [str(v[0]) for _, v in top_codigos],
            },
        },
        "por_estado": sorted(((e, v[0], v[1]) for e, v in por_estado.items()), key=lambda x: x[0]),
    }

"""Consulta detallada de costos de la corrida vigente: por CUPS, servicio (unidad de negocio), centro de costos y recurso.

Todas las vistas aceptan los mismos filtros (sede, unidad de negocio, clase, centro y texto) y se exportan a Excel.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Q as Cond
from django.db.models import Sum

from modelo.models import CentroCosto, Clase, Recurso
from motor.models import Asignacion, CostoCups

from .consultas import costo_centros

CERO = Decimal("0")
ELEMENTOS = ("CP", "IH", "CG", "EC")
VISTAS = {
    "cups": "Por CUPS",
    "servicios": "Por servicio (unidad de negocio)",
    "centros": "Por centro de costos",
    "recursos": "Por recurso",
}


def _dividir(a, b):
    return (a / b).quantize(Decimal("0.01")) if b else None


def centros_filtrados(institucion, f):
    qs = CentroCosto.objects.de(institucion).select_related("unidad_negocio", "sede")
    if f.get("sede"):
        qs = qs.filter(sede=f["sede"])
    if f.get("unidad"):
        qs = qs.filter(unidad_negocio=f["unidad"])
    if f.get("centro"):
        qs = qs.filter(pk=f["centro"].pk)
    if f.get("clase"):
        qs = qs.filter(Cond(clase=f["clase"]) | Cond(clase="", unidad_negocio__clase=f["clase"]))
    return qs


def por_cups(institucion, corrida, f, detalle=False):
    """Filas por CUPS (consolidado de todos los centros filtrados) o por centro + CUPS (detalle)."""
    if corrida is None:
        return []
    qs = CostoCups.objects.filter(corrida=corrida, centro__in=centros_filtrados(institucion, f)).select_related("cups", "centro")
    if f.get("q"):
        qs = qs.filter(Cond(cups__codigo__icontains=f["q"]) | Cond(cups__descripcion__icontains=f["q"]))
    if detalle:
        filas = []
        for c in qs.order_by("centro__codigo", "cups__codigo"):
            filas.append({"pk": c.pk, "centro": c.centro, "cups": c.cups, "cantidad": c.cantidad, "costo_total": c.costo_total,
                          "costo_unitario": c.costo_unitario,
                          **{e: Decimal(str(c.costo_por_elemento.get(e, 0))) for e in ELEMENTOS}})
        return filas
    acum = {}
    for c in qs:
        a = acum.setdefault(c.cups_id, {"cups": c.cups, "cantidad": CERO, "costo_total": CERO, "centros": set(),
                                        **{e: CERO for e in ELEMENTOS}})
        a["cantidad"] += c.cantidad
        a["costo_total"] += c.costo_total
        a["centros"].add(c.centro.codigo)
        for e in ELEMENTOS:
            a[e] += Decimal(str(c.costo_por_elemento.get(e, 0)))
    filas = []
    for a in acum.values():
        a["costo_unitario"] = _dividir(a["costo_total"], a["cantidad"])
        a["n_centros"] = len(a.pop("centros"))
        filas.append(a)
    filas.sort(key=lambda a: -a["costo_total"])
    return filas


def por_centro(institucion, corrida, f):
    """Costo directo (1ª), recibido en 2ª/3ª/4ª, distribuido y final de cada centro, con su producción."""
    if corrida is None:
        return []
    costos = costo_centros(corrida)
    produccion = dict(CostoCups.objects.filter(corrida=corrida).values("centro_id").annotate(c=Sum("cantidad"))
                      .values_list("centro_id", "c"))
    filas = []
    texto = (f.get("q") or "").upper()
    for c in centros_filtrados(institucion, f).order_by("unidad_negocio__orden", "codigo"):
        d = costos.get(c.pk)
        if not d:
            continue
        if texto and texto not in c.codigo.upper() and texto not in c.nombre.upper():
            continue
        prod = produccion.get(c.pk, CERO)
        filas.append({
            "centro": c, "clase": c.clase or c.unidad_negocio.clase, "directo": d.get("directo", CERO),
            "recibido_2": d.get("recibido_2", CERO), "recibido_3": d.get("recibido_3", CERO), "recibido_4": d.get("recibido_4", CERO),
            "distribuido": d.get("distribuido", CERO), "final": d.get("final", CERO), "produccion": prod,
            "costo_unidad": _dividir(d.get("final", CERO), prod) if (c.clase or c.unidad_negocio.clase) == Clase.FINALES else None,
        })
    return filas


def por_servicio(institucion, corrida, f):
    """Por unidad de negocio: directo, final, producción y costo por unidad; y el costo final por sede (pivote)."""
    filas_c = por_centro(institucion, corrida, f)
    acum = {}
    sedes = []
    for r in filas_c:
        un = r["centro"].unidad_negocio
        sede = r["centro"].sede.codigo if r["centro"].sede else "SIN SEDE"
        if sede not in sedes:
            sedes.append(sede)
        a = acum.setdefault(un.pk, {"unidad": un, "directo": CERO, "final": CERO, "produccion": CERO, "centros": 0,
                                    "por_sede": defaultdict(lambda: CERO)})
        a["directo"] += r["directo"]
        a["final"] += r["final"]
        a["produccion"] += r["produccion"]
        a["centros"] += 1
        a["por_sede"][sede] += r["final"]
    filas = sorted(acum.values(), key=lambda a: (a["unidad"].orden, a["unidad"].codigo))
    for a in filas:
        a["costo_unidad"] = _dividir(a["final"], a["produccion"]) if a["unidad"].clase == Clase.FINALES else None
        a["valores_sede"] = [a["por_sede"].get(s, CERO) for s in sedes]
    return filas, sedes


def por_recurso(institucion, corrida, f):
    """Costo del periodo por recurso (1ª distribución) en los centros filtrados, con su participación."""
    if corrida is None:
        return [], CERO
    centros = centros_filtrados(institucion, f)
    datos = dict(Asignacion.objects.filter(corrida=corrida, distribucion=1, centro_destino__in=centros)
                 .values("recurso_id").annotate(v=Sum("valor")).values_list("recurso_id", "v"))
    total = sum(datos.values(), CERO)
    filas = []
    for r in Recurso.objects.de(institucion).select_related("elemento").order_by("elemento__orden", "orden"):
        v = datos.get(r.pk, CERO)
        if v:
            filas.append({"recurso": r, "valor": v, "part": (v / total * 100) if total else None})
    return filas, total

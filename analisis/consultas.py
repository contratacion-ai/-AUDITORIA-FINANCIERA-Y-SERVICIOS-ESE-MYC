"""Consultas agregadas sobre la corrida vigente de un periodo (tablero, matrices e informe)."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum

from modelo.models import CentroCosto, Clase, Elemento, Recurso, UnidadNegocio
from motor.models import Asignacion, CostoCups, Corrida

CERO = Decimal("0")


def corrida_vigente(periodo):
    if periodo is None:
        return None
    return Corrida.objects.filter(periodo=periodo, vigente=True).order_by("-inicio").first()


def var(actual, anterior):
    actual, anterior = actual or CERO, anterior or CERO
    dif = actual - anterior
    pct = (dif / anterior * 100) if anterior else None
    return dif, pct


def por_recurso(corrida):
    """{recurso_id: valor} del costo del periodo (1ª distribución)."""
    if corrida is None:
        return {}
    return dict(Asignacion.objects.filter(corrida=corrida, distribucion=1).values("recurso_id")
                .annotate(v=Sum("valor")).values_list("recurso_id", "v"))


def tabla_recursos(institucion, actual, anterior=None):
    """Filas ELEMENTO | RECURSO | anterior | actual | var $ | var % | part% anterior | part% actual."""
    a, b = por_recurso(actual), por_recurso(anterior)
    total_a, total_b = sum(a.values(), CERO), sum(b.values(), CERO)
    filas = []
    for r in Recurso.objects.de(institucion).select_related("elemento").order_by("elemento__orden", "orden"):
        va, vb = a.get(r.pk, CERO), b.get(r.pk, CERO)
        if not va and not vb:
            continue
        dif, pct = var(va, vb)
        filas.append({"elemento": r.elemento, "recurso": r, "anterior": vb, "actual": va, "dif": dif, "pct": pct,
                      "part_anterior": (vb / total_b * 100) if total_b else None,
                      "part_actual": (va / total_a * 100) if total_a else None})
    return filas, total_a, total_b


def tabla_elementos(institucion, actual, anterior=None):
    filas_r, total_a, total_b = tabla_recursos(institucion, actual, anterior)
    acum = defaultdict(lambda: [CERO, CERO])
    for f in filas_r:
        acum[f["elemento"].pk][0] += f["actual"]
        acum[f["elemento"].pk][1] += f["anterior"]
    filas = []
    for e in Elemento.objects.de(institucion).order_by("orden"):
        va, vb = acum.get(e.pk, [CERO, CERO])
        dif, pct = var(va, vb)
        filas.append({"elemento": e, "actual": va, "anterior": vb, "dif": dif, "pct": pct,
                      "part_actual": (va / total_a * 100) if total_a else None})
    return filas, total_a, total_b


def costo_centros(corrida):
    """{centro_id: {'directo', 'recibido_2', 'recibido_3', 'recibido_4', 'distribuido', 'final'}}."""
    datos = defaultdict(lambda: defaultdict(lambda: CERO))
    if corrida is None:
        return datos
    for d, destino, v in Asignacion.objects.filter(corrida=corrida).values("distribucion", "centro_destino_id") \
            .annotate(v=Sum("valor")).values_list("distribucion", "centro_destino_id", "v"):
        datos[destino]["directo" if d == 1 else f"recibido_{d}"] += v
        datos[destino]["final"] += v
    for origen, v in Asignacion.objects.filter(corrida=corrida, distribucion__gt=1).values("centro_origen_id") \
            .annotate(v=Sum("valor")).values_list("centro_origen_id", "v"):
        datos[origen]["distribuido"] += v
        datos[origen]["final"] -= v
    return datos


def tabla_unidades(institucion, actual, anterior=None, medida="directo"):
    """Costo por unidad de negocio agrupado por clase. `medida`: 'directo' (1ª) o 'final' (tras la 4ª)."""
    ca, cb = costo_centros(actual), costo_centros(anterior)
    centros = list(CentroCosto.objects.de(institucion).values_list("pk", "unidad_negocio_id"))
    por_un_a, por_un_b = defaultdict(lambda: CERO), defaultdict(lambda: CERO)
    for pk, un in centros:
        por_un_a[un] += ca.get(pk, {}).get(medida, CERO)
        por_un_b[un] += cb.get(pk, {}).get(medida, CERO)
    salida = {c: [] for c in Clase.values}
    totales = {c: [CERO, CERO] for c in Clase.values}
    for un in UnidadNegocio.objects.de(institucion).order_by("orden", "codigo"):
        va, vb = por_un_a.get(un.pk, CERO), por_un_b.get(un.pk, CERO)
        dif, pct = var(va, vb)
        salida[un.clase].append({"unidad": un, "actual": va, "anterior": vb, "dif": dif, "pct": pct})
        totales[un.clase][0] += va
        totales[un.clase][1] += vb
    total_general = sum(t[0] for t in totales.values())
    for clase, filas in salida.items():
        for f in filas:
            f["part_clase"] = (f["actual"] / totales[clase][0] * 100) if totales[clase][0] else None
            f["part_total"] = (f["actual"] / total_general * 100) if total_general else None
    return salida, totales


def tabla_sedes(institucion, corrida, codigos_recurso):
    recursos = list(Recurso.objects.de(institucion).filter(codigo__in=codigos_recurso).order_by("orden"))
    filas = defaultdict(lambda: defaultdict(lambda: CERO))
    if corrida is not None:
        for sede, rec, v in Asignacion.objects.filter(corrida=corrida, distribucion=1, recurso__in=recursos) \
                .values("centro_destino__sede__nombre", "recurso_id").annotate(v=Sum("valor")) \
                .values_list("centro_destino__sede__nombre", "recurso_id", "v"):
            filas[sede or "SIN SEDE"][rec] += v
    salida = []
    for sede in sorted(filas, key=lambda s: -sum(filas[s].values())):
        valores = [filas[sede].get(r.pk, CERO) for r in recursos]
        salida.append({"sede": sede, "valores": valores, "total": sum(valores, CERO)})
    totales = [sum((f["valores"][i] for f in salida), CERO) for i in range(len(recursos))]
    return recursos, salida, totales


def matriz(institucion, corrida, etapa=4):
    """Matriz tipo Anexo Técnico 4: centros × recursos (1ª) + recibido de estructura/servicios/intermedios."""
    recursos = list(Recurso.objects.de(institucion).select_related("elemento").order_by("orden"))
    celdas = defaultdict(lambda: defaultdict(lambda: CERO))
    if corrida is not None:
        for destino, rec, v in Asignacion.objects.filter(corrida=corrida, distribucion=1).values("centro_destino_id", "recurso_id") \
                .annotate(v=Sum("valor")).values_list("centro_destino_id", "recurso_id", "v"):
            celdas[destino][rec] = v
    costos = costo_centros(corrida)
    filas = []
    for c in CentroCosto.objects.de(institucion).select_related("unidad_negocio").order_by("unidad_negocio__orden", "codigo"):
        dc = costos.get(c.pk, {})
        valores = [celdas[c.pk].get(r.pk, CERO) for r in recursos]
        recibidos = [dc.get(f"recibido_{d}", CERO) for d in (2, 3, 4) if d <= etapa]
        total = sum(valores, CERO) + sum(recibidos, CERO)
        distribuido = CERO
        if corrida is not None and etapa >= 2:
            distribuido = sum((v for d, v in _salidas(corrida).get(c.pk, {}).items() if d <= etapa), CERO)
        filas.append({"centro": c, "valores": valores, "recibidos": recibidos, "total": total,
                      "distribuido": distribuido, "neto": total - distribuido})
    totales = {
        "valores": [sum((f["valores"][i] for f in filas), CERO) for i in range(len(recursos))],
        "recibidos": [sum((f["recibidos"][i] for f in filas), CERO) for i in range(len([d for d in (2, 3, 4) if d <= etapa]))],
        "total": sum((f["total"] for f in filas), CERO),
        "distribuido": sum((f["distribuido"] for f in filas), CERO),
        "neto": sum((f["neto"] for f in filas), CERO),
    }
    return recursos, filas, totales


_cache_salidas = {}


def _salidas(corrida):
    clave = (corrida.pk, corrida.fin)
    if clave not in _cache_salidas:
        _cache_salidas.clear()
        datos = defaultdict(dict)
        for origen, d, v in Asignacion.objects.filter(corrida=corrida, distribucion__gt=1) \
                .values("centro_origen_id", "distribucion").annotate(v=Sum("valor")) \
                .values_list("centro_origen_id", "distribucion", "v"):
            datos[origen][d] = v
        _cache_salidas[clave] = datos
    return _cache_salidas[clave]


def costo_unitario_cups(corrida):
    """{cups_id: {'cantidad', 'costo_total', 'costo_unitario', 'codigo', 'descripcion'}} ponderado entre centros."""
    salida = {}
    if corrida is None:
        return salida
    for cups, cod, desc, cant, total in CostoCups.objects.filter(corrida=corrida).values("cups_id", "cups__codigo", "cups__descripcion") \
            .annotate(c=Sum("cantidad"), t=Sum("costo_total")).values_list("cups_id", "cups__codigo", "cups__descripcion", "c", "t"):
        salida[cups] = {"codigo": cod, "descripcion": desc, "cantidad": cant, "costo_total": total,
                        "costo_unitario": (total / cant).quantize(Decimal("0.01")) if cant else CERO}
    return salida


def serie_mensual(institucion, n=12):
    """Costo total por periodo (últimos n con corrida vigente) para la tendencia del tablero."""
    corridas = (Corrida.objects.de(institucion).filter(vigente=True).select_related("periodo")
                .order_by("-periodo__anio", "-periodo__mes")[:n])
    datos = []
    for c in reversed(list(corridas)):
        total = Asignacion.objects.filter(corrida=c, distribucion=1).aggregate(s=Sum("valor"))["s"] or CERO
        datos.append({"periodo": c.periodo.etiqueta_corta, "total": total})
    return datos

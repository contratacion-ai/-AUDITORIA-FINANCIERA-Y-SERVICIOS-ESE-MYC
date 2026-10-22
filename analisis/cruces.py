"""Cruces: facturación por ámbito, costo frente a tarifa de notas técnicas y RIPS ↔ facturación ↔ costos."""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Count, Sum

from cargas.models import Facturacion, Rips
from modelo.models import CupsCentroCosto, NotaTecnica, NotaTecnicaItem
from motor.models import CostoCups

from .consultas import costo_centros, costo_unitario_cups

CERO = Decimal("0")
TOLERANCIA_TARIFA = Decimal("1.00")  # pesos


def _margen(ingreso, costo):
    margen = ingreso - costo
    return margen, (margen / ingreso * 100) if ingreso else None


def facturacion_por_ambito(institucion, periodo, corrida, eps_id=None):
    """Análisis por ámbito de facturación (ambulatorio / hospitalario / urgencias...) con costo estimado y margen."""
    unit = {k: v["costo_unitario"] for k, v in costo_unitario_cups(corrida).items()}
    qs = Facturacion.objects.de(institucion).filter(periodo=periodo)
    if eps_id:
        qs = qs.filter(eps_id=eps_id)
    grupos = defaultdict(lambda: {"facturas": set(), "cantidad": CERO, "facturado": CERO, "costo": CERO, "sin_costo": 0,
                                  "eps": defaultdict(lambda: [CERO, CERO])})
    for f in qs.select_related("ambito", "eps").only("numero_factura", "cantidad", "valor_total", "cups_id", "ambito__codigo",
                                                     "ambito__nombre", "ambito_origen", "eps__nombre", "eps_codigo"):
        nombre = f"{f.ambito.codigo} · {f.ambito.nombre}" if f.ambito else f"SIN HOMOLOGAR ({f.ambito_origen or 'vacío'})"
        g = grupos[nombre]
        g["facturas"].add(f.numero_factura)
        g["cantidad"] += f.cantidad
        g["facturado"] += f.valor_total
        costo = unit.get(f.cups_id)
        if costo is None:
            g["sin_costo"] += 1
        else:
            g["costo"] += costo * f.cantidad
        eps = f.eps.nombre if f.eps else f.eps_codigo
        g["eps"][eps][0] += f.valor_total
        g["eps"][eps][1] += (costo or CERO) * f.cantidad
    filas = []
    for nombre in sorted(grupos):
        g = grupos[nombre]
        margen, pct = _margen(g["facturado"], g["costo"])
        filas.append({"ambito": nombre, "facturas": len(g["facturas"]), "cantidad": g["cantidad"], "facturado": g["facturado"],
                      "costo": g["costo"].quantize(Decimal("0.01")), "margen": margen, "margen_pct": pct, "sin_costo": g["sin_costo"],
                      "eps": sorted(({"eps": k, "facturado": v[0], "costo": v[1], "margen": v[0] - v[1]} for k, v in g["eps"].items()),
                                    key=lambda x: -x["facturado"])})
    return filas


def notas_vigentes(institucion, periodo):
    return [n for n in NotaTecnica.objects.de(institucion).select_related("eps") if n.vigente_en(periodo)]


def costo_vs_tarifa(institucion, periodo, corrida, eps_id=None):
    """Por cada ítem de nota técnica vigente (evento/paquete): costo unitario ABC vs tarifa contratada."""
    unit = costo_unitario_cups(corrida)
    notas = [n for n in notas_vigentes(institucion, periodo) if (not eps_id or n.eps_id == int(eps_id))
             and n.modalidad in (NotaTecnica.Modalidad.EVENTO, NotaTecnica.Modalidad.PAQUETE)]
    facturado = defaultdict(lambda: [CERO, CERO])
    for eps, cups, cant, total in Facturacion.objects.de(institucion).filter(periodo=periodo, eps__isnull=False, cups__isnull=False) \
            .values("eps_id", "cups_id").annotate(c=Sum("cantidad"), t=Sum("valor_total")).values_list("eps_id", "cups_id", "c", "t"):
        facturado[(eps, cups)] = [cant, total]
    filas = []
    for item in NotaTecnicaItem.objects.filter(nota__in=notas).select_related("nota__eps", "cups", "ambito"):
        nota = item.nota
        c = unit.get(item.cups_id)
        costo = c["costo_unitario"] if c else None
        cant, total = facturado.get((nota.eps_id, item.cups_id), [CERO, CERO])
        margen = (item.tarifa - costo) if costo is not None else None
        filas.append({
            "nota": nota, "eps": nota.eps, "cups": item.cups, "modalidad": nota.get_modalidad_display(), "tarifa": item.tarifa,
            "costo_unitario": costo, "margen_unitario": margen,
            "margen_pct": (margen / item.tarifa * 100) if margen is not None and item.tarifa else None,
            "cantidad_facturada": cant, "valor_facturado": total,
            "resultado": "SIN COSTO" if costo is None else ("PÉRDIDA" if margen < 0 else "CUBRE COSTO"),
        })
    filas.sort(key=lambda f: (f["resultado"] != "PÉRDIDA", f["margen_unitario"] if f["margen_unitario"] is not None else 0))
    return filas


def contratos_capita(institucion, periodo, corrida):
    """Cápita / PGP: valor mensual contratado frente al costo ABC de lo prestado a la EPS (según RIPS)."""
    unit = {k: v["costo_unitario"] for k, v in costo_unitario_cups(corrida).items()}
    salida = []
    for nota in notas_vigentes(institucion, periodo):
        if nota.modalidad not in (NotaTecnica.Modalidad.CAPITA, NotaTecnica.Modalidad.PGP):
            continue
        costo, actividades, sin_costo = CERO, CERO, 0
        for cups, cant in Rips.objects.de(institucion).filter(periodo=periodo, eps=nota.eps).values_list("cups_id", "cantidad"):
            actividades += cant
            if cups in unit:
                costo += unit[cups] * cant
            else:
                sin_costo += 1
        esperadas = NotaTecnicaItem.objects.filter(nota=nota).aggregate(s=Sum("frecuencia_esperada"))["s"] or CERO
        margen, pct = _margen(nota.valor_mensual, costo)
        salida.append({"nota": nota, "valor_mensual": nota.valor_mensual, "costo": costo.quantize(Decimal("0.01")),
                       "margen": margen, "margen_pct": pct,
                       "indice_costo": (costo / nota.valor_mensual * 100) if nota.valor_mensual else None,
                       "actividades": actividades, "frecuencia_esperada": esperadas, "sin_costo": sin_costo})
    return salida


@dataclass
class Inconsistencia:
    tipo: str
    severidad: str
    descripcion: str
    factura: str = ""
    cups: str = ""
    eps: str = ""
    centro: str = ""
    valor: object = None
    referencia: str = ""


TIPOS = {
    "RIPS_SIN_FACTURA": ("ALTA", "Registro RIPS sin la misma factura y CUPS en facturación"),
    "FACTURA_SIN_RIPS": ("ALTA", "Ítem facturado sin soporte RIPS (Res. 2275 de 2023: RIPS es soporte de la FEV)"),
    "CANTIDAD_DIFERENTE": ("MEDIA", "Cantidad distinta entre RIPS y facturación"),
    "CUPS_NO_CATALOGO": ("ALTA", "CUPS inexistente en el catálogo de la institución"),
    "CUPS_SIN_CENTRO": ("MEDIA", "CUPS sin centro de costos asociado: no recibe costo"),
    "CUPS_SIN_COSTO": ("MEDIA", "CUPS facturado sin costo ABC calculado en el periodo"),
    "FINAL_SIN_PRODUCCION": ("ALTA", "Centro final con costo y sin producción"),
    "PRODUCCION_SIN_COSTO": ("MEDIA", "Centro con producción y sin costo asignado"),
    "TARIFA_MENOR_COSTO": ("ALTA", "Tarifa contratada inferior al costo unitario ABC"),
    "TARIFA_FACTURADA_DIFERENTE": ("MEDIA", "Valor unitario facturado distinto a la tarifa de la nota técnica"),
    "EPS_SIN_NOTA_TECNICA": ("MEDIA", "EPS facturada sin nota técnica vigente"),
    "EPS_NO_CATALOGO": ("MEDIA", "EPS inexistente en el catálogo"),
    "AMBITO_NO_HOMOLOGADO": ("BAJA", "Ámbito de facturación/RIPS sin homologar"),
}


def inconsistencias(institucion, periodo, corrida):
    salida = []

    def add(tipo, **kw):
        sev, desc = TIPOS[tipo]
        salida.append(Inconsistencia(tipo=tipo, severidad=sev, descripcion=desc, **kw))

    fac = list(Facturacion.objects.de(institucion).filter(periodo=periodo).select_related("eps", "cups"))
    rips = list(Rips.objects.de(institucion).filter(periodo=periodo).select_related("eps", "cups"))
    llave_fac, llave_rips = defaultdict(lambda: CERO), defaultdict(lambda: CERO)
    for f in fac:
        llave_fac[(f.numero_factura, f.cups_codigo)] += f.cantidad
    for r in rips:
        llave_rips[(r.numero_factura, r.cups_codigo)] += r.cantidad
    if fac and rips:
        for k, cant in llave_rips.items():
            if k not in llave_fac:
                add("RIPS_SIN_FACTURA", factura=k[0], cups=k[1], valor=cant)
            elif llave_fac[k] != cant:
                add("CANTIDAD_DIFERENTE", factura=k[0], cups=k[1], valor=cant - llave_fac[k],
                    referencia=f"RIPS {cant} / factura {llave_fac[k]}")
        for k, cant in llave_fac.items():
            if k not in llave_rips:
                add("FACTURA_SIN_RIPS", factura=k[0], cups=k[1], valor=cant)
    for codigo in sorted({f.cups_codigo for f in fac if f.cups_id is None} | {r.cups_codigo for r in rips if r.cups_id is None}):
        add("CUPS_NO_CATALOGO", cups=codigo)
    for codigo in sorted({f.eps_codigo for f in fac if f.eps_id is None} | {r.eps_codigo for r in rips if r.eps_id is None and r.eps_codigo}):
        add("EPS_NO_CATALOGO", eps=codigo)
    for valor in sorted({f.ambito_origen for f in fac if f.ambito_id is None} | {r.ambito_origen for r in rips if r.ambito_id is None and r.ambito_origen}):
        add("AMBITO_NO_HOMOLOGADO", referencia=valor or "(vacío)")
    con_centro = set(CupsCentroCosto.objects.de(institucion).values_list("cups_id", flat=True))
    for cups in sorted({(r.cups_id, r.cups_codigo) for r in rips if r.cups_id and r.cups_id not in con_centro}, key=lambda x: x[1]):
        add("CUPS_SIN_CENTRO", cups=cups[1])
    costos = costo_unitario_cups(corrida)
    if corrida is not None:
        for cups in sorted({(f.cups_id, f.cups_codigo) for f in fac if f.cups_id and f.cups_id not in costos}, key=lambda x: x[1]):
            add("CUPS_SIN_COSTO", cups=cups[1])
        for p in corrida.pendientes.filter(distribucion=5).select_related("centro"):
            add("FINAL_SIN_PRODUCCION", centro=p.centro.codigo if p.centro else "", valor=p.valor)
        costo_c = costo_centros(corrida)
        producidos = set(CostoCups.objects.filter(corrida=corrida).values_list("centro_id", flat=True))
        from cargas.models import Produccion

        con_prod = set(Produccion.objects.de(institucion).filter(periodo=periodo).values_list("centro_id", "centro__codigo"))
        for centro_id, codigo in sorted(con_prod, key=lambda x: x[1]):
            if centro_id not in producidos and not costo_c.get(centro_id, {}).get("final"):
                add("PRODUCCION_SIN_COSTO", centro=codigo)
    # Tarifas
    items = {}
    notas = notas_vigentes(institucion, periodo)
    for item in NotaTecnicaItem.objects.filter(nota__in=notas).select_related("nota__eps", "cups"):
        if item.nota.modalidad in (NotaTecnica.Modalidad.EVENTO, NotaTecnica.Modalidad.PAQUETE):
            items[(item.nota.eps_id, item.cups_id)] = item
            c = costos.get(item.cups_id)
            if c and item.tarifa < c["costo_unitario"]:
                add("TARIFA_MENOR_COSTO", eps=item.nota.eps.nombre, cups=item.cups.codigo,
                    valor=c["costo_unitario"] - item.tarifa, referencia=f"tarifa {item.tarifa} / costo {c['costo_unitario']}")
    eps_con_nota = {n.eps_id for n in notas}
    for eps in sorted({(f.eps_id, f.eps.nombre) for f in fac if f.eps_id and f.eps_id not in eps_con_nota}, key=lambda x: x[1]):
        add("EPS_SIN_NOTA_TECNICA", eps=eps[1])
    for f in fac:
        item = items.get((f.eps_id, f.cups_id))
        if item and abs(f.valor_unitario - item.tarifa) > TOLERANCIA_TARIFA:
            add("TARIFA_FACTURADA_DIFERENTE", factura=f.numero_factura, cups=f.cups_codigo, eps=f.eps.nombre,
                valor=f.valor_unitario - item.tarifa, referencia=f"facturado {f.valor_unitario} / tarifa {item.tarifa} (fila {f.fila})")
    orden = {"ALTA": 0, "MEDIA": 1, "BAJA": 2}
    salida.sort(key=lambda i: (orden[i.severidad], i.tipo, i.factura, i.cups))
    return salida


def resumen_inconsistencias(lista):
    conteo = defaultdict(int)
    for i in lista:
        conteo[i.tipo] += 1
    return [{"tipo": t, "severidad": TIPOS[t][0], "descripcion": TIPOS[t][1], "cantidad": conteo[t]} for t in TIPOS if conteo[t]]


def margen_por_eps(institucion, periodo, corrida):
    unit = {k: v["costo_unitario"] for k, v in costo_unitario_cups(corrida).items()}
    datos = defaultdict(lambda: [CERO, CERO, 0])
    for f in Facturacion.objects.de(institucion).filter(periodo=periodo).select_related("eps"):
        k = f.eps.nombre if f.eps else f.eps_codigo
        datos[k][0] += f.valor_total
        datos[k][1] += unit.get(f.cups_id, CERO) * f.cantidad
        datos[k][2] += 1
    filas = []
    for eps, (fac, costo, n) in datos.items():
        margen, pct = _margen(fac, costo)
        filas.append({"eps": eps, "facturado": fac, "costo": costo.quantize(Decimal("0.01")), "margen": margen, "margen_pct": pct, "items": n})
    return sorted(filas, key=lambda f: -f["facturado"])


def resumen_produccion_rips(institucion, periodo):
    return list(Rips.objects.de(institucion).filter(periodo=periodo).values("tipo_registro")
                .annotate(n=Count("id"), cantidad=Sum("cantidad")).order_by("tipo_registro"))

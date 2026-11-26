"""Referencia y contrarreferencia (formato RYC-FO-015): carga mensual, cruce
con la facturación y tablero.

Cruce remisión -> facturación (en este orden):
1. número de factura o de autorización registrado en la remisión;
2. mismo hospital y mismo paciente (documento cifrado) con fecha de servicio
   entre el día anterior al reporte y tres días después de la salida.
Se toman todas las líneas de esa factura y fecha (tarifa básica + km). A las
que no tienen ambulancia se les asigna la placa de la remisión, y la ruta si
la remisión la identifica; nunca se cambia una placa o ruta ya asignada.

Remisión «sin facturar»: trasladada en ambulancia de la ESE (TAB/TAM), sin
línea de facturación, en un mes que sí tiene facturación cargada.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections import Counter, defaultdict
from statistics import mean

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..importacion import hallazgo, normalizar
from ..importacion.referencia import generar_formato, leer_referencia
from ..models import Ambulancia, LoteImportacion, Remision, Sede, TarifaRuta, Traslado
from .comunes import ResolutorCatalogos, guardar_hallazgos

NO_TRASLADADO = ("RECHAZADO", "CANCELADO", "PENDIENTE", "FALLECIDO", "TRASLADO POR OTRO MEDIO")
TIPOS_FLOTA = ("TAB", "TAM")
NOTA_PLACA = "placa tomada de referencia y contrarreferencia (RYC-FO-015)"


def formato_vacio() -> bytes:
    """Formato v002 con las listas de la aplicación (sedes, placas y destinos del Anexo 3)."""
    sedes = list(Sede.objects.filter(activa=True).order_by("nombre").values_list("nombre", flat=True))
    placas = list(Ambulancia.objects.exclude(estado=Ambulancia.FUERA).order_by("placa").values_list("placa", flat=True))
    destinos = sorted({normalizar(d) for d in TarifaRuta.objects.filter(activa=True).values_list("destino", flat=True)})
    return generar_formato(sedes, placas, destinos)


def _aware(d):
    if d is None:
        return None
    return timezone.make_aware(d) if timezone.is_naive(d) else d


def _ruta(res: ResolutorCatalogos, sede: Sede, municipio: str, texto: str):
    rutas = res.rutas.get(normalizar(sede.origen_tarifa), [])
    if municipio:
        for r in rutas:
            if normalizar(r.destino) == municipio:
                return r
    texto = normalizar(texto)
    if texto:
        candidatas = [r for r in rutas if normalizar(r.destino) and normalizar(r.destino) in texto]
        if candidatas:
            return max(candidatas, key=lambda r: len(r.destino))
    return None


def trasladada(rem: Remision) -> bool:
    return not any(rem.estado.startswith(e) for e in NO_TRASLADADO)


def conciliar(remisiones) -> dict:
    """Cruza remisiones con la facturación y asigna placa y ruta. Devuelve conteos."""
    cont = Counter()
    for rem in remisiones:
        if not trasladada(rem):
            continue
        lineas = []
        if rem.factura:
            lineas = list(Traslado.objects.filter(numero_factura=rem.factura, sede=rem.sede))
        if not lineas and rem.autorizacion:
            lineas = list(Traslado.objects.filter(autorizacion=rem.autorizacion, sede=rem.sede))
        if not lineas and rem.documento_hash:
            inicio = timezone.localtime(rem.fecha_reporte).date() - dt.timedelta(days=1)
            fin = timezone.localtime(rem.fecha_salida or rem.fecha_llegada or rem.fecha_reporte).date() + dt.timedelta(days=3)
            candidatas = list(Traslado.objects.filter(sede=rem.sede, documento_hash=rem.documento_hash,
                                                      fecha_servicio__gte=inicio, fecha_servicio__lte=fin))
            if candidatas:
                ref = timezone.localtime(rem.fecha_salida or rem.fecha_reporte).date()
                cerca = min(candidatas, key=lambda t: abs((t.fecha_servicio - ref).days))
                lineas = [t for t in candidatas if t.fecha_servicio == cerca.fecha_servicio
                          and (t.numero_factura == cerca.numero_factura or not cerca.numero_factura)]
        if not lineas:
            if rem.traslado_id:
                rem.traslado, rem.lineas_facturadas = None, 0
                rem.save(update_fields=["traslado", "lineas_facturadas"])
            continue
        principal = sorted(lineas, key=lambda t: (t.categoria != "TRASLADO", -float(t.valor_facturado)))[0]
        rem.traslado, rem.lineas_facturadas = principal, len(lineas)
        rem.save(update_fields=["traslado", "lineas_facturadas"])
        cont["conciliadas"] += 1
        for t in lineas:
            campos = []
            if rem.ambulancia_id and t.ambulancia_id is None:
                t.ambulancia_id = rem.ambulancia_id
                campos.append("ambulancia")
                cont["placas_asignadas"] += 1
            elif rem.ambulancia_id and t.ambulancia_id != rem.ambulancia_id:
                cont["placa_distinta"] += 1
            if rem.ruta_id and t.ruta_id is None:
                t.ruta_id = rem.ruta_id
                t.ruta_inferida = True
                campos.append("ruta")
                cont["rutas_asignadas"] += 1
            if campos:
                if NOTA_PLACA not in t.observaciones_calidad:
                    t.observaciones_calidad = "; ".join(x for x in (t.observaciones_calidad, NOTA_PLACA) if x)[:2000]
                    campos.append("observaciones_calidad")
                t.version += 1
                t.save(update_fields=campos + ["version", "actualizado"])
    return cont


def importar_referencia(archivo, usuario=None, nombre="RYC-FO-015.xlsx", sede: Sede | None = None) -> LoteImportacion:
    """Carga el formato RYC-FO-015 (v001 o v002) y lo cruza con la facturación.

    ``sede``: hospital por defecto para las filas que no lo traen o no se
    reconocen (archivos de un solo hospital). Recargar el archivo actualiza
    los registros (llave: hospital, tipo, fecha de reporte, documento, destino).
    """
    datos = leer_referencia(archivo, nombre, settings.SAL_DOCUMENTO)
    lote = LoteImportacion.objects.create(tipo="REFERENCIA", archivo=nombre[:250], usuario=usuario, sede=sede)
    hallazgos = list(datos["hallazgos"])
    res = ResolutorCatalogos()
    nuevos = actualizados = omitidos = 0
    guardadas = []
    vistos = Counter()
    fuente = nombre[:200]
    with transaction.atomic():
        for r in datos["registros"]:
            s = res.sede(r["sede_texto"]) if r["sede_texto"] else None
            if s is None:
                s = sede
            if s is None:
                hallazgos.append(hallazgo(fuente, r["ubicacion"], "FALTANTE",
                                          f"Hospital que remite no reconocido: «{r['sede_texto']}»; elija el hospital al cargar", "ALTA"))
                omitidos += 1
                continue
            alertas = list(r["alertas"])
            amb = None
            if r["placa"] and r["placa"] not in ("NOAPLICA", "NA", "NINGUNA"):
                amb = res.ambulancia(r["placa"])
                if amb is None:
                    alertas.append(f"placa {r['placa']} no está en la flota")
            elif r["tipo_traslado"] in TIPOS_FLOTA and r["estado"] in ("", "TRASLADADO"):
                alertas.append("traslado terrestre sin placa de ambulancia")
            ruta = _ruta(res, s, r["municipio_destino"], r["destino_texto"] or r["ips_destino"])
            if not ruta and (r["municipio_destino"] or r["destino_texto"]) and r["tipo_traslado"] in TIPOS_FLOTA:
                alertas.append(f"destino sin recorrido en el manual tarifario desde {s.origen_tarifa}")
            reporte = _aware(r["fecha_reporte"])
            destino_llave = r["municipio_destino"] or normalizar(r["ips_destino"] or r["destino_texto"])
            base = f"{s.codigo}|{r['tipo_registro']}|{reporte.isoformat()}|{r['documento_hash'] or r['fila']}|{destino_llave}"
            llave = hashlib.sha256(base.encode("utf-8")).hexdigest()
            vistos[llave] += 1
            if vistos[llave] > 1:
                hallazgos.append(hallazgo(fuente, r["ubicacion"], "DUPLICADO",
                                          "Remisión repetida en el archivo (mismo hospital, fecha, paciente y destino); se toma una vez", "MEDIA"))
                omitidos += 1
                continue
            defaults = {
                "lote": lote, "tipo_registro": r["tipo_registro"][:20], "sede": s, "fecha_reporte": reporte,
                "eps": r["eps"], "regimen": r["regimen"], "tipo_documento": r["tipo_documento"],
                "documento_hash": r["documento_hash"], "edad": r["edad"], "unidad_edad": r["unidad_edad"], "sexo": r["sexo"],
                "especialidad": r["especialidad"], "cie10": r["cie10"], "diagnostico": r["diagnostico"],
                "tipo_traslado": r["tipo_traslado"], "acompanamiento_medico": r["acompanamiento"],
                "placa_reportada": r["placa"], "ambulancia": amb, "ips_destino": r["ips_destino"][:150],
                "municipio_destino": (r["municipio_destino"] or (normalizar(ruta.destino) if ruta else ""))[:80], "ruta": ruta,
                "fecha_llamada": _aware(r["fecha_llamada"]), "fecha_llegada_equipo": _aware(r["fecha_llegada_equipo"]),
                "fecha_aceptacion": _aware(r["fecha_aceptacion"]), "fecha_salida": _aware(r["fecha_salida"]),
                "fecha_llegada": _aware(r["fecha_llegada"]), "km": r["km"], "autorizacion": r["autorizacion"],
                "factura": r["factura"], "estado": r["estado"], "observaciones": r["observaciones"],
                "responsable": r["responsable"], "alertas": "; ".join(alertas)[:500],
            }
            rem, creado = Remision.objects.update_or_create(llave=llave, defaults=defaults)
            nuevos, actualizados = (nuevos + 1, actualizados) if creado else (nuevos, actualizados + 1)
            guardadas.append(rem)
            if alertas:
                hallazgos.append(hallazgo(fuente, r["ubicacion"], "INCONSISTENCIA", "; ".join(alertas)[:400], "BAJA"))
        cont = conciliar(guardadas)

    meses = sorted({f"{r.mes:%Y-%m}" for r in guardadas})
    if lote.sede is None:
        sedes = {r.sede_id for r in guardadas}
        if len(sedes) == 1:
            lote.sede_id = sedes.pop()
    lote.mes = dt.date.fromisoformat(f"{meses[0]}-01") if len(meses) == 1 else None
    lote.detalle = {"version_formato": datos["version"], "hoja": datos["hoja"], "meses": meses, "registros": len(guardadas),
                    **dict(cont)}
    lote.nuevos, lote.actualizados, lote.omitidos = nuevos, actualizados, omitidos
    lote.hallazgos = guardar_hallazgos(hallazgos, lote)
    lote.resumen = (
        f"Formato {datos['version'] or '?'}: {len(guardadas)} remisiones ({', '.join(meses) or 'sin fechas'}); "
        f"{cont['conciliadas']} cruzadas con la facturación, placa asignada a {cont['placas_asignadas']} líneas facturadas"
        f" y ruta a {cont['rutas_asignadas']}"
    )
    lote.save()
    return lote


# ---------------------------------------------------------------------------
# Tablero
# ---------------------------------------------------------------------------


def _prom(valores):
    v = [x for x in valores if x is not None and x >= 0]
    return mean(v) if v else None


def tablero_referencia(desde: dt.date, hasta: dt.date, sede_codigo=None, tipo_traslado=None) -> dict:
    qs = Remision.objects.filter(mes__gte=desde, mes__lte=hasta).select_related("sede", "ambulancia", "ruta", "traslado")
    if sede_codigo:
        qs = qs.filter(sede__codigo=sede_codigo)
    if tipo_traslado:
        qs = qs.filter(tipo_traslado=tipo_traslado)
    rems = list(qs.order_by("fecha_reporte"))
    meses_his = {m for m in Traslado.objects.filter(origen_registro=Traslado.HIS, mes__gte=desde, mes__lte=hasta)
                 .values_list("mes", flat=True).distinct()}

    def sin_facturar(r):
        return (trasladada(r) and r.tipo_registro == "REFERENCIA" and r.tipo_traslado in TIPOS_FLOTA and r.ambulancia_id
                and r.traslado_id is None and r.mes in meses_his)

    tipos = ["TAB", "TAM", "AÉREO", "FLUVIAL", "OTRO"]

    def resumen(grupo):
        g = list(grupo)
        d = {"total": len(g), "trasladadas": sum(1 for r in g if trasladada(r)),
             "contrarreferencias": sum(1 for r in g if r.tipo_registro == "CONTRARREFERENCIA"),
             "con_placa": sum(1 for r in g if r.ambulancia_id), "acompanamiento": sum(1 for r in g if r.acompanamiento_medico),
             "conciliadas": sum(1 for r in g if r.traslado_id), "sin_facturar": sum(1 for r in g if sin_facturar(r)),
             "valor_sin_facturar": sum(float(r.ruta.tarifa) for r in g if sin_facturar(r) and r.ruta_id and r.ruta.tarifa),
             "espera_h": _prom(r.espera_h for r in g), "respuesta_min": _prom(r.respuesta_min for r in g),
             "duracion_h": _prom(r.duracion_h for r in g)}
        esperas = [r.espera_h for r in g if r.espera_h is not None and r.espera_h >= 0]
        d["pct_24h"] = sum(1 for e in esperas if e <= 24) / len(esperas) * 100 if esperas else None
        d["max_espera_h"] = max(esperas) if esperas else None
        for t in tipos:
            d[t] = sum(1 for r in g if (r.tipo_traslado or "OTRO") == t or (t == "OTRO" and r.tipo_traslado not in tipos))
        return d

    def agrupar(clave):
        grupos = defaultdict(list)
        for r in rems:
            grupos[clave(r)].append(r)
        return grupos

    por_mes = [{"sede": k[0], "mes": k[1], **resumen(v)} for k, v in sorted(
        agrupar(lambda r: (r.sede, r.mes)).items(), key=lambda kv: (kv[0][1], kv[0][0].nombre))]
    por_eps = sorted(({"eps": k or "SIN EPS", **resumen(v)} for k, v in agrupar(lambda r: r.eps).items()), key=lambda d: -d["total"])
    por_destino = sorted(({"destino": k[0] or "SIN DESTINO", "ruta": k[1], **resumen(v)}
                          for k, v in agrupar(lambda r: (r.municipio_destino or r.ips_destino, r.ruta)).items()),
                         key=lambda d: -d["total"])
    por_ambulancia = sorted(({"ambulancia": k, **resumen(v), "km": sum(float(r.km or 0) for r in v),
                              "horas": sum(r.duracion_h or 0 for r in v if (r.duracion_h or 0) > 0)}
                             for k, v in agrupar(lambda r: r.ambulancia).items() if k), key=lambda d: -d["total"])
    # Facturado sin remisión: viajes (factura + fecha + paciente) de hospitales y meses con referencia cargada
    cubiertos = {(r.sede_id, r.mes) for r in Remision.objects.filter(mes__gte=desde, mes__lte=hasta)}
    viajes = defaultdict(list)
    tq = Traslado.objects.filter(mes__gte=desde, mes__lte=hasta, categoria="TRASLADO").exclude(origen_registro=Traslado.RIPS)
    if sede_codigo:
        tq = tq.filter(sede__codigo=sede_codigo)
    for t in tq.select_related("sede").prefetch_related("remisiones"):
        if (t.sede_id, t.mes) in cubiertos:
            viajes[(t.sede_id, t.numero_factura or f"#{t.pk}", t.fecha_servicio, t.documento_hash)].append(t)
    sin_remision = [v for v in viajes.values() if not any(t.remisiones.all() for t in v)]
    return {
        "total": resumen(rems), "por_mes": por_mes, "por_eps": por_eps, "por_destino": por_destino,
        "por_ambulancia": por_ambulancia, "remisiones": rems,
        "sin_facturar": [r for r in rems if sin_facturar(r)],
        "alertas": [r for r in rems if r.alertas],
        "facturado_sin_remision": [
            {"sede": v[0].sede, "fecha": v[0].fecha_servicio, "factura": v[0].numero_factura, "lineas": len(v),
             "valor": sum(float(t.valor_facturado) for t in v), "placa": next((t.ambulancia for t in v if t.ambulancia_id), None)}
            for v in sorted(sin_remision, key=lambda v: (v[0].sede.nombre, v[0].fecha_servicio))
        ],
        "tipos": tipos, "meses_sin_facturacion": sorted({r.mes for r in rems} - meses_his),
    }

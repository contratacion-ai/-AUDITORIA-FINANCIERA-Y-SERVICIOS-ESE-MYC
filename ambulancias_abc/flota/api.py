"""API de integración con las aplicaciones de RIPS y de auditoría.

Autenticación: encabezado ``X-API-Key: <clave>`` (claves creadas con
``manage.py crear_clave_api``) o sesión iniciada en el navegador.
Formatos: JSON por defecto; ``?formato=csv`` para CSV (separador ';', UTF-8 con BOM).
Contrato completo en docs/INTEGRACION.md.
"""

from __future__ import annotations

import csv
import datetime as dt
import functools
import hashlib
import io
import secrets

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from abc_engine.calculo import METODO_REAL

from .models import Ambulancia, ClaveAPI, Consumo, HallazgoCalidad, TarifaRuta, Traslado
from .servicios.costeo import METODOS, calcular_mes

VERSION_CONTRATO = "1.0"


def crear_clave(nombre: str, escritura: bool = False) -> tuple[ClaveAPI, str]:
    clave = "abc_" + secrets.token_urlsafe(32)
    obj = ClaveAPI.objects.create(
        nombre=nombre,
        prefijo=clave[:8],
        clave_hash=hashlib.sha256(clave.encode()).hexdigest(),
        puede_escribir=escritura,
    )
    return obj, clave


def _autenticar(request, escritura=False):
    if request.user.is_authenticated:
        if escritura and not request.user.has_perm("flota.add_traslado"):
            return None, "El usuario no tiene permiso de escritura"
        return request.user, None
    clave = request.headers.get("X-API-Key", "")
    if not clave:
        return None, "Falta el encabezado X-API-Key"
    obj = ClaveAPI.objects.filter(clave_hash=hashlib.sha256(clave.encode()).hexdigest(), activa=True).first()
    if obj is None:
        return None, "Clave de API inválida o inactiva"
    if escritura and not obj.puede_escribir:
        return None, "La clave no tiene permiso de escritura"
    ClaveAPI.objects.filter(pk=obj.pk).update(ultimo_uso=timezone.now())
    return obj, None


def requiere_clave(escritura=False):
    def deco(vista):
        @functools.wraps(vista)
        def envoltura(request, *args, **kwargs):
            sujeto, error = _autenticar(request, escritura)
            if error:
                return JsonResponse({"error": error}, status=401)
            request.sujeto_api = sujeto
            return vista(request, *args, **kwargs)

        return envoltura

    return deco


def _mes(request):
    valor = request.GET.get("mes")
    if not valor:
        return None
    try:
        return dt.date.fromisoformat(f"{valor}-01")
    except ValueError:
        return "error"


def _respuesta(request, nombre: str, filas: list[dict], extra: dict | None = None):
    if request.GET.get("formato") == "csv":
        salida = io.StringIO()
        salida.write("﻿")
        if filas:
            w = csv.DictWriter(salida, fieldnames=list(filas[0].keys()), delimiter=";")
            w.writeheader()
            w.writerows(filas)
        resp = HttpResponse(salida.getvalue(), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{nombre}.csv"'
        return resp
    datos = {"contrato": VERSION_CONTRATO, "generado": timezone.now().isoformat(), "registros": len(filas)}
    datos.update(extra or {})
    datos["datos"] = filas
    return JsonResponse(datos, json_dumps_params={"ensure_ascii": False}, safe=False)


def _r(valor, dec=2):
    return None if valor is None else round(float(valor), dec)


@require_GET
@requiere_clave()
def estado(request):
    return JsonResponse(
        {
            "contrato": VERSION_CONTRATO,
            "ambulancias": Ambulancia.objects.count(),
            "traslados": Traslado.objects.count(),
            "rutas": TarifaRuta.objects.count(),
        }
    )


@require_GET
@requiere_clave()
def ambulancias(request):
    filas = []
    for a in Ambulancia.objects.select_related("sede"):
        p = a.perfil_vigente
        filas.append(
            {
                "placa": a.placa,
                "sede_codigo": a.sede.codigo,
                "sede": a.sede.nombre,
                "tipo_combustible": a.tipo_combustible,
                "estado": a.estado,
                "incluir_en_costeo": a.incluir_en_costeo,
                "excluida_promedios": a.excluida_promedios,
                "costo_dia_estandar": _r(p.total_dia) if p else None,
                "km_dia_estandar": _r(p.km_dia) if p else None,
            }
        )
    return _respuesta(request, "ambulancias", filas)


@require_GET
@requiere_clave()
def rutas(request):
    filas = [
        {
            "ruta_codigo": r.codigo,
            "vigencia": r.vigencia,
            "origen": r.origen,
            "destino": r.destino,
            "km": _r(r.km),
            "valor_km": _r(r.valor_km),
            "tarifa": _r(r.tarifa),
            "ambito_geografico": r.ambito_geografico,
        }
        for r in TarifaRuta.objects.filter(activa=True)
    ]
    return _respuesta(request, "rutas", filas)


@require_GET
@requiere_clave()
def traslados(request):
    mes = _mes(request)
    if mes in (None, "error"):
        return JsonResponse({"error": "Indique ?mes=AAAA-MM"}, status=400)
    metodo = request.GET.get("metodo", METODO_REAL)
    if metodo not in METODOS:
        return JsonResponse({"error": f"metodo debe ser uno de {list(METODOS)}"}, status=400)
    res = calcular_mes(mes, metodo)
    costo = {}
    for f in res.filas:
        c = costo.setdefault(f["traslado_id"], {"total": 0.0, "placas": []})
        c["total"] += f["costo"]
        if f["placa"]:
            c["placas"].append(f"{f['placa']}:{f['peso']:.3f}")
    filas = []
    for t in Traslado.objects.filter(mes=mes).select_related("sede", "ambulancia", "ruta").order_by("fecha_servicio", "id"):
        c = costo.get(t.pk, {"total": 0.0, "placas": []})
        ingreso = float(t.valor_facturado)
        filas.append(
            {
                "traslado_id": t.pk,
                "llave_externa": t.llave_externa,
                "origen_registro": t.origen_registro,
                "numero_factura": t.numero_factura,
                "prefactura": t.prefactura,
                "nro_ingreso": t.nro_ingreso,
                "rips_consecutivo": t.rips_consecutivo,
                "conciliado_rips": t.conciliado_rips,
                "documento_hash": t.documento_hash,
                "fecha_servicio": t.fecha_servicio.isoformat(),
                "sede_codigo": t.sede.codigo,
                "placa": t.ambulancia.placa if t.ambulancia_id else "",
                "reparto_placas": " ".join(c["placas"]),
                "ruta_codigo": t.ruta.codigo if t.ruta_id else "",
                "ruta_inferida": t.ruta_inferida,
                "codigo_cups": t.codigo_cups,
                "cantidad": _r(t.cantidad),
                "km_recorridos": _r(t.km_recorridos),
                "ambito": t.ambito,
                "unidad_funcional": t.unidad_funcional,
                "contrato": t.contrato,
                "eps_codigo": t.eps_codigo,
                "valor_facturado": _r(ingreso),
                "costo_abc": _r(c["total"]),
                "margen": _r(ingreso - c["total"]),
                "observaciones_calidad": t.observaciones_calidad,
            }
        )
    return _respuesta(request, f"traslados_{mes:%Y-%m}", filas, {"mes": f"{mes:%Y-%m}", "metodo": metodo})


@require_GET
@requiere_clave()
def costos_ambulancia(request):
    mes = _mes(request)
    if mes in (None, "error"):
        return JsonResponse({"error": "Indique ?mes=AAAA-MM"}, status=400)
    metodo = request.GET.get("metodo", METODO_REAL)
    if metodo not in METODOS:
        return JsonResponse({"error": f"metodo debe ser uno de {list(METODOS)}"}, status=400)
    res = calcular_mes(mes, metodo)
    ingreso = {}
    for f in res.filas:
        if f["placa"]:
            ingreso[f["placa"]] = ingreso.get(f["placa"], 0.0) + f["ingreso"]
    filas = []
    for placa, a in sorted(res.ambulancias.items()):
        fuentes = res.fuente_pool.get(placa, {})
        filas.append(
            {
                "placa": placa,
                "mes": f"{mes:%Y-%m}",
                "dias": res.dias,
                **{f"costo_{k}": _r(a[f"pool_{k}"]) for k in ("combustible", "mantenimiento", "tripulacion", "insumos", "otros")},
                **{f"fuente_{k}": v for k, v in fuentes.items()},
                "costo_total": _r(a["pool_total"]),
                "asignado_traslados": _r(a["asignado_total"]),
                "capacidad_no_utilizada": _r(a["no_utilizado_total"]),
                "km_traslados": _r(a["driver_km"]),
                "horas_traslados": _r(a["driver_horas"]),
                "traslados": _r(a["driver_conteo"], 3),
                "facturado": _r(ingreso.get(placa, 0.0)),
                "margen": _r(ingreso.get(placa, 0.0) - a["asignado_total"]),
            }
        )
    return _respuesta(
        request,
        f"costos_ambulancia_{mes:%Y-%m}",
        filas,
        {"mes": f"{mes:%Y-%m}", "metodo": metodo, "indirectos": res.indirectos, "costo_total_flota": _r(res.total_pool)},
    )


@require_GET
@requiere_clave()
def consumos(request):
    qs = Consumo.objects.select_related("ambulancia")
    mes = _mes(request)
    if mes == "error":
        return JsonResponse({"error": "mes debe ser AAAA-MM"}, status=400)
    if mes:
        qs = qs.filter(mes=mes)
    filas = [
        {
            "consumo_id": c.pk,
            "placa": c.ambulancia.placa,
            "fecha": c.fecha.isoformat(),
            "tipo": c.tipo,
            "descripcion": c.descripcion,
            "cantidad": _r(c.cantidad, 3),
            "unidad": c.unidad,
            "valor": _r(c.valor),
            "odometro": c.odometro,
            "documento_soporte": c.documento_soporte,
            "proveedor": c.proveedor,
        }
        for c in qs
    ]
    return _respuesta(request, "consumos", filas)


@require_GET
@requiere_clave()
def hallazgos(request):
    qs = HallazgoCalidad.objects.all()
    if request.GET.get("pendientes") == "1":
        qs = qs.filter(resuelto=False)
    filas = [
        {
            "hallazgo_id": h.pk,
            "fuente": h.fuente,
            "ubicacion": h.ubicacion,
            "tipo": h.tipo,
            "severidad": h.severidad,
            "descripcion": h.descripcion,
            "resuelto": h.resuelto,
        }
        for h in qs
    ]
    return _respuesta(request, "hallazgos_calidad", filas)


@csrf_exempt
@require_POST
@requiere_clave(escritura=True)
def importar_rips(request):
    from .servicios.importar import importar_rips as _importar

    try:
        lote = _importar(request.body, usuario=request.user if request.user.is_authenticated else None,
                         nombre=request.headers.get("X-Nombre-Archivo", "RIPS (API)"))
    except ValueError as e:
        return JsonResponse({"error": f"JSON inválido: {e}"}, status=400)
    return JsonResponse(
        {"lote": lote.pk, "nuevos": lote.nuevos, "conciliados": lote.actualizados, "omitidos": lote.omitidos, "hallazgos": lote.hallazgos}
    )

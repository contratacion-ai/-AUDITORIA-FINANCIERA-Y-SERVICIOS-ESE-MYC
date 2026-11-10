import io
import logging
from decimal import Decimal, InvalidOperation

import pandas as pd
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from rips_conciliacion.emparejamiento import __doc__ as CRITERIO

from . import servicios, tablero
from .forms import ActualizarArchivoForm, CargaArchivosForm, ConciliacionForm, InicioRapidoForm, ReemplazoForm
from .models import ArchivoCargado, Conciliacion

registro = logging.getLogger("conciliacion")


def _usuario(request):
    return request.user.get_username() if request.user.is_authenticated else "anónimo"


def inicio_rapido(request):
    """Un solo paso para empezar a cargar datos: crea (o elige) la auditoría y la conciliación."""
    from django.utils import timezone

    from auditoria.models import Auditoria

    hoy = timezone.localdate()
    form = InicioRapidoForm(request.POST or None,
                            initial={"nombre": f"Conciliación RIPS vs facturación {hoy:%Y-%m-%d}",
                                     "auditoria": Auditoria.objects.first()})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        auditoria = d["auditoria"]
        if auditoria is None:
            nombre = d["nueva_auditoria"].strip() or f"Auditoría general {hoy.year}"
            auditoria, _ = Auditoria.objects.get_or_create(nombre=nombre)
        conciliacion = Conciliacion.objects.create(auditoria=auditoria, nombre=d["nombre"])
        messages.success(request, "Listo. Ahora seleccione los archivos y pulse “Cargar archivos seleccionados”.")
        return redirect(conciliacion)
    return render(request, "conciliacion/inicio_rapido.html", {
        "form": form,
        "recientes": Conciliacion.objects.select_related("auditoria")[:10],
    })


def crear(request):
    inicial = {}
    if request.GET.get("auditoria"):
        inicial["auditoria"] = request.GET["auditoria"]
    form = ConciliacionForm(request.POST or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        conciliacion = form.save()
        messages.success(request, "Conciliación creada. Cargue los archivos de RIPS y de facturación.")
        return redirect(conciliacion)
    return render(request, "conciliacion/form.html", {"form": form, "titulo": "Nueva conciliación"})


def editar(request, pk):
    conciliacion = get_object_or_404(Conciliacion, pk=pk)
    if _bloqueada(request, conciliacion):
        return redirect(conciliacion)
    form = ConciliacionForm(request.POST or None, instance=conciliacion)
    if request.method == "POST" and form.is_valid():
        form.save()
        conciliacion.marcar_desactualizada()
        messages.success(request, "Parámetros actualizados. Reprocese para aplicarlos.")
        return redirect(conciliacion)
    return render(request, "conciliacion/form.html", {"form": form, "titulo": f"Parámetros – {conciliacion}"})


def _bloqueada(request, conciliacion) -> bool:
    """Mientras se procesa no se permiten cambios de archivos ni de parámetros."""
    if conciliacion.estado == Conciliacion.Estado.PROCESANDO and servicios.en_curso(conciliacion.pk):
        messages.warning(request, "Espere a que termine el procesamiento en curso para hacer cambios.")
        return True
    return False


def detalle(request, pk):
    conciliacion = get_object_or_404(Conciliacion.objects.select_related("auditoria"), pk=pk)
    servicios.verificar_interrumpida(conciliacion)
    incidencias = conciliacion.incidencias.all()
    severidad = request.GET.get("severidad")
    if severidad:
        incidencias = incidencias.filter(severidad=severidad)
    pagina_inc = Paginator(incidencias, 50).get_page(request.GET.get("pinc"))
    return render(request, "conciliacion/detalle.html", {
        "c": conciliacion,
        "archivos": conciliacion.archivos.all(),
        "form_carga": CargaArchivosForm(),
        "historial": conciliacion.historial.all()[:30],
        "incidencias": pagina_inc,
        "n_errores": conciliacion.incidencias.filter(severidad="ERROR").count(),
        "n_advertencias": conciliacion.incidencias.filter(severidad="ADVERTENCIA").count(),
        "tablas": conciliacion.tablas,
        "hallazgos": conciliacion.hallazgos.count(),
        "tipos": ArchivoCargado.Tipo.choices,
    })


@require_POST
def cargar(request, pk):
    conciliacion = get_object_or_404(Conciliacion, pk=pk)
    if _bloqueada(request, conciliacion):
        return redirect(conciliacion)
    form = CargaArchivosForm(request.POST, request.FILES)
    if not form.is_valid():
        for errores in form.errors.values():
            for e in errores:
                messages.error(request, e)
        return redirect(conciliacion)
    d = form.cleaned_data
    por_tipo: dict[str, list] = {}
    for tipo, archivo in d["clasificados"]:
        por_tipo.setdefault(tipo, []).append(archivo)
    creados, duplicados = [], []
    for tipo, archivos in por_tipo.items():
        try:
            c, dup = servicios.cargar_archivos(conciliacion, tipo, archivos, d["sede"], d["hospital"],
                                               d["ambito"], _usuario(request))
        except Exception as exc:  # noqa: BLE001 - se informa al usuario y se registra
            registro.exception("Error al cargar archivos en la conciliación %s", conciliacion.pk)
            messages.error(request, f"No se pudieron guardar los archivos ({', '.join(a.name for a in archivos)}): "
                                    f"{exc.__class__.__name__}: {exc}")
            continue
        creados += c
        duplicados += dup
    for obj in creados:
        messages.success(request, f"'{obj.nombre_original}' cargado como {obj.get_tipo_display()}. "
                                  "Si el tipo no es correcto, cámbielo en la tabla de archivos.")
    for nuevo, existente in duplicados:
        messages.warning(request, f"'{nuevo}' no se cargó: tiene el mismo contenido que '{existente}'.")
    return redirect(conciliacion)


@require_POST
def reemplazar(request, pk):
    archivo = get_object_or_404(ArchivoCargado, pk=pk)
    if _bloqueada(request, archivo.conciliacion):
        return redirect(archivo.conciliacion)
    form = ReemplazoForm(request.POST, request.FILES)
    if form.is_valid():
        if servicios.reemplazar_archivo(archivo, form.cleaned_data["archivo"], _usuario(request)):
            messages.success(request, f"Archivo reemplazado (versión {archivo.version}). Reprocese la conciliación.")
        else:
            messages.info(request, "El archivo nuevo es idéntico al actual; no se reemplazó.")
    else:
        messages.error(request, "Seleccione el archivo de reemplazo.")
    return redirect(archivo.conciliacion)


@require_POST
def actualizar(request, pk):
    archivo = get_object_or_404(ArchivoCargado, pk=pk)
    if _bloqueada(request, archivo.conciliacion):
        return redirect(archivo.conciliacion)
    form = ActualizarArchivoForm(request.POST, instance=ArchivoCargado.objects.get(pk=pk))
    if form.is_valid():
        cambios = servicios.actualizar_archivo(archivo, _usuario(request), **form.cleaned_data)
        messages.success(request, "Datos del archivo actualizados." if cambios else "Sin cambios.")
    return redirect(archivo.conciliacion)


@require_POST
def eliminar(request, pk):
    archivo = get_object_or_404(ArchivoCargado, pk=pk)
    if _bloqueada(request, archivo.conciliacion):
        return redirect(archivo.conciliacion)
    conciliacion = archivo.conciliacion
    nombre = archivo.nombre_original
    servicios.eliminar_archivo(archivo, _usuario(request))
    messages.success(request, f"Archivo '{nombre}' eliminado.")
    return redirect(conciliacion)


@require_POST
def limpiar(request, pk):
    conciliacion = get_object_or_404(Conciliacion, pk=pk)
    if _bloqueada(request, conciliacion):
        return redirect(conciliacion)
    alcance = request.POST.get("alcance", "")
    if alcance not in ("RIPS", "FACTURACION", "CATALOGO", "EQUIVALENCIAS", "ARCHIVOS", "RESULTADOS", "TODO"):
        raise Http404
    n = servicios.limpiar(conciliacion, alcance, _usuario(request))
    messages.success(request, f"Limpieza '{alcance}' realizada ({n} archivo(s) eliminados).")
    return redirect(conciliacion)


@require_POST
def procesar(request, pk):
    conciliacion = get_object_or_404(Conciliacion, pk=pk)
    try:
        if servicios.iniciar_procesamiento(conciliacion):
            conciliacion.refresh_from_db()
            if conciliacion.estado == Conciliacion.Estado.PROCESADA:
                messages.success(request, "Conciliación procesada.")
            elif conciliacion.estado == Conciliacion.Estado.PROCESANDO:
                messages.info(request, "Procesando… Esta página muestra el avance y se actualiza sola. Con archivos "
                                       "grandes puede tardar algunos minutos; no cierre la ventana de la herramienta.")
        else:
            messages.info(request, "La conciliación ya se está procesando.")
    except ValueError as exc:
        messages.error(request, str(exc))
    except Exception as exc:  # noqa: BLE001 - se informa al usuario
        registro.exception("Error al procesar la conciliación %s", conciliacion.pk)
        messages.error(request, f"Error al procesar: {exc.__class__.__name__}: {exc}")
    return redirect(conciliacion)


@require_POST
def hallazgos(request, pk):
    conciliacion = get_object_or_404(Conciliacion, pk=pk)
    if conciliacion.estado != Conciliacion.Estado.PROCESADA:
        messages.error(request, "Procese la conciliación antes de generar hallazgos.")
        return redirect(conciliacion)
    try:
        minimo = Decimal(request.POST.get("valor_minimo") or "0")
    except InvalidOperation:
        minimo = Decimal(0)
    r = servicios.generar_hallazgos(conciliacion, minimo)
    messages.success(request, f"Hallazgos: {r['creados']} creados, {r['actualizados']} actualizados, "
                              f"{r['subsanados']} marcados como subsanados.")
    return redirect(conciliacion.auditoria)


def exportar(request, pk):
    conciliacion = get_object_or_404(Conciliacion, pk=pk)
    formato = request.GET.get("formato", "excel")
    if formato not in ("excel", "csv", "ambos") or not conciliacion.archivo_excel:
        raise Http404("No hay resultados para exportar")
    nombre, contenido, tipo = servicios.paquete_exportacion(conciliacion, formato)
    respuesta = HttpResponse(contenido, content_type=tipo)
    respuesta["Content-Disposition"] = f'attachment; filename="{conciliacion.pk}_{nombre}"'
    return respuesta


COLUMNAS_TABLA = ["estado", "factura", "mes", "sede", "hospital", "categoria", "tipo_codigo", "codigo",
                  "descripcion", "cantidad_fact", "cantidad_rips", "dif_cantidad", "valor_fact", "valor_rips",
                  "dif_valor", "sede_fact", "sede_rips", "mes_fact", "mes_rips", "campo_codigo_fact",
                  "campo_codigo_rips", "ubicacion_fact", "ubicacion_rips", "archivo_rips", "posible_homologacion"]

ORDENES = {"dif_desc": "-dif_valor", "dif_asc": "dif_valor", "factura": "factura", "mes": "mes",
           "valor_fact": "-valor_fact"}


def tablero_view(request, pk):
    conciliacion = get_object_or_404(Conciliacion, pk=pk)
    base = conciliacion.diferencias.all()
    params = request.GET.copy()
    params.setdefault("solo_diferencias", "1")
    qs = tablero.filtrar(base, params)
    datos = tablero.agregados(qs)
    orden = ORDENES.get(params.get("orden"), "id")
    pagina = Paginator(qs.order_by(orden, "id"), 50).get_page(params.get("pagina"))
    sin_pagina = params.copy()
    sin_pagina.pop("pagina", None)
    return render(request, "conciliacion/tablero.html", {
        "c": conciliacion,
        "opciones": tablero.opciones(base),
        "params": params,
        "query": sin_pagina.urlencode(),
        "datos": datos,
        "graficas": datos["graficas"],
        "pagina": pagina,
        "tablas": conciliacion.tablas,
    })


def exportar_filtro(request, pk):
    """Exporta las filas del tablero con los filtros aplicados (xlsx o csv)."""
    conciliacion = get_object_or_404(Conciliacion, pk=pk)
    params = request.GET.copy()
    params.setdefault("solo_diferencias", "1")
    qs = tablero.filtrar(conciliacion.diferencias.all(), params)
    df = pd.DataFrame(list(qs.values_list(*COLUMNAS_TABLA)), columns=COLUMNAS_TABLA)
    if request.GET.get("formato") == "csv":
        contenido = df.to_csv(index=False, sep=";").encode("utf-8-sig")
        respuesta = HttpResponse(contenido, content_type="text/csv")
        respuesta["Content-Disposition"] = f'attachment; filename="diferencias_filtradas_{pk}.csv"'
        return respuesta
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Diferencias filtradas", index=False)
        pd.DataFrame([(k, v) for k, v in params.items() if v], columns=["filtro", "valor"]).to_excel(
            w, sheet_name="Filtros", index=False)
    respuesta = HttpResponse(buffer.getvalue(),
                             content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    respuesta["Content-Disposition"] = f'attachment; filename="diferencias_filtradas_{pk}.xlsx"'
    return respuesta


def criterio(request):
    return render(request, "conciliacion/criterio.html", {"criterio": CRITERIO})


@require_POST
def eliminar_conciliacion(request, pk):
    conciliacion = get_object_or_404(Conciliacion, pk=pk)
    auditoria = conciliacion.auditoria
    conciliacion.delete()
    messages.success(request, "Conciliación eliminada (los hallazgos de la auditoría se conservan).")
    return redirect(auditoria)

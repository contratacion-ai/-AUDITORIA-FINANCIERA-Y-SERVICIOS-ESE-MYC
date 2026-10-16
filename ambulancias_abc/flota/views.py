from __future__ import annotations

import csv
import datetime as dt
import io
import uuid
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Case, Count, IntegerField, Max, Q, Sum, Value, When
from django.forms.models import model_to_dict
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from abc_engine.calculo import METODO_REAL, costo_recorrido, indicadores_flota

from .forms import ConsumoForm, ImportarForm, TrasladoForm
from .models import (
    Ambulancia,
    ConflictoVersion,
    Consumo,
    HallazgoCalidad,
    LoteImportacion,
    Parametro,
    PartidaCostoIndirecto,
    RegistroAuditoria,
    Sede,
    TarifaRuta,
    Traslado,
)
from .servicios import importar
from .servicios.comunes import parametros, registrar
from .servicios.costeo import METODOS, consolidar, matriz_rutas, perfiles_vigentes, precios_vigentes
from .servicios.importar import COLUMNAS_CONSUMO, calcular_km, es_por_km


def _mes_param(valor, defecto: dt.date) -> dt.date:
    try:
        return dt.date.fromisoformat(f"{valor}-01")
    except (TypeError, ValueError):
        return defecto


def mes_referencia() -> dt.date:
    """Mes con más traslados registrados (evita abrir en un mes con pocas líneas)."""
    fila = Traslado.objects.values("mes").annotate(n=Count("id")).order_by("-n", "-mes").first()
    return fila["mes"] if fila else dt.date.today().replace(day=1)


def meses_parciales(desde: dt.date, hasta: dt.date) -> list[dict]:
    """Meses del rango con menos del 30 % de las líneas del mes de mayor volumen."""
    conteo = list(Traslado.objects.values("mes").annotate(n=Count("id")))
    maximo = max((c["n"] for c in conteo), default=0)
    return [c for c in conteo if desde <= c["mes"] <= hasta and maximo and c["n"] < 0.3 * maximo]


def _rango(request):
    ultimo = mes_referencia()
    desde = _mes_param(request.GET.get("desde"), ultimo)
    hasta = _mes_param(request.GET.get("hasta"), max(desde, ultimo))
    if hasta < desde:
        desde, hasta = hasta, desde
    return desde, hasta


def _metodo(request):
    m = request.GET.get("metodo", METODO_REAL)
    return m if m in METODOS else METODO_REAL


def _opciones_filtro():
    return {
        "ambulancias": Ambulancia.objects.order_by("placa").values_list("placa", flat=True),
        "sedes": Sede.objects.order_by("nombre"),
        "ambitos": Traslado.objects.order_by("ambito").values_list("ambito", flat=True).distinct(),
        "unidades": Traslado.objects.order_by("unidad_funcional").values_list("unidad_funcional", flat=True).distinct(),
        "rutas": TarifaRuta.objects.filter(traslados__isnull=False).distinct().order_by("origen", "destino"),
        "metodos": METODOS,
    }


def _con_barras(filas, clave, ancho_max=100.0):
    maximo = max((abs(f.get(clave) or 0) for f in filas), default=0) or 1
    for f in filas:
        f["barra"] = abs(f.get(clave) or 0) / maximo * ancho_max
    return filas


# ---------------------------------------------------------------------------
# Dashboard y consolidado
# ---------------------------------------------------------------------------


@login_required
def dashboard(request):
    desde, hasta = _rango(request)
    metodo = _metodo(request)
    filtros = {k: request.GET.get(k) for k in ("placa", "sede", "ambito", "unidad_funcional", "ruta_id")}
    datos = consolidar(desde, hasta, metodo, filtros)
    pars = parametros()
    perfiles = list(perfiles_vigentes(pars).values())
    ind = indicadores_flota(perfiles, precios_vigentes(pars)) if perfiles else {}
    _con_barras(datos["por_ambulancia"], "costo")
    _con_barras(datos["por_ruta"], "ingreso")
    _con_barras(datos["por_ambito"], "ingreso")
    _con_barras(datos["por_unidad"], "ingreso")
    _con_barras(datos["por_mes"], "ingreso")
    contexto = {
        "datos": datos,
        "desde": desde,
        "hasta": hasta,
        "metodo": metodo,
        "filtros": filtros,
        "ind": ind,
        "supuestos": Parametro.objects.filter(es_supuesto=True),
        "hallazgos_altos": HallazgoCalidad.objects.filter(resuelto=False, severidad="ALTA").count(),
        "sin_ambulancia": Traslado.objects.filter(mes__gte=desde, mes__lte=hasta, ambulancia__isnull=True).count(),
        "consumos_mes": Consumo.objects.filter(mes__gte=desde, mes__lte=hasta).count(),
        "parciales": meses_parciales(desde, hasta),
        **_opciones_filtro(),
    }
    return render(request, "flota/dashboard.html", contexto)


@login_required
def consolidado(request):
    desde, hasta = _rango(request)
    if not request.GET.get("desde"):
        desde = (Traslado.objects.order_by("mes").values_list("mes", flat=True).first()) or desde
    metodo = _metodo(request)
    filtros = {k: request.GET.get(k) for k in ("placa", "sede", "ambito", "unidad_funcional", "ruta_id")}
    datos = consolidar(desde, hasta, metodo, filtros)
    if request.GET.get("formato") == "csv":
        salida = io.StringIO()
        salida.write("﻿")
        w = csv.writer(salida, delimiter=";")
        w.writerow(["mes", "traslados", "lineas", "km", "facturado", "costo_asignado", "margen", "rentabilidad_%", "costo_flota", "capacidad_no_utilizada"])
        for m in datos["por_mes"]:
            w.writerow([f"{m['mes']:%Y-%m}", round(m["traslado"], 2), round(m["linea"], 2), round(m["km"], 1), round(m["ingreso"]),
                        round(m["costo"]), round(m["margen"]), "" if m["rentabilidad"] is None else round(m["rentabilidad"], 2),
                        round(m["pool"]), round(m["no_utilizado"])])
        resp = HttpResponse(salida.getvalue(), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="consolidado_mensual.csv"'
        return resp
    # Traslados y facturación por ámbito y mes (tabla cruzada)
    cruce = (
        Traslado.objects.filter(mes__gte=desde, mes__lte=hasta)
        .values("mes", "ambito", "unidad_funcional")
        .annotate(lineas=Count("id"), traslados=Count("id", filter=Q(km_recorridos__gt=0)), facturado=Sum("valor_facturado"), km=Sum("km_recorridos"))
        .order_by("mes", "ambito", "unidad_funcional")
    )
    return render(
        request,
        "flota/consolidado.html",
        {"parciales": meses_parciales(desde, hasta), "datos": datos, "desde": desde, "hasta": hasta, "metodo": metodo, "filtros": filtros, "cruce": cruce, **_opciones_filtro()},
    )


# ---------------------------------------------------------------------------
# Ambulancias y rentabilidad por ruta
# ---------------------------------------------------------------------------


@login_required
def ambulancias(request):
    pars = parametros()
    precios = precios_vigentes(pars)
    perfiles = perfiles_vigentes(pars)
    filas = []
    for a in Ambulancia.objects.select_related("sede"):
        p = perfiles.get(a.placa)
        filas.append({"amb": a, "p": p, "rend": p.rendimiento_km_gal(precios) if p else None, "galones": p.galones_dia(precios) if p else None})
    filas.sort(key=lambda f: -(f["p"].total_dia if f["p"] else 0))
    maximo = max((f["p"].total_dia for f in filas if f["p"]), default=1)
    for f in filas:
        if f["p"]:
            f["b_trip"] = f["p"].tripulacion_dia / maximo * 100
            f["b_mant"] = f["p"].mantenimiento_dia / maximo * 100
            f["b_comb"] = f["p"].combustible_dia / maximo * 100
    ind = indicadores_flota(list(perfiles.values()), precios) if perfiles else {}
    return render(request, "flota/ambulancias.html", {"filas": filas, "ind": ind, "precios": precios})


@login_required
def ambulancia_detalle(request, placa):
    amb = get_object_or_404(Ambulancia.objects.select_related("sede"), placa=placa)
    pars = parametros()
    precios = precios_vigentes(pars)
    perfil = perfiles_vigentes(pars).get(placa)
    desde = Traslado.objects.order_by("mes").values_list("mes", flat=True).first() or dt.date.today().replace(day=1)
    hasta = Traslado.objects.aggregate(m=Max("mes"))["m"] or desde
    datos = consolidar(desde, hasta, _metodo(request), {"placa": placa})
    datos["parciales"] = meses_parciales(desde, hasta)
    consumos = Consumo.objects.filter(ambulancia=amb).select_related("creado_por")[:100]
    resumen_consumo = Consumo.objects.filter(ambulancia=amb).values("mes", "tipo").annotate(valor=Sum("valor"), cantidad=Sum("cantidad")).order_by("-mes", "tipo")
    return render(
        request,
        "flota/ambulancia_detalle.html",
        {
            "amb": amb,
            "perfil": perfil,
            "rend": perfil.rendimiento_km_gal(precios) if perfil else None,
            "datos": datos,
            "consumos": consumos,
            "resumen_consumo": resumen_consumo,
            "metodo": _metodo(request),
            "metodos": METODOS,
        },
    )


@login_required
def rentabilidad_rutas(request):
    metodo = _metodo(request)
    sede = request.GET.get("sede") or None
    placa = request.GET.get("placa") or None
    solo_origen = request.GET.get("todas") != "1"
    filas = matriz_rutas(metodo, sede, placa, solo_origen)
    rutas = {}
    for f in filas:
        r = rutas.setdefault(f["ruta"].pk, {"ruta": f["ruta"], "filas": [], "min": None, "max": None})
        r["filas"].append(f)
    for r in rutas.values():
        costos = [f["costo"] for f in r["filas"]]
        r["min"], r["max"] = min(costos), max(costos)
        r["perdida"] = sum(1 for f in r["filas"] if f["margen"] is not None and f["margen"] < 0)
    pars = parametros()
    return render(
        request,
        "flota/rentabilidad_rutas.html",
        {
            "rutas": list(rutas.values()),
            "metodo": metodo,
            "metodos": METODOS,
            "sede": sede,
            "placa": placa,
            "solo_origen": solo_origen,
            "sedes": Sede.objects.all(),
            "ambulancias": Ambulancia.objects.values_list("placa", flat=True),
            "factor": pars["factor_ida_vuelta"],
            "n_filas": len(filas),
            "n_perdida": sum(1 for f in filas if f["margen"] is not None and f["margen"] < 0),
        },
    )


# ---------------------------------------------------------------------------
# Traslados
# ---------------------------------------------------------------------------


@login_required
def traslados(request):
    qs = Traslado.objects.select_related("sede", "ambulancia", "ruta")
    g = request.GET
    if g.get("mes"):
        qs = qs.filter(mes=_mes_param(g["mes"], dt.date.today()))
    if g.get("sede"):
        qs = qs.filter(sede__codigo=g["sede"])
    if g.get("placa"):
        qs = qs.filter(ambulancia__placa=g["placa"])
    if g.get("ambito"):
        qs = qs.filter(ambito=g["ambito"])
    if g.get("sin_ambulancia"):
        qs = qs.filter(ambulancia__isnull=True)
    if g.get("observaciones"):
        qs = qs.exclude(observaciones_calidad="")
    if g.get("q"):
        qs = qs.filter(Q(numero_factura__icontains=g["q"]) | Q(nro_ingreso__icontains=g["q"]) | Q(prefactura__icontains=g["q"]))
    totales = qs.aggregate(n=Count("id"), facturado=Sum("valor_facturado"), km=Sum("km_recorridos"))
    pagina = Paginator(qs, 50).get_page(g.get("pagina"))
    meses = Traslado.objects.order_by("-mes").values_list("mes", flat=True).distinct()
    consulta = g.copy()
    consulta.pop("pagina", None)
    return render(
        request,
        "flota/traslados.html",
        {"pagina": pagina, "totales": totales, "meses": meses, "g": g, "consulta": consulta.urlencode(),
         "ambulancias_sede": Ambulancia.objects.exclude(estado=Ambulancia.FUERA).select_related("sede"), **_opciones_filtro()},
    )


def _guardar_traslado(request, form, nuevo: bool):
    t = form.save(commit=False)
    if nuevo:
        t.llave_externa = f"MANUAL|{uuid.uuid4()}"
        t.origen_registro = Traslado.MANUAL
    km = form.cleaned_data.get("km_recorridos")
    if km in (None, ""):
        pars = parametros()
        por_km = es_por_km(t.cantidad, t.valor_unitario, None, pars)
        if t.ruta_id and t.cantidad <= 1 and not por_km:
            t.km_recorridos = t.ruta.km * pars["factor_ida_vuelta"]
        else:
            t.km_recorridos = calcular_km(t.cantidad, por_km, pars)
    if t.ruta_id and "ruta" in form.changed_data:
        t.ruta_inferida = False
    t.guardar_con_version(form.cleaned_data.get("version_leida") or t.version, request.user)
    registrar(request.user, t, "crear" if nuevo else "editar", {c: str(form.cleaned_data.get(c)) for c in form.changed_data})
    return t


@login_required
@permission_required("flota.add_traslado", raise_exception=True)
def traslado_nuevo(request):
    form = TrasladoForm(request.POST or None, initial={"fecha_servicio": dt.date.today(), "ambito": "Urgencias", "cantidad": 1})
    if request.method == "POST" and form.is_valid():
        _guardar_traslado(request, form, True)
        messages.success(request, "Traslado registrado")
        return redirect("flota:traslados")
    return render(request, "flota/formulario.html", {"form": form, "titulo": "Registrar traslado", "volver": "flota:traslados"})


@login_required
@permission_required("flota.change_traslado", raise_exception=True)
def traslado_editar(request, pk):
    t = get_object_or_404(Traslado, pk=pk)
    form = TrasladoForm(request.POST or None, instance=t, initial={"km_recorridos": t.km_recorridos})
    if request.method == "POST" and form.is_valid():
        try:
            _guardar_traslado(request, form, False)
        except ConflictoVersion as e:
            messages.error(request, str(e))
            return redirect("flota:traslado_editar", pk=pk)
        messages.success(request, "Traslado actualizado")
        return redirect(request.GET.get("volver") or "flota:traslados")
    return render(
        request,
        "flota/formulario.html",
        {"form": form, "titulo": f"Editar traslado #{t.pk}", "obj": t, "volver": "flota:traslados",
         "bitacora": RegistroAuditoria.objects.filter(modelo="Traslado", objeto_id=str(t.pk))[:20]},
    )


@login_required
@require_POST
@permission_required("flota.change_traslado", raise_exception=True)
def traslados_asignar(request):
    """Asigna una ambulancia a varios traslados seleccionados (misma sede)."""
    ids = [int(i) for i in request.POST.getlist("seleccion") if i.isdigit()]
    placa = request.POST.get("ambulancia")
    amb = Ambulancia.objects.filter(placa=placa).first() if placa else None
    ok = omitidos = 0
    for t in Traslado.objects.filter(pk__in=ids):
        if amb and amb.sede_id != t.sede_id:
            omitidos += 1
            continue
        anterior = t.ambulancia.placa if t.ambulancia_id else None
        t.ambulancia = amb
        try:
            t.guardar_con_version(t.version, request.user)
        except ConflictoVersion:
            omitidos += 1
            continue
        registrar(request.user, t, "asignar_ambulancia", {"antes": anterior, "despues": placa})
        ok += 1
    messages.info(request, f"{ok} traslados actualizados; {omitidos} omitidos (sede distinta o editados por otro usuario).")
    return redirect(request.POST.get("volver") or "flota:traslados")


# ---------------------------------------------------------------------------
# Consumos
# ---------------------------------------------------------------------------


@login_required
def consumos(request):
    qs = Consumo.objects.select_related("ambulancia", "creado_por")
    g = request.GET
    if g.get("placa"):
        qs = qs.filter(ambulancia__placa=g["placa"])
    if g.get("tipo"):
        qs = qs.filter(tipo=g["tipo"])
    if g.get("mes"):
        qs = qs.filter(mes=_mes_param(g["mes"], dt.date.today()))
    totales = qs.aggregate(n=Count("id"), valor=Sum("valor"))
    por_tipo = qs.values("tipo").annotate(valor=Sum("valor"), cantidad=Sum("cantidad")).order_by("-valor")
    pagina = Paginator(qs, 50).get_page(g.get("pagina"))
    consulta = g.copy()
    consulta.pop("pagina", None)
    return render(
        request,
        "flota/consumos.html",
        {"pagina": pagina, "totales": totales, "por_tipo": por_tipo, "g": g, "tipos": Consumo.TIPOS, "consulta": consulta.urlencode(),
         "ambulancias": Ambulancia.objects.values_list("placa", flat=True)},
    )


def _form_consumo(request, consumo=None):
    nuevo = consumo is None
    form = ConsumoForm(request.POST or None, instance=consumo, initial={"fecha": dt.date.today()} if nuevo else None)
    if request.method == "POST" and form.is_valid():
        c = form.save(commit=False)
        try:
            c.guardar_con_version(form.cleaned_data.get("version_leida") or c.version, request.user)
        except ConflictoVersion as e:
            messages.error(request, str(e))
            return redirect("flota:consumo_editar", pk=consumo.pk)
        registrar(request.user, c, "crear" if nuevo else "editar", {k: str(v) for k, v in model_to_dict(c, fields=form.changed_data).items()})
        messages.success(request, "Consumo guardado")
        return redirect("flota:consumos")
    return render(
        request,
        "flota/formulario.html",
        {"form": form, "titulo": "Registrar consumo" if nuevo else f"Editar consumo #{consumo.pk}", "volver": "flota:consumos", "obj": consumo,
         "bitacora": RegistroAuditoria.objects.filter(modelo="Consumo", objeto_id=str(consumo.pk))[:20] if consumo else None},
    )


@login_required
@permission_required("flota.add_consumo", raise_exception=True)
def consumo_nuevo(request):
    return _form_consumo(request)


@login_required
@permission_required("flota.change_consumo", raise_exception=True)
def consumo_editar(request, pk):
    return _form_consumo(request, get_object_or_404(Consumo, pk=pk))


@login_required
def plantilla_consumos(request):
    salida = io.StringIO()
    salida.write("﻿")
    w = csv.writer(salida, delimiter=";")
    w.writerow(COLUMNAS_CONSUMO)
    w.writerow(["OEU846", "2026-10-01", "COMBUSTIBLE", "Tanqueo", "12.5", "gal", "295625", "152340", "VALE-0001", "Estación Ciudad El Sol"])
    resp = HttpResponse(salida.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="plantilla_consumos.csv"'
    return resp


# ---------------------------------------------------------------------------
# Importación, calidad de datos y parámetros
# ---------------------------------------------------------------------------


@login_required
@permission_required("flota.add_traslado", raise_exception=True)
def importar_vista(request):
    form = ImportarForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        tipo = form.cleaned_data["tipo"]
        archivo = form.cleaned_data["archivo"]
        if tipo in ("tarifas", "ceco") and not request.user.has_perm("flota.change_tarifaruta"):
            messages.error(request, "Solo un administrador puede importar tarifas o costos del centro de costos.")
            return redirect("flota:importar")
        try:
            if tipo == "facturacion":
                lote = importar.importar_facturacion(archivo, request.user, archivo.name)
            elif tipo == "rips":
                lote = importar.importar_rips(archivo.read(), request.user, archivo.name)
            elif tipo == "consumos":
                lote = importar.importar_consumos_csv(archivo, request.user, archivo.name)
            elif tipo == "tarifas":
                lote = importar.importar_tarifas(archivo, request.user, form.cleaned_data.get("vigencia") or 2026, archivo.name)
            else:
                lote = importar.importar_costos_ceco(archivo, request.user, archivo.name)
        except (ValueError, KeyError) as e:
            messages.error(request, f"No se pudo leer el archivo: {e}")
            return redirect("flota:importar")
        registrar(request.user, lote, "importar", {"tipo": tipo, "archivo": archivo.name})
        messages.success(
            request,
            f"{lote.nuevos} nuevos, {lote.actualizados} actualizados/conciliados, {lote.omitidos} omitidos, "
            f"{lote.hallazgos} hallazgos de calidad nuevos. {lote.resumen}",
        )
        return redirect("flota:importar")
    return render(request, "flota/importar.html", {"form": form, "lotes": LoteImportacion.objects.select_related("usuario")[:30]})


@login_required
def calidad(request):
    orden_sev = Case(
        When(severidad="ALTA", then=Value(0)), When(severidad="MEDIA", then=Value(1)), default=Value(2), output_field=IntegerField()
    )
    qs = HallazgoCalidad.objects.annotate(o=orden_sev).order_by("resuelto", "o", "fuente", "ubicacion")
    g = request.GET
    if g.get("fuente"):
        qs = qs.filter(fuente=g["fuente"])
    if g.get("severidad"):
        qs = qs.filter(severidad=g["severidad"])
    if g.get("estado") != "todos":
        qs = qs.filter(resuelto=False)
    resumen = HallazgoCalidad.objects.filter(resuelto=False).annotate(o=orden_sev).values("fuente", "severidad", "o").annotate(n=Count("id")).order_by("fuente", "o")
    return render(
        request,
        "flota/calidad.html",
        {"hallazgos": qs[:500], "resumen": resumen, "g": g,
         "fuentes": HallazgoCalidad.objects.order_by("fuente").values_list("fuente", flat=True).distinct()},
    )


@login_required
@require_POST
@permission_required("flota.change_hallazgocalidad", raise_exception=True)
def hallazgo_resolver(request, pk):
    h = get_object_or_404(HallazgoCalidad, pk=pk)
    h.resuelto = not h.resuelto
    h.nota_resolucion = request.POST.get("nota", "")[:300]
    h.save()
    registrar(request.user, h, "resolver" if h.resuelto else "reabrir", {"nota": h.nota_resolucion})
    return redirect(request.POST.get("volver") or "flota:calidad")


@login_required
def parametros_vista(request):
    pars = parametros()
    perfiles = perfiles_vigentes(pars)
    precios = precios_vigentes(pars)
    ejemplo = None
    if "OKZ365" in perfiles:
        ejemplo = costo_recorrido(perfiles["OKZ365"], 194 * float(pars["factor_ida_vuelta"]), precios)
    return render(
        request,
        "flota/parametros.html",
        {
            "parametros": Parametro.objects.all(),
            "partidas": PartidaCostoIndirecto.objects.all(),
            "total_partidas": PartidaCostoIndirecto.objects.aggregate(v=Sum("valor_mensual"))["v"] or Decimal(0),
            "incluidas": PartidaCostoIndirecto.objects.filter(incluir_en_abc=True).aggregate(v=Sum("valor_mensual"))["v"] or Decimal(0),
            "ejemplo": ejemplo,
        },
    )


@login_required
def bitacora(request):
    return render(request, "flota/bitacora.html", {"registros": RegistroAuditoria.objects.select_related("usuario")[:300]})

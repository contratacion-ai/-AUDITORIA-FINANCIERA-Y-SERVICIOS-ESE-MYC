"""Centro de cargas mensuales, RIPS por hospital, tablas de consumo,
recorridos por sede y facturación por ámbito."""

from __future__ import annotations

import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Count, Max, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from abc_engine.calculo import METODO_REAL

from .exportar import FORMATO_NUM, FORMATO_PESOS, respuesta_xlsx
from .forms import CargaConsumosForm, CargaFacturacionForm, CargaRipsForm
from .models import APH, CATEGORIAS_SERVICIO, Ambulancia, Consumo, LoteImportacion, Sede, Traslado, sumar_meses
from .servicios import importar
from .servicios.comunes import registrar
from .servicios.auditoria_rips import SITUACIONES, auditoria_rips
from .servicios.costeo import METODOS, facturacion_por_ambito, recorridos_por_sede, tabla_consumos
from .servicios.importar import COLUMNAS_CONSUMO


def _mes(valor, defecto):
    try:
        return dt.date.fromisoformat(f"{valor}-01")
    except (TypeError, ValueError):
        return defecto


def _mes_ref():
    return Traslado.objects.aggregate(m=Max("mes"))["m"] or dt.date.today().replace(day=1)


def _rango(request, meses_atras=0):
    ref = _mes_ref()
    hasta = _mes(request.GET.get("hasta"), ref)
    desde = _mes(request.GET.get("desde"), sumar_meses(hasta, -meses_atras))
    return (desde, hasta) if desde <= hasta else (hasta, desde)


# ---------------------------------------------------------------------------
# Centro de cargas
# ---------------------------------------------------------------------------


@login_required
def cargas(request):
    hasta = _mes(request.GET.get("hasta"), max(_mes_ref(), dt.date.today().replace(day=1)))
    meses = [sumar_meses(hasta, -i) for i in range(5, -1, -1)]
    sedes = list(Sede.objects.filter(activa=True).order_by("nombre"))
    fact = {
        (f["sede_id"], f["mes"]): f
        for f in Traslado.objects.filter(mes__in=meses).exclude(origen_registro=Traslado.RIPS)
        .values("sede_id", "mes").annotate(n=Count("id"), valor=Sum("valor_facturado"))
    }
    rips = {
        (f["sede_id"], f["mes"]): f["n"]
        for f in LoteImportacion.objects.filter(tipo="RIPS", mes__in=meses).values("sede_id", "mes").annotate(n=Count("id"))
    }
    aph = {
        (f["sede_id"], f["mes"]): f["n"]
        for f in Traslado.objects.filter(mes__in=meses, categoria=APH).values("sede_id", "mes").annotate(n=Count("id"))
    }
    estado = []
    for s in sedes:
        celdas = []
        for m in meses:
            f = fact.get((s.pk, m))
            celdas.append({"mes": m, "lineas": f["n"] if f else 0, "valor": f["valor"] if f else 0,
                           "rips": rips.get((s.pk, m), 0), "aph": aph.get((s.pk, m), 0)})
        estado.append({"sede": s, "celdas": celdas})
    consumos = {
        (f["tipo"], f["mes"]): f["n"]
        for f in Consumo.objects.filter(mes__in=meses).values("tipo", "mes").annotate(n=Count("id"))
    }
    tablas = []
    for clave, (titulo, tipos, _) in Consumo.TABLAS.items():
        tablas.append({"clave": clave, "titulo": titulo,
                       "celdas": [sum(consumos.get((t, m), 0) for t in tipos) for m in meses]})
    return render(request, "flota/cargas.html", {
        "meses": meses, "estado": estado, "tablas": tablas, "hasta": hasta,
        "lotes": LoteImportacion.objects.select_related("usuario", "sede")[:25],
    })


@login_required
@permission_required("flota.add_traslado", raise_exception=True)
def cargar_facturacion(request):
    form = CargaFacturacionForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        archivo = form.cleaned_data["archivo"]
        try:
            lote = importar.importar_facturacion(archivo, request.user, archivo.name, form.cleaned_data["mes"], form.cleaned_data["sede"])
        except (ValueError, KeyError) as e:
            messages.error(request, f"No se pudo leer el archivo: {e}")
            return redirect("flota:cargar_facturacion")
        registrar(request.user, lote, "importar", {"tipo": "facturacion", "archivo": archivo.name})
        messages.success(request, f"Facturación cargada: {lote.nuevos} nuevas, {lote.actualizados} actualizadas, "
                                  f"{lote.omitidos} omitidas, {lote.hallazgos} hallazgos. {lote.resumen}")
        url = reverse("flota:facturacion_ambito") + f"?desde={form.cleaned_data['mes']:%Y-%m}&hasta={form.cleaned_data['mes']:%Y-%m}"
        if form.cleaned_data["sede"]:
            url += f"&sede={form.cleaned_data['sede'].codigo}"
        return redirect(url)
    return render(request, "flota/cargar.html", {
        "form": form, "titulo": "Cargar facturación del mes (por ámbito)",
        "ayuda": "Reporte de traslados facturados del HIS (formato 'TRASLADOS FACTURACION POR AMBITO'). "
                 "Se toman el ámbito y la unidad funcional de cada línea. Volver a cargar el mismo archivo actualiza, no duplica. "
                 "No se guardan nombres, teléfonos ni direcciones de los pacientes.",
    })


@login_required
@permission_required("flota.add_traslado", raise_exception=True)
def cargar_rips(request):
    form = CargaRipsForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        archivos = [(f.name, f.read()) for f in form.cleaned_data["archivos"]]
        nombre = ", ".join(n for n, _ in archivos)[:250]
        lote = importar.importar_rips(archivos, request.user, nombre, form.cleaned_data["mes"], form.cleaned_data["sede"])
        registrar(request.user, lote, "importar", {"tipo": "rips", "archivo": nombre})
        messages.success(request, f"RIPS cargados. {lote.resumen}")
        return redirect("flota:rips_detalle", pk=lote.pk)
    return render(request, "flota/cargar.html", {
        "form": form, "titulo": "Cargar RIPS (un mes, varios meses o consolidado por hospital)",
        "ayuda": "Se extraen las líneas de traslados asistenciales y de atención prehospitalaria (APH). Puede subir el ZIP "
                 "completo del hospital con sus carpetas por mes, o un consolidado de varios hospitales: el mes se toma de la "
                 "fecha de servicio y el hospital del prefijo de la factura (FHJD, FHSL...) o del código de prestador. "
                 "Las líneas que ya llegaron en la facturación del HIS se concilian (no se duplican) y las que cambian de valor "
                 "se marcan; las demás se crean como «solo en RIPS». Una factura repetida en dos carpetas se toma una vez. "
                 "Después revise la Auditoría RIPS vs facturación. Para clasificar como APH un código específico, regístrelo en "
                 "Administración › Códigos de traslado con categoría APH.",
        "multipart": True,
    })


@login_required
def rips_cargas(request):
    lotes = LoteImportacion.objects.filter(tipo="RIPS").select_related("sede", "usuario")
    if request.GET.get("sede"):
        lotes = lotes.filter(sede__codigo=request.GET["sede"])
    return render(request, "flota/rips_cargas.html", {"lotes": lotes[:200], "sedes": Sede.objects.all(), "g": request.GET})


@login_required
def auditoria_rips_vista(request):
    desde, hasta = _rango(request, meses_atras=11)
    sede = request.GET.get("sede") or None
    categoria = request.GET.get("categoria") or None
    datos = auditoria_rips(desde, hasta, sede, categoria, limite_detalle=50000 if request.GET.get("formato") == "xlsx" else 1000)
    if request.GET.get("formato") == "xlsx":
        meses = [[d["sede"].nombre if d["sede"] else "", d["mes"], d["estado"], d["his_lineas"], d["his_valor"], d["rips_lineas"],
                  d["rips_valor"], d["ambos"], d["diferencia"], d["dif_valor"], d["solo_his"], d["solo_his_valor"], d["solo_rips"],
                  d["solo_rips_valor"], d["sin_factura"], d["rips_no_cargado"], d["his_no_cargado"]] for d in datos["por_mes"]]
        rutas = [[d["sede"].nombre, f"{d['ruta'].origen} → {d['ruta'].destino}" if d["ruta"] else "Sin ruta / tarifa básica / APH",
                  d["his_lineas"], d["his_valor"], d["rips_lineas"], d["rips_valor"], d["ambos"], d["diferencia"], d["dif_valor"],
                  d["solo_his"], d["solo_his_valor"], d["solo_rips"], d["solo_rips_valor"]] for d in datos["por_ruta"]]
        det = [[x["texto"], x["t"].sede.nombre, x["t"].mes, x["t"].fecha_servicio, x["t"].categoria, x["t"].numero_factura,
                x["t"].codigo_cups, x["t"].descripcion, float(x["t"].cantidad), float(x["t"].valor_facturado),
                float(x["t"].valor_rips) if x["t"].valor_rips is not None else None, str(x["t"].ruta) if x["t"].ruta_id else "",
                x["t"].ambito, x["t"].observaciones_calidad] for x in datos["detalle"]]
        P = FORMATO_PESOS
        return respuesta_xlsx(f"auditoria_rips_{desde:%Y-%m}_{hasta:%Y-%m}", [
            ("Por mes y hospital", ["Hospital", "Mes", "Estado", "Líneas facturadas HIS", "Valor HIS", "Líneas RIPS", "Valor RIPS",
                                    "Coinciden", "Con diferencia de valor", "Diferencia (RIPS − HIS)", "Facturado sin RIPS",
                                    "Valor facturado sin RIPS", "En RIPS sin facturación", "Valor RIPS sin facturación",
                                    "Prefacturas sin número", "RIPS no cargado", "Facturación no cargada"],
             meses, {4: P, 6: P, 9: P, 11: P, 13: P}),
            ("Por recorrido", ["Hospital", "Recorrido", "Líneas HIS", "Valor HIS", "Líneas RIPS", "Valor RIPS", "Coinciden",
                               "Con diferencia", "Diferencia (RIPS − HIS)", "Facturado sin RIPS", "Valor", "En RIPS sin facturación", "Valor"],
             rutas, {3: P, 5: P, 8: P, 10: P, 12: P}),
            ("Diferencias", ["Situación", "Hospital", "Mes", "Fecha servicio", "Categoría", "Factura", "Código", "Descripción", "Cantidad",
                             "Valor HIS", "Valor RIPS", "Ruta", "Ámbito", "Observaciones"],
             det, {9: P, 10: P}),
        ])
    return render(request, "flota/auditoria_rips.html", {
        **datos, "desde": desde, "hasta": hasta, "sede": sede or "", "categoria": categoria or "",
        "sedes": Sede.objects.filter(activa=True), "categorias": CATEGORIAS_SERVICIO,
    })


@login_required
def rips_detalle(request, pk):
    lote = get_object_or_404(LoteImportacion.objects.select_related("sede", "usuario"), pk=pk, tipo="RIPS")
    qs = Traslado.objects.filter(lote_rips=lote).select_related("sede", "ambulancia", "ruta").order_by("categoria", "fecha_servicio", "id")
    if request.GET.get("categoria"):
        qs = qs.filter(categoria=request.GET["categoria"])
    resumen = list(qs.values("categoria", "origen_registro").annotate(n=Count("id"), valor=Sum("valor_facturado"), km=Sum("km_recorridos")))
    if request.GET.get("formato") == "xlsx":
        filas = [[t.categoria, t.fecha_servicio, t.sede.nombre, t.numero_factura, t.rips_consecutivo, t.codigo_cups, t.descripcion,
                  float(t.cantidad), float(t.valor_facturado), t.ambito, t.unidad_funcional,
                  "Conciliado con HIS" if t.origen_registro == Traslado.HIS else "Nuevo desde RIPS",
                  t.ambulancia.placa if t.ambulancia_id else "", str(t.ruta) if t.ruta_id else "", float(t.km_recorridos),
                  t.observaciones_calidad] for t in qs]
        return respuesta_xlsx(
            f"rips_{lote.sede.codigo if lote.sede else 'sede'}_{lote.mes:%Y-%m}" if lote.mes else f"rips_{lote.pk}",
            [("Traslados y APH", ["Categoría", "Fecha servicio", "Hospital", "Factura", "Consecutivo RIPS", "Código", "Descripción",
                                  "Cantidad", "Valor", "Ámbito", "Unidad funcional", "Origen", "Placa", "Ruta", "Km", "Observaciones"],
              filas, {8: FORMATO_PESOS})],
        )
    return render(request, "flota/rips_detalle.html", {
        "lote": lote, "traslados": qs[:1000], "n": qs.count(), "resumen": resumen,
        "categorias": CATEGORIAS_SERVICIO, "g": request.GET,
    })


# ---------------------------------------------------------------------------
# Tablas de consumo
# ---------------------------------------------------------------------------


@login_required
def consumos_tabla(request, clave):
    if clave not in Consumo.TABLAS:
        raise Http404
    desde, hasta = _rango(request, meses_atras=5)
    placa = request.GET.get("placa") or None
    datos = tabla_consumos(clave, desde, hasta, placa)
    _, tipos, _ = Consumo.TABLAS[clave]
    detalle = Consumo.objects.filter(tipo__in=tipos, mes__gte=desde, mes__lte=hasta).select_related("ambulancia", "creado_por")
    if placa:
        detalle = detalle.filter(ambulancia__placa=placa)
    if request.GET.get("formato") == "xlsx":
        enc = ["Placa"] + [f"{m:%Y-%m}" for m in datos["meses"]] + ["Total", "Cantidad"]
        if datos["con_estandar"]:
            enc += ["Estándar del estudio", "Diferencia"]
        filas = []
        for f in datos["filas"]:
            fila = [f["placa"], *f["valores"], f["total"], f["cantidad"]]
            if datos["con_estandar"]:
                fila += [f["estandar"], f["diferencia"]]
            filas.append(fila)
        det = [[c.fecha, c.ambulancia.placa, c.get_tipo_display(), c.descripcion, float(c.cantidad), c.unidad, float(c.valor),
                c.meses_prorrateo, c.odometro, c.documento_soporte, c.proveedor] for c in detalle]
        formatos = {i: FORMATO_PESOS for i in range(1, len(enc))}
        return respuesta_xlsx(f"consumos_{clave}_{desde:%Y-%m}_{hasta:%Y-%m}", [
            (datos["titulo"], enc, filas, formatos),
            ("Detalle", ["Fecha", "Placa", "Tipo", "Descripción", "Cantidad", "Unidad", "Valor", "Meses prorrateo",
                         "Odómetro", "Soporte", "Proveedor"], det, {6: FORMATO_PESOS, 4: FORMATO_NUM}),
        ])
    return render(request, "flota/consumos_tabla.html", {
        "clave": clave, "datos": datos, "detalle": detalle[:300], "desde": desde, "hasta": hasta, "placa": placa,
        "tablas": Consumo.TABLAS, "ambulancias": Ambulancia.objects.values_list("placa", flat=True),
        "tipo_inicial": tipos[0], "form_carga": CargaConsumosForm(),
    })


@login_required
@permission_required("flota.add_consumo", raise_exception=True)
def cargar_consumos(request, clave):
    if clave not in Consumo.TABLAS or request.method != "POST":
        raise Http404
    form = CargaConsumosForm(request.POST, request.FILES)
    if form.is_valid():
        archivo = form.cleaned_data["archivo"]
        _, tipos, _ = Consumo.TABLAS[clave]
        lote = importar.importar_consumos(archivo, request.user, archivo.name, tipos[0])
        registrar(request.user, lote, "importar", {"tipo": f"consumos_{clave}", "archivo": archivo.name})
        nivel = messages.success if not lote.omitidos else messages.warning
        nivel(request, f"{lote.nuevos} consumos nuevos, {lote.actualizados} actualizados, {lote.omitidos} filas omitidas"
                       + (" (ver Calidad de datos)." if lote.omitidos else "."))
    else:
        messages.error(request, "Seleccione un archivo .xlsx o .csv")
    return redirect("flota:consumos_tabla", clave=clave)


@login_required
def plantilla_consumos_tabla(request, clave):
    if clave not in Consumo.TABLAS:
        raise Http404
    titulo, tipos, unidad = Consumo.TABLAS[clave]
    ejemplos = {
        "combustible": ["OEU846", dt.date(2026, 10, 1), tipos[0], "Tanqueo", 12.5, "gal", 295625, 1, 152340, "VALE-0001", "Estación Ciudad El Sol"],
        "mantenimiento": ["OKZ365", dt.date(2026, 10, 3), tipos[0], "Cambio de pastillas de freno", 1, "und", 850000, 1, 210500, "OT-120", "Taller"],
        "personal": ["OKZ365", dt.date(2026, 10, 31), tipos[0], "Nómina tripulación octubre", 1, "mes", 13754000, 1, None, "NOM-2026-10", ""],
        "seguros": ["OEU846", dt.date(2026, 3, 15), tipos[0], "SOAT 2026", 1, "und", 1200000, 12, None, "POL-123", "Aseguradora"],
        "imprevistos": ["OYX018", dt.date(2026, 10, 9), tipos[0], "Grúa por varada", 1, "und", 450000, 1, 98000, "FAC-77", "Grúas"],
    }
    fila = ejemplos.get(clave, ["OEU846", dt.date(2026, 10, 1), tipos[0], "", 1, unidad, 0, 1, None, "", ""])
    enc = [c for c in COLUMNAS_CONSUMO if c != "meses_prorrateo"]
    enc.insert(7, "meses_prorrateo")
    return respuesta_xlsx(f"plantilla_{clave}", [(titulo, enc, [fila], {6: FORMATO_PESOS})])


# ---------------------------------------------------------------------------
# Recorridos por sede y facturación por ámbito
# ---------------------------------------------------------------------------


@login_required
def recorridos_sede(request):
    desde, hasta = _rango(request)
    metodo = request.GET.get("metodo", METODO_REAL)
    metodo = metodo if metodo in METODOS else METODO_REAL
    categoria = request.GET.get("categoria") or None
    datos = recorridos_por_sede(desde, hasta, metodo, request.GET.get("sede") or None, categoria)
    if request.GET.get("formato") == "xlsx":
        filas, resumen = [], []
        for b in datos:
            for f in b["filas"]:
                filas.append([b["sede"].nombre, f["ruta"].destino, f["km"], f["tarifa"], f["costo_estandar"], f["margen_estandar"],
                              f["rent_estandar"], f["traslados"], f["km_conducidos"], f["facturado"], f["costo_real"],
                              f["margen_real"], f["rent_real"]])
            o = b["otras"]
            filas.append([b["sede"].nombre, "Sin ruta del anexo / tarifa básica / APH", None, None, None, None, None,
                          o["traslados"], o["km_conducidos"], o["facturado"], o["costo_real"], o["margen_real"], o["rent_real"]])
            t = b["total"]
            resumen.append([b["sede"].nombre, b["n_ambulancias"], t["traslados"], t["km_conducidos"], t["facturado"], t["costo_real"],
                            t["margen_real"], t["rent_real"], t["pool"], t["no_utilizado"], t["resultado_con_capacidad"]])
        return respuesta_xlsx(f"recorridos_por_sede_{desde:%Y-%m}_{hasta:%Y-%m}", [
            ("Resumen por sede", ["Sede", "Ambulancias", "Traslados", "Km conducidos", "Facturado", "Costo ABC", "Margen",
                                  "Rentabilidad %", "Costo flota sede", "Capacidad no utilizada", "Resultado con capacidad"],
             resumen, {4: FORMATO_PESOS, 5: FORMATO_PESOS, 6: FORMATO_PESOS, 7: FORMATO_NUM, 8: FORMATO_PESOS, 9: FORMATO_PESOS, 10: FORMATO_PESOS}),
            ("Recorridos", ["Sede", "Destino", "Km", "Tarifa 2026", "Costo estándar", "Margen estándar", "Rent. estándar %",
                            "Traslados", "Km conducidos", "Facturado", "Costo ABC", "Margen", "Rentabilidad %"],
             filas, {3: FORMATO_PESOS, 4: FORMATO_PESOS, 5: FORMATO_PESOS, 6: FORMATO_NUM, 9: FORMATO_PESOS, 10: FORMATO_PESOS, 11: FORMATO_PESOS, 12: FORMATO_NUM}),
        ])
    return render(request, "flota/recorridos_sede.html", {
        "datos": datos, "desde": desde, "hasta": hasta, "metodo": metodo, "metodos": METODOS, "categoria": categoria,
        "categorias": CATEGORIAS_SERVICIO, "sedes": Sede.objects.filter(activa=True), "sede": request.GET.get("sede", ""),
    })


@login_required
def facturacion_ambito(request):
    desde, hasta = _rango(request)
    sede = request.GET.get("sede") or None
    filas = facturacion_por_ambito(desde, hasta, sede)
    por_ambito = {}
    for f in filas:
        a = por_ambito.setdefault(f["ambito"] or "SIN ÁMBITO", {"ambito": f["ambito"] or "SIN ÁMBITO", "lineas": 0, "traslados": 0, "facturado": 0})
        a["lineas"] += f["lineas"]
        a["traslados"] += f["traslados"]
        a["facturado"] += f["facturado"] or 0
    if request.GET.get("formato") == "xlsx":
        return respuesta_xlsx(f"facturacion_por_ambito_{desde:%Y-%m}_{hasta:%Y-%m}", [(
            "Facturación por ámbito",
            ["Mes", "Hospital", "Ámbito", "Unidad funcional", "Categoría", "Líneas", "Traslados con recorrido", "Km", "Facturado",
             "Sin número de factura", "Conciliadas con RIPS"],
            [[f"{f['mes']:%Y-%m}", f["sede__nombre"], f["ambito"], f["unidad_funcional"], f["categoria"], f["lineas"], f["traslados"],
              float(f["km"] or 0), float(f["facturado"] or 0), f["sin_factura"], f["conciliados"]] for f in filas],
            {8: FORMATO_PESOS},
        )])
    total = {"lineas": sum(f["lineas"] for f in filas), "traslados": sum(f["traslados"] for f in filas),
             "facturado": sum((f["facturado"] or 0) for f in filas)}
    return render(request, "flota/facturacion_ambito.html", {
        "filas": filas, "por_ambito": sorted(por_ambito.values(), key=lambda a: -a["facturado"]), "total": total,
        "desde": desde, "hasta": hasta, "sedes": Sede.objects.filter(activa=True), "sede": sede or "",
    })

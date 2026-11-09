import io
from collections import defaultdict
from decimal import Decimal

import pandas as pd
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import AuditoriaForm, HallazgoGestionForm, HallazgoManualForm
from .models import Auditoria, Hallazgo


def inicio(request):
    auditorias = Auditoria.objects.all()
    resumen = []
    for a in auditorias:
        abiertos = a.hallazgos.filter(estado__in=[Hallazgo.Estado.ABIERTO, Hallazgo.Estado.EN_GESTION])
        resumen.append({
            "a": a,
            "conciliaciones": a.conciliaciones.count(),
            "hallazgos": a.hallazgos.count(),
            "abiertos": abiertos.count(),
            "valor_abierto": sum((abs(v) for v in abiertos.values_list("valor_diferencia", flat=True)), Decimal(0)),
        })
    return render(request, "auditoria/inicio.html", {"resumen": resumen})


def crear(request):
    form = AuditoriaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        auditoria = form.save()
        messages.success(request, "Auditoría creada.")
        return redirect(auditoria)
    return render(request, "auditoria/form.html", {"form": form, "titulo": "Nueva auditoría"})


def editar(request, pk):
    auditoria = get_object_or_404(Auditoria, pk=pk)
    form = AuditoriaForm(request.POST or None, instance=auditoria)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Auditoría actualizada.")
        return redirect(auditoria)
    return render(request, "auditoria/form.html", {"form": form, "titulo": f"Editar – {auditoria}"})


def _hallazgos_filtrados(auditoria, params):
    qs = auditoria.hallazgos.all()
    for campo in ("estado", "prioridad", "tipo", "sede", "hospital", "mes"):
        if params.get(campo):
            qs = qs.filter(**{campo: params[campo]})
    if params.get("factura"):
        qs = qs.filter(factura__icontains=params["factura"])
    return qs


def detalle(request, pk):
    auditoria = get_object_or_404(Auditoria, pk=pk)
    qs = _hallazgos_filtrados(auditoria, request.GET)
    por_tipo = defaultdict(lambda: [0, Decimal(0)])
    for tipo, valor in qs.values_list("tipo", "valor_diferencia"):
        por_tipo[tipo][0] += 1
        por_tipo[tipo][1] += valor
    base = auditoria.hallazgos.all()
    opciones = {c: sorted(v for v in base.values_list(c, flat=True).distinct() if v)
                for c in ("tipo", "sede", "hospital", "mes")}
    params = request.GET.copy()
    params.pop("pagina", None)
    return render(request, "auditoria/detalle.html", {
        "a": auditoria,
        "conciliaciones": auditoria.conciliaciones.all(),
        "hallazgos": Paginator(qs, 50).get_page(request.GET.get("pagina")),
        "por_tipo": sorted(por_tipo.items()),
        "opciones": opciones,
        "estados": Hallazgo.Estado.choices,
        "prioridades": Hallazgo.Prioridad.choices,
        "params": request.GET,
        "query": params.urlencode(),
        "form_manual": HallazgoManualForm(),
    })


def hallazgo(request, pk):
    h = get_object_or_404(Hallazgo, pk=pk)
    form = HallazgoGestionForm(request.POST or None, instance=h)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Hallazgo actualizado.")
        return redirect(h.auditoria)
    return render(request, "auditoria/hallazgo.html", {"h": h, "form": form})


def hallazgo_manual(request, pk):
    auditoria = get_object_or_404(Auditoria, pk=pk)
    form = HallazgoManualForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        h = form.save(commit=False)
        h.auditoria = auditoria
        h.origen = "MANUAL"
        h.save()
        messages.success(request, "Hallazgo registrado.")
        return redirect(auditoria)
    return render(request, "auditoria/form.html", {"form": form, "titulo": f"Nuevo hallazgo – {auditoria}"})


def exportar_hallazgos(request, pk):
    auditoria = get_object_or_404(Auditoria, pk=pk)
    columnas = ["id", "estado", "prioridad", "tipo", "titulo", "factura", "sede", "hospital", "mes", "servicio",
                "codigos", "valor_diferencia", "cantidad_diferencia", "responsable", "accion_recomendada",
                "observaciones", "origen", "conciliacion__nombre", "creado", "actualizado"]
    qs = _hallazgos_filtrados(auditoria, request.GET)
    df = pd.DataFrame(list(qs.values_list(*columnas)), columns=columnas)
    for c in ("creado", "actualizado"):
        df[c] = df[c].astype(str)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as w:
        pd.DataFrame([
            ("Auditoría", auditoria.nombre), ("Entidad", auditoria.entidad), ("Responsable", auditoria.responsable),
            ("Periodo", f"{auditoria.periodo_inicio or ''} a {auditoria.periodo_fin or ''}"),
            ("Hallazgos", len(df)),
            ("Valor neto de diferencias", sum(df["valor_diferencia"], Decimal(0)) if len(df) else Decimal(0)),
        ], columns=["dato", "valor"]).to_excel(w, sheet_name="Resumen", index=False)
        df.to_excel(w, sheet_name="Hallazgos", index=False)
    respuesta = HttpResponse(buffer.getvalue(),
                             content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    respuesta["Content-Disposition"] = f'attachment; filename="hallazgos_auditoria_{pk}.xlsx"'
    return respuesta

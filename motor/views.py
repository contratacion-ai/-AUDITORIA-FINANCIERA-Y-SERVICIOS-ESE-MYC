from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from analisis import consultas as Q
from analisis.exportar import matriz_excel, tabla_excel
from cargas.models import Carga, MovimientoContable
from core import permisos
from core.mixins import InstitucionMixin
from modelo.models import CentroCosto

from .models import Asignacion, CostoCups, Corrida
from .motor import ErrorMotor, ejecutar
from .trazabilidad import trazar_centro

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class MotorView(InstitucionMixin, View):
    permiso_escritura = permisos.EJECUTAR_MOTOR

    def get(self, request):
        periodo = self.periodo_seleccionado()
        ctx = {"periodos": self.periodos(), "periodo": periodo}
        if periodo:
            ctx["corridas"] = Corrida.objects.filter(periodo=periodo).select_related("usuario")
            ctx["cargas"] = (Carga.objects.filter(periodo=periodo, estado=Carga.Estado.APLICADA).values("tipo_archivo")
                             .annotate(n=Sum("filas_validas"), v=Sum("valor_total")).order_by("tipo_archivo"))
            ctx["movimientos"] = MovimientoContable.objects.filter(periodo=periodo).aggregate(v=Sum("valor"))["v"]
        return render(request, "motor/panel.html", ctx)

    def post(self, request):
        periodo = get_object_or_404(self.periodos(), pk=request.POST.get("periodo"))
        try:
            corrida = ejecutar(periodo, request.user)
        except ErrorMotor as exc:
            messages.error(request, f"No se pudo ejecutar: {exc}")
            return redirect(f"{request.path}?periodo={periodo.pk}")
        if corrida.resumen["conservacion_ok"]:
            messages.success(request, f"Distribuciones 1ª–4ª y costeo CUPS completados en {corrida.resumen['segundos']} s.")
        else:
            messages.error(request, "La corrida no cuadra (entrada ≠ finales + pendientes). Revise el detalle.")
        return redirect("motor:corrida", pk=corrida.pk)


class CorridaView(InstitucionMixin, View):
    def get(self, request, pk):
        c = get_object_or_404(Corrida.objects.de(self.institucion).select_related("periodo"), pk=pk)
        pendientes = c.pendientes.select_related("recurso", "centro", "movimiento__carga").order_by("distribucion", "-valor")
        return render(request, "motor/corrida.html", {"c": c, "pendientes": pendientes[:500], "n_pendientes": pendientes.count()})


class MatrizView(InstitucionMixin, View):
    def get(self, request):
        periodo = self.periodo_seleccionado()
        etapa = int(request.GET["etapa"]) if request.GET.get("etapa") in {"1", "2", "3", "4"} else 4
        corrida = Q.corrida_vigente(periodo)
        recursos, filas, totales = Q.matriz(self.institucion, corrida, etapa)
        if request.GET.get("solo_con_valor", "1") == "1":
            filas = [f for f in filas if f["total"] or f["distribuido"]]
        if request.GET.get("exportar"):
            resp = HttpResponse(matriz_excel(self.institucion, periodo, recursos, filas, totales, etapa), content_type=XLSX)
            resp["Content-Disposition"] = f'attachment; filename="distribucion_{etapa}_{periodo.etiqueta if periodo else ""}.xlsx"'
            return resp
        return render(request, "motor/matriz.html", {
            "periodos": self.periodos(), "periodo": periodo, "corrida": corrida, "etapa": etapa, "recursos": recursos,
            "filas": filas, "totales": totales, "extra": [x for d, x in ((2, "De estructura"), (3, "De servicios"),
                                                                       (4, "De intermedios")) if d <= etapa]})


class TrazabilidadCentroView(InstitucionMixin, View):
    def get(self, request, corrida_pk, centro_pk):
        corrida = get_object_or_404(Corrida.objects.de(self.institucion), pk=corrida_pk)
        centro = get_object_or_404(CentroCosto.objects.de(self.institucion), pk=centro_pk)
        filas = trazar_centro(corrida, centro)
        recibidas = (Asignacion.objects.filter(corrida=corrida, centro_destino=centro)
                     .values("distribucion", "centro_origen__codigo", "centro_origen__nombre", "inductor__codigo", "metodo")
                     .annotate(v=Sum("valor"), cant=Sum("cantidad_inductor")).order_by("distribucion", "-v"))
        entregadas = (Asignacion.objects.filter(corrida=corrida, centro_origen=centro)
                      .values("distribucion", "centro_destino__codigo", "centro_destino__nombre", "inductor__codigo")
                      .annotate(v=Sum("valor")).order_by("-v"))
        if request.GET.get("exportar"):
            contenido = tabla_excel("TRAZABILIDAD", f"{centro} · corrida #{corrida.pk} · {corrida.periodo}",
                                    ["VALOR", "RECURSO", "RUTA", "ARCHIVO", "HOJA", "FILA", "CUENTA", "PERIODO"],
                                    [[f["valor"], f["recurso"].codigo, f["ruta"], f["archivo"], f["hoja"], f["fila"], f["cuenta"],
                                      str(f["periodo"])] for f in filas])
            resp = HttpResponse(contenido, content_type=XLSX)
            resp["Content-Disposition"] = f'attachment; filename="trazabilidad_{centro.codigo}.xlsx"'
            return resp
        total = sum((f["valor"] for f in filas), Decimal(0))
        return render(request, "motor/trazabilidad.html", {
            "corrida": corrida, "centro": centro, "filas": filas[:300], "n_filas": len(filas), "total": total,
            "recibidas": recibidas, "entregadas": entregadas})


class AsignacionesView(InstitucionMixin, View):
    """Listado auditable de asignaciones con su tasa (costo por unidad de inductor)."""

    def get(self, request, corrida_pk):
        corrida = get_object_or_404(Corrida.objects.de(self.institucion), pk=corrida_pk)
        qs = Asignacion.objects.filter(corrida=corrida).select_related(
            "recurso", "centro_origen", "centro_destino", "inductor", "movimiento__carga", "regla")
        for campo in ("distribucion", "recurso__codigo", "centro_destino__codigo", "centro_origen__codigo"):
            if request.GET.get(campo):
                qs = qs.filter(**{campo: request.GET[campo]})
        if request.GET.get("exportar"):
            filas = [[a.distribucion, a.recurso.codigo, a.centro_origen.codigo if a.centro_origen else "", a.centro_destino.codigo,
                      a.get_metodo_display(), a.inductor.codigo if a.inductor else "", a.base, a.cantidad_inductor, a.total_inductor,
                      a.tasa, a.ajuste_conciliacion, a.valor, a.movimiento.carga.nombre_original if a.movimiento else "",
                      a.movimiento.fila if a.movimiento else "", a.regla.nivel if a.regla else ""] for a in qs[:200000]]
            contenido = tabla_excel("ASIGNACIONES", f"Corrida #{corrida.pk} · {corrida.periodo}",
                                    ["DIST", "RECURSO", "ORIGEN", "DESTINO", "MÉTODO", "INDUCTOR", "BASE", "CANTIDAD", "TOTAL INDUCTOR",
                                     "TASA", "AJUSTE CONCILIACIÓN", "VALOR", "ARCHIVO", "FILA", "NIVEL REGLA"], filas)
            resp = HttpResponse(contenido, content_type=XLSX)
            resp["Content-Disposition"] = f'attachment; filename="asignaciones_{corrida.pk}.xlsx"'
            return resp
        return render(request, "motor/asignaciones.html", {"corrida": corrida,
                                                           "pagina": Paginator(qs.order_by("distribucion", "pk"), 100).get_page(request.GET.get("page"))})


class CostosCupsView(InstitucionMixin, View):
    def get(self, request):
        periodo = self.periodo_seleccionado()
        corrida = Q.corrida_vigente(periodo)
        qs = CostoCups.objects.filter(corrida=corrida).select_related("cups", "centro").order_by("centro__codigo", "cups__codigo") \
            if corrida else CostoCups.objects.none()
        q = request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(cups__codigo__icontains=q) | qs.filter(cups__descripcion__icontains=q) | qs.filter(centro__codigo__iexact=q)
        if request.GET.get("exportar"):
            contenido = tabla_excel("PRODUCCION CUPS", f"{self.institucion} · {periodo}",
                                    ["CÓDIGO CECO", "CENTRO", "CUPS", "DESCRIPCIÓN", "CANTIDAD", "PESO", "COSTO TOTAL", "COSTO UNITARIO",
                                     "CP", "IH", "CG", "EC", "FUENTE PRODUCCIÓN"],
                                    [[c.centro.codigo, c.centro.nombre, c.cups.codigo, c.cups.descripcion, c.cantidad, c.peso_relativo,
                                      c.costo_total, c.costo_unitario] + [Decimal(c.costo_por_elemento.get(e, 0)) for e in ("CP", "IH", "CG", "EC")]
                                     + [c.fuente_produccion] for c in qs])
            resp = HttpResponse(contenido, content_type=XLSX)
            resp["Content-Disposition"] = 'attachment; filename="costos_cups.xlsx"'
            return resp
        return render(request, "motor/cups.html", {"periodos": self.periodos(), "periodo": periodo, "corrida": corrida, "q": q,
                                                   "pagina": Paginator(qs, 100).get_page(request.GET.get("page"))})


class TrazabilidadCupsView(InstitucionMixin, View):
    def get(self, request, pk):
        cc = get_object_or_404(CostoCups.objects.de(self.institucion).select_related("corrida", "centro", "cups"), pk=pk)
        total_centro = CostoCups.objects.filter(corrida=cc.corrida, centro=cc.centro).aggregate(s=Sum("costo_total"))["s"]
        factor = cc.costo_total / total_centro if total_centro else Decimal(0)
        filas = trazar_centro(cc.corrida, cc.centro, factor=factor, limite=300)
        hermanos = CostoCups.objects.filter(corrida=cc.corrida, centro=cc.centro).select_related("cups").order_by("-costo_total")
        return render(request, "motor/trazabilidad_cups.html", {"cc": cc, "filas": filas, "factor": factor, "hermanos": hermanos,
                                                                "total_centro": total_centro})

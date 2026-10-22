from decimal import Decimal

from django import forms
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views import View

from cargas.models import Facturacion
from core import permisos
from core.auditoria import foto, registrar
from core.mixins import InstitucionMixin
from modelo.models import Ambito, CentroCosto, Clase, ConfiguracionInforme, Eps, NotaTecnicaItem, UnidadNegocio
from motor.models import CostoCups

from . import consultas as Q
from . import cruces as X
from .exportar import informe_excel, tabla_excel
from .informe import construir

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CERO = Decimal("0")


def _f(v):
    return float(v or 0)


class FiltrosMixin(InstitucionMixin):
    def filtros(self):
        r = self.request.GET
        periodo = self.periodo_seleccionado()
        comp = None
        if r.get("comparar") == "0":
            comp = None
        elif r.get("comparar"):
            comp = self.periodos().filter(pk=r["comparar"]).first()
        elif periodo:
            # Por defecto: mismo mes del año anterior; si no existe, el periodo inmediatamente anterior.
            comp = self.periodos().filter(anio=periodo.anio - 1, mes=periodo.mes).first()
            if comp is None:
                anteriores = [p for p in self.periodos() if (p.anio, p.mes) < (periodo.anio, periodo.mes)]
                comp = anteriores[0] if anteriores else None
        return {
            "periodo": periodo, "comparar": comp,
            "unidad": UnidadNegocio.objects.de(self.institucion).filter(pk=r.get("unidad")).first() if r.get("unidad") else None,
            "eps": Eps.objects.de(self.institucion).filter(pk=r.get("eps")).first() if r.get("eps") else None,
            "ambito": Ambito.objects.de(self.institucion).filter(pk=r.get("ambito")).first() if r.get("ambito") else None,
        }

    def opciones(self):
        return {"periodos": self.periodos(), "unidades": UnidadNegocio.objects.de(self.institucion),
                "lista_eps": Eps.objects.de(self.institucion), "ambitos": Ambito.objects.de(self.institucion)}


class TableroView(FiltrosMixin, View):
    def get(self, request):
        f = self.filtros()
        periodo, comp = f["periodo"], f["comparar"]
        corrida, corrida_comp = Q.corrida_vigente(periodo), Q.corrida_vigente(comp)
        ctx = {**self.opciones(), **f, "corrida": corrida, "corrida_comp": corrida_comp}
        if corrida is None:
            return render(request, "analisis/tablero.html", ctx)
        elementos, total, total_comp = Q.tabla_elementos(self.institucion, corrida, corrida_comp)
        costos, costos_comp = Q.costo_centros(corrida), Q.costo_centros(corrida_comp)
        centros = CentroCosto.objects.de(self.institucion).select_related("unidad_negocio")
        if f["unidad"]:
            centros = centros.filter(unidad_negocio=f["unidad"])
        finales = [(c, costos.get(c.pk, {}).get("final", CERO), costos_comp.get(c.pk, {}).get("final", CERO))
                   for c in centros if c.clase == Clase.FINALES]
        finales = [(c, v, a, Q.var(v, a)[1]) for c, v, a in finales if v or a]
        finales.sort(key=lambda x: -x[1])
        un_final, _ = Q.tabla_unidades(self.institucion, corrida, corrida_comp, "final")
        un_directo, _ = Q.tabla_unidades(self.institucion, corrida, corrida_comp, "directo")
        fac = Facturacion.objects.de(self.institucion).filter(periodo=periodo)
        if f["eps"]:
            fac = fac.filter(eps=f["eps"])
        if f["ambito"]:
            fac = fac.filter(ambito=f["ambito"])
        unit = Q.costo_unitario_cups(corrida)
        facturado = sum((x.valor_total for x in fac), CERO)
        costo_facturado = sum((unit[x.cups_id]["costo_unitario"] * x.cantidad for x in fac if x.cups_id in unit), CERO)
        por_eps = X.margen_por_eps(self.institucion, periodo, corrida)
        por_ambito = X.facturacion_por_ambito(self.institucion, periodo, corrida, f["eps"].pk if f["eps"] else None)
        cups_qs = CostoCups.objects.filter(corrida=corrida).select_related("cups", "centro")
        if f["unidad"]:
            cups_qs = cups_qs.filter(centro__unidad_negocio=f["unidad"])
        tarifas = {}
        items = NotaTecnicaItem.objects.de(self.institucion).filter(
            nota__in=X.notas_vigentes(self.institucion, periodo), nota__modalidad__in=["EVENTO", "PAQUETE"])
        if f["eps"]:
            items = items.filter(nota__eps=f["eps"])
        for it in items:
            tarifas.setdefault(it.cups_id, []).append(it.tarifa)
        top_cups = []
        for c in cups_qs.order_by("-costo_total")[:15]:
            t = tarifas.get(c.cups_id)
            prom = sum(t) / len(t) if t else None
            top_cups.append({"c": c, "tarifa": prom, "margen": (prom - c.costo_unitario) if prom is not None else None})
        serie = Q.serie_mensual(self.institucion)
        dif, pct = Q.var(total, total_comp)
        personal = next((e for e in elementos if e["elemento"].codigo == "CP"), None)
        graficos = {
            "elementos": {"labels": [e["elemento"].nombre for e in elementos], "data": [_f(e["actual"]) for e in elementos]},
            "unidades": {"labels": [x["unidad"].codigo for x in un_final[Clase.FINALES]],
                         "nombres": [x["unidad"].nombre for x in un_final[Clase.FINALES]],
                         "actual": [_f(x["actual"]) for x in un_final[Clase.FINALES]],
                         "anterior": [_f(x["anterior"]) for x in un_final[Clase.FINALES]]},
            "centros": {"labels": [x[0].codigo for x in finales[:15]], "nombres": [x[0].nombre for x in finales[:15]],
                        "actual": [_f(x[1]) for x in finales[:15]], "anterior": [_f(x[2]) for x in finales[:15]]},
            "serie": {"labels": [s["periodo"] for s in serie], "data": [_f(s["total"]) for s in serie]},
            "eps": {"labels": [e["eps"] for e in por_eps[:10]], "facturado": [_f(e["facturado"]) for e in por_eps[:10]],
                    "costo": [_f(e["costo"]) for e in por_eps[:10]]},
            "ambitos": {"labels": [a["ambito"] for a in por_ambito], "facturado": [_f(a["facturado"]) for a in por_ambito],
                        "costo": [_f(a["costo"]) for a in por_ambito]},
        }
        ctx.update({
            "total": total, "total_comp": total_comp, "dif": dif, "pct": pct, "elementos": elementos,
            "pct_personal": personal["part_actual"] if personal else None,
            "costo_finales": sum((x[1] for x in finales), CERO), "facturado": facturado, "costo_facturado": costo_facturado,
            "margen": facturado - costo_facturado, "margen_pct": ((facturado - costo_facturado) / facturado * 100) if facturado else None,
            "finales": finales[:25], "un_directo": un_directo, "por_eps": por_eps, "por_ambito": por_ambito, "top_cups": top_cups,
            "graficos": graficos,
        })
        return render(request, "analisis/tablero.html", ctx)


class CrucesView(FiltrosMixin, View):
    pestanas = ("ambito", "tarifas", "capita", "inconsistencias")

    def get(self, request):
        f = self.filtros()
        periodo = f["periodo"]
        corrida = Q.corrida_vigente(periodo)
        pestana = request.GET.get("vista") if request.GET.get("vista") in self.pestanas else "ambito"
        ctx = {**self.opciones(), **f, "corrida": corrida, "vista": pestana}
        if periodo is None:
            return render(request, "analisis/cruces.html", ctx)
        eps_id = f["eps"].pk if f["eps"] else None
        if pestana == "ambito":
            ctx["filas"] = X.facturacion_por_ambito(self.institucion, periodo, corrida, eps_id)
        elif pestana == "tarifas":
            ctx["filas"] = X.costo_vs_tarifa(self.institucion, periodo, corrida, eps_id)
        elif pestana == "capita":
            ctx["filas"] = X.contratos_capita(self.institucion, periodo, corrida)
        else:
            lista = X.inconsistencias(self.institucion, periodo, corrida)
            tipo = request.GET.get("tipo")
            ctx["resumen"] = X.resumen_inconsistencias(lista)
            ctx["filas"] = [i for i in lista if not tipo or i.tipo == tipo][:1000]
            ctx["tipo"] = tipo
            ctx["total_incons"] = len(lista)
            if request.GET.get("exportar"):
                contenido = tabla_excel("INCONSISTENCIAS", f"{self.institucion} · {periodo}",
                                        ["SEVERIDAD", "TIPO", "DESCRIPCIÓN", "FACTURA", "CUPS", "EPS", "CENTRO", "VALOR", "REFERENCIA"],
                                        [[i.severidad, i.tipo, i.descripcion, i.factura, i.cups, i.eps, i.centro, i.valor, i.referencia]
                                         for i in lista])
                resp = HttpResponse(contenido, content_type=XLSX)
                resp["Content-Disposition"] = f'attachment; filename="inconsistencias_{periodo.etiqueta}.xlsx"'
                return resp
        if request.GET.get("exportar") and pestana == "tarifas":
            contenido = tabla_excel("COSTO VS TARIFA", f"{self.institucion} · {periodo}",
                                    ["EPS", "CONTRATO", "MODALIDAD", "CUPS", "DESCRIPCIÓN", "TARIFA", "COSTO UNITARIO ABC", "MARGEN UNITARIO",
                                     "MARGEN %", "CANTIDAD FACTURADA", "VALOR FACTURADO", "RESULTADO"],
                                    [[x["eps"].nombre, x["nota"].contrato, x["modalidad"], x["cups"].codigo, x["cups"].descripcion,
                                      x["tarifa"], x["costo_unitario"], x["margen_unitario"],
                                      float(x["margen_pct"]) / 100 if x["margen_pct"] is not None else None,
                                      x["cantidad_facturada"], x["valor_facturado"], x["resultado"]] for x in ctx["filas"]])
            resp = HttpResponse(contenido, content_type=XLSX)
            resp["Content-Disposition"] = f'attachment; filename="costo_vs_tarifa_{periodo.etiqueta}.xlsx"'
            return resp
        return render(request, "analisis/cruces.html", ctx)


class InformeView(FiltrosMixin, View):
    def get(self, request):
        f = self.filtros()
        if f["periodo"] is None:
            messages.info(request, "Cree un periodo y ejecute el motor para generar el informe.")
            return redirect("core:periodos")
        datos = construir(self.institucion, f["periodo"], f["comparar"])
        if request.GET.get("exportar"):
            registrar("EXPORTAR_INFORME", descripcion=f"Informe {f['periodo']} vs {f['comparar']}")
            resp = HttpResponse(informe_excel(datos), content_type=XLSX)
            resp["Content-Disposition"] = f'attachment; filename="Informe_ABC_{f["periodo"].etiqueta}.xlsx"'
            return resp
        return render(request, "analisis/informe.html", {**self.opciones(), **f, "d": datos})


class ConfigInformeForm(forms.ModelForm):
    class Meta:
        model = ConfiguracionInforme
        fields = ["elaborado_por", "cargo", "dirigido_a", "introduccion", "conclusiones", "recomendaciones", "umbral_variacion",
                  "recursos_por_sede"]
        widgets = {k: forms.Textarea(attrs={"rows": 5}) for k in ("introduccion", "conclusiones", "recomendaciones")}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for c in self.fields.values():
            c.widget.attrs.setdefault("class", "form-control")


class ConfigInformeView(InstitucionMixin, View):
    permiso_escritura = permisos.EDITAR_INFORME

    def get(self, request):
        cfg, _ = ConfiguracionInforme.objects.get_or_create(institucion=self.institucion)
        return render(request, "analisis/config_informe.html", {"form": ConfigInformeForm(instance=cfg)})

    def post(self, request):
        cfg, _ = ConfiguracionInforme.objects.get_or_create(institucion=self.institucion)
        antes = foto(cfg)
        form = ConfigInformeForm(request.POST, instance=cfg)
        if form.is_valid():
            form.save()
            registrar("EDITAR", objeto=cfg, detalle={"antes": antes, "despues": foto(cfg)})
            messages.success(request, "Configuración del informe guardada.")
            return redirect("analisis:informe")
        return render(request, "analisis/config_informe.html", {"form": form})

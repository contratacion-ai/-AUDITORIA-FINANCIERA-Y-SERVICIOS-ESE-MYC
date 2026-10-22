"""Parametrización: catálogos editables (CRUD genérico con aislamiento por institución) y valores de inductores."""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from core import permisos
from core.auditoria import foto, registrar
from core.mixins import InstitucionMixin
from core.models import Periodo

from . import models as M
from .catalogo_base import cargar_catalogo_base


@dataclass
class Catalogo:
    slug: str
    modelo: type
    titulo: str
    nivel: int
    campos: list
    columnas: list
    buscar: list
    ayuda: str = ""


CATALOGOS = {c.slug: c for c in [
    Catalogo("elementos", M.Elemento, "Elementos del costo", 1, ["codigo", "nombre", "orden", "activo"],
             ["codigo", "nombre", "orden"], ["codigo", "nombre"],
             "Agrupación de recursos homogéneos (Res. DDC-000002/2014, Anexo Técnico 2)."),
    Catalogo("recursos", M.Recurso, "Recursos", 1,
             ["codigo", "nombre", "elemento", "orden", "inductor_convenido", "tipo_mano_obra", "clases_permitidas", "activo"],
             ["codigo", "nombre", "elemento", "tipo_mano_obra", "clases_permitidas"], ["codigo", "nombre"],
             "Conceptos que consumen las actividades; se asignan directa o indirectamente a los centros de costos."),
    Catalogo("cuentas", M.CuentaRecurso, "Cuentas contables - Recursos", 1, ["prefijo", "recurso", "excluir", "descripcion"],
             ["prefijo", "recurso", "excluir", "descripcion"], ["prefijo", "descripcion", "recurso__codigo"],
             "Se aplica el prefijo de cuenta más largo que coincida con la cuenta del movimiento."),
    Catalogo("unidades", M.UnidadNegocio, "Unidades de negocio", 2, ["codigo", "nombre", "clase", "orden", "activo"],
             ["codigo", "nombre", "clase"], ["codigo", "nombre"],
             "Actividades homogéneas con una misma finalidad hospitalaria. La clase define el orden de distribución."),
    Catalogo("sedes", M.Sede, "Sedes", 2, ["codigo", "nombre", "municipio", "activo"], ["codigo", "nombre", "municipio"],
             ["codigo", "nombre", "municipio"]),
    Catalogo("centros", M.CentroCosto, "Centros de costos", 2, ["codigo", "nombre", "unidad_negocio", "clase", "sede", "activo"],
             ["codigo", "nombre", "unidad_negocio", "clase", "sede", "activo"], ["codigo", "nombre"],
             "Unidades de producción donde se registran costos. Estructura y servicios = indirectos; intermedios y finales = directos."),
    Catalogo("homologacion", M.HomologacionCentro, "Homologación centro de costos", 2, ["codigo_origen", "centro"],
             ["codigo_origen", "centro"], ["codigo_origen", "centro__codigo", "centro__nombre"],
             "Código o nombre que usa la contabilidad/nómina de la institución → centro del modelo."),
    Catalogo("inductores", M.Inductor, "Inductores", 3, ["codigo", "nombre", "tipo", "unidad_medida", "descripcion", "activo"],
             ["codigo", "nombre", "tipo", "unidad_medida"], ["codigo", "nombre"],
             "Criterios de distribución (Anexo Técnico 3)."),
    Catalogo("reglas", M.ReglaDistribucion, "Reglas de distribución", 3,
             ["distribucion", "recurso", "unidad_negocio", "centro_origen", "periodo", "inductor", "clases_destino", "misma_sede",
              "observacion", "activo"],
             ["distribucion", "recurso", "unidad_negocio", "centro_origen", "periodo", "inductor", "clases_destino", "misma_sede", "nivel"],
             ["recurso__codigo", "centro_origen__codigo", "inductor__codigo", "observacion"],
             "Se aplica la regla más específica: N4 periodo > N3 centro origen > N2 unidad de negocio > N1 general."),
    Catalogo("mano-obra", M.DistribucionManoObra, "Distribución mano de obra (Empleado / OPS)", 3,
             ["tipo", "documento", "nombre", "centro", "porcentaje", "periodo"],
             ["tipo", "documento", "nombre", "centro", "porcentaje", "periodo"], ["documento", "nombre", "centro__codigo"],
             "Porcentaje de cada persona por centro de costos. Debe sumar 100 por persona y periodo."),
    Catalogo("cups", M.Cups, "Catálogo CUPS", 3, ["codigo", "descripcion", "grupo", "activo"], ["codigo", "descripcion", "grupo"],
             ["codigo", "descripcion"]),
    Catalogo("cups-centros", M.CupsCentroCosto, "CUPS por centro de costos", 3, ["cups", "centro", "ambito", "peso_relativo"],
             ["cups", "centro", "ambito", "peso_relativo"], ["cups__codigo", "cups__descripcion", "centro__codigo"],
             "Centro que produce cada CUPS y su peso relativo (UVR, minutos). Asocia RIPS y facturación a centros."),
    Catalogo("eps", M.Eps, "EPS / pagadores", 3, ["codigo", "nit", "nombre", "regimen", "activo"], ["codigo", "nit", "nombre", "regimen"],
             ["codigo", "nit", "nombre"]),
    Catalogo("ambitos", M.Ambito, "Ámbitos de facturación", 3, ["codigo", "nombre"], ["codigo", "nombre"], ["codigo", "nombre"],
             "Ámbitos del software de facturación (360): ambulatorio, hospitalario, urgencias…"),
    Catalogo("ambitos-homologacion", M.AmbitoHomologacion, "Homologación de ámbitos", 3, ["valor_origen", "ambito"],
             ["valor_origen", "ambito"], ["valor_origen"]),
    Catalogo("notas-tecnicas", M.NotaTecnica, "Notas técnicas EPS", 3,
             ["eps", "contrato", "modalidad", "vigencia_desde", "vigencia_hasta", "manual_tarifario", "ajuste_porcentaje",
              "valor_mensual", "poblacion", "condiciones"],
             ["eps", "contrato", "modalidad", "vigencia_desde", "vigencia_hasta", "valor_mensual"], ["contrato", "eps__nombre"]),
    Catalogo("notas-items", M.NotaTecnicaItem, "Ítems de notas técnicas (tarifas)", 3,
             ["nota", "cups", "tarifa", "frecuencia_esperada", "ambito", "observaciones"],
             ["nota", "cups", "tarifa", "frecuencia_esperada", "ambito"], ["cups__codigo", "nota__contrato", "nota__eps__nombre"]),
    Catalogo("indicadores", M.IndicadorReferencia, "Indicadores de referencia (benchmarking)", 3,
             ["codigo", "nombre", "formula", "minimo", "maximo", "es_valor", "fuente", "accion_recomendada", "orden"],
             ["codigo", "nombre", "formula", "minimo", "maximo"], ["codigo", "nombre"]),
]}

NIVELES = {
    1: ("Nivel 1 · Estructura normativa (general)",
        "Elementos, recursos y su relación con el plan de cuentas. Común al sector (Res. DDC-000002/2014)."),
    2: ("Nivel 2 · Estructura institucional",
        "Unidades de negocio, sedes, centros de costos y su homologación con la contabilidad de la institución."),
    3: ("Nivel 3 · Criterios de distribución y cruces",
        "Inductores, reglas de distribución 1ª–4ª, % mano de obra, CUPS, EPS, ámbitos y notas técnicas."),
    4: ("Nivel 4 · Valores del periodo (particular)",
        "Cantidades de inductores del mes, producción, % de mano de obra y reglas de excepción solo para un periodo."),
}

FK_PERIODICOS = {"periodo"}


def formulario_para(cat, institucion):
    class Formulario(forms.ModelForm):
        class Meta:
            model = cat.modelo
            fields = cat.campos
            widgets = {"vigencia_desde": forms.DateInput(attrs={"type": "date"}),
                       "vigencia_hasta": forms.DateInput(attrs={"type": "date"}),
                       "condiciones": forms.Textarea(attrs={"rows": 3}), "descripcion": forms.Textarea(attrs={"rows": 2})}

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.instance.institucion = institucion
            for nombre, campo in self.fields.items():
                if isinstance(campo, forms.ModelChoiceField):
                    qs = campo.queryset.model.objects.filter(institucion=institucion)
                    if campo.queryset.model is Periodo:
                        qs = qs.order_by("-anio", "-mes")
                    campo.queryset = qs
                css = "form-check-input" if isinstance(campo.widget, forms.CheckboxInput) else "form-control form-control-sm"
                if isinstance(campo.widget, forms.Select):
                    css = "form-select form-select-sm"
                campo.widget.attrs.setdefault("class", css)

        def _get_validation_exclusions(self):
            # Las restricciones únicas incluyen la institución, que no es un campo del formulario.
            exclusiones = super()._get_validation_exclusions()
            exclusiones.discard("institucion")
            return exclusiones

    return Formulario


def obtener_catalogo(slug):
    if slug not in CATALOGOS:
        raise Http404("Catálogo inexistente")
    return CATALOGOS[slug]


def valor_columna(obj, columna):
    v = getattr(obj, columna, "")
    if hasattr(obj, f"get_{columna}_display"):
        v = getattr(obj, f"get_{columna}_display")()
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Sí" if v else "No"
    return v


class ParametrizacionView(InstitucionMixin, View):
    def get(self, request):
        grupos = []
        for nivel, (titulo, desc) in NIVELES.items():
            items = [(c, c.modelo.objects.de(self.institucion).count()) for c in CATALOGOS.values() if c.nivel == nivel]
            grupos.append({"nivel": nivel, "titulo": titulo, "descripcion": desc, "items": items})
        vacio = not M.Recurso.objects.de(self.institucion).exists()
        return render(request, "modelo/parametrizacion.html", {"grupos": grupos, "vacio": vacio})

    def post(self, request):
        if not self.puede(permisos.PARAMETRIZAR):
            raise PermissionDenied
        resultado = cargar_catalogo_base(self.institucion, usuario=request.user)
        messages.success(request, "Catálogo base Res. 002/2014 cargado: " + ", ".join(f"{k} {v}" for k, v in resultado.items()))
        return redirect("modelo:parametrizacion")


class CatalogoListaView(InstitucionMixin, View):
    def get(self, request, slug):
        cat = obtener_catalogo(slug)
        qs = cat.modelo.objects.de(self.institucion)
        q = request.GET.get("q", "").strip()
        if q:
            filtro = Q()
            for campo in cat.buscar:
                filtro |= Q(**{f"{campo}__icontains": q})
            qs = qs.filter(filtro)
        if request.GET.get("exportar"):
            from analisis.exportar import tabla_excel

            filas = [[str(valor_columna(o, c)) for c in cat.columnas] for o in qs[:100000]]
            etiquetas = [self._etiqueta(cat, c) for c in cat.columnas]
            resp = HttpResponse(tabla_excel(cat.titulo.upper(), self.institucion.nombre, etiquetas, filas),
                                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            resp["Content-Disposition"] = f'attachment; filename="{slug}.xlsx"'
            return resp
        if not qs.ordered:
            qs = qs.order_by("pk")
        pagina = Paginator(qs, 50).get_page(request.GET.get("page"))
        filas = [(o, [valor_columna(o, c) for c in cat.columnas]) for o in pagina]
        extra = {}
        if slug == "mano-obra":
            sumas = (qs.values("tipo", "documento", "periodo__anio", "periodo__mes").annotate(t=Sum("porcentaje"))
                     .exclude(t=Decimal("100")))
            extra["descuadres"] = list(sumas[:50])
        return render(request, "modelo/catalogo_lista.html", {
            "cat": cat, "pagina": pagina, "filas": filas, "q": q, "nivel": NIVELES[cat.nivel][0],
            "encabezados": [self._etiqueta(cat, c) for c in cat.columnas], **extra})

    @staticmethod
    def _etiqueta(cat, columna):
        try:
            return cat.modelo._meta.get_field(columna).verbose_name.capitalize()
        except Exception:
            return columna.replace("_", " ").capitalize()


class CatalogoEditarView(InstitucionMixin, View):
    permiso_escritura = permisos.PARAMETRIZAR

    def _objeto(self, cat, pk):
        if pk is None:
            return None
        return get_object_or_404(cat.modelo.objects.de(self.institucion), pk=pk)

    def get(self, request, slug, pk=None):
        cat = obtener_catalogo(slug)
        obj = self._objeto(cat, pk)
        form = formulario_para(cat, self.institucion)(instance=obj)
        return render(request, "modelo/catalogo_form.html", {"cat": cat, "form": form, "obj": obj,
                                                             "solo_lectura": not self.puede(permisos.PARAMETRIZAR)})

    def post(self, request, slug, pk=None):
        cat = obtener_catalogo(slug)
        obj = self._objeto(cat, pk)
        if request.POST.get("eliminar") and obj is not None:
            antes = foto(obj)
            try:
                obj.delete()
            except ProtectedError:
                messages.error(request, "No se puede eliminar: hay información que depende de este registro. Desactívelo.")
                return redirect("modelo:catalogo_editar", slug=slug, pk=pk)
            registrar("ELIMINAR", objeto=None, descripcion=f"{cat.titulo}: {antes}"[:300],
                      detalle={"modelo": cat.modelo._meta.label, "antes": antes})
            messages.success(request, "Registro eliminado.")
            return redirect("modelo:catalogo", slug=slug)
        antes = foto(obj) if obj else None
        form = formulario_para(cat, self.institucion)(request.POST, instance=obj)
        if form.is_valid():
            try:
                with transaction.atomic():
                    nuevo = form.save()
            except IntegrityError:
                form.add_error(None, "Ya existe un registro con esos datos.")
            else:
                despues = foto(nuevo)
                cambios = {k: {"antes": antes.get(k), "despues": v} for k, v in despues.items() if antes and antes.get(k) != v}
                registrar("EDITAR" if antes else "CREAR", objeto=nuevo,
                          detalle={"cambios": cambios} if antes else {"valores": despues})
                messages.success(request, "Registro guardado.")
                return redirect("modelo:catalogo", slug=slug)
        return render(request, "modelo/catalogo_form.html", {"cat": cat, "form": form, "obj": obj})


class ValoresInductorView(InstitucionMixin, View):
    """Captura de cantidades del inductor por centro (manual: Gastos generales → Distribución), con % automático."""

    permiso_escritura = permisos.PARAMETRIZAR

    def _contexto(self, request):
        inductores = M.Inductor.objects.de(self.institucion).filter(tipo=M.Inductor.Tipo.CANTIDAD, activo=True)
        inductor = inductores.filter(pk=request.GET.get("inductor") or request.POST.get("inductor")).first() or inductores.first()
        pid = request.GET.get("periodo") or request.POST.get("periodo") or ""
        periodo = self.periodos().filter(pk=pid).first() if pid not in ("", "permanente") else None
        oid = request.GET.get("origen") or request.POST.get("origen") or ""
        origen = M.CentroCosto.objects.de(self.institucion).filter(pk=oid).first() if oid else None
        clases = request.GET.getlist("clase") or request.POST.getlist("clase") or list(M.Clase.values)
        return inductores, inductor, periodo, origen, clases

    def get(self, request):
        inductores, inductor, periodo, origen, clases = self._contexto(request)
        centros = M.CentroCosto.objects.de(self.institucion).filter(activo=True, clase__in=clases).select_related("unidad_negocio")
        valores = {}
        if inductor:
            valores = dict(M.ValorInductor.objects.de(self.institucion).filter(
                inductor=inductor, periodo=periodo, centro_origen=origen).values_list("centro_id", "cantidad"))
        total = sum(valores.values(), Decimal(0))
        filas = [(c, valores.get(c.pk), (valores.get(c.pk, 0) / total * 100) if total else None) for c in centros]
        return render(request, "modelo/valores_inductor.html", {
            "inductores": inductores, "inductor": inductor, "periodo": periodo, "origen": origen, "clases": clases,
            "todas_clases": M.Clase.choices, "filas": filas, "total": total, "periodos": self.periodos(),
            "origenes": M.CentroCosto.objects.de(self.institucion).filter(activo=True).exclude(clase=M.Clase.FINALES)})

    def post(self, request):
        inductores, inductor, periodo, origen, clases = self._contexto(request)
        if inductor is None:
            raise Http404
        if periodo is not None and periodo.cerrado:
            messages.error(request, "El periodo está cerrado.")
            return redirect(request.get_full_path())
        cambios, errores = 0, []
        with transaction.atomic():
            for c in M.CentroCosto.objects.de(self.institucion).filter(activo=True, clase__in=clases):
                crudo = request.POST.get(f"c_{c.pk}", "").strip()
                existente = M.ValorInductor.objects.de(self.institucion).filter(
                    inductor=inductor, centro=c, periodo=periodo, centro_origen=origen).first()
                if crudo == "":
                    if existente:
                        existente.delete()
                        cambios += 1
                    continue
                try:
                    from cargas.limpieza import numero

                    cantidad = numero(crudo)
                except (InvalidOperation, ValueError):
                    errores.append(f"{c.codigo}: '{crudo}' no es numérico")
                    continue
                if cantidad < 0:
                    errores.append(f"{c.codigo}: valor negativo")
                    continue
                if existente is None or existente.cantidad != cantidad:
                    M.ValorInductor.objects.update_or_create(
                        institucion=self.institucion, inductor=inductor, centro=c, periodo=periodo, centro_origen=origen,
                        defaults={"cantidad": cantidad})
                    cambios += 1
            registrar("VALORES_INDUCTOR", objeto=inductor, detalle={
                "periodo": periodo.etiqueta if periodo else "permanente", "origen": origen.codigo if origen else None,
                "cambios": cambios})
        for e in errores:
            messages.error(request, e)
        messages.success(request, f"{cambios} valores actualizados.")
        params = f"?inductor={inductor.pk}&periodo={periodo.pk if periodo else 'permanente'}&origen={origen.pk if origen else ''}"
        return redirect(request.path + params + "".join(f"&clase={c}" for c in clases))


class ResumenReglasView(InstitucionMixin, View):
    """Vista de la parametrización de distribuciones: qué regla aplica a cada recurso y a cada centro (nivel efectivo)."""

    def get(self, request):
        from motor.motor import Motor

        periodo = self.periodo_seleccionado()
        filas_rec, filas_cc = [], []
        if periodo:
            motor = Motor(periodo)
            motor.recursos = {r.pk: r for r in M.Recurso.objects.de(self.institucion)}
            motor.reglas = defaultdict(list)
            for r in M.ReglaDistribucion.objects.de(self.institucion).filter(activo=True).select_related("inductor"):
                if r.periodo_id in (None, periodo.pk):
                    motor.reglas[r.distribucion].append(r)
            for r in M.Recurso.objects.de(self.institucion).select_related("elemento"):
                filas_rec.append((r, motor.regla(1, recurso_id=r.pk)))
            for c in M.CentroCosto.objects.de(self.institucion).filter(activo=True).exclude(clase=M.Clase.FINALES) \
                    .select_related("unidad_negocio"):
                d = M.DISTRIBUCION_DE_CLASE.get(c.clase)
                filas_cc.append((c, d, motor.regla(d, centro=c)))
        return render(request, "modelo/resumen_reglas.html", {"periodo": periodo, "periodos": self.periodos(),
                                                              "filas_rec": filas_rec, "filas_cc": filas_cc})

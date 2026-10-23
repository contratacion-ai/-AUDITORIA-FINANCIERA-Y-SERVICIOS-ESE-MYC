import json

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from core import permisos
from core.auditoria import foto, registrar
from core.mixins import InstitucionMixin

from .esquemas import ESQUEMAS, TIPOS_CHOICES, esquema
from .lectores import ErrorArchivo, hojas_del_libro, leer_tabla
from .models import Carga, PlantillaMapeo
from .plantilla import generar_plantilla
from .procesador import ErrorCargaArchivo, anular_carga, procesar_carga, procesar_libro_estandar, sugerir_mapeo

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class CargaForm(forms.Form):
    tipo_archivo = forms.ChoiceField(choices=TIPOS_CHOICES, label="Tipo de archivo")
    periodo = forms.ModelChoiceField(queryset=None, required=False)
    plantilla = forms.ModelChoiceField(queryset=None, required=False, label="Plantilla de mapeo",
                                       help_text="Vacío = detección automática de columnas por nombre.")
    hoja = forms.CharField(required=False, help_text="Vacío = la de la plantilla o la primera hoja.")
    archivo = forms.FileField()
    aplicar_parcial = forms.BooleanField(required=False, label="Aplicar las filas válidas aunque haya errores")

    def __init__(self, institucion, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Periodo

        self.fields["periodo"].queryset = Periodo.objects.de(institucion).filter(estado="ABIERTO")
        self.fields["plantilla"].queryset = PlantillaMapeo.objects.de(institucion).filter(activo=True)
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-check-input" if isinstance(f.widget, forms.CheckboxInput)
                                      else "form-select" if isinstance(f.widget, forms.Select) else "form-control")

    def clean(self):
        datos = super().clean()
        tipo = datos.get("tipo_archivo")
        if tipo and esquema(tipo).periodico and tipo != "MANO_OBRA" and not datos.get("periodo"):
            self.add_error("periodo", "Este tipo de archivo requiere un periodo abierto.")
        p = datos.get("plantilla")
        if p and tipo and p.tipo_archivo != tipo:
            self.add_error("plantilla", "La plantilla es de otro tipo de archivo.")
        return datos


def _contenido(archivo):
    if archivo.size > settings.CARGA_MAX_MB * 1024 * 1024:
        raise ErrorCargaArchivo(f"El archivo supera {settings.CARGA_MAX_MB} MB.")
    return archivo.read()


class CargasView(InstitucionMixin, View):
    def get(self, request):
        qs = Carga.objects.de(self.institucion).select_related("periodo", "usuario", "plantilla")
        if request.GET.get("periodo"):
            qs = qs.filter(periodo_id=request.GET["periodo"])
        if request.GET.get("tipo"):
            qs = qs.filter(tipo_archivo=request.GET["tipo"])
        return render(request, "cargas/lista.html", {
            "pagina": Paginator(qs, 30).get_page(request.GET.get("page")), "periodos": self.periodos(),
            "tipos": TIPOS_CHOICES, "form": CargaForm(self.institucion), "esquemas": ESQUEMAS.values()})


class NuevaCargaView(InstitucionMixin, View):
    permiso = permisos.CARGAR

    def get(self, request):
        return render(request, "cargas/nueva.html", {"form": CargaForm(self.institucion, initial=request.GET),
                                                     "esquemas": ESQUEMAS.values()})

    def post(self, request):
        form = CargaForm(self.institucion, request.POST, request.FILES)
        if not form.is_valid():
            return render(request, "cargas/nueva.html", {"form": form, "esquemas": ESQUEMAS.values()})
        d = form.cleaned_data
        try:
            res = procesar_carga(institucion=self.institucion, usuario=request.user, tipo_archivo=d["tipo_archivo"],
                                 contenido=_contenido(d["archivo"]), nombre=d["archivo"].name,
                                 periodo=d["periodo"], plantilla=d["plantilla"], hoja=d["hoja"] or None,
                                 aplicar_parcial=d["aplicar_parcial"])
        except ErrorCargaArchivo as exc:
            form.add_error("archivo", str(exc))
            return render(request, "cargas/nueva.html", {"form": form, "esquemas": ESQUEMAS.values()})
        c = res.carga
        if c.estado == Carga.Estado.APLICADA:
            messages.success(request, f"Carga aplicada: {c.filas_validas} filas válidas de {c.filas_leidas}.")
        else:
            messages.error(request, f"Carga rechazada: {c.filas_error} filas con error. Corrija el archivo o aplique las filas válidas.")
        return redirect("cargas:detalle", pk=c.pk)


class LibroEstandarView(InstitucionMixin, View):
    permiso = permisos.CARGAR

    def post(self, request):
        from core.models import Periodo

        periodo = get_object_or_404(Periodo.objects.de(self.institucion), pk=request.POST.get("periodo"))
        archivo = request.FILES.get("archivo")
        if archivo is None:
            messages.error(request, "Adjunte el libro de la plantilla estándar.")
            return redirect("cargas:lista")
        try:
            resultados = procesar_libro_estandar(institucion=self.institucion, usuario=request.user,
                                                 contenido=_contenido(archivo), nombre=archivo.name, periodo=periodo,
                                                 aplicar_parcial=bool(request.POST.get("aplicar_parcial")))
        except ErrorCargaArchivo as exc:
            messages.error(request, str(exc))
            return redirect("cargas:lista")
        for hoja, r in resultados:
            if isinstance(r, str):
                messages.error(request, f"{hoja}: {r}")
            elif r.carga.estado == Carga.Estado.APLICADA:
                messages.success(request, f"{hoja}: {r.carga.filas_validas} filas aplicadas.")
            else:
                messages.error(request, f"{hoja}: rechazada con {r.carga.filas_error} filas con error (carga #{r.carga.pk}).")
        return redirect("cargas:lista")


class DetalleCargaView(InstitucionMixin, View):
    permiso_escritura = permisos.CARGAR

    def get(self, request, pk):
        c = get_object_or_404(Carga.objects.de(self.institucion), pk=pk)
        errores = c.errores.all()
        nivel = request.GET.get("nivel")
        if nivel:
            errores = errores.filter(nivel=nivel)
        if request.GET.get("exportar"):
            from analisis.exportar import tabla_excel

            contenido = tabla_excel("ERRORES", f"Carga #{c.pk} · {c.nombre_original}", ["NIVEL", "FILA", "COLUMNA", "VALOR", "MENSAJE"],
                                    [[e.nivel, e.fila, e.columna, e.valor, e.mensaje] for e in errores])
            resp = HttpResponse(contenido, content_type=XLSX)
            resp["Content-Disposition"] = f'attachment; filename="errores_carga_{c.pk}.xlsx"'
            return resp
        return render(request, "cargas/detalle.html", {
            "c": c, "pagina": Paginator(errores, 100).get_page(request.GET.get("page")), "esquema": esquema(c.tipo_archivo),
            "n_errores": c.errores.filter(nivel="ERROR").count(), "n_advertencias": c.errores.filter(nivel="ADVERTENCIA").count()})

    def post(self, request, pk):
        c = get_object_or_404(Carga.objects.de(self.institucion), pk=pk)
        motivo = request.POST.get("motivo", "").strip()
        if not motivo:
            messages.error(request, "Indique el motivo de la anulación.")
            return redirect("cargas:detalle", pk=pk)
        try:
            n = anular_carga(c, request.user, motivo)
            messages.success(request, f"Carga anulada; {n} registros eliminados. Vuelva a ejecutar el motor del periodo.")
        except ErrorCargaArchivo as exc:
            messages.error(request, str(exc))
        return redirect("cargas:detalle", pk=pk)


class DescargarArchivoView(InstitucionMixin, View):
    def get(self, request, pk):
        if not (self.puede(permisos.CARGAR) or self.puede(permisos.VER_BITACORA)):
            raise PermissionDenied("Su rol no puede descargar archivos originales.")
        c = get_object_or_404(Carga.objects.de(self.institucion), pk=pk)
        if not c.archivo:
            raise Http404("El archivo original no se conserva (contiene datos de pacientes); se guarda su huella SHA-256.")
        registrar("DESCARGAR_ARCHIVO", objeto=c)
        return FileResponse(c.archivo.open("rb"), as_attachment=True, filename=c.nombre_original)


class PlantillaEstandarView(InstitucionMixin, View):
    def get(self, request):
        resp = HttpResponse(generar_plantilla(), content_type=XLSX)
        resp["Content-Disposition"] = 'attachment; filename="Plantilla_Carga_Mensual_ABC.xlsx"'
        return resp


# ----------------------------------------------------------------------------- mapeos
class MapeoForm(forms.ModelForm):
    class Meta:
        model = PlantillaMapeo
        fields = ["nombre", "tipo_archivo", "hoja", "fila_encabezado", "separador_decimal", "formato_fecha",
                  "omitir_duplicados", "activo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "form-check-input" if isinstance(f.widget, forms.CheckboxInput)
                                      else "form-select" if isinstance(f.widget, forms.Select) else "form-control")


class MapeosView(InstitucionMixin, View):
    def get(self, request):
        return render(request, "cargas/mapeos.html", {
            "mapeos": PlantillaMapeo.objects.de(self.institucion), "tipos": TIPOS_CHOICES})


class MapeoEditarView(InstitucionMixin, View):
    """Editor del mapeo de columnas. Con un archivo de muestra sugiere las correspondencias automáticamente."""

    permiso_escritura = permisos.PARAMETRIZAR

    def _ctx(self, form, obj, tipo, encabezados=None, sugerido=None, hojas=None):
        esq = esquema(tipo)
        mapeo = (sugerido or {}) or (obj.mapeo if obj else {})
        constantes = obj.constantes if obj else {}
        campos = [{"c": c, "origen": mapeo.get(c.nombre, ""), "constante": constantes.get(c.nombre, "")} for c in esq.campos]
        return {"form": form, "obj": obj, "esq": esq, "campos": campos, "encabezados": encabezados or [], "hojas": hojas or [],
                "solo_lectura": not self.puede(permisos.PARAMETRIZAR)}

    def get(self, request, pk=None):
        obj = get_object_or_404(PlantillaMapeo.objects.de(self.institucion), pk=pk) if pk else None
        tipo = obj.tipo_archivo if obj else request.GET.get("tipo", "ESTADO_FINANCIERO")
        if tipo not in ESQUEMAS:
            tipo = "ESTADO_FINANCIERO"
        form = MapeoForm(instance=obj, initial={"tipo_archivo": tipo})
        return render(request, "cargas/mapeo_form.html", self._ctx(form, obj, tipo))

    def post(self, request, pk=None):
        obj = get_object_or_404(PlantillaMapeo.objects.de(self.institucion), pk=pk) if pk else None
        form = MapeoForm(request.POST, instance=obj)
        tipo = request.POST.get("tipo_archivo") if request.POST.get("tipo_archivo") in ESQUEMAS else "ESTADO_FINANCIERO"
        if request.POST.get("sugerir") and request.FILES.get("muestra"):
            muestra = request.FILES["muestra"]
            try:
                contenido = _contenido(muestra)
                hojas = hojas_del_libro(contenido, muestra.name)
                encabezados, _, _ = leer_tabla(contenido, muestra.name, request.POST.get("hoja", ""),
                                               int(request.POST.get("fila_encabezado") or 1))
            except (ErrorArchivo, ErrorCargaArchivo, ValueError) as exc:
                messages.error(request, f"No se pudo leer la muestra: {exc}")
                return render(request, "cargas/mapeo_form.html", self._ctx(form, obj, tipo))
            sugerido = sugerir_mapeo(encabezados, esquema(tipo))
            messages.info(request, f"Se detectaron {len(encabezados)} columnas; {len(sugerido)} campos sugeridos. Revise y guarde.")
            return render(request, "cargas/mapeo_form.html", self._ctx(form, obj, tipo, encabezados, sugerido, hojas))
        if not form.is_valid():
            return render(request, "cargas/mapeo_form.html", self._ctx(form, obj, tipo))
        antes = foto(obj) if obj else None
        m = form.save(commit=False)
        m.institucion = self.institucion
        esq = esquema(m.tipo_archivo)
        m.mapeo = {c.nombre: request.POST.get(f"origen_{c.nombre}", "").strip() for c in esq.campos
                   if request.POST.get(f"origen_{c.nombre}", "").strip()}
        m.constantes = {c.nombre: request.POST.get(f"constante_{c.nombre}", "").strip() for c in esq.campos
                        if request.POST.get(f"constante_{c.nombre}", "").strip()}
        faltan = [c.etiqueta for c in esq.requeridos if c.nombre not in m.mapeo and c.nombre not in m.constantes
                  and not (esq.codigo == "ESTADO_FINANCIERO" and c.nombre == "valor")]
        if faltan:
            messages.error(request, "Faltan campos obligatorios: " + ", ".join(faltan))
            return render(request, "cargas/mapeo_form.html", self._ctx(form, obj, m.tipo_archivo))
        m.es_estandar = obj.es_estandar if obj else False
        m.save()
        registrar("EDITAR_MAPEO" if antes else "CREAR_MAPEO", objeto=m, detalle={"mapeo": m.mapeo, "constantes": m.constantes,
                                                                                 "antes": antes and json.loads(json.dumps(antes))})
        messages.success(request, "Plantilla de mapeo guardada.")
        return redirect("cargas:mapeos")

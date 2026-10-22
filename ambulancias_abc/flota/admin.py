from django.contrib import admin, messages

from .models import (
    Ambulancia,
    ClaveAPI,
    CodigoTraslado,
    HallazgoCalidad,
    LoteImportacion,
    Parametro,
    PartidaCostoIndirecto,
    PerfilCostoEstudio,
    RegistroAuditoria,
    Sede,
    TarifaRuta,
)
from .servicios.comunes import registrar


class AuditadoAdmin(admin.ModelAdmin):
    """Registra en la bitácora los cambios hechos desde el administrador."""

    def save_model(self, request, obj, form, change):
        if hasattr(obj, "version") and change:
            obj.version += 1
        super().save_model(request, obj, form, change)
        registrar(request.user, obj, "editar" if change else "crear", {c: str(form.cleaned_data.get(c)) for c in form.changed_data})


@admin.register(Sede)
class SedeAdmin(AuditadoAdmin):
    list_display = ("codigo", "nombre", "municipio", "origen_tarifa", "codigo_prestador", "prefijos_factura", "activa")


class PerfilInline(admin.TabularInline):
    model = PerfilCostoEstudio
    extra = 0


@admin.register(Ambulancia)
class AmbulanciaAdmin(AuditadoAdmin):
    list_display = ("placa", "sede", "tipo_combustible", "estado", "incluir_en_costeo", "excluida_promedios")
    list_filter = ("sede", "tipo_combustible", "estado", "incluir_en_costeo")
    search_fields = ("placa",)
    inlines = [PerfilInline]


@admin.register(Parametro)
class ParametroAdmin(AuditadoAdmin):
    list_display = ("clave", "valor", "unidad", "es_supuesto", "fuente")
    list_filter = ("es_supuesto",)
    readonly_fields = ("version",)


@admin.register(TarifaRuta)
class TarifaRutaAdmin(AuditadoAdmin):
    list_display = ("origen", "destino", "km", "tarifa", "vigencia", "ambito_geografico", "activa")
    list_filter = ("vigencia", "origen", "ambito_geografico", "activa")
    search_fields = ("origen", "destino", "codigo")


@admin.register(CodigoTraslado)
class CodigoTrasladoAdmin(AuditadoAdmin):
    list_display = ("codigo", "descripcion", "modalidad", "categoria", "activo")
    list_editable = ("categoria",)


@admin.register(PartidaCostoIndirecto)
class PartidaAdmin(AuditadoAdmin):
    list_display = ("categoria", "concepto", "valor_mensual", "incluir_en_abc", "fuente")
    list_editable = ("incluir_en_abc",)


@admin.register(HallazgoCalidad)
class HallazgoAdmin(admin.ModelAdmin):
    list_display = ("severidad", "fuente", "ubicacion", "tipo", "resuelto")
    list_filter = ("severidad", "tipo", "fuente", "resuelto")
    search_fields = ("descripcion", "ubicacion")


@admin.register(LoteImportacion)
class LoteAdmin(admin.ModelAdmin):
    list_display = ("fecha", "tipo", "archivo", "usuario", "nuevos", "actualizados", "omitidos", "hallazgos")
    list_filter = ("tipo",)


@admin.register(RegistroAuditoria)
class RegistroAdmin(admin.ModelAdmin):
    list_display = ("fecha", "usuario", "accion", "modelo", "objeto_id")
    list_filter = ("modelo", "accion")
    readonly_fields = [f.name for f in RegistroAuditoria._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ClaveAPI)
class ClaveAPIAdmin(admin.ModelAdmin):
    list_display = ("nombre", "prefijo", "puede_escribir", "activa", "creada", "ultimo_uso")
    readonly_fields = ("prefijo", "creada", "ultimo_uso")
    fields = ("nombre", "puede_escribir", "activa", "prefijo", "creada", "ultimo_uso")

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)
        from .api import crear_clave

        nuevo, clave = crear_clave(obj.nombre, obj.puede_escribir)
        obj.pk = nuevo.pk
        messages.warning(request, f"Clave de API para '{obj.nombre}' (cópiela ahora, no se volverá a mostrar): {clave}")

from django.contrib import admin

from .models import ArchivoCargado, Conciliacion, Diferencia, HistorialArchivo, Incidencia


class ArchivoInline(admin.TabularInline):
    model = ArchivoCargado
    extra = 0
    fields = ("tipo", "nombre_original", "sede", "hospital", "ambito", "version", "activo")
    readonly_fields = ("nombre_original", "version")


@admin.register(Conciliacion)
class ConciliacionAdmin(admin.ModelAdmin):
    list_display = ("nombre", "auditoria", "estado", "version_rips", "procesada")
    list_filter = ("estado",)
    inlines = [ArchivoInline]


@admin.register(Diferencia)
class DiferenciaAdmin(admin.ModelAdmin):
    list_display = ("factura", "codigo", "estado", "sede", "mes", "valor_fact", "valor_rips", "dif_valor")
    list_filter = ("estado", "sede", "mes", "categoria")
    search_fields = ("factura", "codigo")


admin.site.register(Incidencia)
admin.site.register(HistorialArchivo)

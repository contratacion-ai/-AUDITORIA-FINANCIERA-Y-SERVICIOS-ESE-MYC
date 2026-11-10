from django.contrib import admin

from .models import Auditoria, Hallazgo


@admin.register(Auditoria)
class AuditoriaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "entidad", "estado", "responsable", "creada")
    list_filter = ("estado",)
    search_fields = ("nombre", "entidad")


@admin.register(Hallazgo)
class HallazgoAdmin(admin.ModelAdmin):
    list_display = ("titulo", "auditoria", "tipo", "factura", "sede", "mes", "valor_diferencia", "estado", "prioridad")
    list_filter = ("estado", "prioridad", "tipo", "sede")
    search_fields = ("titulo", "factura", "codigos")

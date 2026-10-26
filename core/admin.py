from django.contrib import admin

from .models import Bitacora, Institucion, Membresia, Periodo


@admin.register(Institucion)
class InstitucionAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "nit", "municipio", "activo")
    search_fields = ("codigo", "nombre", "nit")


@admin.register(Membresia)
class MembresiaAdmin(admin.ModelAdmin):
    list_display = ("usuario", "institucion", "rol", "activo")
    list_filter = ("rol", "institucion", "activo")
    search_fields = ("usuario__username",)


@admin.register(Periodo)
class PeriodoAdmin(admin.ModelAdmin):
    list_display = ("institucion", "anio", "mes", "estado")
    list_filter = ("institucion", "estado")


@admin.register(Bitacora)
class BitacoraAdmin(admin.ModelAdmin):
    list_display = ("fecha", "usuario", "institucion", "accion", "modelo", "descripcion", "ip")
    list_filter = ("accion", "institucion")
    search_fields = ("descripcion", "usuario__username")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

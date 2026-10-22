from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from . import permisos
from .models import Periodo


class InstitucionMixin(LoginRequiredMixin):
    """Exige institución activa y el permiso indicado. Toda consulta debe filtrarse por `self.institucion`."""

    permiso = permisos.VER
    permiso_escritura = None  # permiso exigido para POST (si difiere)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.institucion is None:
            messages.info(request, "Seleccione la institución con la que va a trabajar.")
            return redirect("core:seleccionar_institucion")
        self.institucion = request.institucion
        self.rol = request.rol
        requerido = self.permiso
        if request.method not in ("GET", "HEAD", "OPTIONS") and self.permiso_escritura:
            requerido = self.permiso_escritura
        if not permisos.tiene_permiso(request.user, request.rol, requerido):
            raise PermissionDenied("Su rol no tiene permiso para esta acción.")
        return super().dispatch(request, *args, **kwargs)

    def puede(self, permiso):
        return permisos.tiene_permiso(self.request.user, self.rol, permiso)

    def periodos(self):
        return Periodo.objects.de(self.institucion).order_by("-anio", "-mes")

    def periodo_seleccionado(self, nombre="periodo"):
        pid = self.request.GET.get(nombre) or self.request.POST.get(nombre)
        qs = self.periodos()
        if pid:
            return qs.filter(pk=pid).first()
        return qs.first()


def exigir_periodo_abierto(periodo):
    if periodo is None:
        raise PermissionDenied("Debe seleccionar un periodo.")
    if periodo.cerrado:
        raise PermissionDenied(f"El periodo {periodo} está cerrado; no admite cambios.")

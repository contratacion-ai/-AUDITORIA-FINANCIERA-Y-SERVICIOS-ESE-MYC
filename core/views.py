from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from . import permisos
from .auditoria import registrar
from .middleware import SESION_INSTITUCION, instituciones_de
from .mixins import InstitucionMixin
from .models import Bitacora, Membresia, Periodo, Rol


class LoginConBloqueo(auth_views.LoginView):
    """Bloquea temporalmente usuario+IP tras varios intentos fallidos y registra los accesos en la bitácora."""

    template_name = "core/login.html"

    def _llave(self):
        usuario = (self.request.POST.get("username") or "").lower()[:150]
        return f"login-fallos:{usuario}:{self.request.META.get('REMOTE_ADDR', '')}"

    def post(self, request, *args, **kwargs):
        if cache.get(self._llave(), 0) >= settings.LOGIN_MAX_INTENTOS:
            form = self.get_form()
            form.add_error(None, "Demasiados intentos fallidos. Intente de nuevo en unos minutos.")
            registrar("LOGIN_BLOQUEADO", descripcion=request.POST.get("username", "")[:150])
            return self.form_invalid(form, contar=False)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form, contar=True):
        if contar:
            llave = self._llave()
            cache.set(llave, cache.get(llave, 0) + 1, settings.LOGIN_BLOQUEO_SEGUNDOS)
            registrar("LOGIN_FALLIDO", descripcion=self.request.POST.get("username", "")[:150])
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self._llave())
        respuesta = super().form_valid(form)
        registrar("LOGIN", usuario=form.get_user(), descripcion="Inicio de sesión")
        return respuesta


@login_required
def seleccionar_institucion(request):
    instituciones = instituciones_de(request.user)
    if request.method == "POST":
        inst = get_object_or_404(instituciones, pk=request.POST.get("institucion"))
        request.session[SESION_INSTITUCION] = inst.pk
        registrar("SELECCION_INSTITUCION", institucion=inst, descripcion=inst.nombre)
        return redirect("analisis:tablero")
    if instituciones.count() == 1 and not request.GET.get("cambiar"):
        request.session[SESION_INSTITUCION] = instituciones.first().pk
        return redirect("analisis:tablero")
    membresias = {m.institucion_id: m.get_rol_display() for m in Membresia.objects.filter(usuario=request.user, activo=True)}
    return render(request, "core/seleccionar.html", {
        "instituciones": [(i, membresias.get(i.pk, "Superusuario")) for i in instituciones]})


class PeriodoForm(forms.ModelForm):
    class Meta:
        model = Periodo
        fields = ["anio", "mes"]


class PeriodosView(InstitucionMixin, View):
    permiso_escritura = permisos.CERRAR_PERIODO

    def get(self, request):
        return render(request, "core/periodos.html", {"periodos": self.periodos(), "form": PeriodoForm(),
                                                      "hoy": timezone.localdate()})

    def post(self, request):
        accion = request.POST.get("accion")
        if accion == "crear":
            form = PeriodoForm(request.POST)
            if form.is_valid():
                p = form.save(commit=False)
                p.institucion = self.institucion
                try:
                    p.full_clean()
                    p.save()
                    registrar("CREAR_PERIODO", objeto=p)
                    messages.success(request, f"Periodo {p} creado.")
                except (IntegrityError, forms.ValidationError) as exc:
                    messages.error(request, f"No se pudo crear el periodo: {exc}")
            else:
                messages.error(request, "Datos de periodo no válidos.")
        elif accion in ("cerrar", "abrir"):
            p = get_object_or_404(self.periodos(), pk=request.POST.get("periodo"))
            p.estado = Periodo.Estado.CERRADO if accion == "cerrar" else Periodo.Estado.ABIERTO
            p.cerrado_por = request.user if accion == "cerrar" else None
            p.cerrado_en = timezone.now() if accion == "cerrar" else None
            p.save()
            registrar("CERRAR_PERIODO" if accion == "cerrar" else "ABRIR_PERIODO", objeto=p)
            messages.success(request, f"Periodo {p} {'cerrado' if accion == 'cerrar' else 'reabierto'}.")
        return redirect("core:periodos")


class BitacoraView(InstitucionMixin, View):
    permiso = permisos.VER_BITACORA

    def get(self, request):
        qs = Bitacora.objects.filter(institucion=self.institucion).select_related("usuario")
        accion, usuario, desde = request.GET.get("accion"), request.GET.get("usuario"), request.GET.get("desde")
        if accion:
            qs = qs.filter(accion=accion)
        if usuario:
            qs = qs.filter(usuario__username__icontains=usuario)
        if desde:
            qs = qs.filter(fecha__date__gte=desde)
        if request.GET.get("exportar"):
            from analisis.exportar import tabla_excel

            filas = [[b.fecha.strftime("%Y-%m-%d %H:%M:%S"), b.usuario.username if b.usuario else "", b.accion, b.modelo,
                      b.objeto_id, b.descripcion, str(b.detalle)[:2000], b.ip] for b in qs[:50000]]
            registrar("EXPORTAR_BITACORA", descripcion=f"{len(filas)} registros")
            contenido = tabla_excel("BITACORA", f"{self.institucion.nombre} · bitácora de auditoría",
                                    ["FECHA", "USUARIO", "ACCIÓN", "MODELO", "ID", "DESCRIPCIÓN", "DETALLE", "IP"], filas)
            resp = HttpResponse(contenido, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            resp["Content-Disposition"] = 'attachment; filename="bitacora.xlsx"'
            return resp
        pagina = Paginator(qs, 50).get_page(request.GET.get("page"))
        acciones = Bitacora.objects.filter(institucion=self.institucion).values_list("accion", flat=True).distinct().order_by("accion")
        return render(request, "core/bitacora.html", {"pagina": pagina, "acciones": acciones})


class UsuariosView(InstitucionMixin, View):
    """Asignación de usuarios y roles a la institución activa (aislamiento por membresía)."""

    permiso = permisos.GESTIONAR_USUARIOS

    def get(self, request):
        membresias = Membresia.objects.filter(institucion=self.institucion).select_related("usuario").order_by("usuario__username")
        return render(request, "core/usuarios.html", {"membresias": membresias, "roles": Rol.choices,
                                                      "matriz": [(p, permisos.DESCRIPCION[p], [r in permisos.MATRIZ[p] for r, _ in Rol.choices])
                                                                 for p in permisos.MATRIZ]})

    def post(self, request):
        User = get_user_model()
        accion = request.POST.get("accion")
        if accion == "asignar":
            username = request.POST.get("username", "").strip()
            rol = request.POST.get("rol")
            if rol not in Rol.values:
                raise PermissionDenied("Rol no válido.")
            usuario = User.objects.filter(username=username).first()
            if usuario is None:
                clave = request.POST.get("password", "")
                from django.contrib.auth.password_validation import validate_password

                try:
                    validate_password(clave)
                except forms.ValidationError as exc:
                    messages.error(request, "Contraseña no válida: " + " ".join(exc.messages))
                    return redirect("core:usuarios")
                usuario = User.objects.create_user(username=username, password=clave, email=request.POST.get("email", ""))
                registrar("CREAR_USUARIO", objeto=usuario, descripcion=username)
            m, creada = Membresia.objects.update_or_create(usuario=usuario, institucion=self.institucion,
                                                           defaults={"rol": rol, "activo": True})
            registrar("ASIGNAR_ROL", objeto=m, detalle={"usuario": username, "rol": rol, "nueva": creada})
            messages.success(request, f"{username} asignado como {m.get_rol_display()}.")
        elif accion == "revocar":
            m = get_object_or_404(Membresia, pk=request.POST.get("membresia"), institucion=self.institucion)
            if m.usuario == request.user:
                messages.error(request, "No puede revocar su propio acceso.")
            else:
                m.activo = False
                m.save()
                registrar("REVOCAR_ROL", objeto=m, detalle={"usuario": m.usuario.username})
                messages.success(request, f"Acceso de {m.usuario.username} revocado.")
        return redirect("core:usuarios")


def inicio(request):
    if not request.user.is_authenticated:
        return redirect("login")
    return redirect("analisis:tablero" if request.institucion else "core:seleccionar_institucion")

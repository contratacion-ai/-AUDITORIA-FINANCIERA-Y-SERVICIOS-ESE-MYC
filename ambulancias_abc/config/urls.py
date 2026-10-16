from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth import views as auth_views
from django.urls import include, path, re_path, reverse_lazy
from django.views.static import serve



class CambiarClave(auth_views.PasswordChangeView):
    template_name = "flota/formulario_clave.html"
    success_url = reverse_lazy("flota:dashboard")

    def form_valid(self, form):
        messages.success(self.request, "Clave cambiada")
        return super().form_valid(form)


admin.site.site_header = "Costeo ABC Ambulancias - ESE Moreno y Clavijo"
admin.site.site_title = "Costeo ABC Ambulancias"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("ingresar/", auth_views.LoginView.as_view(template_name="flota/login.html"), name="login"),
    path("salir/", auth_views.LogoutView.as_view(), name="logout"),
    path("clave/", CambiarClave.as_view(), name="cambiar_clave"),
    path("api/v1/", include("flota.api_urls")),
    path("", include("flota.urls")),
]

# Archivos estáticos servidos por Django (aplicación de intranet con waitress;
# en la nube detrás de un proxy, el proxy puede servir STATIC_ROOT directamente).
if not settings.DEBUG:
    urlpatterns += [re_path(r"^static/(?P<path>.*)$", serve, {"document_root": settings.STATIC_ROOT})]

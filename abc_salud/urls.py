from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core.views import LoginConBloqueo, inicio, primer_acceso_view

urlpatterns = [
    path("", inicio, name="inicio"),
    path("cuenta/ingresar/", LoginConBloqueo.as_view(), name="login"),
    path("primer-acceso/", primer_acceso_view, name="primer_acceso"),
    path("cuenta/salir/", auth_views.LogoutView.as_view(), name="logout"),
    path("cuenta/clave/", auth_views.PasswordChangeView.as_view(template_name="core/clave.html", success_url="/"),
         name="password_change"),
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("parametrizacion/", include("modelo.urls")),
    path("cargas/", include("cargas.urls")),
    path("motor/", include("motor.urls")),
    path("analisis/", include("analisis.urls")),
]

handler403 = "core.views_errores.error_403"
handler404 = "core.views_errores.error_404"
handler500 = "core.views_errores.error_500"

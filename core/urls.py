from django.urls import path

from . import views

app_name = "core"
urlpatterns = [
    path("institucion/", views.seleccionar_institucion, name="seleccionar_institucion"),
    path("periodos/", views.PeriodosView.as_view(), name="periodos"),
    path("bitacora/", views.BitacoraView.as_view(), name="bitacora"),
    path("usuarios/", views.UsuariosView.as_view(), name="usuarios"),
]

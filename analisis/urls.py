from django.urls import path

from . import views

app_name = "analisis"
urlpatterns = [
    path("tablero/", views.TableroView.as_view(), name="tablero"),
    path("cruces/", views.CrucesView.as_view(), name="cruces"),
    path("informe/", views.InformeView.as_view(), name="informe"),
    path("informe/configuracion/", views.ConfigInformeView.as_view(), name="config_informe"),
]

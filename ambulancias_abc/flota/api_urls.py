from django.urls import path

from . import api

app_name = "api"

urlpatterns = [
    path("estado", api.estado, name="estado"),
    path("ambulancias", api.ambulancias, name="ambulancias"),
    path("rutas", api.rutas, name="rutas"),
    path("traslados", api.traslados, name="traslados"),
    path("costos-ambulancia", api.costos_ambulancia, name="costos_ambulancia"),
    path("consumos", api.consumos, name="consumos"),
    path("hallazgos", api.hallazgos, name="hallazgos"),
    path("rips", api.importar_rips, name="importar_rips"),
]

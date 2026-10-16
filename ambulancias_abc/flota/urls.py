from django.urls import path

from . import views, views_cargas

app_name = "flota"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("consolidado/", views.consolidado, name="consolidado"),
    path("ambulancias/", views.ambulancias, name="ambulancias"),
    path("ambulancias/<str:placa>/", views.ambulancia_detalle, name="ambulancia"),
    path("rentabilidad/rutas/", views.rentabilidad_rutas, name="rentabilidad_rutas"),
    path("traslados/", views.traslados, name="traslados"),
    path("traslados/nuevo/", views.traslado_nuevo, name="traslado_nuevo"),
    path("traslados/asignar/", views.traslados_asignar, name="traslados_asignar"),
    path("traslados/<int:pk>/", views.traslado_editar, name="traslado_editar"),
    path("consumos/", views.consumos, name="consumos"),
    path("consumos/nuevo/", views.consumo_nuevo, name="consumo_nuevo"),
    path("consumos/plantilla.csv", views.plantilla_consumos, name="plantilla_consumos"),
    path("consumos/<int:pk>/", views.consumo_editar, name="consumo_editar"),
    path("importar/", views.importar_vista, name="importar"),
    path("cargas/", views_cargas.cargas, name="cargas"),
    path("cargas/facturacion/", views_cargas.cargar_facturacion, name="cargar_facturacion"),
    path("cargas/rips/", views_cargas.cargar_rips, name="cargar_rips"),
    path("rips/", views_cargas.rips_cargas, name="rips_cargas"),
    path("rips/<int:pk>/", views_cargas.rips_detalle, name="rips_detalle"),
    path("consumos/tabla/<slug:clave>/", views_cargas.consumos_tabla, name="consumos_tabla"),
    path("consumos/tabla/<slug:clave>/cargar/", views_cargas.cargar_consumos, name="cargar_consumos"),
    path("consumos/tabla/<slug:clave>/plantilla.xlsx", views_cargas.plantilla_consumos_tabla, name="plantilla_consumos_tabla"),
    path("recorridos/", views_cargas.recorridos_sede, name="recorridos_sede"),
    path("facturacion/", views_cargas.facturacion_ambito, name="facturacion_ambito"),
    path("calidad/", views.calidad, name="calidad"),
    path("calidad/<int:pk>/resolver/", views.hallazgo_resolver, name="hallazgo_resolver"),
    path("parametros/", views.parametros_vista, name="parametros"),
    path("bitacora/", views.bitacora, name="bitacora"),
]

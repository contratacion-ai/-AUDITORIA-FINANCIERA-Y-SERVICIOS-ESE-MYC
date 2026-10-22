from django.urls import path

from . import views

app_name = "cargas"
urlpatterns = [
    path("", views.CargasView.as_view(), name="lista"),
    path("nueva/", views.NuevaCargaView.as_view(), name="nueva"),
    path("libro-estandar/", views.LibroEstandarView.as_view(), name="libro_estandar"),
    path("plantilla-estandar/", views.PlantillaEstandarView.as_view(), name="plantilla_estandar"),
    path("mapeos/", views.MapeosView.as_view(), name="mapeos"),
    path("mapeos/nuevo/", views.MapeoEditarView.as_view(), name="mapeo_nuevo"),
    path("mapeos/<int:pk>/", views.MapeoEditarView.as_view(), name="mapeo_editar"),
    path("<int:pk>/", views.DetalleCargaView.as_view(), name="detalle"),
    path("<int:pk>/archivo/", views.DescargarArchivoView.as_view(), name="archivo"),
]

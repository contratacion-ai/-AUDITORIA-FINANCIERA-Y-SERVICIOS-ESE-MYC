from django.urls import path

from . import views

app_name = "conciliacion"

urlpatterns = [
    path("nueva/", views.crear, name="crear"),
    path("criterio-emparejamiento/", views.criterio, name="criterio"),
    path("<int:pk>/", views.detalle, name="detalle"),
    path("<int:pk>/parametros/", views.editar, name="editar"),
    path("<int:pk>/eliminar/", views.eliminar_conciliacion, name="eliminar"),
    path("<int:pk>/cargar/", views.cargar, name="cargar"),
    path("<int:pk>/limpiar/", views.limpiar, name="limpiar"),
    path("<int:pk>/procesar/", views.procesar, name="procesar"),
    path("<int:pk>/hallazgos/", views.hallazgos, name="hallazgos"),
    path("<int:pk>/exportar/", views.exportar, name="exportar"),
    path("<int:pk>/tablero/", views.tablero_view, name="tablero"),
    path("<int:pk>/tablero/exportar/", views.exportar_filtro, name="exportar_filtro"),
    path("archivo/<int:pk>/reemplazar/", views.reemplazar, name="reemplazar"),
    path("archivo/<int:pk>/actualizar/", views.actualizar, name="actualizar"),
    path("archivo/<int:pk>/eliminar/", views.eliminar, name="eliminar_archivo"),
]

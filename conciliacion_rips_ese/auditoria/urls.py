from django.urls import path

from . import views

app_name = "auditoria"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("auditorias/nueva/", views.crear, name="crear"),
    path("auditorias/<int:pk>/", views.detalle, name="detalle"),
    path("auditorias/<int:pk>/editar/", views.editar, name="editar"),
    path("auditorias/<int:pk>/hallazgos/nuevo/", views.hallazgo_manual, name="hallazgo_manual"),
    path("auditorias/<int:pk>/hallazgos/exportar/", views.exportar_hallazgos, name="exportar_hallazgos"),
    path("hallazgos/<int:pk>/", views.hallazgo, name="hallazgo"),
]

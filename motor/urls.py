from django.urls import path

from . import views

app_name = "motor"
urlpatterns = [
    path("", views.MotorView.as_view(), name="panel"),
    path("corrida/<int:pk>/", views.CorridaView.as_view(), name="corrida"),
    path("corrida/<int:corrida_pk>/asignaciones/", views.AsignacionesView.as_view(), name="asignaciones"),
    path("corrida/<int:corrida_pk>/centro/<int:centro_pk>/", views.TrazabilidadCentroView.as_view(), name="traza_centro"),
    path("matriz/", views.MatrizView.as_view(), name="matriz"),
    path("cups/", views.CostosCupsView.as_view(), name="cups"),
    path("cups/<int:pk>/", views.TrazabilidadCupsView.as_view(), name="traza_cups"),
]

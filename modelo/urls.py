from django.urls import path

from . import views

app_name = "modelo"
urlpatterns = [
    path("", views.ParametrizacionView.as_view(), name="parametrizacion"),
    path("valores-inductor/", views.ValoresInductorView.as_view(), name="valores_inductor"),
    path("resumen-reglas/", views.ResumenReglasView.as_view(), name="resumen_reglas"),
    path("<slug:slug>/", views.CatalogoListaView.as_view(), name="catalogo"),
    path("<slug:slug>/nuevo/", views.CatalogoEditarView.as_view(), name="catalogo_nuevo"),
    path("<slug:slug>/<int:pk>/", views.CatalogoEditarView.as_view(), name="catalogo_editar"),
]

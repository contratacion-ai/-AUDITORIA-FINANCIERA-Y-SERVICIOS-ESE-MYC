from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path

# Identificador usado por el lanzador para reconocer su propia instancia (evita conflictos de puerto)
FIRMA_SALUD = "auditoria-ese-myc-ok"

urlpatterns = [
    path("salud/", lambda request: HttpResponse(FIRMA_SALUD, content_type="text/plain")),
    path("admin/", admin.site.urls),
    path("conciliacion/", include("conciliacion.urls")),
    path("", include("auditoria.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

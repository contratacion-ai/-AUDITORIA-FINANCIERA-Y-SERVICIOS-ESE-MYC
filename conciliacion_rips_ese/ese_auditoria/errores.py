"""Páginas de error comprensibles para la versión instalada.

La herramienta solo escucha en 127.0.0.1 (el propio equipo), por eso puede mostrar al usuario la causa
del error para que la reporte. El detalle completo queda en el archivo de registro.
"""

import logging
import sys
import traceback

from django.conf import settings
from django.shortcuts import render

registro = logging.getLogger("ese_auditoria")


def error_500(request):
    tipo, valor, tb = sys.exc_info()
    detalle = "".join(traceback.format_exception(tipo, valor, tb)) if tipo else ""
    return render(request, "error.html", {
        "titulo": "Ocurrió un error inesperado",
        "mensaje": f"{tipo.__name__}: {valor}" if tipo else "Error interno",
        "detalle": detalle[-4000:],
        "archivo_registro": getattr(settings, "ARCHIVO_REGISTRO", ""),
    }, status=500)


def error_csrf(request, reason=""):
    return render(request, "error.html", {
        "titulo": "La página expiró",
        "mensaje": "Vuelva a la página anterior, recárguela (F5) y repita la acción. "
                   "Si abrió la herramienta en dos navegadores o reinició la herramienta, use solo una ventana.",
        "detalle": reason,
        "archivo_registro": getattr(settings, "ARCHIVO_REGISTRO", ""),
    }, status=403)


def error_413(request, exception=None):  # pragma: no cover - lo maneja Django como 400
    return render(request, "error.html", {"titulo": "Solicitud demasiado grande", "mensaje": str(exception)}, status=400)

from django.shortcuts import render


def error_403(request, exception=None):
    return render(request, "errores/error.html", {"codigo": 403, "mensaje": str(exception) if exception else
                                                  "No tiene permiso para esta acción."}, status=403)


def error_404(request, exception=None):
    return render(request, "errores/error.html", {"codigo": 404, "mensaje": "La página o el registro no existe "
                                                  "o no pertenece a su institución."}, status=404)


def error_500(request):
    return render(request, "errores/error.html", {"codigo": 500, "mensaje": "Error interno. El incidente quedó registrado."},
                  status=500)

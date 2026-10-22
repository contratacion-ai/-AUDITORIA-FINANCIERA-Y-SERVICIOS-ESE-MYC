from .auditoria import set_request
from .models import Institucion, Membresia

SESION_INSTITUCION = "institucion_id"


def instituciones_de(usuario):
    """Instituciones a las que el usuario tiene acceso (el superusuario ve todas)."""
    if not usuario.is_authenticated:
        return Institucion.objects.none()
    if usuario.is_superuser:
        return Institucion.objects.filter(activo=True)
    return Institucion.objects.filter(
        activo=True, membresias__usuario=usuario, membresias__activo=True
    ).distinct()


class InstitucionMiddleware:
    """Resuelve la institución activa de la sesión y valida que el usuario tenga membresía.

    Si la membresía fue revocada, la institución deja de estar disponible en la siguiente petición.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_request(request)
        request.institucion = None
        request.rol = None
        usuario = getattr(request, "user", None)
        if usuario is not None and usuario.is_authenticated:
            inst_id = request.session.get(SESION_INSTITUCION)
            if inst_id:
                if usuario.is_superuser:
                    request.institucion = Institucion.objects.filter(pk=inst_id, activo=True).first()
                    request.rol = "ADMIN" if request.institucion else None
                else:
                    m = (
                        Membresia.objects.select_related("institucion")
                        .filter(usuario=usuario, institucion_id=inst_id, activo=True, institucion__activo=True)
                        .first()
                    )
                    if m:
                        request.institucion = m.institucion
                        request.rol = m.rol
                if request.institucion is None:
                    request.session.pop(SESION_INSTITUCION, None)
        try:
            return self.get_response(request)
        finally:
            set_request(None)

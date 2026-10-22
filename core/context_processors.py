from . import permisos
from .middleware import instituciones_de


def institucion(request):
    usuario = getattr(request, "user", None)
    if usuario is None or not usuario.is_authenticated:
        return {}
    rol = getattr(request, "rol", None)
    puede = {p: permisos.tiene_permiso(usuario, rol, p) for p in permisos.MATRIZ}
    return {
        "institucion_activa": getattr(request, "institucion", None),
        "rol_activo": rol,
        "mis_instituciones": instituciones_de(usuario),
        "puede": puede,
    }

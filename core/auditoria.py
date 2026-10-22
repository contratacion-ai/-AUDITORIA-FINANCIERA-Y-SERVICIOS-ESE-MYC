"""Registro de auditoría: usuario actual por hilo y helpers para escribir en la bitácora."""
import threading
from datetime import date, datetime
from decimal import Decimal

from django.db.models import Model

_local = threading.local()


def set_request(request):
    _local.request = request


def get_request():
    return getattr(_local, "request", None)


def _ip(request):
    if request is None:
        return None
    reenviada = request.META.get("HTTP_X_FORWARDED_FOR")
    if reenviada:
        return reenviada.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def serializar(valor):
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    if isinstance(valor, Model):
        return valor.pk
    if isinstance(valor, (list, tuple)):
        return [serializar(v) for v in valor]
    if isinstance(valor, dict):
        return {k: serializar(v) for k, v in valor.items()}
    return valor


def foto(instancia):
    """Representación serializable de los campos concretos de un modelo."""
    datos = {}
    for campo in instancia._meta.concrete_fields:
        datos[campo.attname] = serializar(getattr(instancia, campo.attname))
    return datos


def registrar(accion, *, institucion=None, objeto=None, descripcion="", detalle=None, usuario=None):
    from .models import Bitacora

    request = get_request()
    if usuario is None and request is not None and getattr(request, "user", None) and request.user.is_authenticated:
        usuario = request.user
    if institucion is None and request is not None:
        institucion = getattr(request, "institucion", None)
    if institucion is None and objeto is not None:
        institucion = getattr(objeto, "institucion", None)
    return Bitacora.objects.create(
        usuario=usuario,
        institucion=institucion,
        accion=accion,
        modelo=objeto._meta.label if objeto is not None else "",
        objeto_id=str(objeto.pk) if objeto is not None and objeto.pk is not None else "",
        descripcion=(descripcion or (str(objeto) if objeto is not None else ""))[:300],
        detalle=serializar(detalle or {}),
        ip=_ip(request),
    )

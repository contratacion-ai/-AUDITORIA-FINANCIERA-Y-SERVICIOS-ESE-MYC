"""Primer acceso: el instalador crea el administrador SIN contraseña y el usuario la define en el navegador.

La página de primer acceso solo funciona con un enlace de un solo uso (token aleatorio guardado en
datos/primer_acceso.token, legible solo en el servidor), para que nadie más en la red pueda tomar la cuenta.
"""
import hmac
import os
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model


def ruta():
    return settings.DATOS_DIR / "primer_acceso.token"


def pendiente():
    """Administrador activo que aún no tiene contraseña (o None)."""
    for u in get_user_model().objects.filter(is_superuser=True, is_active=True).order_by("pk"):
        if not u.has_usable_password():
            return u
    return None


def generar():
    token = secrets.token_urlsafe(24)
    archivo = ruta()
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(token, encoding="ascii")
    if os.name != "nt":
        os.chmod(archivo, 0o600)
    return token


def valido(token):
    archivo = ruta()
    if not token or not archivo.is_file():
        return False
    return hmac.compare_digest(archivo.read_text(encoding="ascii").strip(), token)


def consumir():
    try:
        ruta().unlink()
    except FileNotFoundError:
        pass


def enlace(token, puerto=8000, host="localhost"):
    return f"http://{host}:{puerto}/primer-acceso/?t={token}"

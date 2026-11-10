"""Avance fino del procesamiento (dentro de paquetes y archivos grandes).

``conciliar`` registra una función de avance; los lectores llaman a ``detalle(...)`` con frecuencia y
este módulo la reenvía como máximo una vez por segundo, agregando la memoria en uso del equipo. Si la
función de avance lanza una excepción (por ejemplo, porque el usuario canceló), se propaga y detiene
el procesamiento de inmediato.
"""

from __future__ import annotations

import contextvars
import time

_avance = contextvars.ContextVar("avance_conciliacion", default=None)
_ultimo = contextvars.ContextVar("avance_ultimo", default=0.0)
INTERVALO = 1.0


class ProcesoCancelado(Exception):
    """El usuario pidió cancelar el procesamiento."""


def establecer(funcion):
    return _avance.set(funcion)


def restablecer(token) -> None:
    _avance.reset(token)


def memoria() -> str:
    """Memoria usada por la herramienta y libre en el equipo (texto corto), si psutil está disponible."""
    try:
        import psutil

        usada = psutil.Process().memory_info().rss / 1024 ** 3
        libre = psutil.virtual_memory().available / 1024 ** 3
    except Exception:  # noqa: BLE001
        return ""
    texto = f"memoria: {usada:.1f} GB en uso, {libre:.1f} GB libres".replace(".", ",")
    if libre < 0.6:
        texto += " ⚠ el equipo casi no tiene memoria libre: cierre otros programas (Excel, navegador con muchas pestañas)"
    return texto


def detalle(mensaje: str, forzar: bool = False) -> None:
    """Informa una subetapa (archivo interno, filas leídas...). No cambia el porcentaje."""
    funcion = _avance.get()
    if funcion is None:
        return
    ahora = time.monotonic()
    if not forzar and ahora - _ultimo.get() < INTERVALO:
        return
    _ultimo.set(ahora)
    funcion(mensaje, None)

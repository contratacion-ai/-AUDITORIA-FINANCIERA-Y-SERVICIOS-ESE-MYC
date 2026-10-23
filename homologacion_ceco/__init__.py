"""Homologación de costos por centro de costos (CECO) del estado financiero a la resolución.

Uso rápido:

    from homologacion_ceco import cargar_referencia, procesar, exportar
    ref = cargar_referencia()                      # archivo de referencia incluido (o una ruta)
    resultado = procesar(["lote.zip"], ref)
    exportar(resultado, "resultado.xlsx")

Línea de comandos: python -m homologacion_ceco --help

Las funciones se importan al usarlas (PEP 562), para que quien solo necesita un submódulo liviano —p. ej. la
herramienta ABC al revisar si un archivo es un reporte CTRP— no cargue pandas.
"""
__version__ = "1.0.0"

_PEREZOSOS = {
    "cargar_referencia": ("referencia", "cargar_referencia"),
    "procesar": ("procesador", "procesar"),
    "Opciones": ("procesador", "Opciones"),
    "exportar": ("exportar", "exportar"),
}
__all__ = [*_PEREZOSOS, "__version__"]


def __getattr__(nombre):
    if nombre in _PEREZOSOS:
        import importlib

        modulo, atributo = _PEREZOSOS[nombre]
        return getattr(importlib.import_module(f".{modulo}", __name__), atributo)
    raise AttributeError(f"module {__name__!r} has no attribute {nombre!r}")

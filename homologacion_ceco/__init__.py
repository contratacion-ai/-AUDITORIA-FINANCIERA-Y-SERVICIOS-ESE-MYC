"""Homologación de costos por centro de costos (CECO) del estado financiero a la resolución.

Uso rápido:

    from homologacion_ceco import cargar_referencia, procesar, exportar
    ref = cargar_referencia()                      # archivo de referencia incluido (o una ruta)
    resultado = procesar(["lote.zip"], ref)
    exportar(resultado, "resultado.xlsx")

Línea de comandos: python -m homologacion_ceco --help
"""
__version__ = "1.0.0"

from .exportar import exportar  # noqa: E402
from .procesador import Opciones, procesar  # noqa: E402
from .referencia import cargar_referencia  # noqa: E402

__all__ = ["cargar_referencia", "procesar", "exportar", "Opciones", "__version__"]

"""Motor de conciliación Facturación vs RIPS para la ESE Moreno y Clavijo.

Paquete independiente de Django: puede usarse desde la línea de comandos
(``python -m rips_conciliacion``), desde scripts o desde la aplicación web.
"""

from .modelos import Incidencia, ResultadoCarga
from .conciliador import ConfiguracionConciliacion, ResultadoConciliacion, conciliar

__all__ = [
    "Incidencia",
    "ResultadoCarga",
    "ConfiguracionConciliacion",
    "ResultadoConciliacion",
    "conciliar",
]

__version__ = "1.0.0"

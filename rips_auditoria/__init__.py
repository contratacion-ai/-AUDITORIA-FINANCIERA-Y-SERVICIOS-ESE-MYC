"""Procesamiento de RIPS (Resolución 3374 de 2000, Anexo Técnico No. 1) para
auditoría financiera: carga TXT/ZIP, validación, organización por hospital y
mes, plantillas Excel, exportación Excel/JSON y homologación configurable."""

__version__ = "1.3.0"

from .config import cargar_configuracion  # noqa: E402
from .procesamiento import ResultadoProcesamiento, UnidadAnalisis, procesar  # noqa: E402

__all__ = ["procesar", "cargar_configuracion", "ResultadoProcesamiento", "UnidadAnalisis", "__version__"]

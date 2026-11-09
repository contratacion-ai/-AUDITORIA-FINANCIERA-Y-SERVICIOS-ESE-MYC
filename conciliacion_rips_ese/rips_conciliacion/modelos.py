"""Estructuras de datos compartidas por el motor."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

VERSION_3374 = "Resolución 3374 de 2000 (TXT)"
VERSION_2275 = "Resolución 2275 de 2023 (JSON)"
VERSION_NO_DETERMINADA = "NO DETERMINADA"

# Severidades de incidencia
ERROR = "ERROR"  # el registro/archivo no pudo interpretarse y se excluyó
ADVERTENCIA = "ADVERTENCIA"  # se interpretó, pero hay algo que revisar


@dataclass
class Incidencia:
    """Problema encontrado al leer o validar un archivo.

    Se acumula y se reporta; nunca detiene el procesamiento del resto.
    """

    archivo: str
    severidad: str
    mensaje: str
    linea: int | None = None
    campo: str | None = None
    valor: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResultadoCarga:
    """Resultado de leer un archivo (o un paquete ZIP/RAR completo)."""

    archivo: str
    version: str = VERSION_NO_DETERMINADA
    # registros por tipo de archivo/servicio: {"AF": [dict, ...], "AC": [...], ...}
    registros: dict[str, list[dict]] = field(default_factory=dict)
    incidencias: list[Incidencia] = field(default_factory=list)
    # detalle por cada archivo físico leído
    archivos: list[dict] = field(default_factory=list)

    def agregar(self, tipo: str, registro: dict) -> None:
        self.registros.setdefault(tipo, []).append(registro)

    def incidencia(self, severidad: str, mensaje: str, **kwargs) -> None:
        archivo = kwargs.pop("archivo", self.archivo)
        self.incidencias.append(
            Incidencia(archivo=archivo, severidad=severidad, mensaje=mensaje, **kwargs)
        )

    def fusionar(self, otro: "ResultadoCarga") -> None:
        for tipo, regs in otro.registros.items():
            self.registros.setdefault(tipo, []).extend(regs)
        self.incidencias.extend(otro.incidencias)
        self.archivos.extend(otro.archivos)

    @property
    def total_registros(self) -> int:
        return sum(len(v) for v in self.registros.values())

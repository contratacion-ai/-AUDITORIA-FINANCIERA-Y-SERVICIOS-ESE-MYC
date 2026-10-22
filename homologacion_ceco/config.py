"""Constantes del dominio y carga de la configuración editable (carpeta datos/)."""
import json
from functools import lru_cache
from pathlib import Path

PAQUETE = Path(__file__).resolve().parent
DATOS = PAQUETE / "datos"
REFERENCIA_POR_DEFECTO = PAQUETE / "referencia" / "HOMOLOGACION_CECO_EF_vs_RESOLUCION_ESE_MYC_2026.xlsx"

ACTIVO, VERIFICAR, NO_APLICA, EXCLUIR = "ACTIVO", "VERIFICAR", "NO APLICA", "EXCLUIR"
SIN_HOMOLOGAR = "SIN HOMOLOGAR"
ESTADOS = (ACTIVO, VERIFICAR, NO_APLICA, EXCLUIR)
MOTIVO_ESTADO = {
    VERIFICAR: "CECO en estado VERIFICAR: validar con contabilidad/habilitación antes de cargar",
    NO_APLICA: "CECO NO APLICA: estructura CHIP no habilitada en primer nivel (no debería recibir gasto)",
    EXCLUIR: "CECO EXCLUIR: partida que no es costo",
    SIN_HOMOLOGAR: "CECO sin homologación: no está en el maestro y ninguna regla lo resuelve",
}

PRODUCTIVO = "PRODUCTIVO (final)"
APOYO_ASISTENCIAL = "APOYO ASISTENCIAL (intermedio)"
APOYO_LOGISTICO = "APOYO LOGÍSTICO"
ADMINISTRATIVO = "ADMINISTRATIVO"
TIPOS = (PRODUCTIVO, APOYO_ASISTENCIAL, APOYO_LOGISTICO, ADMINISTRATIVO)

CONFIANZAS = ("Alta", "Media", "Baja")

# Severidad de los registros no cargados.
ERROR, AVISO, INFO = "ERROR", "AVISO", "INFO"


def _json(nombre):
    with open(DATOS / nombre, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=None)
def reglas_nombre():
    return tuple(_json("reglas_nombre.json")["reglas"])


@lru_cache(maxsize=None)
def campos_columnas():
    return _json("columnas.json")["campos"]


@lru_cache(maxsize=None)
def sedes_y_puc():
    return _json("sedes_y_puc.json")


def sede_corta(sede):
    """Primer token de la sede, igual que la fórmula de la llave del maestro: LEFT(I, FIND(" ", I&" ")-1)."""
    sede = (sede or "").strip()
    return sede.split(" ", 1)[0] if sede else ""


def llave(cod_res, sede):
    """Llave de carga CECO|SEDE (columna G del maestro)."""
    return f"{cod_res}|{sede_corta(sede)}"

"""Carga de configuración (YAML) con valores por defecto.

La configuración controla todo lo que el Anexo Técnico no fija o que cada
institución puede querer ajustar sin tocar código: severidad de cada regla,
tolerancias de valores, cómo se determina el hospital y el período, y la
ruta del archivo de homologación.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import yaml

if getattr(sys, "frozen", False):
    # Ejecutable generado con PyInstaller: la salida y la configuración
    # editable viven junto al .exe; los recursos empaquetados, en _MEIPASS.
    RAIZ_PROYECTO = Path(sys.executable).resolve().parent
    _RECURSOS = Path(getattr(sys, "_MEIPASS", RAIZ_PROYECTO))
else:
    RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
    _RECURSOS = RAIZ_PROYECTO
_DIR_CONFIG = RAIZ_PROYECTO / "config" if (RAIZ_PROYECTO / "config").exists() else _RECURSOS / "config"
CONFIG_POR_DEFECTO = _DIR_CONFIG / "configuracion.yaml"
MAPEO_POR_DEFECTO = _DIR_CONFIG / "homologacion_auditoria.yaml"

DEFAULTS: dict[str, Any] = {
    "lectura": {
        # Orden de intento de decodificación. latin-1 nunca falla, por eso va
        # al final como red de seguridad.
        "codificaciones": ["utf-8-sig", "cp1252", "latin-1"],
        # Corrige texto doblemente codificado (p. ej. "EDUCACIÃ“N" -> "EDUCACIÓN"),
        # frecuente cuando un TXT UTF-8 se reabre como ANSI y se vuelve a guardar.
        "reparar_mojibake": True,
        # Si existe XX000123_..._CORREGIDO.txt junto a XX000123.txt, usar el corregido.
        "preferir_corregidos": True,
        # Extensiones que se inventarían pero no se procesan (p. ej. RIPS JSON
        # de la Res. 2275 y facturas XML que vienen en las mismas carpetas).
        "extensiones_ignoradas": [".json", ".xml", ".pdf"],
    },
    "organizacion": {
        # Orden de prioridad para fijar el período (AAAA-MM) de cada remisión:
        #   carpeta  -> nombre de carpeta del mes ("05. Mayo") + año inferido
        #   factura  -> mes más frecuente de la fecha de expedición en AF
        #   remision -> fecha de remisión del CT
        "prioridad_periodo": ["carpeta", "factura", "remision"],
        # Nombre de hospital por código de prestador, usado cuando la ruta no
        # trae carpeta de hospital (p. ej. al cargar un TXT suelto).
        "hospitales_por_prestador": {},
        "hospital_por_defecto": None,
    },
    "validacion": {
        # Tolerancia (en pesos) para comparar valores calculados vs. reportados.
        "tolerancia_valores": 1.0,
        # Severidad por código de regla. Valores: ERROR, ADVERTENCIA, INFO,
        # OMITIR (no se reporta). Ver validacion.REGLAS para la lista completa.
        "severidades": {},
        # Máximo de incidencias por regla y archivo; evita que un error
        # sistemático (p. ej. nombre de entidad largo en cada línea de AF)
        # inunde el reporte. 0 = sin límite.
        "max_incidencias_por_regla_archivo": 0,
    },
    "homologacion": {
        "habilitada": True,
        "archivo_mapeo": str(MAPEO_POR_DEFECTO),
    },
    "exportacion": {
        "excel": True,
        "json": True,
        "consolidado": True,
    },
}


def _fusionar(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _fusionar(out[k], v)
        else:
            out[k] = v
    return out


def cargar_configuracion(ruta: str | Path | None = None) -> dict[str, Any]:
    """Devuelve la configuración efectiva: DEFAULTS + archivo YAML.

    Si ``ruta`` es None se usa ``config/configuracion.yaml`` cuando existe.
    """
    if ruta is None:
        ruta = CONFIG_POR_DEFECTO if CONFIG_POR_DEFECTO.exists() else None
    if ruta is None:
        return copy.deepcopy(DEFAULTS)
    ruta = Path(ruta)
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el archivo de configuración: {ruta}")
    with ruta.open(encoding="utf-8") as fh:
        datos = yaml.safe_load(fh) or {}
    if not isinstance(datos, dict):
        raise ValueError(f"Configuración inválida en {ruta}: se esperaba un mapa YAML")
    cfg = _fusionar(DEFAULTS, datos)
    # Rutas relativas del mapeo se resuelven respecto al archivo de config.
    mapeo = Path(cfg["homologacion"]["archivo_mapeo"])
    if not mapeo.is_absolute():
        candidato = (ruta.parent / mapeo).resolve()
        otros = [RAIZ_PROYECTO / mapeo, _DIR_CONFIG / mapeo.name]
        cfg["homologacion"]["archivo_mapeo"] = str(
            candidato if candidato.exists() else next((o for o in otros if o.exists()), RAIZ_PROYECTO / mapeo))
    return cfg

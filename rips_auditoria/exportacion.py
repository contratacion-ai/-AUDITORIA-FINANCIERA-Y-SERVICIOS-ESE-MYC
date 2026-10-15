"""Exportación de un resultado a disco, organizada por hospital y mes.

Estructura de salida::

    <salida>/
      RIPS_consolidado.xlsx / .json        (todos los hospitales y meses)
      <Hospital>/<AAAA-MM>/
        RIPS_<Hospital>_<AAAA-MM>.xlsx     (plantilla Anexo Técnico con datos)
        RIPS_<Hospital>_<AAAA-MM>.json
        AUDITORIA_<Hospital>_<AAAA-MM>.xlsx (homologado para auditoría financiera)
        AUDITORIA_<Hospital>_<AAAA-MM>.json
      APLICATIVO_AUDITORIA/
        RIPS_AUDITORIA_<AAAA-MM>.xlsx       (para Cargas > RIPS del aplicativo v5,
                                            con todas las sedes acumuladas del mes)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from . import aplicativo, tablero
from .config import carpeta_datos
from .excel import exportar_consolidado_excel, exportar_unidad_excel
from .homologacion import cargar_mapeo, exportar_homologacion_excel, exportar_homologacion_json, homologar
from .json_export import exportar_consolidado_json, exportar_unidad_json

log = logging.getLogger(__name__)


def nombre_seguro(texto: str) -> str:
    limpio = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(texto)).strip(" .")
    return re.sub(r"\s+", " ", limpio) or "SIN_NOMBRE"


def exportar_unidad(resultado, hospital: str, periodo: str, salida: str | Path, cfg: dict | None = None,
                    mapeo: dict | None = None) -> list[Path]:
    """Exporta una combinación hospital-mes de forma independiente."""
    cfg = cfg or resultado.config
    exp = cfg["exportacion"]
    unidad = resultado.unidad(hospital, periodo)
    carpeta = Path(salida) / nombre_seguro(hospital) / nombre_seguro(periodo)
    base = f"{nombre_seguro(hospital)}_{nombre_seguro(periodo)}"
    archivos: list[Path] = []
    if exp.get("excel", True):
        archivos.append(exportar_unidad_excel(unidad, carpeta / f"RIPS_{base}.xlsx"))
    if exp.get("json", True):
        archivos.append(exportar_unidad_json(unidad, carpeta / f"RIPS_{base}.json"))
    if cfg["homologacion"].get("habilitada", True):
        mapeo = mapeo or cargar_mapeo(cfg["homologacion"]["archivo_mapeo"])
        hom = homologar(unidad, mapeo)
        titulo = f"{hospital} - {periodo}"
        if exp.get("excel", True):
            archivos.append(exportar_homologacion_excel(hom, unidad.conciliacion, carpeta / f"AUDITORIA_{base}.xlsx",
                                                        titulo))
        if exp.get("json", True):
            archivos.append(exportar_homologacion_json(hom, unidad.conciliacion, carpeta / f"AUDITORIA_{base}.json",
                                                       {"hospital": hospital, "periodo": periodo}))
    return archivos


def exportar_todo(resultado, salida: str | Path, cfg: dict | None = None, hospital: str | None = None,
                  periodo: str | None = None) -> list[Path]:
    """Exporta todas las unidades hospital-mes (opcionalmente filtradas) y el consolidado."""
    cfg = cfg or resultado.config
    salida = Path(salida)
    salida.mkdir(parents=True, exist_ok=True)
    mapeo = cargar_mapeo(cfg["homologacion"]["archivo_mapeo"]) if cfg["homologacion"].get("habilitada", True) else None
    archivos: list[Path] = []
    for h, p in resultado.unidades():
        if (hospital and h != hospital) or (periodo and p != periodo):
            continue
        log.info("Exportando %s / %s", h, p)
        archivos += exportar_unidad(resultado, h, p, salida, cfg, mapeo)
    app = cfg.get("aplicativo_auditoria") or {}
    if app.get("habilitado", True):
        log.info("Generando archivos mensuales para el aplicativo de auditoría")
        acumulado = app.get("carpeta_acumulado") or carpeta_datos() / "acumulado_aplicativo"
        archivos += aplicativo.exportar_para_aplicativo(resultado, salida, app, acumulado, hospital, periodo)
        # Indicadores por sede-mes (usuarios, atenciones, errores…) para el tablero de análisis
        tablero.actualizar_indicadores(resultado, acumulado, hospital, periodo)
    if cfg["exportacion"].get("consolidado", True) and not (hospital or periodo):
        if cfg["exportacion"].get("excel", True):
            log.info("Exportando consolidado Excel")
            archivos.append(exportar_consolidado_excel(resultado, salida / "RIPS_consolidado.xlsx"))
        if cfg["exportacion"].get("json", True):
            log.info("Exportando consolidado JSON")
            archivos.append(exportar_consolidado_json(resultado, salida / "RIPS_consolidado.json"))
    return archivos

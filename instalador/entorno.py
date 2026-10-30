"""Entorno de la aplicación instalada (Windows, por usuario, sin permisos de administrador).

* Programa: ``%LOCALAPPDATA%\\Programs\\AuditoriaRIPS`` (``python\\`` portátil + ``app\\``).
* Datos del usuario (RIPS, base consolidada, maestros, informes, configuración editable):
  ``%LOCALAPPDATA%\\AuditoriaRIPS``. Se conservan al actualizar o desinstalar.

La configuración editable (red de prestadores, parámetros, criterios) se copia a la carpeta de datos la
primera vez y desde ahí se usa, para que las actualizaciones del programa no borren los cambios. Los
archivos normativos (diccionario RIPS, catálogos, control oficial, plantillas) se renuevan siempre.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent
LOCAL = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "AuditoriaRIPS"
DATOS = LOCAL / "datos"
CONFIG = LOCAL / "config"
INFORMES = LOCAL / "informes"
PUERTO = int(os.environ.get("RIPS_PUERTO", "8765"))
EDITABLES = {"red_prestadores.yaml", "parametros_contrato.yaml", "criterios_evento.yaml"}


def preparar() -> None:
    for d in (DATOS, CONFIG, INFORMES):
        d.mkdir(parents=True, exist_ok=True)
    origen = APP / "config"
    for f in origen.rglob("*"):
        if f.is_dir():
            continue
        destino = CONFIG / f.relative_to(origen)
        destino.parent.mkdir(parents=True, exist_ok=True)
        if f.name not in EDITABLES or not destino.exists():
            shutil.copy2(f, destino)
    os.environ["RIPS_DATA_DIR"] = str(DATOS)
    os.environ["RIPS_CONFIG_DIR"] = str(CONFIG)
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))
    os.chdir(LOCAL)


def registro():
    """pythonw no tiene consola: la salida va a un archivo de registro."""
    log = open(LOCAL / "registro.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
    return log


def mensaje(titulo: str, texto: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, texto, titulo, 0x40)
    except Exception:
        print(f"{titulo}: {texto}")

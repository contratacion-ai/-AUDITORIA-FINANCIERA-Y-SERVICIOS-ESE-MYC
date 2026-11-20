# -*- mode: python ; coding: utf-8 -*-
# Especificación PyInstaller del ejecutable CargueRIPS.
# Uso: pyinstaller --noconfirm empaquetado/CargueRIPS.spec   (desde la raíz del proyecto)
#
# Genera dist/CargueRIPS/ en modo carpeta ("onedir"): arranca mucho más rápido
# que un único .exe con pandas adentro. Junto al ejecutable se copian config/,
# plantillas/ y el README para que la configuración siga siendo editable.

import shutil
from pathlib import Path

RAIZ = Path(SPECPATH).parent

a = Analysis(
    [str(RAIZ / "empaquetado" / "lanzador_cargue.py")],
    pathex=[str(RAIZ)],
    datas=[(str(RAIZ / "config"), "config")],  # copia de respaldo dentro del paquete
    hiddenimports=["xlsxwriter", "openpyxl", "yaml"],
    excludes=["pytest", "matplotlib", "IPython", "notebook", "scipy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CargueRIPS",
    console=True,  # muestra el avance y el resumen del cargue
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="CargueRIPS", upx=False)

destino = Path(DISTPATH) / "CargueRIPS"
for carpeta in ("config", "plantillas"):
    shutil.copytree(RAIZ / carpeta, destino / carpeta, dirs_exist_ok=True)
shutil.copy2(RAIZ / "README.md", destino / "LEAME.md")
(destino / "salida").mkdir(exist_ok=True)

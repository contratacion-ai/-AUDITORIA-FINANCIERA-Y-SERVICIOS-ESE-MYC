#!/usr/bin/env bash
# Genera el ejecutable CargueRIPS para Linux/macOS (el .exe de Windows se
# construye con construir_exe.bat en un equipo Windows).
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Ejecute primero ./instalar.sh" >&2; exit 1; }
.venv/bin/python -m pip install "pyinstaller>=6" --quiet
.venv/bin/python -m PyInstaller --noconfirm --clean empaquetado/CargueRIPS.spec
echo "Ejecutable: dist/CargueRIPS/CargueRIPS"

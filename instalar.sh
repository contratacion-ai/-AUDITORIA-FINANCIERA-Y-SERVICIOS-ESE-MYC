#!/usr/bin/env bash
# Instalador - Auditoría RIPS (Res. 3374 de 2000) para Linux / macOS.
# Uso: ./instalar.sh   (requiere Python 3.10 o superior)
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "[ERROR] Se requiere Python 3.10 o superior (https://www.python.org/downloads/)." >&2
  exit 1
fi
echo "[1/3] Python $($PY -c 'import platform; print(platform.python_version())') encontrado."

if [ ! -x .venv/bin/python ]; then
  echo "[2/3] Creando entorno de la aplicación en .venv ..."
  "$PY" -m venv .venv
else
  echo "[2/3] Entorno .venv ya existe; se actualizará."
fi

echo "[3/3] Instalando dependencias ..."
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/python -m pip install -r requirements.txt --quiet
.venv/bin/python -m rips_auditoria --version
chmod +x iniciar_cargue.sh

echo
echo "Instalación completada. Para cargar RIPS:"
echo "  ./iniciar_cargue.sh                 (pregunta qué cargar)"
echo "  ./iniciar_cargue.sh lote.zip        (carga directa)"

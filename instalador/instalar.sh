#!/usr/bin/env bash
# Instalador para Linux/macOS: crea el entorno, instala dependencias y (opcional) carga datos de demostración.
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || true)
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)'; then
    echo "Se necesita Python 3.10 o superior (instálelo con el gestor de paquetes del sistema)."; exit 1
fi
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
if [ -f demo/control_ese.db ] && [ ! -f datos/control_ese.db ]; then
    read -r -p "¿Cargar datos de DEMOSTRACIÓN (cifras ficticias)? [s/N] " r
    if [[ "$r" =~ ^[sS]$ ]]; then
        mkdir -p datos && cp demo/control_ese.db datos/control_ese.db
        echo "Usuarios de prueba: admin / Demo2026* (central)   fortul / Demo2026* (solo Hospital de Fortul)"
    fi
fi
echo "Instalación terminada. Para iniciar:  ./iniciar_app.sh"

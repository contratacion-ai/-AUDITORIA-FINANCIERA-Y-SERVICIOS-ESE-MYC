#!/usr/bin/env bash
# Inicia el cargue de RIPS. Sin argumentos pregunta qué cargar.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "La aplicación no está instalada. Ejecute primero ./instalar.sh" >&2
  exit 1
fi
# Sin argumentos abre la ventana de cargue en el navegador; con rutas, cargue en consola.
if [ $# -eq 0 ]; then exec .venv/bin/python -m rips_auditoria.web; fi
exec .venv/bin/python -m rips_auditoria.cargue "$@"

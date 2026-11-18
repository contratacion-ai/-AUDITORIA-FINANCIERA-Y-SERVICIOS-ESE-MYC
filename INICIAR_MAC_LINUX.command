#!/usr/bin/env bash
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
[ -z "$PY" ] && { echo "Instale Python 3.11+ desde https://www.python.org/downloads/"; exit 1; }
if [ ! -x .venv/bin/python ]; then
  echo "Preparando entorno (solo la primera vez)..."
  "$PY" -m venv .venv && .venv/bin/python -m pip install -q -r requirements.txt || { echo "Error instalando dependencias"; exit 1; }
fi
(sleep 4; (command -v open >/dev/null && open http://localhost:8000) || (command -v xdg-open >/dev/null && xdg-open http://localhost:8000)) &
echo "Abra http://localhost:8000  (Ctrl+C para salir)"
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

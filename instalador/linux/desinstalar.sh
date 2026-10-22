#!/usr/bin/env bash
# Quita el servicio de Costos ABC. Conserva los datos salvo con --borrar-todo.
set -uo pipefail
DESTINO="${DESTINO:-/opt/costos-abc}"
[[ $EUID -eq 0 ]] || { echo "Ejecute con sudo."; exit 1; }
systemctl disable --now costos-abc 2>/dev/null; rm -f /etc/systemd/system/costos-abc.service; systemctl daemon-reload
[[ -f "$DESTINO/docker-compose.yml" ]] && (cd "$DESTINO" && docker compose down 2>/dev/null)
rm -f /etc/cron.daily/costos-abc-respaldo
if [[ "${1:-}" == "--borrar-todo" ]]; then
  read -r -p "Se borrarán la base de datos y los respaldos. Escriba BORRAR: " R
  [[ "$R" == "BORRAR" ]] && rm -rf "$DESTINO" && echo "Eliminado."
else
  echo "Servicio eliminado. Datos conservados en $DESTINO/datos y $DESTINO/.env"
fi

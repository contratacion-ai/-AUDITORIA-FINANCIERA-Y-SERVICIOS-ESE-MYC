#!/usr/bin/env bash
# Instalador para Linux y macOS: sin sudo, en la carpeta del usuario, con entorno virtual propio
# (no modifica el Python del sistema ni otras herramientas). Volver a ejecutarlo actualiza y conserva los datos.
set -euo pipefail

ORIGEN="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ "$(uname)" == "Darwin" ]]; then
  DESTINO="${AUDITORIA_ESE_DESTINO:-$HOME/Library/Application Support/AuditoriaESE}"
else
  DESTINO="${AUDITORIA_ESE_DESTINO:-${XDG_DATA_HOME:-$HOME/.local/share}/auditoria-ese}"
fi
PYTHON="${PYTHON:-python3}"

command -v "$PYTHON" >/dev/null || { echo "Se requiere Python 3.11 o superior ($PYTHON no encontrado)."; exit 1; }
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "Se requiere Python 3.11 o superior."; exit 1; }

echo "Instalando en: $DESTINO (sin permisos de administrador)"
mkdir -p "$DESTINO/datos"

if [[ ! -x "$DESTINO/venv/bin/python" ]]; then
  "$PYTHON" -m venv "$DESTINO/venv"
fi
"$DESTINO/venv/bin/python" -m pip install --disable-pip-version-check -q --upgrade pip
"$DESTINO/venv/bin/python" -m pip install --disable-pip-version-check -q --only-binary=:all: -r "$ORIGEN/requirements.txt"

rm -rf "$DESTINO/app"
mkdir -p "$DESTINO/app"
for item in auditoria conciliacion ese_auditoria rips_conciliacion templates escritorio ejemplos manage.py requirements.txt README.md LICENSE; do
  [[ -e "$ORIGEN/$item" ]] && cp -R "$ORIGEN/$item" "$DESTINO/app/"
done
find "$DESTINO/app" -name __pycache__ -type d -prune -exec rm -rf {} +

cat > "$DESTINO/iniciar.sh" <<EOF
#!/usr/bin/env bash
export AUDITORIA_ESE_DATOS="$DESTINO/datos"
cd "$DESTINO/app" && exec "$DESTINO/venv/bin/python" escritorio/lanzador.py "\$@"
EOF
chmod +x "$DESTINO/iniciar.sh"

if [[ "$(uname)" != "Darwin" ]]; then
  mkdir -p "$HOME/.local/share/applications"
  cat > "$HOME/.local/share/applications/auditoria-ese.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Auditoria ESE
Comment=Auditoría financiera y de servicios - ESE Moreno y Clavijo
Exec="$DESTINO/iniciar.sh"
Terminal=true
Categories=Office;
EOF
fi

"$DESTINO/venv/bin/python" -c "import django, pandas, waitress, whitenoise, rarfile, openpyxl; print('Librerías OK')"
echo
echo "Instalación terminada. Para abrir la herramienta ejecute:"
echo "  \"$DESTINO/iniciar.sh\""

#!/usr/bin/env bash
# Genera dist/CostosABC-Setup-<versión>.exe (sin administrador) y dist/CostosABC-Servidor-Setup-<versión>.exe
# (servidor de red): instaladores gráficos para Windows 64 bits que incluyen
# Python 3.12 portátil (paquete oficial de CPython publicado en NuGet) y todas las dependencias.
# Requiere: makensis (apt install nsis / choco install nsis), python3 con pip, curl, unzip.
set -euo pipefail
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"
PY_VERSION="${PY_VERSION:-3.12.10}"
VERSION="${VERSION:-$(date +%Y.%m.%d)-$(git rev-parse --short HEAD)}"
CACHE="$RAIZ/dist/cache"; mkdir -p "$CACHE" dist
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
STAGE="$TMP/CostosABC"; mkdir -p "$STAGE"

echo "==> Aplicación"
git ls-files -z --cached --others --exclude-standard | grep -zv -e '^tests/' -e '^homologacion_ceco/tests/' -e '^instalador/linux/' | xargs -0 -I{} cp --parents {} "$STAGE"/
cp instalador/LEAME_INSTALACION.txt "$STAGE/LEAME_INSTALACION.txt"
echo "$VERSION" > "$STAGE/VERSION"

echo "==> Python $PY_VERSION portátil para Windows"
NUPKG="$CACHE/python.$PY_VERSION.nupkg"
[[ -f "$NUPKG" ]] || curl -fsSL -o "$NUPKG" "https://api.nuget.org/v3-flatcontainer/python/$PY_VERSION/python.$PY_VERSION.nupkg"
unzip -q "$NUPKG" 'tools/*' -d "$TMP/nupkg"
mv "$TMP/nupkg/tools" "$STAGE/python"

echo "==> Dependencias (win_amd64, cp312)"
PYV="$(echo "$PY_VERSION" | cut -d. -f1,2)"
# Los marcadores de plataforma se evalúan aquí (Linux): se ajusta la lista para Windows explícitamente.
grep -v -i '^gunicorn' requirements.txt > "$TMP/req-win.txt"
echo "tzdata" >> "$TMP/req-win.txt"   # Windows no trae la base de zonas horarias (America/Bogota)
python3 -m pip install -q --target "$STAGE/python/Lib/site-packages" --platform win_amd64 --python-version "$PYV" \
  --implementation cp --only-binary=:all: --no-compile -r "$TMP/req-win.txt"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
# Traducciones de Django: solo español e inglés (miles de archivos menos → instalación más rápida)
find "$STAGE/python/Lib/site-packages/django" -type d -name locale | while read -r d; do
  find "$d" -mindepth 1 -maxdepth 1 -type d ! -name es ! -name es_CO ! -name en -exec rm -rf {} +
done

echo "==> Compilando los instaladores"
# Por usuario (sin permisos de administrador) y servidor de red (requiere administrador)
SALIDA="$RAIZ/dist/CostosABC-Setup-$VERSION.exe"
makensis -V2 -DVERSION="$VERSION" -DSTAGE="$STAGE" -DSALIDA="$SALIDA" instalador/windows/costosabc.nsi
SALIDA_SRV="$RAIZ/dist/CostosABC-Servidor-Setup-$VERSION.exe"
makensis -V2 -DSERVIDOR -DVERSION="$VERSION" -DSTAGE="$STAGE" -DSALIDA="$SALIDA_SRV" instalador/windows/costosabc.nsi
echo "Instalador sin administrador: $SALIDA ($(du -h "$SALIDA" | cut -f1))"
echo "Instalador de servidor:       $SALIDA_SRV ($(du -h "$SALIDA_SRV" | cut -f1))"

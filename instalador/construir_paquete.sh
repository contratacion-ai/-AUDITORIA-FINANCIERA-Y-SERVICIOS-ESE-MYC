#!/usr/bin/env bash
# Construye dist/CostosABC-<version>.zip: aplicación + instaladores + dependencias (wheels) para instalar sin internet
# en Windows (64 bits) y Linux (x86_64), Python 3.11, 3.12 y 3.13.
set -euo pipefail
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"
VERSION="${VERSION:-$(date +%Y.%m.%d)-$(git rev-parse --short HEAD)}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
DEST="$TMP/CostosABC"
mkdir -p "$DEST" dist
git ls-files -z --cached --others --exclude-standard | grep -zv '^tests/' | xargs -0 -I{} cp --parents {} "$DEST"/
if [[ "${SIN_WHEELS:-0}" != "1" ]]; then
  mkdir -p "$DEST/wheels"
  for plat in win_amd64 manylinux2014_x86_64; do
    for py in 3.11 3.12 3.13; do
      python3 -m pip download -q -r requirements.txt -d "$DEST/wheels" --only-binary=:all: \
        --platform "$plat" --python-version "$py" --implementation cp
    done
  done
fi
cp instalador/LEAME_INSTALACION.txt "$DEST/LEAME_INSTALACION.txt"
echo "$VERSION" > "$DEST/VERSION"
(cd "$TMP" && zip -qr9 "$RAIZ/dist/CostosABC-$VERSION.zip" CostosABC)
echo "Paquete: dist/CostosABC-$VERSION.zip ($(du -h "dist/CostosABC-$VERSION.zip" | cut -f1))"

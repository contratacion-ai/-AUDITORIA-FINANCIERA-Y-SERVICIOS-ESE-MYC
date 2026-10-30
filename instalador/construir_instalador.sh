#!/usr/bin/env bash
# Construye el instalador Windows (por usuario, sin administrador) desde Linux o macOS.
#
# Requisitos: bash, curl, unzip, python3 con pip, Pillow (icono) y NSIS (makensis).
#   Debian/Ubuntu:  sudo apt-get install nsis unzip      ·  pip install pillow
#
# Uso:  bash instalador/construir_instalador.sh
# Resultado: instalador/dist/Instalador_AuditoriaRIPS_<versión>.exe
#
# Incluye Python 3.12 portátil para Windows x64 (paquete oficial "python" de NuGet) y las dependencias
# como ruedas win_amd64, de modo que el equipo destino no necesita Python ni internet ni administrador.
set -euo pipefail
cd "$(dirname "$0")"
RAIZ="$(cd .. && pwd)"
PYVER="3.12.7"
VERSION="$(python3 -c "import re;print(re.search(r'\"(.+)\"', open('$RAIZ/rips_auditoria/__init__.py').read().split('__version__')[1]).group(1))")"
B="build/AuditoriaRIPS"
rm -rf build dist && mkdir -p "$B" dist build/cache

echo "== Python portátil $PYVER (win x64)"
curl -sSfL -o build/cache/python.nupkg "https://api.nuget.org/v3-flatcontainer/python/$PYVER/python.$PYVER.nupkg"
unzip -q build/cache/python.nupkg "tools/*" -d build/cache/py
mv build/cache/py/tools "$B/python"
rm -rf "$B/python/Lib/test" "$B/python/Lib/idlelib" "$B/python/Lib/tkinter" "$B/python/Lib/turtledemo" \
       "$B/python/tcl" "$B/python/Lib/ensurepip"

echo "== Dependencias (ruedas win_amd64, cp312)"
grep -vE '^(#|pytest|httpx)' "$RAIZ/requirements.txt" | sed '/^$/d' > build/req.txt
printf 'colorama\ntzdata\n' >> build/req.txt
python3 -m pip install -q --no-compile --target "$B/python/Lib/site-packages" --platform win_amd64 \
  --python-version 3.12 --implementation cp --only-binary=:all: -r build/req.txt
find "$B/python/Lib/site-packages" -type d \( -name tests -o -name __pycache__ \) -prune -exec rm -rf {} +
rm -rf "$B/python/Lib/site-packages/bin"

echo "== Aplicación"
mkdir -p "$B/app"
cp -r "$RAIZ/rips_auditoria" "$RAIZ/api" "$RAIZ/config" "$RAIZ/referencias" "$B/app/"
cp "$RAIZ"/instalador/{entorno.py,iniciar.pyw,sincronizar.pyw,detener.pyw,detener_silencioso.pyw} "$B/app/"
cp "$RAIZ/README.md" "$B/app/LEAME.md"
find "$B/app" -name __pycache__ -prune -exec rm -rf {} +

echo "== Icono"
python3 - <<'PY'
from PIL import Image, ImageDraw
im = Image.new("RGBA", (256, 256), (31, 78, 121, 255))
d = ImageDraw.Draw(im)
d.rounded_rectangle([16, 16, 240, 240], 40, fill=(31, 78, 121, 255), outline=(255, 255, 255, 255), width=10)
d.rectangle([108, 56, 148, 200], fill="white")
d.rectangle([56, 108, 200, 148], fill="white")
im.save("build/icono.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
PY

echo "== NSIS"
makensis -V2 -DVERSION="$VERSION" instalador.nsi
ls -la dist/

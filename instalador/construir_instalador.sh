#!/usr/bin/env bash
# Construye el instalador de Windows (.exe) desde Linux o macOS, sin Windows:
#   bash instalador/construir_instalador.sh [carpeta_salida]
# Requisitos: makensis (apt install nsis · brew install makensis), python3 con pip, curl, unzip, internet.
# Empaqueta: Python 3.12 oficial para Windows x64 (paquete NuGet «python» de la PSF) + librerías de
# instalador/requirements-windows.txt (ruedas win_amd64) + el aplicativo. El .exe resultante funciona sin internet.
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
SALIDA="$(mkdir -p "${1:-$RAIZ/dist}" && cd "${1:-$RAIZ/dist}" && pwd)"
PYVER="3.12.10"
VERSION="$(python3 -c "import re;print(re.search(r'VERSION = \"([^\"]+)\"', open('$RAIZ/app/db.py').read())[1])")"
TMP="$(mktemp -d)"; STAGE="$TMP/stage"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$STAGE"

echo "▶ Python $PYVER para Windows (NuGet)…"
curl -fsSL -o "$TMP/python.nupkg" "https://api.nuget.org/v3-flatcontainer/python/$PYVER/python.$PYVER.nupkg"
unzip -q "$TMP/python.nupkg" "tools/*" -d "$TMP/nuget"
mv "$TMP/nuget/tools" "$STAGE/python"

echo "▶ Librerías (ruedas win_amd64 · cp312)…"
python3 -m pip install -q --disable-pip-version-check --target "$STAGE/python/Lib/site-packages" --platform win_amd64 \
  --python-version 3.12 --implementation cp --abi cp312 --only-binary=:all: -r "$RAIZ/instalador/requirements-windows.txt"

echo "▶ Limpieza (pruebas de Python, Tk, cachés)…"
rm -rf "$STAGE/python/Lib/test" "$STAGE/python/Lib/idlelib" "$STAGE/python/Lib/tkinter" "$STAGE/python/Lib/turtledemo" \
       "$STAGE/python/tcl" "$STAGE/python/include" "$STAGE/python/libs" "$STAGE/python/Lib/site-packages/bin"
rm -f "$STAGE/python/DLLs/"_tkinter.pyd "$STAGE/python/DLLs/"tcl*.dll "$STAGE/python/DLLs/"tk*.dll
find "$STAGE/python" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$STAGE/python/Lib/site-packages" -path "*/tests" -type d -prune -exec rm -rf {} +

echo "▶ Aplicativo…"
cp -r "$RAIZ/app" "$RAIZ/static" "$STAGE/"
find "$STAGE/app" -name __pycache__ -type d -prune -exec rm -rf {} +
cp "$RAIZ/iniciar.py" "$RAIZ/LEEME_PRIMERO.txt" "$RAIZ/README.md" "$RAIZ/RECOMENDACIONES_MEJORA.md" "$RAIZ/LICENSE" "$RAIZ/instalador/icono.ico" "$STAGE/"
mkdir -p "$STAGE/datos_demo" && python3 "$RAIZ/tests/generar_datos_demo.py" "$STAGE/datos_demo" >/dev/null

echo "▶ Compilando instalador con NSIS…"
EXE="$SALIDA/Instalador_Auditoria_ESE_MyC_v$VERSION.exe"
makensis -V2 -DSTAGE="$STAGE" -DVERSION="$VERSION" -DOUTFILE="$EXE" "$RAIZ/instalador/aplicativo.nsi"
echo "✔ $EXE ($(du -h "$EXE" | cut -f1))"

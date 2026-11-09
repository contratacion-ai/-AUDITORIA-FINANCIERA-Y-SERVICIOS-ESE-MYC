#!/usr/bin/env bash
# Construye el instalador "todo incluido" para Windows 10/11 de 64 bits.
#
# Resultado: dist/Instalador_ConciliacionRIPS_ESE_<version>.zip
#   - Python 3.12 oficial (paquete NuGet "python" de la Python Software Foundation, versión portable)
#   - Todas las librerías ya instaladas para Windows (no descarga nada al instalar: funciona sin Internet)
#   - La aplicación y el instalador INSTALAR_WINDOWS.bat (por usuario, sin administrador)
#
# Se ejecuta en Linux o macOS con Python 3, curl y zip (no requiere Windows).
#   bash instalador/construir_paquete_windows.sh
set -euo pipefail

PY_VERSION="${PY_VERSION:-3.12.10}"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"      # carpeta conciliacion_rips_ese
TRABAJO="$(mktemp -d)"
trap 'rm -rf "$TRABAJO"' EXIT
FECHA="$(date +%Y%m%d)"
NOMBRE="Instalador_ConciliacionRIPS_ESE_${FECHA}"
SALIDA="$RAIZ/dist"
DESTINO="$TRABAJO/$NOMBRE"

echo "==> Descargando Python $PY_VERSION para Windows (NuGet, Python Software Foundation)"
curl -fsSL -o "$TRABAJO/python.nupkg" "https://api.nuget.org/v3-flatcontainer/python/$PY_VERSION/python.$PY_VERSION.nupkg"
mkdir -p "$TRABAJO/nupkg"
(cd "$TRABAJO/nupkg" && unzip -q "$TRABAJO/python.nupkg")

echo "==> Copiando la aplicación"
mkdir -p "$DESTINO"
for item in auditoria conciliacion ese_auditoria rips_conciliacion templates escritorio ejemplos instalador \
            manage.py requirements.txt README.md INSTALAR_WINDOWS.bat .gitattributes; do
  cp -R "$RAIZ/$item" "$DESTINO/"
done
rm -rf "$DESTINO/instalador/windows/offline" "$DESTINO/dist"
find "$DESTINO" -name __pycache__ -type d -prune -exec rm -rf {} +

PYDIR="$DESTINO/instalador/windows/offline/python_listo"
mkdir -p "$PYDIR"
cp -R "$TRABAJO/nupkg/tools/." "$PYDIR/"

echo "==> Instalando librerías para Windows de 64 bits dentro del Python portable"
python3 -m pip install --disable-pip-version-check -q \
  --target "$PYDIR/Lib/site-packages" --upgrade \
  --platform win_amd64 --python-version "${PY_VERSION%.*}" --implementation cp --only-binary=:all: \
  -r "$RAIZ/requirements.txt"

# Aísla este Python: con un archivo ._pth ignora PYTHONPATH, PYTHONHOME, el registro y cualquier
# otra instalación de Python del equipo. "..\app" es la carpeta de la aplicación ya instalada.
MAYOR_MENOR="$(echo "$PY_VERSION" | awk -F. '{print $1$2}')"
printf 'python%s.zip\r\nLib\r\nDLLs\r\nLib\\site-packages\r\n..\\app\r\nimport site\r\n' "$MAYOR_MENOR" \
  > "$PYDIR/python${MAYOR_MENOR}._pth"
echo "$PY_VERSION" > "$PYDIR/VERSION_CONCILIACION_RIPS_ESE.txt"

cat > "$DESTINO/LEAME_PRIMERO.txt" <<'EOF'
CONCILIACION FACTURACION vs RIPS - ESE MORENO Y CLAVIJO
=======================================================

INSTALAR (Windows 10/11 de 64 bits, NO requiere administrador ni Internet):
  1. Descomprima este archivo ZIP (clic derecho > Extraer todo...).
  2. Abra la carpeta extraida y haga doble clic en INSTALAR_WINDOWS.bat
  3. Al terminar, la herramienta se abre en su navegador. Despues abrala con el
     acceso directo "Conciliacion RIPS ESE" del escritorio o del menu Inicio.

- Se instala solo para su usuario en %LOCALAPPDATA%\ConciliacionRIPS_ESE
- Trae su propio Python aislado: no modifica otros programas ni otras herramientas.
- Funciona en cualquier navegador: la ventana de inicio muestra la direccion
  (por ejemplo http://127.0.0.1:8640/) para pegarla en Edge, Chrome, Firefox, etc.
- Para ACTUALIZAR, ejecute de nuevo INSTALAR_WINDOWS.bat de una version nueva:
  sus datos se conservan.
- Para DESINSTALAR: menu Inicio > Conciliacion RIPS ESE > Desinstalar.
- La herramienta NO pide usuario ni contrasena. Solo el panel opcional
  "Administracion" (/admin) los pide, y no trae ninguno de fabrica: creelo con
  menu Inicio > Conciliacion RIPS ESE > Crear usuario administrador.

Si Windows muestra "Windows protegio su PC", pulse "Mas informacion" y luego
"Ejecutar de todas formas" (el instalador es un script, no un programa firmado).
EOF
sed -i 's/$/\r/' "$DESTINO/LEAME_PRIMERO.txt"

echo "==> Comprimiendo"
mkdir -p "$SALIDA"
rm -f "$SALIDA/$NOMBRE.zip"
(cd "$TRABAJO" && zip -qr -9 "$SALIDA/$NOMBRE.zip" "$NOMBRE")
echo "Instalador listo: $SALIDA/$NOMBRE.zip ($(du -h "$SALIDA/$NOMBRE.zip" | cut -f1))"

#!/usr/bin/env bash
# =============================================================================
# Construye el instalador de Windows del Cargue RIPS (desde Linux o macOS).
#
#   bash instalador/construir_instalador.sh <motor_python_windows>
#
# <motor_python_windows> es un Python 3.12 de Windows x64 completo (con Lib/ y
# DLLs/), en cualquiera de estas formas:
#   * el instalador del aplicativo de auditoría (Instalador_Auditoria_ESE_MyC_v5.0.exe
#     o su .zip): se toma su carpeta python/ (Python oficial 3.12);
#   * una carpeta con python.exe, python312.dll, Lib/ y DLLs/.
# Las librerías (pandas, openpyxl, XlsxWriter, PyYAML, FastAPI, uvicorn…) se
# descargan de PyPI como binarios para Windows (requiere internet al construir).
#
# Requisitos: python3, pip, makensis (NSIS 3), 7z (p7zip) para extraer el .exe,
# zip y Pillow (para las imágenes del asistente).
# Resultado: dist/Instalador_Cargue_RIPS_ESE_MyC_v<versión>.exe y su .zip
# =============================================================================
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
MOTOR="${1:-}"
[ -n "$MOTOR" ] || { echo "Uso: $0 <instalador v5 .exe/.zip | carpeta con python.exe de Windows>" >&2; exit 1; }
for h in python3 makensis zip; do command -v "$h" >/dev/null || { echo "Falta la herramienta: $h" >&2; exit 1; }; done

VERSION="$(cd "$RAIZ" && python3 -c 'import rips_auditoria; print(rips_auditoria.__version__)')"
BUILD="$RAIZ/build/instalador"
APP="$BUILD/app"
DIST="$RAIZ/dist"
rm -rf "$BUILD"; mkdir -p "$APP" "$DIST" "$BUILD/extraido"

echo "[1/6] Motor Python de Windows desde: $MOTOR"
ORIGEN=""
if [ -d "$MOTOR" ]; then
  ORIGEN="$MOTOR"
else
  case "${MOTOR,,}" in
    *.zip) (cd "$BUILD/extraido" && unzip -q "$MOTOR") ;;
    *.exe) command -v 7z >/dev/null || { echo "Para extraer un .exe instale 7z (p7zip-full)" >&2; exit 1; }
           7z x -y -o"$BUILD/extraido" "$MOTOR" >/dev/null ;;
    *) echo "Formato de motor no reconocido: $MOTOR" >&2; exit 1 ;;
  esac
  # ZIP que contiene el instalador .exe del v5
  EXE_INTERNO="$(find "$BUILD/extraido" -maxdepth 2 -iname 'Instalador_*.exe' | head -1)"
  if [ -n "$EXE_INTERNO" ]; then 7z x -y -o"$BUILD/extraido/v5" "$EXE_INTERNO" >/dev/null; fi
fi
if [ -z "$ORIGEN" ]; then
  ORIGEN="$(dirname "$(find "$BUILD/extraido" -iname python.exe -path '*python*' | head -1)")"
fi
[ -f "$ORIGEN/python.exe" ] || { echo "No se encontró python.exe en el motor indicado" >&2; exit 1; }
DLL="$(ls "$ORIGEN" | grep -Ei '^python3[0-9]+\.dll$' | head -1)"
PYVER="$(echo "$DLL" | sed -E 's/python3([0-9]+)\.dll/3.\1/I')"
[ -d "$ORIGEN/Lib" ] || { echo "El motor no trae Lib/ (use un Python completo, no el 'embeddable')" >&2; exit 1; }
echo "      Python $PYVER para Windows ($DLL)"
mkdir -p "$APP/python"
( cd "$ORIGEN" && tar cf - --exclude='Lib/site-packages' --exclude='__pycache__' . ) | ( cd "$APP/python" && tar xf - )
mkdir -p "$APP/python/Lib/site-packages"

echo "[2/6] Librerías para Windows x64 / Python $PYVER (PyPI)"
grep -vE '^\s*(#|$)' "$RAIZ/requirements.txt" > "$BUILD/requisitos.txt"
python3 -m pip install --quiet --no-compile --upgrade --target "$APP/python/Lib/site-packages" \
  --platform win_amd64 --python-version "$PYVER" --implementation cp --only-binary=:all: \
  -r "$BUILD/requisitos.txt"
rm -rf "$APP/python/Lib/site-packages/bin"

echo "[3/6] Aplicación"
( cd "$RAIZ" && tar cf - --exclude='__pycache__' --exclude='*.pyc' rips_auditoria config plantillas LICENSE README.md ) | ( cd "$APP" && tar xf - )
cp "$RAIZ/empaquetado/iniciar_instalado.py" "$APP/iniciar.py"
cp "$RAIZ/empaquetado/permitir_red.bat" "$APP/permitir_red.bat"
cp "$RAIZ/rips_auditoria/web/static/icono_ese.ico" "$APP/icono.ico"
cat > "$APP/LEAME.txt" <<EOF
CARGUE DE RIPS (Res. 3374 de 2000) - ESE MORENO Y CLAVIJO        versión $VERSION
=================================================================================

ABRIR: acceso "Cargue RIPS ESE MyC" (Escritorio o menú Inicio > ESE Moreno y Clavijo).
Se abre una ventana minimizada (el servidor local; no la cierre) y el navegador con la
ventana de cargue en http://localhost:8100.

CARGAR:
 1. Arrastre el ZIP descargado, la carpeta del hospital o los TXT, o escriba la ruta de la
    carpeta en este equipo (recomendado para lotes grandes).
 2. Deje marcado "Enviar cada mes al aplicativo de auditoría" para que cada mes quede
    cargado en la pestaña Cargas > RIPS del aplicativo de Auditoría de Facturación.
 3. Clic en "Iniciar cargue". Al terminar verá el resumen por hospital y mes, el resultado
    del envío y la validación del cruce del aplicativo.

ENLACE CON EL APLICATIVO DE AUDITORÍA (v5):
 - Si está cerrado, el cargue lo abre solo (si está instalado en este equipo).
 - El archivo de cada mes lleva TODAS las sedes procesadas (el aplicativo reemplaza el mes
   completo en cada carga), así que puede cargar un hospital a la vez sin perder los demás.
 - Si en el aplicativo ya cargó PARAM CONTRATO (Software 360), se usan sus nombres de
   contrato y su modalidad.

TABLERO DE ANÁLISIS: botón "Tablero de análisis" (http://localhost:8100/tablero).
 Por sede, mes, empresa (EPS), servicio y CUPS; comparativos entre parámetros de mayor a
 menor; frecuencias de uso mes a mes y por sede; alertas con acciones recomendadas; análisis
 automático; consolidado RIPS completo (CSV) e informe Excel para la gerencia. Usa los mismos
 datos homologados que se envían al aplicativo de auditoría y permite reenviar meses.

INFORME POR CUPS ENRIQUECIDO (pestaña "CUPS enriquecido" del tablero): cargue el Excel/CSV
 complementario por CUPS y el tarifario del Manual UVB (.xlsb). Se agregan sus columnas al
 informe y se completan y guardan las descripciones que faltan.

DEPURACIÓN DE DATOS (pestaña "Depuración de datos"): limpiar un mes, una sede o ambos antes de
 volver a cargar, depurar duplicados (siempre con vista previa y confirmación; lo eliminado
 queda en la carpeta "papelera") y cargar RIPS en JSON (Res. 2275) sin duplicar.

CARGUE SIN LÍMITE: se pueden subir carpetas con miles de TXT y archivos con todas sus líneas.

USO EN RED (otros computadores):
 1. Una sola vez, en el equipo servidor: menú Inicio > ESE Moreno y Clavijo >
    "Permitir acceso en red" (pide administrador; solo redes privadas/de dominio).
 2. Abra "Cargue RIPS ESE MyC (en red)". La ventana muestra las direcciones, p. ej.
      Cargue y tablero:  http://192.168.1.10:8100
      Auditoria (v5):    http://192.168.1.10:8000
    y la CLAVE DE ACCESO que deben escribir los demás equipos.
 3. Deje abiertos en el servidor esa ventana y el aplicativo de Auditoría.
 Clave fija: config\configuracion.yaml > red > clave. Sin clave fija se genera una y se
 guarda en Documentos\Cargue RIPS ESE MyC\acceso_red.json.

RESULTADOS: Documentos\\Cargue RIPS ESE MyC\\cargue_<fecha>\\
  <Hospital>\\<AAAA-MM>\\RIPS_*.xlsx       datos validados (estructura del Anexo Técnico No. 1)
  <Hospital>\\<AAAA-MM>\\AUDITORIA_*.xlsx  homologación para auditoría financiera
  APLICATIVO_AUDITORIA\\RIPS_AUDITORIA_<AAAA-MM>.xlsx  archivo para Cargas > RIPS

CONFIGURACIÓN: carpeta "config" de la instalación (configuracion.yaml y
homologacion_auditoria.yaml). Se conserva al actualizar.

Si Windows muestra "Windows protegió su equipo": Más información > Ejecutar de todas formas.
EOF
sed -i 's/$/\r/' "$APP/LEAME.txt"

echo "[4/6] Imágenes del asistente (logo y colores de la ESE)"
python3 "$RAIZ/instalador/recursos.py" "$BUILD/recursos" >/dev/null

echo "[5/6] Compilando el instalador con NSIS"
SALIDA="$DIST/Instalador_Cargue_RIPS_ESE_MyC_v$VERSION.exe"
makensis -V2 -DVERSION="$VERSION" -DAPPDIR="$APP" -DRECURSOS="$BUILD/recursos" -DSALIDA="$SALIDA" \
  "$RAIZ/instalador/instalador.nsi"

echo "[6/6] Empaquetando ZIP"
( cd "$DIST" && rm -f "Instalador_Cargue_RIPS_ESE_MyC_v$VERSION.zip" && \
  cp "$APP/LEAME.txt" LEAME_PRIMERO.txt && \
  zip -q -9 "Instalador_Cargue_RIPS_ESE_MyC_v$VERSION.zip" "$(basename "$SALIDA")" LEAME_PRIMERO.txt && rm LEAME_PRIMERO.txt )
ls -la "$DIST"
echo "Listo: $SALIDA"

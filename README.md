# AUDITORÍA FINANCIERA Y DE SERVICIOS – ESE MORENO Y CLAVIJO

Aplicación para realizar auditoría financiera y de servicios de una ESE de primer nivel.

## Módulo `rips_auditoria`: RIPS (Res. 3374 de 2000) → auditoría financiera

Aplicación en **Python + pandas** que carga archivos RIPS en TXT (sueltos, por carpeta o en ZIP,
incluidos ZIP dentro de ZIP), los **valida** contra el **Anexo Técnico No. 1 de la Resolución 3374
de 2000**, los **organiza por hospital y luego por mes**, y los **exporta a Excel y JSON** con la
estructura del Anexo, además de una versión **homologada** para la herramienta de auditoría
financiera.

### Instalador de Windows (recomendado)

`Instalador_Cargue_RIPS_ESE_MyC_v<versión>.exe`: asistente en español con el logo y los colores de
la ESE (verde y vino tinto). **No requiere internet, Python ni permisos de administrador**: trae su
propio Python 3.12 y todas las librerías (las mismas versiones de pandas/numpy que el aplicativo de
Auditoría v5).

- Programa: `%LOCALAPPDATA%\Programs\CargueRIPS_ESE_MyC` · Resultados y acumulado:
  `Documentos\Cargue RIPS ESE MyC` (se conservan al actualizar; al desinstalar se pregunta).
- Accesos: **Escritorio** «Cargue RIPS ESE MyC» y **menú Inicio › ESE Moreno y Clavijo** (cargue,
  resultados, guía, desinstalar y, si está instalado, «Auditoria de Facturacion ESE MyC»).
- Al abrir aparece una ventana minimizada (el servidor local) y el navegador con la **ventana de
  cargue** (`http://localhost:8100`): arrastrar ZIP/carpeta/TXT o escribir la ruta, avance por fases,
  resumen por hospital y mes, incidencias, descarga de archivos y envío al aplicativo de auditoría.
- Construirlo (desde Linux/macOS, sin Windows):
  `bash instalador/construir_instalador.sh <Instalador_Auditoria_ESE_MyC_v5.0.zip | carpeta con python.exe>`
  → `dist/`. Toma el Python 3.12 de Windows del instalador v5 y descarga de PyPI las librerías para
  Windows x64. Requiere `makensis` (NSIS 3), `7z`, `zip` y Pillow.
- Windows SmartScreen puede advertir que no está firmado: «Más información» › «Ejecutar de todas formas».

### Enlace con el aplicativo de Auditoría de Facturación ESE MyC (v5)

Las dos aplicaciones trabajan juntas por la API local del aplicativo (`http://localhost:8000`):

| Sentido | Qué hace |
|---|---|
| Cargue → Auditoría | Genera `APLICATIVO_AUDITORIA/RIPS_AUDITORIA_<AAAA-MM>.xlsx` en la **plantilla RIPS homologada del aplicativo** (MES, EPS, CONTRATO, MODALIDAD, SEDE, FACTURA_FEV, CODIGO_CUPS_CUMS, VALOR…, hoja `CONSOLIDADO RIPS`) y, si se marca, lo **envía** a `POST /api/cargas/rips` y espera el estado «ok». Si el aplicativo está cerrado y está instalado, lo abre. |
| Auditoría → Cargue | Lee **PARAM CONTRATO** (`/api/parametros`) para usar el nombre exacto del contrato de Software 360 y su modalidad, y trae la **validación del cruce** (`/api/conciliacion/validacion-cruce`) para mostrarla al terminar. |

- **Acumulado por mes:** el aplicativo reemplaza el mes completo en cada carga de RIPS; por eso el
  archivo de cada mes lleva **todas las sedes procesadas** (se guardan en `acumulado_aplicativo`).
  Reprocesar una sede reemplaza sólo esa sede.
- Línea de comandos: `python -m rips_auditoria procesar lote.zip -s salida/ --enviar-aplicativo`
  (`--sin-aplicativo` para no generar el archivo mensual). Configuración en
  `config/configuracion.yaml › aplicativo_auditoria` (contratos, EPS/NIT por código, modalidad).
- Verificado contra el código real del aplicativo v5: los 8 meses del ZIP de Hospital San Antonio
  quedaron cargados en estado «ok», con sedes y EPS homologadas a los nombres de Software 360.

### Tablero de análisis (`/tablero`)

Botón **«Tablero de análisis»** de la ventana de cargue (`http://localhost:8100/tablero`). Lee el
**mismo acumulado homologado que se envía al aplicativo de auditoría** (`acumulado_aplicativo/<AAAA-MM>.csv`)
más `indicadores.csv` (usuarios únicos, registros por archivo, errores y diferencias por sede-mes, que
cada cargue guarda). Filtros por período, sede, empresa (EPS), servicio, modalidad, contrato y CUPS.

| Pestaña | Contenido |
|---|---|
| Resumen | Valor de servicios y FEV, facturas, cantidad, usuarios, atenciones por usuario, alertas; tendencias mensuales; valor por sede, EPS y servicio; análisis automático y acciones recomendadas |
| Comparativos | Tabla cruzada de **cualquier par de parámetros** (sede, mes, EPS, servicio, CUPS, contrato, modalidad, validación) × medida (valor, cantidad, facturas, registros, valor FEV), de mayor a menor, con mapa de calor, total, participación y variación del último mes |
| Frecuencias de uso | Atenciones por usuario y cantidad de servicios mes a mes y por sede; cantidad por servicio y mes |
| RIPS por empresa / sede / servicio / CUPS | Gráfico de mayor a menor y tablas completas (EPS × sede, contrato, modalidad, sede × mes, indicadores de cargue, CUPS × sede, CUPS × mes) |
| Consolidado RIPS | Todas las filas homologadas, con búsqueda, orden y **CSV completo** |
| Alertas y análisis | Alertas priorizadas (CRÍTICA, ALTA, MEDIA, INFORMATIVA) con acción recomendada: meses sin RIPS, variaciones mensuales, facturas con diferencia AF, servicios sin factura, facturas sin servicios, valores en cero, concentración por EPS, valor unitario atípico de un CUPS entre sedes, errores de estructura y cambios en la frecuencia de uso. Umbrales en `configuracion.yaml › tablero` |
| Aplicativo de auditoría | Reenvía meses del acumulado al aplicativo (todas las sedes) y muestra su validación del cruce |

**Informe Excel** para la gerencia (resumen, análisis, recomendaciones, gráficos, alertas, comparativos y
consolidado completo; el consolidado se divide en hojas de 1.000.000 de filas si hace falta).

**Sin límite de archivos ni de líneas:** el formulario de carga admite carpetas con miles de TXT (Starlette
limita por defecto un formulario a 1000 archivos; aquí se amplió) y cada TXT se procesa completo.

### Informe RIPS por CUPS enriquecido, depuración de datos y JSON (v1.3)

**Informe enriquecido** (Tablero › «CUPS enriquecido»; también se aplica a «RIPS por CUPS» y a su hoja del
informe Excel): el informe conserva sus columnas y agrega al final:

- Las columnas del **archivo complementario** (Excel .xlsx/.xlsb o CSV con `CODIGO_CUPS_CUMS`), unidas por
  código. «promedio mes» (o «pormedio mes»), las columnas de EPS (`nueva eps`, `sanitas`, `coosalud`,
  `fomag`…), `otras`, `evento`, `capita` y `pgp` **se calculan** con los datos cargados y los filtros
  (valor de servicios; `catalogo_cups.medida`). EPS + otras = valor; evento + capita = valor.
- La **tarifa del tarifario UVB** (p. ej. `SOAT-2026-UVB (tarifario)`) y `fuente_descripcion`.
- **Descripciones faltantes** completadas en este orden: RIPS (AM/AT) → archivo complementario → tarifario
  UVB. Se guardan en `catalogos/descripciones_cups.csv` y se reutilizan; se actualizan al cargar un
  catálogo y en cada cargue. Los catálogos también se guardan (no hay que volver a cargarlos).

**Depuración de datos** (Tablero › «Depuración de datos» o `python -m rips_auditoria datos …`):

| Acción | Cómo |
|---|---|
| Limpiar por mes, sede o ambos | «Ver qué se eliminaría» → lista y resumen → casilla de confirmación + diálogo → «Confirmar eliminación». Consola: `datos limpiar --mes 2026-05 --sede "Hospital San Antonio"` (pide escribir SI) |
| Duplicados reales | Filas idénticas y facturas repetidas en otro mes (misma sede, factura, código, cantidad y valor); se conserva la del mes más antiguo. Consola: `datos duplicados` |
| JSON | RIPS JSON de la Res. 2275 o filas homologadas, en el cargue normal, en la pestaña o con `datos json <rutas> [--hospital] [--mes]` |
| Catálogos | Pestaña «CUPS enriquecido» o `datos catalogo --complemento RIPS_cups.csv --tarifario Tarifa_UVB.xlsb` |

Nada se borra sin vista previa y confirmación explícita; la confirmación lleva una huella de los datos vistos
(si cambiaron, hay que volver a revisar) y lo eliminado se copia en `acumulado_aplicativo/papelera/`.
Los JSON se depuran con la misma lógica de los TXT (sede y códigos normalizados, registros repetidos dentro
del JSON contados una vez, agregación por la llave de la plantilla) y sólo se agregan las filas que no estén
ya cargadas en ningún mes. Nota: reprocesar los TXT de una sede-mes la reemplaza completa (incluidas filas
que vinieron de JSON); vuelva a cargar el JSON si lo necesita.

### Uso desde otros computadores de la red

Acceso **«Cargue RIPS ESE MyC (en red)»** (o `python -m rips_auditoria.web --red`, o
`red.habilitada: true`). En el equipo servidor:

- La ventana de cargue y el tablero quedan en `http://<IP del servidor>:8100`.
- El aplicativo de auditoría v5 (que sólo escucha en 127.0.0.1) se publica con un **puente** en
  `http://<IP del servidor>:8000`, sin modificarlo.
- Los equipos remotos deben escribir la **clave de acceso** que muestra la ventana del servidor
  (`red.clave` para fijarla; si no, se genera una y se guarda en `acceso_red.json`). 5 intentos
  fallidos bloquean esa IP 2 minutos. El propio servidor no pide clave.
- Una vez, «Permitir acceso en red» crea la regla del Firewall de Windows (puertos 8000-8010 y
  8100-8110, sólo redes privadas o de dominio).

### Instalación desde el código (Windows con Python)

1. Instale Python 3.10 o superior desde <https://www.python.org/downloads/> y marque
   **"Add python.exe to PATH"**.
2. Descargue esta aplicación (botón *Code → Download ZIP* en GitHub) y descomprímala, por
   ejemplo en `C:\AuditoriaRIPS`.
3. Doble clic en **`instalar.bat`**. Crea un entorno propio (`.venv`), instala las librerías
   (requiere internet) y deja el acceso directo **"Cargue RIPS"** en el escritorio.
4. Doble clic en **"Cargue RIPS"** (o `iniciar_cargue.bat`) abre la ventana de cargue en el
   navegador; si **arrastra** un ZIP o carpeta sobre `iniciar_cargue.bat`, el cargue se hace en
   consola y al terminar se abre la carpeta de resultados.

**Linux / macOS**: `./instalar.sh` y luego `./iniciar_cargue.sh lote.zip` (sin argumentos
pregunta qué cargar).

Volver a ejecutar el instalador actualiza las librerías sin perder la configuración.

**Ejecutable para equipos sin Python (`CargueRIPS.exe`)**

En **un** equipo Windows con la aplicación instalada, doble clic en **`construir_exe.bat`**.
Genera `dist\CargueRIPS\` y `dist\CargueRIPS_v<versión>.zip`. Ese ZIP se copia a los demás
equipos, se descomprime en una carpeta del usuario (p. ej. *Documentos*, no en *Archivos de
programa*) y se usa con doble clic en `CargueRIPS.exe` o arrastrando el ZIP de RIPS sobre él.
Junto al `.exe` quedan `config\` (editable), `plantillas\` y `salida\`. El ejecutable se
construye con PyInstaller a partir de `empaquetado/CargueRIPS.spec`; en Linux/macOS el
equivalente es `./construir_exe.sh`.

### Instalación manual (desarrolladores)

```bash
pip install -r requirements.txt          # pandas, openpyxl, XlsxWriter, PyYAML, FastAPI, uvicorn
pip install -r requirements-dev.txt      # + pytest y httpx, para ejecutar las pruebas
```

Requiere Python 3.10 o superior.

### Uso rápido (línea de comandos)

```bash
# Lote completo: ZIP con carpetas Hospital/NN. Mes/<remisión>/Rips/*.txt
python -m rips_auditoria procesar Hospital_San_Antonio.zip -s salida/

# Varios ZIP, carpetas y TXT a la vez
python -m rips_auditoria procesar lote1.zip "D:/RIPS/Hospital X" AF000123.txt -s salida/

# TXT sueltos de una remisión (el hospital no está en la ruta: se indica)
python -m rips_auditoria procesar CT000123.txt AF000123.txt US000123.txt AC000123.txt \
    --hospital "Hospital San Antonio" -s salida/

# Sólo una combinación hospital-mes
python -m rips_auditoria procesar lote.zip -s salida/ --filtro-hospital "Hospital San Antonio" --filtro-mes 2026-05

# Ver qué hospitales/meses trae una carga, sin exportar
python -m rips_auditoria listar lote.zip

# Plantilla Excel vacía del Anexo Técnico No. 1
python -m rips_auditoria plantilla -s plantillas/Plantilla_RIPS_Res3374_AnexoTecnico1.xlsx
```

Opciones de `procesar`: `-c/--config`, `-m/--mapeo`, `--sin-excel`, `--sin-json`,
`--sin-consolidado`, `--sin-homologacion`, `-v` (detalle). Códigos de salida: `0` procesado
(aunque haya incidencias), `1` error de uso/configuración, `2` no se encontraron archivos RIPS.

### Uso desde Python

```python
from rips_auditoria import procesar
from rips_auditoria.exportacion import exportar_unidad, exportar_todo

res = procesar(["Hospital_San_Antonio.zip"])
res.unidades()                      # [("Hospital San Antonio", "2026-01"), ...]
u = res.unidad("Hospital San Antonio", "2026-05")
u.datos["AC"]                       # DataFrame de consultas de ese hospital-mes
u.incidencias                       # incidencias de validación de ese hospital-mes
u.conciliacion                      # AF vs. servicios, factura por factura
u.resumen()                         # cifras clave
exportar_unidad(res, "Hospital San Antonio", "2026-05", "salida/")
```

### Salida

```
salida/
├── RIPS_consolidado.xlsx | .json        todos los hospitales y meses
└── <Hospital>/<AAAA-MM>/
    ├── RIPS_<Hospital>_<AAAA-MM>.xlsx     plantilla del Anexo con los datos
    ├── RIPS_<Hospital>_<AAAA-MM>.json
    ├── AUDITORIA_<Hospital>_<AAAA-MM>.xlsx homologado para auditoría financiera
    └── AUDITORIA_<Hospital>_<AAAA-MM>.json
```

**Excel RIPS** (misma estructura que `plantillas/Plantilla_RIPS_Res3374_AnexoTecnico1.xlsx`):
`Resumen`, `Remisiones`, una hoja por archivo del Anexo (`CT AF US AD AC AP AU AH AN AM AT`) con
los campos en el orden y con el nombre del Anexo (el comentario de cada encabezado muestra nombre
técnico, longitud, tipo, obligatoriedad y valores permitidos; los campos con tabla tienen lista
desplegable), `Conciliacion_Facturas`, `Resumen_Incidencias`, `Incidencias`, `Diccionario`,
`Catalogos` y `Reglas_Validacion`. Las columnas grises (hospital, período, remisión, archivo,
línea) permiten rastrear cada registro hasta el TXT original.

**JSON**: `{metadatos, hospital, periodo, resumen, remisiones, registros: {CT, AF, ..., AT},
conciliacion_facturas, incidencias}`; el consolidado anida
`hospitales → <hospital> → meses → <AAAA-MM> → {...}`. Los nombres de campo son los técnicos
del `Diccionario`; los valores numéricos válidos salen como número y las fechas en el formato
del Anexo (`dd/mm/aaaa`).

### Validaciones (Res. 3374, Anexo Técnico No. 1 y Art. 9)

Ninguna validación detiene el proceso: todo queda como incidencia con hospital, período,
remisión, archivo, línea, campo, valor, regla, severidad y mensaje.

| Nivel | Reglas |
|---|---|
| Carga | ZIP corrupto, archivo ilegible, nombre no reconocido o no normativo (tipo + remisión de 1 a 6 dígitos), duplicados, versión `_CORREGIDO` usada, codificación distinta de UTF-8, texto con doble codificación reparado |
| Estructura | cantidad de campos (el registro se excluye), coma final, comillas, línea vacía, archivo vacío, registro duplicado |
| Campo | obligatoriedad (incluida la condicionada, p. ej. datos del vinculado), longitud máxima, fecha `dd/mm/aaaa`, hora `hh:mm`, número con punto decimal, formato CIE-10, tabla de valores permitidos |
| Remisión | archivos obligatorios (CT, AF, US), CT vs. archivos y totales, factura del servicio existe en AF, usuario existe en US, facturas sin servicios, valor total = cantidad × unitario, neto = valor − cuota, coherencia de fechas, factura fuera del período |
| Financiera | conciliación del neto AF contra la suma de servicios RIPS (`DIFERENCIA_VALOR_FACTURA`) |

La severidad de cada regla (`ERROR`, `ADVERTENCIA`, `INFO`, `OMITIR`) se cambia en
`config/configuracion.yaml` → `validacion.severidades`.

### Organización por hospital y mes

* **Remisión**: archivos con el mismo número de remisión en la misma carpeta.
* **Hospital** (en orden): `--hospital` → carpeta anterior a la del mes → primera carpeta de la
  ruta → `hospitales_por_prestador` de la configuración → nombre del ZIP → `PRESTADOR_<código>`.
* **Mes** (`organizacion.prioridad_periodo`): carpeta del mes (`05. Mayo`, `MAYO 2026`,
  `2026-05`; el año se infiere de las facturas) → mes más frecuente de expedición en AF →
  fecha de remisión del CT. Cada remisión pertenece a un único mes; las facturas expedidas en
  otro mes se advierten (`FACTURA_FUERA_DE_PERIODO`).

### Homologación con la herramienta de auditoría financiera

El esquema destino se define en `config/homologacion_auditoria.yaml`, **sin tocar código**.
El perfil por defecto replica las hojas y columnas de la plantilla de carga masiva
`Plantilla_RIPS_Servicios` (estructura de la Res. 2275 de 2023): `Usuarios`, `Consultas`,
`Procedimientos`, `Medicamentos`, `Hospitalizaciones`, `Urgencias`, `RecienNacidos`,
`OtrosServicios`, más `Transacciones` (cabecera de factura con valores) y `numFactura` en cada
servicio. Cada columna se define con `origen`, `plantilla`, `constante` o `condicional`, y
opcionalmente `transformacion`, `tabla` de equivalencias y `por_defecto`. El archivo valida su
propia definición antes de procesar y explica qué corregir. Los supuestos de cada equivalencia
están en la lista `supuestos` del YAML y se copian a la hoja `Supuestos` del Excel de auditoría.

### Decisiones ante vacíos de la norma (documentadas en el código)

* **Catálogos posteriores**: los RIPS reales ya usan códigos de normas posteriores (documento
  CN/PT, finalidad 11–44, causa 21–49). Se reportan como `ADVERTENCIA`
  (`VALOR_CATALOGO_EXTENDIDO`), no como error.
* **Código del prestador**: el Anexo fija 10 posiciones pero el REPS usa 12 (10 + sede); se
  aceptan 12.
* **Registros mal formados**: no se intenta "acomodar" un registro con campos de más o de menos
  (p. ej. una coma dentro de un nombre); se rechaza y se reporta con la línea original.
* **Fin de línea `\r\r\n`** (algunos software de facturación) se trata como un solo salto.
* **Conciliación**: neto AF = servicios (AC neto + AP + AM + AT, o AD si sólo hay agrupado) −
  copago − descuentos. En facturas de **cápita / PyM global** la diferencia es esperada (el AF
  trae el valor del contrato y los RIPS valoran por evento) y debe leerse como tal.
* **Archivos JSON/XML** de la Res. 2275 que vienen en las mismas carpetas se inventarían como
  ignorados (este módulo procesa TXT de la Res. 3374).

### Pruebas

```bash
python -m pytest -q
```

Cubren: estructura del Anexo, remisión válida, remisión con errores de estructura y contenido,
ZIP con varios hospitales (con ZIP anidado y ZIP corrupto), TXT sueltos, carpeta de mes,
versión corregida, homologación (por defecto y personalizada, validación del mapeo), plantilla,
exportación Excel/JSON, CLI, ventana web (incluido un cargue de más de 1000 archivos), tablero
(agregados, filtros, comparativos, tablas, CSV, Excel, alertas) y acceso en red (clave, bloqueo, puente).
Los datos de prueba son sintéticos (`tests/fabrica.py`).

### Estructura

```
rips_auditoria/
  especificacion.py   Anexo Técnico No. 1: campos, longitudes, tipos, tablas (única fuente de verdad)
  lectura.py          TXT, carpetas, ZIP/ZIP anidados; remisiones; hospital y mes desde la ruta
  parser.py           parseo del TXT con manejo de errores por registro y codificación
  validacion.py       validaciones de campo, cruzadas y conciliación de facturas
  procesamiento.py    orquestación y resultado consultable por hospital-mes
  excel.py            plantilla y exportación Excel parametrizadas
  json_export.py      exportación JSON (por partes, apta para lotes grandes)
  homologacion.py     capa de mapeo configurable a la herramienta de auditoría
  exportacion.py      escritura de archivos por hospital/mes y consolidado
  cli.py              línea de comandos
  cargue.py           inicio guiado del cargue en consola (selección de archivos, abre la salida)
  aplicativo.py       enlace con el aplicativo de Auditoría ESE MyC (plantilla RIPS, acumulado, API)
  tablero.py          tablero de análisis: agregados, comparativos, frecuencias, alertas, informe Excel
  catalogo_cups.py    archivo complementario y tarifario UVB: informe por CUPS enriquecido y descripciones
  depuracion.py       limpieza por mes/sede y duplicados, con vista previa y confirmación
  json_rips.py        lectura de RIPS JSON (Res. 2275 u homologados) e integración sin duplicar
  web/                ventana de cargue y tablero (FastAPI + páginas con la imagen de la ESE)
  web/red.py          acceso en red con clave y puente hacia el aplicativo de auditoría
config/
  configuracion.yaml           parámetros (severidades, período, hospitales, exportación)
  homologacion_auditoria.yaml  mapeo a la herramienta de auditoría y supuestos
plantillas/
  Plantilla_RIPS_Res3374_AnexoTecnico1.xlsx
instalar.bat / instalar.sh               instalador
construir_exe.bat / construir_exe.sh     genera el ejecutable CargueRIPS (PyInstaller, opcional)
instalador/                              instalador de Windows (NSIS): script, imágenes y construcción
empaquetado/                             especificación PyInstaller y punto de entrada
iniciar_cargue.bat / iniciar_cargue.sh   inicio del cargue
tests/
```

Rendimiento de referencia (lote de un hospital, 8 meses, 2.947 TXT, 562.220 registros): lectura y
validación ≈ 25 s; exportación completa con consolidado ≈ 6 min (el consolidado anual es la parte más
pesada; `--sin-consolidado` lo omite).

> Los datos reales de pacientes y las salidas (`salida/`, `*.zip`) están excluidos del
> repositorio en `.gitignore`.

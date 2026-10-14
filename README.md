# AUDITORÍA FINANCIERA Y DE SERVICIOS – ESE MORENO Y CLAVIJO

Aplicación para realizar auditoría financiera y de servicios de una ESE de primer nivel.

## Módulo `rips_auditoria`: RIPS (Res. 3374 de 2000) → auditoría financiera

Aplicación en **Python + pandas** que carga archivos RIPS en TXT (sueltos, por carpeta o en ZIP,
incluidos ZIP dentro de ZIP), los **valida** contra el **Anexo Técnico No. 1 de la Resolución 3374
de 2000**, los **organiza por hospital y luego por mes**, y los **exporta a Excel y JSON** con la
estructura del Anexo, además de una versión **homologada** para la herramienta de auditoría
financiera.

### Instalación

```bash
pip install -r requirements.txt          # pandas, openpyxl, XlsxWriter, PyYAML
pip install -r requirements-dev.txt      # + pytest, para ejecutar las pruebas
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
exportación Excel/JSON y CLI. Los datos de prueba son sintéticos (`tests/fabrica.py`).

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
config/
  configuracion.yaml           parámetros (severidades, período, hospitales, exportación)
  homologacion_auditoria.yaml  mapeo a la herramienta de auditoría y supuestos
plantillas/
  Plantilla_RIPS_Res3374_AnexoTecnico1.xlsx
tests/
```

Rendimiento de referencia (lote de un hospital, 8 meses, 2.947 TXT, 562.220 registros): lectura y
validación ≈ 25 s; exportación completa con consolidado ≈ 6 min (el consolidado anual es la parte más
pesada; `--sin-consolidado` lo omite).

> Los datos reales de pacientes y las salidas (`salida/`, `*.zip`) están excluidos del
> repositorio en `.gitignore`.

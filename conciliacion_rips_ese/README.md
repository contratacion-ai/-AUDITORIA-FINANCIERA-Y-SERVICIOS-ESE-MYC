# Auditoría financiera y de servicios – ESE Moreno y Clavijo

Aplicación para la auditoría financiera y de servicios de una ESE de primer nivel. Su primer
procedimiento es la **conciliación entre facturación y RIPS**: carga los RIPS (TXT, JSON, ZIP, RAR)
y la facturación (incluida la facturación por ámbito), y muestra las diferencias por sede, hospital,
mes, factura, servicio, CUMS y código. Las diferencias se convierten en **hallazgos de auditoría**
con prioridad, acción recomendada y seguimiento.

```
rips_conciliacion/   Motor en Python/Pandas, independiente de Django (también se usa por consola)
auditoria/           Herramienta de auditoría: encargos de auditoría y hallazgos
conciliacion/        App Django: carga y gestión de archivos, procesamiento, tablero y exportación
templates/           Interfaz web en español
escritorio/          Lanzador de escritorio (puerto libre, abre el navegador, datos del usuario)
instalador/          Instaladores sin administrador para Windows y Linux/macOS
ejemplos/            Archivos de demostración (ZIP 3374, RAR 2275, facturación, catálogo, equivalencias)
tests/               Pruebas automáticas (pytest)
```

## Instalación para usuarios (sin permisos de administrador)

### Windows 10/11 (64 bits)

1. Descargue el proyecto (botón **Code → Download ZIP** en GitHub) y descomprímalo.
2. Abra la carpeta **`conciliacion_rips_ese`** y haga doble clic en **`INSTALAR_WINDOWS.bat`**.
3. Al terminar, la herramienta se abre en el navegador. Después se abre con el acceso directo
   **"Conciliacion RIPS ESE"** del escritorio o del menú Inicio.

El instalador:
- **No pide permisos de administrador.** Instala todo en `%LOCALAPPDATA%\ConciliacionRIPS_ESE`, la carpeta del
  usuario.
- **No interfiere con otras herramientas.** Trae su propio Python aislado (versión "embebida"), así que
  no modifica el PATH, el registro, el Python que ya tenga el equipo ni otros programas. Los accesos
  directos son solo del usuario. La herramienta escucha solo en `127.0.0.1` (no se expone en la red ni
  pide abrir el firewall). Busca un puerto libre a partir del 8640 y nunca usa uno ocupado por otro
  programa. Si ya está abierta, reutiliza la instancia en lugar de abrir otra.
- **Convive con las demás herramientas del repositorio.** Todo el código está en su propia carpeta
  (`conciliacion_rips_ese/`), así que unir esta rama no modifica archivos de otras herramientas. Usa
  nombres propios: carpeta `ConciliacionRIPS_ESE`, accesos "Conciliacion RIPS ESE" y puertos desde el
  8640. No coinciden con los de otras herramientas de la ESE (por ejemplo, las que usan 8765, 8000 o
  `AuditoriaESE_MyC`).
- **Funciona en cualquier navegador** (Edge, Chrome, Firefox, Brave, Opera). Se abre en el navegador
  predeterminado y la ventana de inicio muestra la dirección (por ejemplo, `http://127.0.0.1:8640/`)
  para pegarla en otro. No usa CDN ni dependencias de Internet al ejecutarse: las gráficas vienen
  incluidas.
- **Se actualiza volviendo a ejecutarlo.** Reemplaza la aplicación y **conserva los datos**
  (`%LOCALAPPDATA%\ConciliacionRIPS_ESE\datos`: base de datos y archivos cargados).
- **Se desinstala** desde *Inicio → Conciliacion RIPS ESE → Desinstalar*. Pregunta antes de borrar los datos.
- **Panel de Administración (opcional):** la herramienta se usa sin iniciar sesión. El panel
  `/admin/` sí pide usuario y contraseña, y **no trae ninguno de fábrica**. Para crearlo, abra
  *Inicio → Conciliacion RIPS ESE → Crear usuario administrador*, escriba un nombre y una contraseña
  (no se ven al escribir). Si olvida la contraseña, ejecútelo de nuevo con el mismo nombre para
  restablecerla.

**Paquete "todo incluido" (recomendado para distribuir en la ESE):**
`bash instalador/construir_paquete_windows.sh`, ejecutado en Linux o macOS, genera
`dist/Instalador_ConciliacionRIPS_ESE_<fecha>.zip` (unos 70 MB). El paquete trae Python 3.12 oficial
(paquete NuGet de la Python Software Foundation) con todas las librerías ya instaladas para Windows.
Se descomprime, se hace doble clic en `INSTALAR_WINDOWS.bat` y el instalador solo copia archivos:
no descarga nada ni necesita Internet. Python va dentro de un único `python.zip` que el instalador abre
directamente en `%LOCALAPPDATA%`. Así el paquete solo tiene unos 100 archivos con rutas cortas y se puede
descomprimir con el Explorador de Windows sin el error *"Ruta de acceso demasiado larga" (0x80010135)*.

**Equipos sin Internet o con navegación restringida:** en un equipo con Internet ejecute
`instalador\windows\preparar_offline.ps1` (clic derecho → *Ejecutar con PowerShell*). Eso crea la carpeta
`instalador\windows\offline` con Python y todas las librerías. Copie el proyecto completo al equipo
destino y ejecute `INSTALAR_WINDOWS.bat`.

> Si una política de grupo de la ESE bloquea los scripts de PowerShell, el área de sistemas puede
> permitir este instalador; no requiere privilegios elevados.

### Linux / macOS

```bash
bash conciliacion_rips_ese/instalador/linux_mac/instalar.sh
```

Requiere Python 3.11 o superior. Instala en `~/.local/share/conciliacion-rips-ese` (Linux) o en
`~/Library/Application Support/ConciliacionRIPS_ESE` (macOS) con un entorno virtual propio, sin `sudo`.
Para abrir la herramienta use el script `iniciar.sh` que indica el instalador.

## Instalación para desarrollo

Todos los comandos se ejecutan dentro de la carpeta `conciliacion_rips_ese/`.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py createsuperuser   # opcional, para /admin
python manage.py runserver
```

Abra http://127.0.0.1:8000. Para probar con datos de ejemplo: `python manage.py cargar_demo`.

**RAR:** los RAR comprimidos se extraen con una herramienta que ya tenga el equipo, sin instalar nada
más. En Windows se detectan automáticamente WinRAR, 7-Zip o el `tar.exe` que trae Windows 10/11. En
Linux/macOS se usa `unrar`, `unar`, `7z` o `bsdtar`. Si no hay ninguna, la herramienta informa qué
archivo no pudo extraer y sigue con el resto. Los RAR "almacenados" (sin compresión) se leen siempre.

## Uso con los archivos reales de la ESE

1. Pulse **⬆ Cargar datos** en el inicio. Esto crea la conciliación dentro de una auditoría nueva o de una
   existente.
2. **Cargar archivos en cualquier formato.** Se pueden seleccionar varios a la vez; el tipo y el formato se
   reconocen **por el contenido**, no por la extensión:

   | Qué | Formatos aceptados |
   |---|---|
   | Paquetes | ZIP, RAR, 7Z, GZ, TAR (.tar.gz, .tgz, .tar.bz2, .tar.xz), anidados en cualquier combinación |
   | RIPS oficiales | TXT de la Res. 3374 (AF, US, AC, AP, AU, AH, AN, AM, AT, CT), con cualquier extensión y en UTF-8, ANSI o UTF-16; JSON de la Res. 2275 |
   | RIPS en hoja de cálculo | Excel o CSV con los nombres de columna oficiales (`numFactura`, `codConsulta`, `vrServicio`… o los del anexo 3374), o un libro con hojas `AC`, `AM`… sin encabezado. Si la tabla no tiene estructura oficial, se procesa por las columnas reconocidas (factura, código, cantidad, valor) y la versión se informa como **NO DETERMINADA** |
   | Facturación | XLSX, XLSM, XLS (97-2003), XLSB, ODS (LibreOffice), CSV/TXT con `;`, `,`, `|` o tabulador, JSON (lista de registros), XML (registros repetidos) y cualquiera de ellos dentro de un comprimido |

   - **RIPS por sede:** paquetes con los meses en carpetas (`RIPS_SEDE_TAME.zip/ENERO_2024/AF000123.txt`,
     `.../2024-02/FE2001.json`).
   - **Facturación por ámbito:** un libro con una hoja por ámbito (Ambulatorio, Urgencias…) o un archivo por
     ámbito. Las columnas se reconocen por nombre (`Numero Factura`, `Factura`, `CUPS`, `CUMS`, `Codigo`,
     `Cantidad`, `Valor Total`, `Valor Unitario`, `Fecha Factura`, `Mes`, `Sede`, `Hospital`, `Ambito`,
     `Farmacia`…), aunque el encabezado no esté en la primera fila. Las filas de "TOTAL" se ignoran.
   - **Catálogo de sedes (recomendado):** columnas `codigo_prestador`, `sede` y `hospital`, en cualquier
     formato tabular. Asigna sede y hospital a los RIPS por el código de habilitación.
   - **Equivalencias (opcional):** columnas `codigo_facturacion` y `codigo_rips`, para códigos homologados.
   - Los PDF, imágenes o documentos de Word dentro de un paquete se ignoran con un aviso. Si un archivo
     quedó con un tipo equivocado, se corrige en la tabla de archivos.
3. Pulsar **Procesar conciliación** y revisar el **reporte general** (archivos, versión detectada, errores).
4. Explorar el **tablero** con filtros (sede, hospital, mes, estado, servicio, tipo de código, factura,
   código) y exportar.
5. **Generar hallazgos** para llevar las diferencias a la auditoría y gestionarlas.

### Gestión de archivos: limpiar, actualizar y reemplazar

- **Reemplazar:** sube una versión corregida de un archivo; conserva su asignación de sede/hospital,
  sube el número de versión y borra del disco la versión anterior.
- **Actualizar:** cambia la sede, el hospital o el ámbito asignados, o desactiva el archivo sin borrarlo.
- **Eliminar:** borra el archivo (registro y archivo físico).
- **Limpiar:** borra en bloque solo los RIPS, solo la facturación, el catálogo, las equivalencias, todos
  los archivos, solo los resultados, o todo.
- Un archivo con el mismo contenido que otro ya cargado no se duplica.
- Cada cambio queda en el **historial** (acción, archivo, huella SHA-256, usuario, fecha) y deja la
  conciliación en estado "debe reprocesarse".

### Vínculo con la herramienta de auditoría

Cada conciliación pertenece a una auditoría. **Generar hallazgos** crea un hallazgo por factura y tipo
de diferencia, con prioridad y acción recomendada (por ejemplo, "Factura sin RIPS → reportar el RIPS
antes de radicar"). Al reprocesar:
- se actualizan los valores de los hallazgos sin perder su estado, responsable ni observaciones;
- los hallazgos cuya diferencia desapareció se marcan **SUBSANADO** de forma automática;
- los que vuelven a aparecer se reabren.

Los hallazgos se filtran y exportan a Excel desde la auditoría y se conservan aunque se elimine la conciliación.

### Uso por consola (sin servidor web)

```bash
python -m rips_conciliacion \
  --rips RIPS_SEDE_TAME.zip RIPS_SEDE_FORTUL.rar "rips_sueltos/@SARAVENA" \
  --facturacion facturacion_por_ambito.xlsx \
  --catalogo-sedes catalogo_sedes.csv --equivalencias equivalencias.csv \
  --salida resultados --formato ambos
```

`archivo@SEDE` asigna la sede de forma explícita. Para paquetes muy grandes cargados en la web:
`python manage.py procesar_conciliacion <id> --hallazgos`.

## Detección de la versión de RIPS

Se determina **por el contenido**, archivo por archivo:

| Contenido | Versión |
|---|---|
| JSON con `numFactura` y `usuarios[].servicios` (aunque la extensión sea .txt) | Resolución 2275 de 2023 |
| Texto separado por comas cuyo tipo (AF, US, AC, AP, AU, AH, AN, AM, AT, CT) se reconoce por el prefijo del nombre o, si el nombre no es estándar, por la forma de las líneas, y cuyas líneas tienen el número de campos del anexo técnico | Resolución 3374 de 2000 |
| Cualquier otro caso | **NO DETERMINADA**, con el motivo (por ejemplo, "AF pero ninguna línea tiene 17 campos", "JSON inválido en línea 4, columna 1") |

La herramienta nunca asume una versión. Un archivo sin versión determinada no se procesa y se reporta
como error. Un paquete puede tener ambas versiones y el reporte las muestra.

## Validaciones y errores

- TXT: número de campos por línea, campos obligatorios, valores numéricos y fechas `dd/mm/aaaa`.
  Cada error indica **archivo, línea, campo y valor**. El registro con error se excluye y el resto sigue.
- JSON: errores con la ruta exacta (`usuarios[0].servicios.consultas[2].codConsulta`).
- Archivo de control CT: se compara el "Total de registros" declarado con las líneas reales.
- Facturación: fila de Excel o línea del CSV, columna y valor.
- Los nombres de campo se conservan tal como vienen: llaves del JSON y columnas de facturación sin
  cambios. Los TXT de la 3374 no traen nombres, así que se usan los del anexo técnico. Los códigos no se
  modifican; la comparación usa una llave normalizada (mayúsculas y sin espacios) y el reporte muestra el
  código original y el **campo de origen** (`Código del medicamento`, `codTecnologiaSalud`, `CUMS`…).

## Archivos grandes

- El procesamiento corre **en segundo plano**. La página se actualiza sola y muestra:
  - la etapa y el archivo que se está leyendo, también **dentro de los ZIP** (por ejemplo,
    `Hospital.zip → OCTUBRE/HSA1234.json (12.982 archivos leídos)`) y las filas leídas de cada Excel;
  - la **memoria en uso y libre** del equipo, con un aviso si se está agotando;
  - una barra de avance y el tiempo transcurrido;
  - el botón **Cancelar procesamiento**.
- Mientras procesa no se pueden modificar los archivos de esa conciliación. Si se cierra la herramienta a
  mitad del proceso, la conciliación queda como *interrumpida* y se procesa de nuevo.
- Uso de memoria reducido:
  - los datos comunes (archivo, usuario del JSON) se comparten en lugar de copiarse en cada registro;
  - de cada servicio del JSON se conservan solo los campos que usa la conciliación;
  - los registros crudos se liberan apenas se convierten en líneas (los RIPS antes de leer la facturación).
- Las hojas de cálculo se leen con *calamine* y el Excel de resultados se escribe con *xlsxwriter*.
- **Límite por hoja de Excel:** ninguna hoja pasa de 100.000 filas. *Diferencias* trae las de mayor valor
  absoluto; el detalle completo y las líneas grandes van solo en el CSV, que siempre trae todo. Una hoja
  *Solo en CSV* lo explica.
- Las descargas de Google Drive (`Carpeta-20261027T135526Z-1-001.zip`) y las carpetas por factura se
  reconocen: la sede se toma de la carpeta del hospital, no de la carpeta de cada factura.
- Mediciones de referencia:
  - 400.000 líneas de facturación (Excel de 28 MB) más 388.000 de RIPS: unos **2 minutos**, antes más
    de 25;
  - ZIP de Drive con 20.000 facturas JSON, PDF y archivos CUV, más esa facturación (519.758 pares
    factura-código): **3 minutos y medio**.
- Los tiempos de cada etapa quedan en `datos/registro_errores.log`.

## Exactitud de los cálculos

- Todo valor y cantidad se convierte a `Decimal`, nunca a `float`. Los JSON se leen con
  `parse_float=Decimal`. No se redondea ni se estima: la tolerancia por defecto es 0, así que una
  diferencia de un centavo se reporta.
- Separador decimal de la facturación: se infiere **por archivo** a partir de valores inequívocos
  (`1.234,56` o `1,234.56`). Si todos los valores son ambiguos (`30.000`), se aplica la convención
  colombiana (punto para miles) y se emite una advertencia. El separador puede fijarse en los parámetros.
- Base de datos: los valores se guardan con 6 decimales y los totales del tablero se suman en Python con
  `Decimal`. Si un valor tiene más de 6 decimales, se rechaza en lugar de redondearse. En producción se
  recomienda PostgreSQL (variables `DJANGO_DB_*`).
- Excel muestra hasta 15 dígitos significativos; los CSV llevan la cifra exacta.

## Propuesta de emparejamiento (para validar con la ESE)

**Criterio: llave compuesta jerárquica y agregada → (número de factura, código).**

1. **Factura:** número normalizado (mayúsculas, solo letras y dígitos: `FE-0012`, `fe 0012` y
   `FE0012` son la misma factura). Opcionalmente se pueden ignorar los ceros a la izquierda (`FE12`).
2. **Código:** CUPS, CUMS o código de servicio, en mayúsculas y sin espacios, homologable con la tabla de
   equivalencias.
3. Dentro de cada par (factura, código) se **suman** cantidades y valores de cada fuente y se comparan
   los totales.

**Por qué este criterio y no el número de línea**
- Los RIPS 3374 no tienen número de línea, y en los JSON 2275 el `consecutivo` se reinicia por usuario y
  por tipo de servicio, así que no hay una línea común a las dos fuentes.
- La facturación divide o agrupa líneas (el mismo medicamento entregado dos veces), así que el orden y el
  número de líneas cambian entre fuentes.
- La factura es el documento que se radica y se glosa, y el código es lo que se audita. Una diferencia en
  (factura, código) es justo lo que el auditor debe corregir, y el resultado es determinístico: no hay
  emparejamientos "probables".

**Supuestos**
- El número de factura de la facturación y el de RIPS (`Número de la factura` / `numFactura`)
  corresponden al mismo documento una vez normalizados.
- Ambas fuentes usan CUPS/CUMS (o existe la tabla de equivalencias).
- Se compara el valor bruto del servicio: `Valor de la consulta`, `Valor del procedimiento`,
  `Valor total de medicamento`, `Valor total del material e insumo` o `vrServicio`. El neto del AF
  (descontados copago, comisión y descuentos) se verifica aparte contra el detalle del RIPS.

**Casos no resueltos (se reportan, no se fuerzan)**
- Factura en una sola fuente: `FACTURA_NO_REPORTADA_EN_RIPS` o `FACTURA_NO_ESTA_EN_FACTURACION`.
- El mismo servicio con distinto código: aparece como `CODIGO_NO_REPORTADO_EN_RIPS` y
  `CODIGO_NO_ESTA_EN_FACTURACION` en la misma factura. Cuando la raíz coincide (CUMS `19912345-01`
  frente a `19912345-1`) se marca `posible_homologacion`, y se resuelve agregando la equivalencia.
- Facturas colectivas con varios pacientes: se concilia el total por código, no por paciente.
- Urgencias (AU), hospitalización (AH) y recién nacidos (AN) no tienen valor en RIPS: se cuentan en el
  reporte general pero no generan líneas para conciliar.

**Preguntas para validar con la ESE:** ¿los números de factura llevan el mismo prefijo en las dos
fuentes? ¿Hay ceros a la izquierda? ¿Facturación usa CUMS con el sufijo de presentación? ¿La factura se
cruza por valor bruto o por neto? ¿Cómo se registran las notas crédito o débito (`tipoNota`)?

## Niveles de salida

1. **Reporte general de revisión:** archivos leídos, versión detectada, registros por tipo, errores y
   advertencias, facturas por estado, totales y diferencia global, y conteo del cruce de códigos.
2. **Reporte detallado** (Excel con varias hojas o CSV):
   - `Diferencias` y `Detalle factura-código`: estado, factura, mes, sede, hospital, servicio, código, CUMS,
     cantidades, valores y diferencias, con archivo y línea de origen en cada fuente.
   - `Facturas`: comparación por factura, con el neto del AF.
   - `Cruce de códigos` (global y por sede): códigos solo en facturación, solo en RIPS o en ambas.
   - `Sede-Hospital-Mes`, `Por sede`, `Por hospital`, `Por mes`, `Por servicio`, `Hospital-Servicio`.
   - `Consumo por código / servicio / hospital / farmacias`, con ranking de mayor consumo.
   - `Otros códigos RIPS` (diagnósticos y código de servicio), `Incidencias`, `Archivos`, líneas de cada
     fuente y `Criterio emparejamiento`.
3. **Tablero interactivo** (Django): filtros, indicadores, gráficas por sede, mes, hospital, servicio,
   tipo de diferencia y los 15 códigos con mayor diferencia, más una tabla paginada que se exporta a
   Excel o CSV con los filtros aplicados.

Exportación completa: **Excel**, **CSV** (ZIP) o **ambos**, desde la conciliación o por consola.

## Pruebas

```bash
pytest
```

Las pruebas cubren la carga de TXT, JSON, ZIP (también anidados) y RAR, la facturación en CSV y en Excel
por ámbito, la detección de versión (incluidos los casos no determinados), el cruce de códigos, las
diferencias monetarias y de cantidad exactas (incluida la suma 0,1 + 0,1 + 0,1 y la diferencia de un
centavo), las equivalencias, la exportación, la consola y la app web: carga, duplicados, reemplazo,
actualización, eliminación, limpieza, procesamiento, tablero, exportación y el ciclo de hallazgos.

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
2. Haga doble clic en **`INSTALAR_WINDOWS.bat`**.
3. Al terminar, la herramienta se abre en el navegador. Después se abre con el acceso directo
   **"Auditoria ESE"** del escritorio o del menú Inicio.

El instalador:
- **No pide permisos de administrador.** Instala todo en `%LOCALAPPDATA%\AuditoriaESE`, la carpeta del
  usuario.
- **No interfiere con otras herramientas.** Trae su propio Python aislado (versión "embebida"), así que
  no modifica el PATH, el registro, el Python que ya tenga el equipo ni otros programas. Los accesos
  directos son solo del usuario. La herramienta escucha solo en `127.0.0.1` (no se expone en la red ni
  pide abrir el firewall). Busca un puerto libre a partir del 8765 y nunca usa uno ocupado por otro
  programa. Si ya está abierta, reutiliza la instancia en lugar de abrir otra.
- **Funciona en cualquier navegador** (Edge, Chrome, Firefox, Brave, Opera). Se abre en el navegador
  predeterminado y la ventana de inicio muestra la dirección (por ejemplo, `http://127.0.0.1:8765/`)
  para pegarla en otro. No usa CDN ni dependencias de Internet al ejecutarse: las gráficas vienen
  incluidas.
- **Se actualiza volviendo a ejecutarlo.** Reemplaza la aplicación y **conserva los datos**
  (`%LOCALAPPDATA%\AuditoriaESE\datos`: base de datos y archivos cargados).
- **Se desinstala** desde *Inicio → Auditoria ESE → Desinstalar*. Pregunta antes de borrar los datos.

**Equipos sin Internet o con navegación restringida:** en un equipo con Internet ejecute
`instalador\windows\preparar_offline.ps1` (clic derecho → *Ejecutar con PowerShell*). Eso crea la carpeta
`instalador\windows\offline` con Python y todas las librerías. Copie el proyecto completo al equipo
destino y ejecute `INSTALAR_WINDOWS.bat`.

> Si una política de grupo de la ESE bloquea los scripts de PowerShell, el área de sistemas puede
> permitir este instalador; no requiere privilegios elevados.

### Linux / macOS

```bash
bash instalador/linux_mac/instalar.sh
```

Requiere Python 3.11 o superior. Instala en `~/.local/share/auditoria-ese` (Linux) o en
`~/Library/Application Support/AuditoriaESE` (macOS) con un entorno virtual propio, sin `sudo`.
Para abrir la herramienta use el script `iniciar.sh` que indica el instalador.

## Instalación para desarrollo

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

1. **Crear la auditoría** (por ejemplo, "Auditoría EPS X – vigencia 2024") y dentro de ella una **conciliación**.
2. **Cargar archivos** en la conciliación (se pueden seleccionar varios a la vez):
   - **RIPS:** ZIP o RAR por sede, con los meses en carpetas (`RIPS_SEDE_TAME.zip/ENERO_2024/AF000123.txt`,
     `.../2024-02/FE2001.json`). También se aceptan TXT o JSON sueltos y ZIP anidados (un ZIP por mes dentro
     del ZIP de la sede).
   - **Facturación:** CSV, TXT delimitado, XLSX o XLS con encabezado. Puede ser un libro con una hoja por
     ámbito (Ambulatorio, Urgencias, Hospitalización…) o un archivo por ámbito (indique el ámbito al cargarlo).
     Las columnas se reconocen por nombre (`Numero Factura`, `Factura`, `CUPS`, `CUMS`, `Codigo`, `Cantidad`,
     `Valor Total`, `Valor Unitario`, `Fecha Factura`, `Mes`, `Sede`, `Hospital`, `Ambito`, `Farmacia`…),
     aunque el encabezado no esté en la primera fila. Las filas de "TOTAL" se ignoran.
   - **Catálogo de sedes (recomendado):** `codigo_prestador;sede;hospital`. Asigna sede y hospital a los
     RIPS por el código de habilitación.
   - **Equivalencias (opcional):** `codigo_facturacion;codigo_rips`, para códigos homologados.
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

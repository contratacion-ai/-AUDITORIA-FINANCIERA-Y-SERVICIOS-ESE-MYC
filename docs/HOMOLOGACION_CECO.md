# Homologación de costos por centro de costos (CECO)

Herramienta en Python + Pandas (`homologacion_ceco/`) que toma los archivos de costos por centro de costos del
estado financiero (Excel, CSV o ZIP), los normaliza a un esquema común, los limpia y valida, los homologa al CECO
de la resolución con el archivo de referencia **HOMOLOGACION_CECO_EF_vs_RESOLUCION_ESE_MYC_2026.xlsx** y produce,
en **un solo libro de Excel**, el estado financiero homologado, la carga lista para la herramienta de costos y
la trazabilidad completa. Incluye una **plantilla de carga directa**.

Reconoce directamente el **estado de resultados por tercero y centro de costo (CTRP) del software contable** de la
ESE (§5.1). Es independiente de la aplicación Django: solo requiere `pandas`, `openpyxl` y `xlrd`
(`homologacion_ceco/requirements.txt`). La hoja *CARGA HERRAMIENTA* se carga tal cual en la herramienta ABC
(Cargas → Estado financiero, hoja `CARGA HERRAMIENTA`).

## 1. Uso

```bash
pip install -r homologacion_ceco/requirements.txt

# Procesar uno o varios archivos, carpetas o ZIP (también ZIP dentro de ZIP)
python -m homologacion_ceco procesar lote_enero.zip EF_FEB_2026.xlsx --salida resultado.xlsx

# Opciones frecuentes
#   --periodo 2026-01       periodo para archivos que no lo traen (ni en columna ni en el nombre)
#   --mapeo mapeo.json      columnas fijas o sinónimos para archivos con encabezados atípicos
#   --usar-movimiento       usar débito − crédito aunque el archivo traiga SALDO (balances acumulados)
#   --activar-nuevos        cargar CECO nuevos que una regla homologa (ver §4)
#   --clases 4,5,6,7        clases de cuenta a leer (por defecto; la carga para la herramienta ABC lleva solo 5, 6, 7)

python -m homologacion_ceco plantilla --salida Plantilla_Carga_CECO.xlsx   # plantilla de carga directa
python -m homologacion_ceco validar-referencia                             # control de calidad del maestro
python -m homologacion_ceco ejemplo --salida lote_ejemplo.zip              # lote SINTÉTICO para probar
python -m homologacion_ceco --referencia otra_version.xlsx procesar ...    # usar otra versión del maestro
```

Código de salida: `0` todo cargado · `2` terminó, pero hay rechazos, retenidos o archivos con error (revise el
libro) · `1` no se pudo procesar (referencia inválida, mapeo mal escrito).

Desde Python:

```python
from homologacion_ceco import cargar_referencia, procesar, exportar, Opciones
res = procesar(["lote.zip"], cargar_referencia(), Opciones(periodo="2026-01"))
exportar(res, "resultado.xlsx")
res.consolidado      # DataFrame listo para cargar (periodo, cuenta, CECO resolución, sede, valor)
```

## 2. Flujo

```
ENTRADAS (.xlsx .xls .csv .txt .zip)
  │  lectores.py   ZIP en memoria (anidados, límites de tamaño, cifrados/corruptos → error y sigue);
  │                duplicados de archivo por SHA-256; CSV: codificación y separador automáticos
  ▼
TABLAS CRUDAS (una por hoja)
  │  columnas.py   fila de encabezado (primeras 30 filas) y columnas equivalentes por sinónimos /
  │                similitud; formatos LARGO, MATRIZ (CECO en columnas) y POR CECO (código en el nombre)
  ▼
REGISTROS  (id R0000001…, archivo, hoja, fila de Excel)
  │  normalizar.py códigos, cuentas, montos en centavos exactos, periodos AAAA-MM
  │  procesador.py validaciones → RECHAZADO; reglas (clase, subtotales, cero, duplicado) → EXCLUIDO
  ▼
HOMOLOGACIÓN  reglas.py — maestro primero; reglas de estructura y nombre para CECO nuevos
  │  estado ACTIVO → CARGADO · VERIFICAR / NO APLICA / EXCLUIR / SIN HOMOLOGAR → RETENIDO
  ▼
CONSOLIDACIÓN  periodo + cuenta + llave CECO|SEDE (id C000001…)  →  conciliación (debe CUADRAR)
  ▼
SALIDA  exportar.py — un libro .xlsx (ver §6)
```

## 3. Archivo de referencia (fuente de verdad)

Incluido en `homologacion_ceco/referencia/`. La herramienta lee:

| Hoja | Uso |
|---|---|
| **C COSTOS ESTADO FINANCIERO** | Maestro: Código y Nombre del CECO E.F., **col. D** código de resolución (editable), tipo, sede/IPS, estado, confianza, criterio y método |
| CENTROS DE COSTOS RESOLUCION | Catálogo para validar la col. D y obtener el nombre (col. E) |
| CENTROS DE COSTOS UTILIZADOS | Lista «Utilizados» (col. F «¿en lista utilizados?») |
| TABLA HERRAMIENTA | Su esquema se replica en la hoja *CARGA HERRAMIENTA* / *CECO NUEVOS* |

Las fórmulas del libro **se recalculan en Python** con la misma lógica (nombre de resolución, ¿en
utilizados?, llave `D & "|" & LEFT(I, FIND(" ", I & " ") - 1)`), así que un cambio en la columna D se aplica
aunque el libro no se haya abierto en Excel. Hallazgos del control de calidad (hoja *CONTROL REFERENCIA* y
`validar-referencia`), equivalentes a la hoja RESUMEN HOMOLOGACIÓN:

* conteo por estado (hoy: 399 ACTIVO, 51 VERIFICAR, 96 NO APLICA, 3 EXCLUIR = 549);
* códigos homologados no válidos en la resolución (hoy 0) → esa fila se trata como VERIFICAR;
* **ACTIVOS asignados a CECO fuera de «Utilizados»** (hoy 43: I01, D02, P01, P03, A32, L04, M07…) → se cargan
  pero se alertan: agréguelos a «Utilizados» para que la herramienta de costos los reconozca;
* **CECO «Utilizados» sin ningún CECO E.F. ACTIVO** (hoy 10);
* estados o tipos no válidos, códigos E.F. duplicados (se usa la primera fila), filas sin sede.

## 4. Reglas de homologación

**Estados** — solo `ACTIVO` se carga. `VERIFICAR` (validar con contabilidad/habilitación), `NO APLICA`
(estructura CHIP no habilitada en primer nivel; si recibe gasto se genera la alerta *NO APLICA CON GASTO*) y
`EXCLUIR` (partidas que no son costo) quedan en la hoja **RETENIDOS** con su motivo y se muestran aparte en el
estado financiero. Nunca se cargan en silencio.

**Código del CECO E.F.** (7 dígitos) `G SS NNNN`: G grupo (1 asistencial, 2 administrativo, 3 financiero,
4 logístico, 5 seguros, 6 mantenimiento de sedes, 7 transporte), SS sede (01 sede administrativa, 02 HSLA,
03 HSFF, 04 HSAT, 05 HSJDP, 06 HSJCN, 07 HSRP, 08 CSPA, 09 CSJSC…), NNNN servicio CHIP.

Precedencia:

1. **Maestro**: si el código está en el maestro, se usan su código de resolución, sede, tipo y estado.
2. **CECO nuevo** (no está en el maestro), se propone:
   1. reglas de nombre que **obligan a validar** o **excluir** — recién nacidos (J09), quirófanos (C01),
      consulta especializada (A32), medicina legal, otros servicios conexos, consultoría; saldos iniciales,
      impuestos retenidos, ampliación/adecuación de infraestructura;
   2. **estructura del código**: grupo + servicio CHIP aprendidos del maestro, solo cuando todas las sedes
      tienen el mismo código de resolución (la sede 01 se excluye por ser irregular). Conserva NO APLICA;
   3. **nombre** (sede central y casos irregulares), `homologacion_ceco/datos/reglas_nombre.json`: consulta
      externa → A10, laboratorio clínico → L07, imagenología/RX/dosimetría → L02, farmacia/medicamentos → M05,
      mantenimiento → P02 (R03 en la sede central), servicios públicos → R02 *(nota: distribuir por m²)*,
      promoción y mantenimiento → N03, transporte de pacientes → N08, urgencias → B01, odontología → E01…;
   4. sin resultado → `SIN HOMOLOGAR`.
3. Un CECO nuevo queda en **VERIFICAR** con su propuesta (hoja **CECO NUEVOS**, con las columnas del maestro
   para copiarlas) salvo que se use `--activar-nuevos`; aun así no se cargan los de regla de validación ni
   los que no tienen sede determinable.

**Sede/IPS** de un CECO nuevo: la del archivo (columna SEDE) → subsede en el nombre (p. ej. Filipinas) →
segmento SS del código (si una sede es ≥ 80 % del segmento en el maestro) → sigla o municipio en el nombre
(`datos/sedes_y_puc.json`).

**Control de las reglas**: aplicadas a los 549 CECO del maestro sin mirarlo, proponen el mismo código de
resolución en el 100 % y la misma sede en el 98,9 % (las diferencias son subsedes como La Pesquera o La 20).
Las reglas se calibraron con este maestro: la coincidencia es dentro de la muestra, no una predicción para
códigos futuros; por eso un CECO nuevo nunca se carga sin decisión explícita.

**Llave CECO|SEDE**: primer token de la sede, igual que la columna G (p. ej. `A10|HSLA`). HSAT - Tame y HSAT -
Filipinas comparten la IPS `HSAT`, como en el maestro.

## 5. Archivos de entrada y mapeo de columnas

### 5.1 Estado de resultados del software contable (reporte CTRP)

El reporte **CTRPEstadoResultadosTerCen** (estado de resultados por tercero y centro de costo) se reconoce solo,
sin mapeo: `python -m homologacion_ceco procesar CTRPEstadoResultadosTerCen.xlsx --salida resultado.xlsx`.

| En el reporte | Cómo se usa |
|---|---|
| Filas de cuenta (Nivel Clase → Subauxiliar, `OidCuentaContable`, `CodigoCuentaContable`) | Jerarquía y nombres del estado de resultados; totales de control por clase |
| Filas de detalle (tercero × centro de costo) **sin código de cuenta** | La cuenta se toma de `OidCuentaProceso` → cuenta de detalle (subauxiliar) |
| `CodigoCentroCosto`, `NombreCentroCosto` | Código del CECO E.F. que se homologa con el maestro |
| `ValorSaldoDebito`, `ValorSaldoCredito` (o `ValorMovimiento…` con `--usar-movimiento`) | Valor = débito − crédito; los ingresos (naturaleza crédito) se muestran en positivo |
| Pie del reporte (`DescripcionFiltros`: «ESTADO DE RESULTADOS DEL MES JULIO DE 2026 …») | Periodo 2026-07 |
| `OidTercero` | Solo el identificador interno, para no confundir filas iguales de distintos terceros (p. ej. nómina por empleado). **No** se copian nombres ni documentos de terceros |
| Hojas CALSIFICACION CUENTA, PUC, CENTROS DE COSTOS | Se omiten (el catálogo de CECO es el mismo del maestro) |

Controles: si una cuenta de detalle trae un total que no se explica por su detalle por CECO (o no trae detalle),
la diferencia se registra **RECHAZADA – sin centro de costo** con alerta *CUENTA SIN DETALLE POR CECO*; el
RESUMEN compara, por clase, el total del reporte con lo leído (*Control contra los totales del reporte de
origen*: debe decir CUADRA). Los textos que el software exporta con tildes dañadas («CAPITACIÃ“N») se reparan.

### 5.2 Otros archivos

Se reconocen encabezados distintos para el mismo campo (sin importar mayúsculas, tildes o signos), por
sinónimo, sin espacios o por similitud (≥ 0,88). Los sinónimos están en `datos/columnas.json`:

| Campo | Ejemplos de encabezado reconocidos |
|---|---|
| CECO (requerido) | CODIGO CECO, CECO, Cód. Centro Costo, CENTRO DE COSTOS, CC |
| Cuenta (requerido) | CUENTA, Código Cuenta, CUENTA CONTABLE, AUXILIAR, PUC |
| Valor (o débito/crédito) | VALOR, SALDO FINAL, Nuevo Saldo, MOVIMIENTO, TOTAL, IMPORTE · DEBITO/DEBE, CREDITO/HABER |
| Periodo | PERIODO, MES, FECHA, FECHA CORTE |
| Otros | NOMBRE CECO, NOMBRE CUENTA, SEDE/IPS, DESCRIPCION/DETALLE, COMPROBANTE |

* El encabezado puede estar en cualquiera de las primeras 30 filas (títulos del reporte arriba).
* **Formato MATRIZ**: cuentas en filas y un CECO por columna (encabezados con el código de 7 dígitos).
* **Formato POR CECO**: sin columna CECO; el código de 7 dígitos se toma del nombre de la hoja o del archivo
  (`1040502_imagenologia_2026-03.csv`).
* Las columnas «… (AUTO)» de la plantilla se ignoran. No se leen datos de terceros (NIT/cédula/nombres).
* El mapeo usado queda en la hoja **MAPEO COLUMNAS** (archivo, hoja, fila de encabezado, campo, columna,
  método y puntaje).

**Archivo de mapeo** (`--mapeo mapeo.json`) para los archivos reales que no se reconozcan solos:

```json
{
  "sinonimos": {"cod_ceco": ["CENTRO RESP"], "valor": ["VALOR NETO MES"]},
  "archivos": {
    "nomina_*.xlsx": {"cod_ceco": "#B", "cuenta": "CTA PUC", "valor": "TOTAL DEVENGADO"},
    "balance.xlsx:Hoja2": {"valor": "MOV MES"}
  }
}
```

`#B` = columna por letra. Los patrones usan comodines sobre el nombre del archivo (o `archivo:hoja`). Una
columna fija inexistente deja esa hoja en ERROR con el detalle, y el resto del lote se procesa.

## 6. Libro de salida

| Hoja | Contenido |
|---|---|
| RESUMEN | Parámetros, huella SHA-256 de la referencia, **conciliación** y control de calidad del maestro |
| **EF HOMOLOGADO** | Estado de resultados por periodo: clase → grupo → cuenta → subcuenta → auxiliar × **sede/IPS** + total; total de ingresos, de gastos y costos y **resultado del periodo**; debajo, los gastos y costos no cargados (por estado del CECO y sin centro de costo) y el total leído, que cuadra con el reporte |
| **EF POR CECO** | Por periodo: gastos y costos por tipo de centro → CECO de resolución × sede/IPS |
| **CARGA HERRAMIENTA** | Consolidado de gastos y costos (clases 5, 6, 7) listo para cargar (PERIODO, CUENTA, NOMBRE CUENTA, CENTRO DE COSTOS, SEDE, LLAVE, TIPO, VALOR, DESCRIPCION, ID CONSOLIDADO, CECO E.F. ORIGEN, EN LISTA UTILIZADOS) |
| DETALLE CARGADO | Cada registro cargado con su origen, homologación e ID consolidado |
| RETENIDOS | VERIFICAR / NO APLICA / EXCLUIR / SIN HOMOLOGAR con su motivo |
| RECHAZOS Y EXCLUSIONES | Errores de datos y exclusiones por regla, con severidad y motivo |
| ALERTAS | Cargados con observación: fuera de «Utilizados», sede o nombre distinto al maestro, NO APLICA con gasto |
| CECO NUEVOS | Propuestas para agregar al maestro (mismas columnas) |
| MAPEO COLUMNAS · ARCHIVOS | Mapeo usado; estado de cada archivo/hoja (PROCESADO, OMITIDO, DUPLICADO, ERROR) |
| **TRAZABILIDAD** | Bitácora de transformaciones: MAPEO, LIMPIEZA (valor original → normalizado), VALIDACION, REGLA, HOMOLOGACION, CONSOLIDACION |
| CONTROL REFERENCIA · SUPUESTOS | Hallazgos del maestro; supuestos aplicados |

**Rastrear una cifra**: en *EF HOMOLOGADO* / *CARGA HERRAMIENTA* tome el `ID CONSOLIDADO` → filtre
*DETALLE CARGADO* por ese ID (archivo, hoja y fila de Excel de cada registro) → filtre *TRAZABILIDAD* por el
`ID REGISTRO` para ver cada transformación. **Conciliación**: leído = rechazado + excluido + retenido + cargado,
y consolidado = cargado; si no cuadra el proceso se detiene (no se entrega un resultado inconsistente).

Si una hoja supera el límite de filas de Excel, el detalle completo se escribe además en un CSV junto al libro.

## 7. Limpieza y validaciones

| Situación | Destino |
|---|---|
| Sin CECO, sin cuenta, cuenta con letras, monto ilegible (`ABC`), periodo inválido (`31/13/2026`) o ausente | RECHAZADO (ERROR) |
| Cuenta fuera de las clases 4, 5, 6, 7 (balance) | EXCLUIDO (INFO) |
| Cuenta que es prefijo de otra del mismo CECO y hoja (subtotal), o subtotal sin CECO | EXCLUIDO (INFO) — evita duplicar |
| Filas de TOTAL del reporte, encabezados repetidos, valor cero | EXCLUIDO (INFO) |
| Fila idéntica (periodo, CECO, cuenta, valor, descripción, comprobante), aun en otro archivo | EXCLUIDO (AVISO) |
| Archivo con el mismo contenido que otro ya leído | DUPLICADO (no se procesa) |
| Excel/ZIP dañado, cifrado, formato no soportado, bomba ZIP, > 100 MB | ERROR/OMITIDO del archivo; el lote sigue |

Montos: `1.234.567,89` · `1,234,567.89` · `$ (1.000)` · `500-` · `1.234` (miles) → centavos exactos. Periodos:
fechas, seriales de Excel, `2026-01`, `01/2026`, `202601`, `31/01/2026`, `ENERO 2026`, `ENE-2026`.

## 8. Plantilla de carga directa

`plantillas/Plantilla_Carga_CECO.xlsx` (o `python -m homologacion_ceco plantilla`), generada desde el maestro:

* **CARGA**: columnas azules a diligenciar (PERIODO, CODIGO CECO con **lista desplegable**, CUENTA, NOMBRE
  CUENTA, VALOR, DESCRIPCION) y columnas grises calculadas: nombre del CECO, CECO de resolución, llave
  CECO|SEDE, estado y **VALIDACIÓN** de la fila (OK / ERROR: … / NO SE CARGA: CECO en estado …), con colores.
* **RESUMEN**: filas listas, con error y no cargables, y totales por estado, antes de cargar.
* **CATALOGO CECO** e **INSTRUCCIONES**. Sin macros: funciona en Excel, LibreOffice y Google Sheets.

La herramienta reconoce la plantilla sin mapeo (sola o dentro de un ZIP) y omite sus hojas auxiliares.

## 9. Supuestos (no se recibieron los archivos fuente reales)

1. Los archivos traen una fila por CECO y cuenta (o matriz cuentas × CECO, o un archivo por CECO), con valor del
   mes o débito/crédito. Si se reciben otras estructuras, se agregan sinónimos en `datos/columnas.json` o un
   archivo de mapeo; no hay que cambiar código.
2. Si el archivo trae SALDO y DÉBITO/CRÉDITO, se usa el saldo. Para balances **acumulados** del año use
   `--usar-movimiento` (débito − crédito del mes).
3. El formato del estado financiero de origen es el reporte CTRP del software contable (§5.1) o la jerarquía del catálogo de cuentas CGN (clase 1 dígito,
   grupo 2, cuenta 4, subcuenta 6, auxiliar) con las IPS en columnas. Los niveles y nombres de clase/grupo
   están en `datos/sedes_y_puc.json`; los demás nombres se toman del propio archivo.
4. Las cuentas que son prefijo de otras en el mismo CECO y hoja son subtotales (en el PUC no se registra en
   cuentas de nivel superior). Si un archivo trae solo cuentas de mayor, use `--incluir-subtotales`.
5. Un separador único seguido de exactamente 3 dígitos es separador de miles (`1.234` = mil doscientos
   treinta y cuatro).
6. El maestro manda sobre el archivo: si el archivo trae otra sede o un nombre muy distinto para el código, se
   usa el maestro y se genera una alerta.

## 10. Caso de prueba (lote de ejemplo, datos sintéticos)

`python -m homologacion_ceco ejemplo` genera un ZIP con: balance de enero con títulos y subtotales; CSV de
febrero (cp1252, `;`, débito/crédito, `$`, paréntesis, una fila duplicada, tres filas con error); ZIP anidado de
marzo con una matriz cuentas × CECO y un archivo por CECO; un Excel dañado, un PDF y una copia duplicada.

| Periodo | Cargado | Detalle |
|---|---:|---|
| 2026-01 | 15.300.000,50 | A10\|HSLA 3.000.000 · L07\|HSLA 2.500.000,50 · R02\|HSLA 800.000 · T01\|ESE01 9.000.000 |
| 2026-02 | 6.600.000,00 | A10\|HSLA 3.100.000 · L07\|HSLA 2.300.000 (2.400.000 − 100.000) · A10\|HSAT 1.250.000 · R02\|HSLA −50.000 |
| 2026-03 | 6.010.000,00 | A10\|HSLA 3.200.000 · A10\|HSAT 1.300.000 · R02\|HSLA 820.000 · L02\|HSAT 690.000 |
| **Total** | **27.910.000,50** | 13 líneas consolidadas |

Retenidos 2.550.000 (recién nacidos VERIFICAR 400.000; UCI NO APLICA 150.000 con alerta; saldos iniciales
EXCLUIR 1.000.000; CECO nuevo 1100201 700.000; CECO nuevo quirófanos 300.000). Rechazados 600.000 (sin CECO
500.000, periodo 31/13/2026 100.000, y 'ABC' sin valor). Excluidos: subtotales 5, 51, 5101 y 510101, caja
(clase 1), fila TOTAL GENERAL, duplicado de 1.250.000. Conciliación: **CUADRA**.

## 11. Pruebas

```bash
pytest homologacion_ceco/tests        # 111 pruebas de la herramienta (sin Django)
pytest tests/test_homologacion_ceco_abc.py   # la hoja CARGA HERRAMIENTA entra a la herramienta ABC
```

Cubren el reporte CTRP del software contable (cuenta por OidCuentaProceso, periodo del pie, terceros sin
duplicar ni copiar, cuentas sin detalle por CECO, control contra los totales, tildes dañadas), limpieza de montos/periodos/códigos, lectura del maestro y recálculo de fórmulas, reglas (incluido el
contraste con los 549 CECO), formatos largo/matriz/por CECO, ZIP anidados/dañados/límites, duplicados,
subtotales, estados no cargables, mapeo del usuario, conciliación, trazabilidad hasta la fila de Excel,
protección contra fórmulas en textos de origen, plantilla (validaciones y carga directa) y línea de comandos.

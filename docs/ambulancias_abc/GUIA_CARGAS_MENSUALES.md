# Guía de cargas mensuales

Menú **Cargas del mes**. Todas las cargas se pueden repetir: volver a cargar el mismo archivo actualiza los registros, no los duplica. La tabla *Estado de las cargas por hospital* muestra, por mes, cuántas líneas de facturación, cargas de RIPS, APH y consumos hay.

## 1. Facturación del mes por ámbito

Botón **Cargar facturación del mes**.

| Campo | Uso |
|---|---|
| Mes | Mes que se está cargando. Las líneas con fecha de servicio de otro mes se cargan igual (quedan en su mes real) y se informa cuántas son. |
| Hospital | Opcional. Si se elige, solo se cargan las líneas de ese hospital (útil cuando cada hospital envía su propio archivo). |
| Archivo | Reporte del HIS "TRASLADOS FACTURACION POR AMBITO" (.xlsx). |

Al terminar se abre **Facturación por ámbito**: mes × hospital × ámbito × unidad funcional × servicio (traslado/APH), con líneas, traslados con recorrido, km, valor, líneas sin número de factura y líneas conciliadas con RIPS. Botón *Descargar Excel*.

## 2. RIPS: un mes, varios meses o consolidado por hospital

Botón **Cargar RIPS**. Mes y hospital son **opcionales**:

| Caso | Mes | Hospital |
|---|---|---|
| RIPS de un mes de un hospital | el mes | el hospital |
| ZIP del hospital con todo el año (carpetas `01. Enero`, `02. Febrero`…) | vacío | el hospital o «Detectar» |
| Consolidado de varios hospitales (p. ej. descarga de Salud360) | vacío | «Detectar» |

Archivos aceptados (uno o varios a la vez):

- **ZIP**, con carpetas por mes y ZIP dentro de ZIP. Comprimir es lo recomendado para archivos grandes (hasta 1 GB descomprimido por cargue).
- **JSON** de la Resolución 2275 de 2023 (una factura o una lista de facturas).
- **Archivos planos** de la Resolución 3374 de 2000: `AF` (facturas), `AT` (otros servicios) y `AP` (procedimientos). `US`, `AC`, `AH`, `AM`, `AU`, `CT` no se leen.
- Los XML de factura electrónica y otros archivos dentro del ZIP se ignoran.

**Mes de cada línea**: el de su fecha de servicio (en `AT`, que no trae fecha, el inicio del periodo de la factura del `AF` de la misma carpeta). Si la carpeta dice otro mes, se anota en la observación (servicio de agosto enviado en la carpeta de septiembre).

**Hospital de cada línea** (si no se eligió): 1) código de prestador registrado en la sede; 2) **prefijo del número de factura**: FHSA Tame, FHSL Arauquita, FHJD Puerto Rondón, FHSF Fortul, FHSR La Esmeralda, FHCN Cravo Norte, FCSJ Puerto Jordán, FCSP Panamá (editables en *Administración › Sedes › Prefijos de factura*; también se aprenden de la facturación HIS cargada). El primer cargue de cada hospital registra su código de prestador si la sede no lo tiene.

La aplicación **extrae solo los traslados y las atenciones prehospitalarias (APH)**:

| Regla (en este orden) | Resultado |
|---|---|
| Código registrado en *Administración › Códigos de traslado* | Su categoría (TRASLADO o APH) |
| Nombre del servicio con "PREHOSPITALARIA" o que empieza por "APH", o autorización "APH" | APH |
| Nombre del servicio con "TRASLADO" o "AMBULANCIA" | TRASLADO |
| Archivo AT con tipo de servicio 2 (traslados) | TRASLADO |

Para que un código de APH se reconozca siempre, regístrelo en *Códigos de traslado* con categoría **APH**.

Cada línea extraída se busca en la facturación del HIS:
1. misma factura, código y valor (y documento del paciente, cifrado): **conciliada**, no se duplica;
2. misma factura y código con **otro valor**: conciliada **con diferencia**; se guarda el valor RIPS;
3. sin equivalente: se crea como **solo en RIPS**, con la ruta inferida por km cuando aplica.

Controles de calidad del cargue (pantalla *Calidad* y resultado del cargue):
- **Facturas repetidas** en más de una carpeta o archivo: sus líneas se toman una sola vez.
- **Líneas incompletas** (p. ej. `AP` con 14 campos en vez de 15, número de factura sin consecutivo): un hallazgo por archivo con el número de las líneas.
- **Periodos de factura inconsistentes** (inicio más de un año antes de la expedición).
- Líneas sin hospital reconocido, fuera del mes cargado o de otro prestador.

Cargar dos veces el mismo archivo no duplica ni vuelve a conciliar. La pantalla de resultado muestra el detalle por hospital y mes de servicio y el botón **Descargar traslados y APH (Excel)**. El menú **RIPS** lista todas las cargas (las de varios meses muestran el rango).

## 2b. Auditoría RIPS vs facturación

Menú **Auditoría RIPS**. Filtros: desde, hasta, hospital y servicio (traslados/APH). Para cada hospital y mes:

| Situación | Significado |
|---|---|
| Coinciden | Facturado en el HIS y reportado en RIPS con el mismo valor |
| Valor distinto | Está en ambos con otro valor (se muestra la diferencia RIPS − HIS) |
| Facturado, falta en RIPS | El hospital tiene RIPS de ese mes cargados y la línea no aparece |
| En RIPS, falta en facturación | Reportado en RIPS sin línea en la facturación HIS del mes |
| Prefactura sin número | Facturado sin número de factura: todavía no puede estar en RIPS |
| RIPS no cargado / facturación no cargada | Falta el otro archivo: no se cuenta como diferencia |

Un hospital y mes se consideran con RIPS cargados cuando sus facturas declaran ese mes (inicio o fin del periodo del `AF`, o fecha de servicio en JSON) y son al menos `rips_min_facturas_mes` (parámetro, 5 por defecto).

La pantalla trae el estado por mes (*Cuadra*, *Con diferencias*, *Sin RIPS cargado*, *Sin facturación HIS cargada*), la tabla **por recorrido** y la lista de líneas con diferencia (con enlace para corregir cada traslado). **Descargar auditoría (Excel)** trae las tres hojas completas.

## 3. Tablas de consumos

Menú **Consumos** (o los botones naranjas de *Cargas del mes*). Una pestaña por tabla:

| Tabla | Tipos que agrupa | Recurso ABC |
|---|---|---|
| Combustible | Combustible | Combustible (reemplaza el estándar del estudio en el mes) |
| Mantenimiento | Mantenimiento, lubricantes, llantas | Mantenimiento (reemplaza el estándar) |
| Personal | Nómina de la tripulación | Tripulación (reemplaza el estándar) |
| Seguros e impuestos | SOAT, pólizas, impuesto vehicular, revisión técnico-mecánica | Otros (se suma; por número de traslados) |
| Imprevistos | Imprevistos | Otros (se suma) |
| Insumos | Insumos y dispositivos médicos | Insumos (se suma) |

En cada tabla:
- **Registrar**: un registro con fecha, placa, valor, cantidad, odómetro, soporte y proveedor.
- **Descargar plantilla Excel** y **Cargar (Excel o CSV)**: las filas sin columna `tipo` toman el tipo de la tabla. Columnas: `placa, fecha, tipo, descripcion, cantidad, unidad, valor, meses_prorrateo, odometro, documento_soporte, proveedor`. En `tipo` se aceptan sinónimos: PERSONAL, NOMINA, SEGUROS, IMPUESTOS, SOAT, IMPREVISTO, ACPM, GASOLINA.
- **Meses de prorrateo**: para pagos que cubren varios meses (SOAT anual = 12), el valor se reparte en partes iguales desde el mes de la fecha.
- Matriz **ambulancia × mes** con total, cantidad y comparación con el estándar del informe técnico (combustible, mantenimiento y personal). En combustible, si hay lecturas de odómetro, se calcula el km/gal real.
- **Descargar tabla (Excel)** con la matriz y el detalle.

## 4. Recorridos por sede y rentabilidad

Menú **Recorridos por sede**. Filtros: periodo, sede, servicio (traslados, APH o ambos) y método de costeo.

- **Resumen por sede**: ambulancias, traslados, km, facturado, costo ABC asignado, margen y rentabilidad, costo de la flota de la sede, capacidad no utilizada y resultado incluyendo la capacidad ociosa.
- **Por cada sede**, todos sus recorridos del Anexo 3: km, tarifa 2026, **costo estándar** (viaje de ida y vuelta, promedio de las ambulancias de la sede y rango entre la más barata y la más cara), margen y rentabilidad estándar, y lo **real del periodo**: traslados, facturado, costo ABC, margen y rentabilidad.
- Una fila adicional agrupa lo que no corresponde a una ruta del anexo: km no encontrados, traslados con tarifa básica y APH.
- **Descargar Excel** con el resumen y el detalle.

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

## 2. RIPS por mes y por hospital

Botón **Cargar RIPS por mes y hospital**. Se indica el mes y el hospital y se seleccionan uno o varios archivos:

- **JSON** de la Resolución 2275 de 2023 (una factura o una lista de facturas).
- **Archivos planos** de la Resolución 3374 de 2000: `AF` (facturas), `AT` (otros servicios) y `AP` (procedimientos). El `US` no se lee.
- **ZIP** con cualquiera de los anteriores.

La aplicación **extrae solo los traslados y las atenciones prehospitalarias (APH)**:

| Regla (en este orden) | Resultado |
|---|---|
| Código registrado en *Administración › Códigos de traslado* | Su categoría (TRASLADO o APH) |
| Nombre del servicio con "PREHOSPITALARIA" o que empieza por "APH", o autorización "APH" | APH |
| Nombre del servicio con "TRASLADO" o "AMBULANCIA" | TRASLADO |
| Archivo AT con tipo de servicio 2 (traslados) | TRASLADO |

Para que un código de APH se reconozca siempre, regístrelo en *Códigos de traslado* con categoría **APH**. No se precargaron códigos APH porque no venían en las fuentes entregadas.

Cada línea extraída:
- si ya vino en la facturación del HIS (misma factura, código y valor), se marca **conciliada** y no se duplica;
- si no, se crea en el hospital elegido, con la ruta inferida por km cuando aplica.

Se marcan para revisión las líneas con fecha fuera del mes cargado y las de un código de prestador distinto al del hospital (registre el código REPS en *Administración › Sedes*). En los planos, el `AT` no trae fecha: se usa la fecha de inicio del periodo de la factura (`AF`) y se deja la observación.

La pantalla de resultado muestra facturas, líneas revisadas, traslados y APH extraídos, conciliados y nuevos, con filtro por categoría y botón **Descargar traslados y APH (Excel)**. El menú **RIPS** lista todas las cargas por mes y hospital.

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

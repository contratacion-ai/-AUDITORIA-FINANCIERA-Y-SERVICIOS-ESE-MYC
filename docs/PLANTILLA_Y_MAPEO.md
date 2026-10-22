# Plantilla estándar de carga mensual y mapeo de columnas

## Plantilla estándar

Archivo: `plantillas/Plantilla_Carga_Mensual_ABC.xlsx` (también en *Cargas → Descargar plantilla estándar*; se genera desde
los mismos esquemas que valida el sistema). Una hoja por tipo; encabezados **naranja = obligatorios**, **azul = opcionales**;
cada encabezado tiene un comentario con su descripción y un ejemplo.

| Hoja | Uso | Obligatorios |
|---|---|---|
| ESTADO_FINANCIERO | Fuente oficial del costo del mes (saldos/movimientos clase 5, 7) | CUENTA, VALOR (o DEBITO/CREDITO) |
| NOMINA | Soporte de R1/R2 por persona y centro | DOCUMENTO, VALOR (+ cuenta o recurso) |
| CONTRATISTAS | Soporte de R2/R3; sin centro → % OPS | DOCUMENTO, VALOR |
| INVENTARIOS | Salidas de almacén/farmacia por centro (R4–R7, R12) | VALOR |
| DEPRECIACION | Depreciación/amortización por centro (R27, R28) | VALOR |
| OTROS_AREA | Otros soportes por centro o por sede (servicios públicos…) | VALOR |
| VALORES_INDUCTOR | Cantidades del inductor por centro (y centro origen) | INDUCTOR, CENTRO DE COSTOS, CANTIDAD |
| MANO_OBRA | % Empleado/OPS por persona; suma 100 | TIPO, DOCUMENTO, CENTRO DE COSTOS, PORCENTAJE |
| PRODUCCION | Producción por centro y CUPS (si no, se derivan de RIPS) | CENTRO DE COSTOS, CUPS, CANTIDAD |
| FACTURACION | Detalle facturado (software 360 u otro HIS) | NUMERO FACTURA, EPS, CUPS, CANTIDAD, VALOR TOTAL |
| RIPS | Consultas, procedimientos, urgencias, hospitalización | NUMERO FACTURA, CUPS |
| NOTA_TECNICA | Contratos y tarifas por EPS (catálogo) | EPS, CONTRATO, MODALIDAD, VIGENCIAS, CUPS, TARIFA |
| CUPS | Catálogo CUPS y centro que los produce, peso (catálogo) | CUPS, DESCRIPCION |
| CENTROS_COSTO | Catálogo de centros + código contable homologado (catálogo) | CODIGO, NOMBRE, UNIDAD DE NEGOCIO |
| CUENTAS_RECURSO | Cuenta / prefijo → recurso (catálogo) | CUENTA |

### Instrucciones de diligenciamiento

1. Llene solo las hojas que use en el mes; no cambie los encabezados.
2. Valores: `1.234.567,89`, `1234567.89`, `$ 1.234`, `(1.000)` y `1.000-` son válidos.
3. Fechas `dd/mm/aaaa`, `aaaa-mm-dd` o fecha de Excel.
4. CENTRO DE COSTOS: código del modelo (`A10`) o el código/nombre contable registrado en *Homologación centro de costos*.
5. CUENTA: el recurso se asigna por el prefijo más largo registrado en *Cuentas contables - Recursos*.
6. DOCUMENTO PACIENTE se convierte en un seudónimo irreversible al cargar; el número nunca se guarda.
7. Puede cargar cada hoja por separado (*Nueva carga*, eligiendo la hoja) o el libro completo (*Cargar libro estándar completo*).

### Qué valida y limpia el sistema

Estructura (hoja, fila de encabezado, columnas obligatorias), extensión y tamaño, filas vacías, valores nulos en campos
obligatorios, números con separadores de miles/decimales y símbolos, fechas, tildes/mayúsculas/espacios en códigos y
encabezados, caracteres invisibles, filas duplicadas (se reportan y omiten), códigos inexistentes (cuenta, recurso, centro,
sede, inductor, CUPS), porcentajes que no suman 100, fechas fuera del periodo (advertencia), archivo idéntico ya aplicado
(rechazo por huella SHA-256), periodo cerrado. Con errores, la carga se rechaza completa salvo que se marque
*aplicar las filas válidas*.

## Mapeo de columnas por institución

Cuando los archivos de una institución no usan la plantilla (p. ej. la nómina exporta `DocumentoTercero`,
`NombreCentroCosto`, `ValorNuevoSaldo`), se crea una **plantilla de mapeo** en *Cargas → Mapeo de columnas*:

* **Tipo de archivo**, **hoja** y **fila del encabezado** (para reportes con títulos arriba).
* Por cada campo estándar: la **columna de origen** por su nombre (sin importar tildes/mayúsculas) o por su letra con `#` (`#E`).
* **Valor fijo** por campo (p. ej. `RECURSO = R1` para una nómina de planta; `SEDE = S02` para un archivo de una sede).
* **Separador decimal** (automático, coma o punto) y **formato de fecha**.
* *Leer encabezados y sugerir*: suba un archivo de muestra y el sistema propone las correspondencias (sinónimos + similitud).

Los mapeos son por institución: un consultor puede tener un mapeo distinto para cada cliente sin que se mezclen.
Cada carga guarda qué columnas se usaron (detalle de la carga).

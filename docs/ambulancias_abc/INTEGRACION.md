# Integración con las aplicaciones de RIPS y de auditoría

Principio: **cada dato tiene un solo dueño**. Esta aplicación es dueña de los costos (perfiles, consumos, asignación ABC). La facturación y los RIPS siguen siendo de sus sistemas; aquí se importan con llaves naturales para no duplicar, y se exportan los resultados con las mismas llaves.

## Llaves de correspondencia

| Objeto | Llave | Formato | Uso |
|---|---|---|---|
| Ambulancia | `placa` | `OEU846` (mayúsculas, sin guion) | Todas las apps |
| Sede | `sede_codigo` | `tame`, `arauquita`, `pto-rondon`, `fortul`, `la-esmeralda`, `cravo-norte`, `pto-jordan`, `panama` | Todas las apps; RIPS por `codigo_prestador` (configúrelo en Administración › Sedes) |
| Recorrido | `ruta_codigo` + `vigencia` | `tame-saravena`, `2026` | Manual tarifario |
| Traslado | `llave_externa` | ver abajo | Idempotencia de importaciones |
| Traslado ↔ facturación | `numero_factura` + `codigo_cups` + `valor_facturado` (+ `documento_hash`) | — | Conciliación con RIPS |
| Traslado ↔ HIS | `nro_ingreso`, `prefactura` | — | Auditoría de cuentas |
| Paciente | `documento_hash` | SHA-256(sal \| tipo \| número) | Cruce sin guardar el documento |

`llave_externa`:
- HIS: `HIS|<prefactura>|<ingreso>|<cups>|<fecha servicio>|<cantidad>|<valor>|<n>` (n = ocurrencia dentro del archivo).
- RIPS: `RIPS|<numFactura>|<hash16>|<cups>|<otrosServicios/procedimientos>|<consecutivo>`.
- Manual: `MANUAL|<uuid>`.

> Para que la app de RIPS calcule el mismo `documento_hash`, debe usar la misma sal (`sal_documento` en `ambulancias.ini`). Compártala por un canal seguro, o haga el cruce solo por factura + CUPS + valor.

## Importación (hacia esta aplicación)

| Fuente | Cómo | Formato |
|---|---|---|
| Facturación de traslados (HIS) | Pantalla *Importar* o `manage.py importar_archivo facturacion <xlsx>` | Columnas: `SEDE, COD. PROCEDIMIENTO, CANTIDAD, VALOR ACTIVIDAD, VALOR TOTAL, FECHA DE SERVICIO, FACTURA NRO, PRE-FACTURA NRO, NRO INGRESO, AMBITO, UNIDAD FUNCIONAL, CONTRATO, NOMBRE EPS, CODIGO EPS, REGIMEN DEL PACIENTE, TIPO IDE., NUMERO IDE.` (nombres, teléfonos y direcciones se ignoran) |
| RIPS | `POST /api/v1/rips` (JSON, clave con escritura), pantalla *Importar* o `importar_archivo rips <json>` | JSON Res. 2275/2023: factura (`numFactura`) → `usuarios[]` → `servicios.otrosServicios[]` (`codTecnologiaSalud`, `cantidadOS`, `vrUnitOS`, `vrServicio`, `fechaSuministroTecnologia`, `consecutivo`, `codPrestador`) y `servicios.procedimientos[]` (`codProcedimiento`, `vrServicio`, `fechaInicioAtencion`) |
| RIPS por mes y hospital | Pantalla *Cargas del mes › Cargar RIPS* (varios archivos) o `POST /api/v1/rips?mes=AAAA-MM&sede=<codigo>` | Además del JSON: archivos planos Res. 3374/2000 (`AF`, `AT`, `AP`) y ZIP. Se extraen traslados y APH (ver [GUIA_CARGAS_MENSUALES.md](GUIA_CARGAS_MENSUALES.md)) |
| Consumos | Pantalla *Importar* o `importar_archivo consumos <csv>` | `placa;fecha;tipo;descripcion;cantidad;unidad;valor;odometro;documento_soporte;proveedor` (tipo: COMBUSTIBLE, MANTENIMIENTO, LUBRICANTE, LLANTAS, INSUMO, TRIPULACION, OTRO) |
| Manual tarifario | `importar_archivo tarifas <xlsx> --vigencia 2026` | Hoja que contenga "TRASLADO": ORIGEN, DESTINO, Km y columna del año |

### JSON de intercambio de traslados (desde la aplicación de RIPS)

Para migrar los traslados que ya tiene la aplicación de RIPS **con la placa, el destino y los km** (datos que el RIPS de la Res. 2275 no trae), la aplicación de RIPS exporta este JSON (ejemplo completo: [ejemplo_intercambio_traslados.json](ejemplo_intercambio_traslados.json)):

```json
{"formato": "costeo-abc-traslados", "version": "1.0", "origen": "app-rips",
 "traslados": [{"numero_factura": "FHSA15037", "fecha_servicio": "2026-09-20", "codigo_cups": "601T02",
                "cantidad": 101, "valor_unitario": 17000, "valor_facturado": 1717000, "sede_codigo": "tame",
                "tipo_documento": "CC", "numero_documento": "…", "placa": "OEU846", "municipio_destino": "SARAVENA", "km": 202}]}
```

| Campo | Obligatorio | Uso |
|---|---|---|
| `numero_factura`, `codigo_cups`, `valor_facturado`, `fecha_servicio` (AAAA-MM-DD) | Sí | Conciliación con la facturación HIS y llave para no duplicar |
| `cantidad`, `valor_unitario`, `descripcion`, `ambito`, `autorizacion`, `eps_nombre`, `consecutivo` | No | Como en el RIPS |
| `categoria` | No | `TRASLADO` o `APH`; si falta, se clasifica con las reglas del RIPS |
| `sede_codigo` o `cod_prestador` | No | Hospital (si faltan, se reconoce por el prefijo de la factura) |
| `tipo_documento` + `numero_documento`, o `documento_hash` | No | El número se cifra al llegar (SHA-256 con la sal); no se guarda |
| `placa`, `municipio_destino`, `km` | No | Se asignan al traslado (placa y recorrido solo si no tenía; km en los traslados creados desde este archivo) |

También se acepta una lista simple de traslados (`[{...}, {...}]`). Envío: pantalla *Cargas del mes › Cargar cualquier archivo* (o *Cargar RIPS*), o `POST /api/v1/rips` con el JSON en el cuerpo, o `POST /api/v1/archivos`. Reenviar el mismo archivo no duplica; si un envío posterior trae la placa o el destino que faltaban, se completan.

### Cargar cualquier archivo (pantalla y API)

*Cargas del mes › Cargar cualquier archivo* y `POST /api/v1/archivos` (clave con escritura; campo multipart `archivo`, uno o varios, o el cuerpo crudo con `X-Nombre-Archivo`; `mes` y `sede` opcionales) reconocen cada archivo por su contenido: RIPS (JSON, JSON de intercambio, AF/AT/AP, ZIP), facturación del HIS, informe de mantenimiento y combustible, formato RYC-FO-015, consumos (Excel o CSV), manual tarifario y costos del centro de costos. Los `.xls` se convierten solos; un ZIP puede mezclar Excel y RIPS; XML, PDF e imágenes se informan sin detener la carga. Respuesta: `{"resultados": [{"archivo", "tipo", "lote", "error", "mensaje", "nuevos", "actualizados", "hallazgos"}]}` (HTTP 422 si ningún archivo se pudo cargar).

```powershell
Invoke-RestMethod "http://servidor-abc:8090/api/v1/archivos" -Method Post -Headers $h -Form @{ archivo = Get-Item .\traslados.json }
```

**Reglas de no duplicación**
- Reimportar el mismo archivo del HIS actualiza las líneas existentes (por `llave_externa`); no crea nuevas. La placa y la ruta corregidas a mano no se sobrescriben.
- El RIPS primero busca la línea ya importada del HIS (factura + CUPS + valor, y hash del documento si coincide) y solo la marca `conciliado_rips` con su `consecutivo`. Crea un traslado solo si no existe en el HIS y el `codPrestador` corresponde a una sede.
- Los códigos de traslado reconocidos se configuran en *Administración › Códigos de traslado* (por defecto 601T01, 601T02, S31307).

## Exportación (hacia auditoría u otras apps)

Autenticación: encabezado `X-API-Key`. Cree la clave con `manage.py crear_clave_api auditoria` (solo lectura) o `crear_clave_api rips --escritura`. Añada `formato=csv` para CSV (`;`, UTF-8 con BOM, abre directo en Excel).

| Endpoint | Parámetros | Contenido |
|---|---|---|
| `GET /api/v1/estado` | — | Versión del contrato y conteos |
| `GET /api/v1/ambulancias` | — | placa, sede, combustible, estado, costo/día estándar |
| `GET /api/v1/rutas` | — | ruta_codigo, vigencia, origen, destino, km, valor_km, tarifa |
| `GET /api/v1/traslados` | `mes=AAAA-MM`, `metodo=REAL\|CAPACIDAD` | Cada línea con `categoria` (TRASLADO/APH), llaves (factura, ingreso, RIPS, hash), placa o reparto, ruta, km, valor facturado, **costo ABC**, margen y observaciones de calidad |
| `GET /api/v1/costos-ambulancia` | `mes`, `metodo` | Costo por recurso y su fuente (REAL/ESTANDAR), asignado, capacidad no utilizada, km, horas, traslados, facturado y margen por placa; `costo_total_flota` |
| `GET /api/v1/consumos` | `mes` (opcional) | Consumos con soporte, odómetro y proveedor |
| `GET /api/v1/hallazgos` | `pendientes=1` | Inconsistencias de las fuentes |
| `POST /api/v1/rips` | cuerpo JSON RIPS o JSON de intercambio; `mes=AAAA-MM` y `sede=<codigo>` opcionales; `X-Nombre-Archivo` opcional | `{lote, nuevos, conciliados, omitidos, hallazgos, traslados, aph}` |
| `POST /api/v1/archivos` | uno o varios archivos de cualquier tipo; `mes`, `sede` opcionales | resultado por archivo |

Ejemplo (PowerShell):

```powershell
$h = @{ "X-API-Key" = "abc_..." }
Invoke-RestMethod "http://servidor-abc:8090/api/v1/traslados?mes=2026-09" -Headers $h
Invoke-WebRequest "http://servidor-abc:8090/api/v1/costos-ambulancia?mes=2026-09&formato=csv" -Headers $h -OutFile costos.csv
Invoke-RestMethod "http://servidor-abc:8090/api/v1/rips" -Method Post -Headers $h -ContentType "application/json" -InFile factura.json
```

El campo `contrato` de cada respuesta indica la versión (hoy `1.0`). Cambios incompatibles subirán la versión mayor y se publicarán en una ruta nueva (`/api/v2/`).

## Uso sugerido por la app de auditoría

1. Descargar `traslados?mes=` y cruzar por `numero_factura` / `nro_ingreso` con las glosas.
2. Revisar las líneas con `observaciones_calidad` (doble cobro base + km, duplicados, km sin ruta en el anexo, sin factura).
3. Usar `costos-ambulancia` para el informe mensual de rentabilidad por unidad.

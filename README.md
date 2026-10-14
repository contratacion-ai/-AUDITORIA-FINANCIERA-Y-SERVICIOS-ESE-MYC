# Auditoría de Facturación · ESE Moreno y Clavijo — v4.0

**Winston Gerlein Gutiérrez Pastrana – Profesional de Costos**

Carga mensual de **facturación por ámbito (Software 360)**, **RIPS**, **costos** y **notas técnicas EPS**; concilia los tres ejes,
valida radicación factura a factura y exporta todo a Excel.

## Ejecutar (un clic: INICIAR_WINDOWS.bat o INICIAR_MAC_LINUX.command; ver LEEME_PRIMERO.txt)
Manual:
    pip install -r requirements.txt
    uvicorn app.main:app --port 8000        # UI: http://localhost:8000  ·  API/OpenAPI: http://localhost:8000/docs
Variable opcional `AUDIT_DATA_DIR` (por defecto `./data`: base SQLite `auditoria.db` + `uploads/`).

## Módulos
| Módulo | Responsabilidad |
|---|---|
| `app/loaders.py` | Extracción. Facturación: lector **streaming** del XML del xlsx (libros de 500 MB, ~11 s por mes de 220 mil líneas). RIPS/costos/notas: lectura flexible por alias de columnas, Excel/CSV, detecta fila de encabezados y mes. |
| `app/processing.py` | Agrega, persiste, reemplaza el mes si se recarga, registra historial (`carga`). |
| `app/analysis.py` | Conciliaciones, radicación, contratos, notas técnicas, hallazgos, informes 1–8, recomendaciones. |
| `app/exporter.py` | Libro Excel consolidado (19 hojas) o informe individual. |
| `app/main.py` | API FastAPI + sirve `static/index.html` (dashboard). |

## Endpoints principales (detalle en `/docs`)
| Método y ruta | Uso |
|---|---|
| `POST /api/cargas/{facturacion\|rips\|costos\|notas}` (form: `file`, `mes?`) | Sube y procesa en segundo plano; facturación crea una carga por hoja mensual |
| `GET /api/cargas`, `/api/cargas/{id}` | Historial (fecha, mes, estado, filas, mensaje de error) |
| `GET /api/plantillas/{rips\|costos\|notas}` | Plantilla Excel con columnas reconocidas |
| `GET /api/resumen?mes=` | KPIs, serie mensual, ámbitos, recomendaciones |
| `GET /api/informes/{nombre}?mes=` | Informes 1–8 parametrizados (`/api/informes` lista) |
| `GET /api/conciliacion/{fact-rips\|tri-eje\|contratos\|radicacion\|notas-tecnicas\|hallazgos}` | Reconciliaciones |
| `GET /api/export?mes=` · `/api/export/{hoja}` | Excel consolidado / individual |

## Estructuras de datos (SQLite)
`carga` (historial) · `fact` (mes×EPS×contrato×sede×servicio×ámbito×régimen×CUPS: cantidad, valor, líneas) · `dim` (informes 3–8 por sede) ·
`factura` (valor por factura, nº de contratos) · `hallazgo` (reglas R01–R07) · `rips` · `costo` · `nota` · `param_contrato` (modalidad).

## Reglas de auditoría de línea
R01 sin factura · R02 valor ≤ 0 · R03 total ≠ valor actividad × cantidad · R04 cantidad ≤ 0 · R05 sin CUPS/medicamento · R06 fecha fuera del mes · R07 factura en varios contratos.

## Supuestos y límites
- **Privacidad:** no se almacenan nombres ni documentos de pacientes; solo agregados y conteo de pacientes únicos por sede.
- Cruce Facturación↔RIPS por **código EPS** y por **número de factura** (se normaliza a mayúsculas sin símbolos). Si el RIPS trae otro identificador de EPS, use la plantilla.
- Servicios se cruzan por nombre normalizado: si RIPS/costos usan otra nomenclatura de servicios, las filas aparecerán separadas hasta homologarlas.
- Umbrales (2 % / 5 %) en `app/db.py`. Sin autenticación ni tests (prototipo). RIPS/costos se leen con pandas en memoria.

## Homologación de formatos (v2) — llaves comunes entre las 4 fuentes
| Llave | Software 360 | RIPS | Costos | Notas técnicas |
|---|---|---|---|---|
| Periodo | hoja «ENERO 2026» | MES (acepta `01-ENERO`) | MES | — |
| EPS | NOMBRE EPS | EPS | — | EPS (alias editables) |
| Contrato | CONTRATO | Contrato / Descripción FEV | — | CONTRATO (opcional) |
| Modalidad | PARAM CONTRATO | MODALIDAD | — | MODALIDAD |
| Sede | SEDE | Nombre Sede | SEDE | — |
| Factura | FACTURA NRO | Factura FEV | — | — |
| Código | COD. PROCEDIMIENTO / MEDICAMENTO | Código CUPS/CUMS (+ tabla `hom_codigo`) | CUPS | CUPS |
| Servicio | SERVICIO (PARAM SERVICIO) | por CUPS | SERVICIO | categoría |

- `GET /api/plantillas/{rips|costos|notas|parametros|codigos}`: plantilla + hoja INSTRUCCIONES con la llave de cada columna.
- `POST /api/homologar/{rips|notas|costos}`: convierte el archivo nativo (consolidado RIPS, comparativo de notas técnicas) al formato homologado.
- `GET /api/conciliacion/validacion-cruce`: % de cada llave que cruza entre fuentes y ejemplos que no cruzan.
- `GET /api/homologacion/codigos-pendientes`: códigos del RIPS que no existen en 360 (para completar y cargar como tipo `codigos`).
- **Cápita**: el valor cápita (FEV global) no está en el detalle de 360; se audita por **eventos**. **Evento**: por valor FEV.
- Informe 5 (paciente) guarda NUMERO IDE; para anonimizar defina `AUDIT_ENMASCARAR_IDE=1` antes de iniciar.

## Dashboard (v3)
Pestañas con gráficas: Resumen · **Comparativos** (sedes, cápita vs evento por mes, servicios, margen %, top contratos, evolución mensual,
comparativo mes a mes con variación y «lectura rápida» automática) · Informes (barras apiladas por sede) · Validación del cruce ·
Facturación vs RIPS · Tri-eje · Contratos · Radicación · Notas técnicas · Hallazgos · Cargas.
Endpoints nuevos: `GET /api/comparativos`, `GET /api/comparativo/mensual?nivel=`, `GET /api/estados/{radicacion|notas|contratos|tri-eje}`.
Las gráficas usan Chart.js desde cdnjs (se necesita internet al abrir el dashboard).

## Novedades v4.0 (por hoja / pestaña)
| Hoja / pestaña | Cambio |
|---|---|
| Todas las hojas del libro | Portada con índice vinculado; banda de título, autoría «Winston Gerlein Gutiérrez Pastrana – Profesional de Costos», encabezado y pie de impresión (paginación), Calibri y paleta azul única, semáforo en la columna `estado`. |
| **Resumen** | Nueva pestaña/hoja **Resumen por empresa** (`/api/resumen/empresas`, hojas `Resumen_Empresa` y `Resumen_Empresa_Mes`): facturado, cápita/evento/otras, líneas, facturas, contratos, brecha Fact–RIPS y hallazgos por EPS. |
| **Comparativos** | Se retira «Cápita vs Evento por mes». Nuevos: top 10 empresas (cápita/evento apilado), Pareto de servicios (concentración + % acumulado) y margen % por sede. En el libro: `Comparativo_{Sede,Servicio,EPS,Contrato}_Mes`. |
| **Informe 3 Servicio** | Barras horizontales (top 18 servicios) + facturado por sede; en Excel, gráfica horizontal nativa junto a la tabla. |
| **Facturación vs RIPS** | Regla de actividad (ver supuestos): `fact.origen_cups` indica CUPS / INTERNO 360 / ACTIVIDAD / SIN IDENTIFICAR; hoja `Regla_Actividad`. |
| **Costos (nueva)** | Pestaña `💰 Costos` y hojas `Costos_Cruce`, `Costos_Validaciones`: cruce por CUPS entre Notas técnicas → RIPS → Costos → Facturación. Endpoints `/api/costos/{cruce,resumen,validaciones}`. |
| **Plantillas** | Hoja LEEME, encabezado en fila 4 (azul = obligatoria, gris = opcional), comentarios por columna, listas desplegables (MODALIDAD, TIPO_COSTO), validación numérica ≥ 0, ejemplo en cursiva. Nuevas columnas: `DESCRIPCION_SERVICIO` (RIPS), `TIPO_COSTO` (costos), `COSTO_UNITARIO` (notas). |

### Correcciones (también aplicadas a la versión anterior v3)
Texto en línea (`inlineStr`) que se descartaba; fechas en texto (`dd/mm/aaaa`) que abortaban la carga; hojas con filas de título antes del encabezado (junio); `ZeroDivisionError` en Tri-eje cuando una combinación tenía valor facturado y cantidad 0.

### Validaciones de la carga de costos (`carga_validacion`)
ERROR (la fila no se carga): mes no reconocido, sin CUPS ni servicio, valor o cantidad negativos. ADVERTENCIA (se carga): sede vacía, fila sin cantidad ni valor, costo unitario × cantidad ≠ valor (> 2 %), llave repetida. «fila» = n.º de registro leído.

### Supuestos para validar
1. **Regla de actividad:** la ACTIVIDAD se normaliza (mayúsculas, sin tildes ni signos, sin el sufijo «(5326)» ni «+») y se asocia con el CUPS **numérico** más frecuente de la facturación. Se usa cuando una línea llega **sin código** (facturación) o un registro de RIPS llega sin CUPS (usa la descripción). Prioridad: CUPS explícito > actividad > CUMS homologado. Los códigos G-/D- son «INTERNO 360» y no se reemplazan.
2. **RIPS:** la FEV es la llave de radicación; cápita se audita por eventos, evento por valor FEV; la descripción se toma de «DESCRIPCION DE SERVICIO».
3. **Notas técnicas:** «TOTAL SERVICIOS NOTA TECNICA» = eventos mensuales de cápita; «COSTOS» (hoja NT NUEVA EPS) = costo unitario por CUPS y modalidad; «Homologo/CodigoCUPS» de PARAMETRIZACION NT EPS son la llave CUPS.
4. **Costos:** tolerancia de producción 10 % (costos vs RIPS/facturación) y 2 % de consistencia unitario × cantidad (`app/db.py`).
5. «Hoja Tres» = Informe 3 Servicio.

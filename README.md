# Auditoría RIPS · CAPITA · EVENTO · PGP — E.S.E. Moreno y Clavijo

Aplicación para auditar y controlar los RIPS y la facturación de la E.S.E. Departamental de Primer
Nivel Moreno y Clavijo (NIT 900034131) con Nueva EPS (EPS037/EPSS37), en los municipios de
**Tame, Arauca, Fortul, Cravo Norte y Puerto Rondón**.

* **Carga** RIPS en TXT (Res. 3374/2000), JSON (Res. 2275/2023 o consolidado), XLSX consolidado,
  ZIP, RAR o carpetas completas, organizados por hospital/municipio y mes.
* **Valida** cada archivo (estructura del Anexo Técnico, NIT, EPS, periodo, duplicados, campos
  obligatorios) sin detener la carga.
* **Separa y deduplica** la misma información que llega en varios formatos o en varias carpetas.
* **Clasifica** cada servicio como CAPITA o EVENTO (catálogo OFERTADO/NO OFERTADO + Nota Técnica),
  con trazabilidad de la regla aplicada.
* **Calcula** el costo CAPITA = costo unitario NT × frecuencia RIPS, los escenarios de negociación,
  el PGP y el corredor de EVENTO, con el mismo modelo y los mismos nombres de grupo que los
  dashboards de referencia.
* **Cruza** RIPS vs Nota Técnica, EVENTO vs tarifario pactado y RIPS vs facturación por ámbito.
* **Informa** en Excel (26 hojas), PDF ejecutivo con recomendaciones y JSON para guardar/restaurar.

Los datos de pacientes se procesan en el equipo local (`datos/`, excluido de git) y los informes
agregados usan seudónimos (`PAC-xxxxxxxxxxxx`).

## Totales de control

Las pruebas reproducen los valores del dashboard FINAL (enero–agosto 2026):

| Indicador | Control |
|---|---|
| Costo CAPITA ene–ago | $8.053.016.408 |
| CUPS | 748.058 |
| Pacientes únicos (suma mensual) | 114.923 |
| Asistencias | 213.814 |
| Promedio/mes · Esc.1 | $1.006.627.051 |
| Esc.2 CAPITA ampliada | $1.112.074.196 |
| Esc.3 CAPITA demanda real | $1.253.045.148 |
| PGP-2 (UPC + EVENTO) | $3.045.623.529 |

```bash
python -m rips_auditoria.cli controles
```

## Instalación

Requiere Python 3.11 o superior.

```bash
pip install -r requirements.txt
```

Para leer **RAR** instale además una de estas herramientas: `unrar`, `unar`, `bsdtar` o `7z`
(en Windows, 7-Zip, con `7z.exe` en el PATH). Sin ellas, el RAR se reporta como
`RAR_SIN_HERRAMIENTA` y el resto de la carga continúa.

## Uso mes a mes

### 1. Una sola vez (o cuando cambien): cargar los maestros

| Maestro | Archivo | Comando |
|---|---|---|
| Catálogo, NT COSTOS y NT por municipio | `INFORME DE SERVICIOS 2026 BASE DE DATOS ANALISIS.xlsx` | `python -m rips_auditoria.cli maestro informe "INFORME....xlsx"` |
| Tarifario pactado de evento | plantilla `config/plantillas/tarifario.csv` | `python -m rips_auditoria.cli maestro tarifario tarifario.xlsx` |
| Facturación por ámbito | plantilla `config/plantillas/facturacion.csv` | `python -m rips_auditoria.cli maestro facturacion facturacion_agosto.xlsx` |
| Despacho de medicamentos (opcional) | plantilla `config/plantillas/despacho_medicamentos.csv` | `python -m rips_auditoria.cli maestro despacho_medicamentos despacho.csv` |

Mientras no se cargue el informe de servicios, la herramienta usa un **catálogo parcial** extraído
del dashboard CAPITA·EVENTO (346 códigos con marca OFERTADO/NO OFERTADO y 361 códigos de la NT,
sin costos). Con ese catálogo se clasifica y se agrupa, pero el costo CAPITA queda en $0 y el
informe lo advierte.

### 2. Cada mes: organizar los RIPS

Estructura recomendada (la misma que exporta Google Drive):

```
Hospital San Jose de Cravo Norte/
  08. Agosto/
    NUEVA EPS CAPITA SUBSIDIADO/   CT011210.txt AF011210.txt US011210.txt AC… AP… AM… AT…
    NUEVA EPS EVENTO SUBSIDIADO/   …
    SANITAS EVENTO CONTRIBUTIVO/   …   (otras EPS: se cargan y se excluyen del análisis Nueva EPS)
```

El municipio sale del código de habilitación del prestador (por ejemplo `81794…` = Tame) y, si
falta, del nombre de la sede. El mes sale de la carpeta `NN. Mes`; si no hay, de las fechas del AF.
Todo se puede corregir a mano (interfaz o parámetros `--hospital --municipio --periodo --eps`).

### 3a. Procesar desde el escritorio (sin navegador)

```bash
python scripts/procesar_carpeta.py "D:/RIPS/Hospital San Jose de Cravo Norte" --pdf
```

```bash
python -m rips_auditoria.cli procesar RIPS_TAME_AGOSTO.zip RIPS_ARAUCA_AGOSTO.rar --salida informes --pdf
```

En Windows también puede arrastrar la carpeta sobre `scripts/procesar_mes.bat`.
Se generan en `informes/`:

* `validacion_<lote>.xlsx`: índice hospital-mes, remisiones, incidencias y archivos ignorados.
* `informe_<lote>.xlsx`: informe completo (hojas descritas abajo).
* `informe_<lote>.pdf`: informe ejecutivo para alta gerencia.
* `estado_<lote>.json`: estado completo para restaurar el análisis.

### 3b. Procesar desde la interfaz web local

```bash
uvicorn api.app:app --host 127.0.0.1 --port 8000
```

En Windows: `scripts/iniciar_servidor.bat`. Abra http://127.0.0.1:8000 y siga las pestañas:

1. **Cargar RIPS**: arrastre archivos/carpetas, ZIP o RAR (o elija una carpeta completa).
2. **Lotes y validación**: revise el índice hospital-mes, corrija hospital, municipio, mes o EPS de
   cada remisión y descargue la validación.
3. **Maestros**: suba el informe de servicios, el tarifario y la facturación; descargue las plantillas.
4. **Análisis e informes**: seleccione uno o varios lotes (por ejemplo los 5 municipios del mes), ejecute
   y descargue Excel, PDF o el estado JSON. *Restaurar desde JSON* recupera un análisis anterior.
5. **Referencia**: totales de control y acceso a los dashboards originales, sin modificarlos.

La API queda documentada en http://127.0.0.1:8000/docs.

### 4. Acumular el año

Analice juntos los lotes de todos los meses (en la interfaz, marque varios lotes; en escritorio, pase
varias carpetas). Si un mismo mes se cargó dos veces, los registros repetidos se cuentan una sola vez.

## Hojas del informe Excel

`Resumen` (KPI, recomendaciones, supuestos) · `Detalle Mensual CAPITA` · `CAPITA Grupos` ·
`Escenarios` · `PGP Mensual` · `Corredor EVENTO` · `TOP 20 EVENTO` · `Simulación E→C` · `Riesgo` ·
`Frecuencias Municipio` · `Frecuencias Mes` · `Servicios` · `Pareto Valor` · `Pareto Frecuencia` ·
`Top Costo CAPITA` · `Costo Paciente` · `NT vs RIPS` · `EVENTO vs Tarifario` · `RIPS vs Facturación` ·
`Conciliación Facturas` · `Hallazgos` · `Validación` · `Trazabilidad` · `Sin costo NT` ·
`Comparación Referencia` · `Supuestos`.

## Estructura del proyecto

```
rips_auditoria/          lógica de cálculo (sin interfaz)
  ingesta/               archivos, ZIP/RAR, TXT 3374, JSON 2275, consolidados, contexto, deduplicación, validación
  servicios.py           tabla única de servicios (paciente seudonimizado)
  clasificacion.py       CAPITA/EVENTO, grupo y subgrupo con trazabilidad
  calculos/              capita.py (costo NT×RIPS, KPI, escenarios, PGP), evento.py, cruces.py, hallazgos.py
  referencias/           lectura de los dashboards HTML y catálogo semilla
  informes/              excel.py, pdf.py, recomendaciones.py
  maestros.py            NT, catálogo, tarifario, facturación (almacenamiento local)
  analisis.py            orquestador y guardado/restauración JSON
  cli.py                 uso de escritorio
api/                     FastAPI + interfaz web (templates/, static/)
config/                  red de prestadores, parámetros del contrato, diccionario RIPS, plantillas
referencias/             dashboards HTML originales (solo lectura)
scripts/                 scripts de escritorio (.py y .bat)
tests/                   pruebas unitarias (datos ficticios) + pruebas opcionales con datos reales
docs/                    estructura de entrada, reglas de clasificación, supuestos y pendientes
```

## Pruebas

```bash
python -m pytest -q
```

Pruebas opcionales con los archivos reales (no se versionan por contener datos de pacientes):

```bash
RIPS_MUESTRA_ZIP=ruta/Centro_de_Salud_Jesus_Coronel.zip RIPS_CONSOLIDADO_JSON=ruta/RIPS_consolidado.json RIPS_CONSOLIDADO_XLSX=ruta/RIPS_consolidado.xlsx python -m pytest -q
```

## Documentación

* [docs/ESTRUCTURA_ENTRADA.md](docs/ESTRUCTURA_ENTRADA.md): formatos aceptados y estructura de carpetas.
* [docs/REGLAS_CLASIFICACION.md](docs/REGLAS_CLASIFICACION.md): CAPITA/EVENTO, grupos, cálculos y cruces.
* [docs/SUPUESTOS_Y_PENDIENTES.md](docs/SUPUESTOS_Y_PENDIENTES.md): supuestos explícitos, inconsistencias
  encontradas en los artefactos y datos pendientes de confirmar.

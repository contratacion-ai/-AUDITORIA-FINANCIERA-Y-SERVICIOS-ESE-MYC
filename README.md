# Auditoría RIPS · CÁPITA · EVENTO — E.S.E. Moreno y Clavijo

Herramienta para cargar, depurar y analizar los RIPS de la E.S.E. Departamental de Primer Nivel
Moreno y Clavijo (NIT 900034131) con Nueva EPS (EPS037/EPSS37), en **Tame, Arauquita, Fortul,
Cravo Norte y Puerto Rondón**. Se concentra en dos modalidades: **CÁPITA** y **EVENTO**.

* **Carga** carpetas RIPS por hospital tal como están en Google Drive (TXT Res. 3374, JSON Res. 2275,
  consolidados JSON/XLSX, ZIP, RAR) y **actualiza mes a mes una base consolidada**: volver a cargar un
  hospital-mes reemplaza la versión anterior, nunca se suma dos veces.
* **Sincroniza** las carpetas de Google Drive para escritorio: solo reprocesa los meses que cambiaron.
* **Depura y limpia**: valida cada archivo (Anexo Técnico, NIT, EPS, periodo, campos obligatorios),
  elimina registros repetidos entre formatos, carpetas y cargas, y permite borrar de la base un
  hospital-mes o vaciarla.
* **Frecuencias Nueva EPS vs RIPS**: compara la frecuencia mensual prevista en la Nota Técnica por
  municipio con lo registrado en los RIPS de cápita (por municipio-mes, por grupo y por servicio).
* **CÁPITA**: costo = costo unitario de la Nota Técnica × frecuencia RIPS, por grupo y mes; pacientes,
  asistencias, costo por usuario y por atención.
* **EVENTO**: facturación mensual, Top 20, Pareto, traslados y cruce con el tarifario pactado.
* **Hallazgos** de auditoría e informes en Excel, PDF y JSON. Pacientes seudonimizados (`PAC-…`).

## Instalación en Windows (sin permisos de administrador)

Ejecute `Instalador_AuditoriaRIPS_<versión>.exe`. Se instala **solo para el usuario actual**, en
`%LOCALAPPDATA%\Programs\AuditoriaRIPS`, sin pedir permisos de administrador y sin necesitar Python
ni internet (incluye Python 3.12 portátil y todas las librerías). Crea en el menú Inicio:

* **Auditoría RIPS**: abre la aplicación en el navegador (servidor local `127.0.0.1:8765`).
* **Sincronizar carpetas de Drive**: actualiza la base con lo nuevo de Drive sin abrir el navegador.
* **Cerrar Auditoría RIPS**, **Carpeta de datos e informes** y **Desinstalar**.

Los datos (base consolidada, maestros, informes y configuración editable) quedan en
`%LOCALAPPDATA%\AuditoriaRIPS` y se conservan al actualizar o desinstalar.

> Windows SmartScreen puede advertir que el instalador no está firmado: elija *Más información →
> Ejecutar de todas formas*. No requiere permisos de administrador.

Para construir el instalador (desde Linux/macOS con NSIS): `bash instalador/construir_instalador.sh`.

## Uso mes a mes

1. **Una sola vez: maestros.** Pestaña *Maestros* → cargue el libro
   `INFORME DE SERVICIOS 2026 BASE DE DATOS ANALISIS.xlsx` (catálogo de marcación, costos NT COSTOS y
   frecuencias mensuales de la Nota Técnica por municipio). Opcionales: tarifario de evento y
   facturación por ámbito (plantillas en `config/plantillas/`).
2. **Una sola vez: carpetas de Drive.** Pestaña *Base de datos y Drive* → agregue la ruta local de la
   carpeta de cada hospital en Google Drive para escritorio, por ejemplo
   `G:\Mi unidad\RIPS\Hospital San José de Cravo Norte`.
3. **Cada vez que los hospitales actualicen Drive:** botón **Sincronizar cambios** (o el acceso
   *Sincronizar carpetas de Drive*). Solo se reprocesan las carpetas de mes nuevas o modificadas; cada
   hospital-mes sincronizado reemplaza al anterior en la base. También puede cargar a mano una carpeta,
   ZIP o RAR en la pestaña *Cargar RIPS*.
4. **Revisar y limpiar.** En *Base de datos y Drive* se ve cada hospital-mes cargado (facturas,
   servicios, valor, errores, fecha y origen). *Eliminar* quita un hospital-mes y *Vaciar toda la base*
   empieza de cero. En *Cargar RIPS* puede corregir hospital, municipio, mes o EPS de una remisión; la
   base se actualiza con la corrección.
5. **Analizar.** Pestaña *Análisis CÁPITA · EVENTO*: elija municipios, hospitales y meses (o nada para
   toda la base) y descargue el informe Excel o PDF.

Estructura de carpetas esperada (la misma de Drive):

```
Hospital San Jose de Cravo Norte/
  08. Agosto/
    NUEVA EPS CAPITA SUBSIDIADO/   CT…txt AF…txt US…txt AC… AP… AM… AT…
    NUEVA EPS EVENTO SUBSIDIADO/   …
    SANITAS EVENTO CONTRIBUTIVO/   …   (otras EPS: se cargan y se excluyen del análisis Nueva EPS)
```

El municipio sale del código de habilitación del prestador (`81794…` = Tame, `81065…` = Arauquita) y el
mes de la carpeta `NN. Mes`.

### Escritorio / línea de comandos

```bash
python -m rips_auditoria.cli fuentes agregar "G:/Mi unidad/RIPS/Hospital San Jose de Cravo Norte"
```

```bash
python -m rips_auditoria.cli sincronizar --informe --pdf
```

```bash
python -m rips_auditoria.cli procesar "D:/RIPS/Hospital X" --pdf
```

```bash
python -m rips_auditoria.cli base listar
```

```bash
python -m rips_auditoria.cli base eliminar --hospital "Hospital X" --periodo 2026-08
```

```bash
python -m rips_auditoria.cli analizar --municipio TAME --pdf
```

## Informe Excel

`Resumen` · `Detalle Mensual CAPITA` · `CAPITA Grupos` · `Frecuencias NT Municipio` ·
`Frecuencias NT Grupo` · `Frecuencias NT Código` · `Frecuencias Fuera NT` · `EVENTO Mensual` ·
`TOP 20 EVENTO` · `Riesgo` · `Frecuencias Municipio` · `Frecuencias Mes` · `Servicios` · `Pareto Valor` ·
`Pareto Frecuencia` · `Top Costo CAPITA` · `Costo Paciente` · `NT vs RIPS` · `EVENTO vs Tarifario` ·
`RIPS vs Facturación` · `Conciliación Facturas` · `Hallazgos` · `Validación` · `Trazabilidad` ·
`Sin costo NT` · `Comparación Referencia` · `Conciliación Informe` · `Supuestos`.

## Totales de control (oficiales)

Fuente: libro INFORME DE SERVICIOS 2026 vigente, con la cifra actual de medicamentos. Serie mensual en
`config/control_oficial.yaml`; las pruebas la reproducen con el motor de cálculo.

| Indicador (ene–ago 2026) | Control oficial | Dashboard FINAL (histórico) |
|---|---|---|
| Costo CÁPITA | $8.264.369.795 | $8.053.016.408 |
| Medicamentos (grupo) | $236.095.610 | $24.742.224 |
| CUPS / CUMS | 748.058 / 554.587 | 748.058 / 57.238 |
| Pacientes únicos (suma mensual) / asistencias | 114.923 / 213.814 | igual |
| Promedio mensual CÁPITA | $1.033.046.224 | $1.006.627.051 |
| Costo/usuario · costo/atención | $71.803 · $8.626 | $69.781 · $11.130 |
| Facturación EVENTO | $7.462.498.070 | igual |
| Nota Técnica mensual por municipio | Tame 94.139,59 · Arauquita 107.359,72 · Fortul 55.767,15 · Cravo Norte 2.111,63 · Puerto Rondón 3.388,34 | — |

## Desarrollo

```bash
pip install -r requirements.txt
```

```bash
uvicorn api.app:app --host 127.0.0.1 --port 8000
```

```
rips_auditoria/          lógica de cálculo (sin interfaz)
  ingesta/               archivos, ZIP/RAR, TXT 3374, JSON 2275, consolidados, contexto, deduplicación, validación
  base.py                base consolidada por (hospital, mes): actualizar, listar, eliminar, vaciar
  fuentes.py             carpetas de Google Drive vigiladas y sincronización por huella
  servicios.py           tabla única de servicios (paciente seudonimizado)
  clasificacion.py       CÁPITA/EVENTO, grupo y subgrupo con trazabilidad
  calculos/              capita.py, frecuencias.py (NT vs RIPS), evento.py, cruces.py, hallazgos.py
  referencias/           dashboards HTML, libro INFORME DE SERVICIOS, control oficial
  informes/              excel.py, pdf.py, recomendaciones.py
  maestros.py · analisis.py · cli.py
api/                     FastAPI + interfaz web
instalador/              lanzadores Windows, script NSIS y construcción del instalador
config/                  red de prestadores, parámetros, diccionario RIPS, control oficial, plantillas
referencias/             dashboards HTML originales (solo lectura)
tests/                   pruebas (datos ficticios) + pruebas opcionales con datos reales
docs/                    estructura de entrada, reglas de clasificación, supuestos y pendientes
```

Pruebas:

```bash
python -m pytest -q
```

Con los archivos reales (no se versionan por contener datos de pacientes):

```bash
RIPS_INFORME_XLSX=ruta/INFORME.xlsx RIPS_MUESTRA_ZIP=ruta/muestra.zip RIPS_CONSOLIDADO_JSON=ruta/RIPS_consolidado.json RIPS_CONSOLIDADO_XLSX=ruta/RIPS_consolidado.xlsx python -m pytest -q
```

## Documentación

* [docs/ESTRUCTURA_ENTRADA.md](docs/ESTRUCTURA_ENTRADA.md): formatos, carpetas, validación, depuración, base y Drive.
* [docs/REGLAS_CLASIFICACION.md](docs/REGLAS_CLASIFICACION.md): CÁPITA/EVENTO, grupos, cálculos, frecuencias y cruces.
* [docs/SUPUESTOS_Y_PENDIENTES.md](docs/SUPUESTOS_Y_PENDIENTES.md): supuestos, conciliación del libro y pendientes.

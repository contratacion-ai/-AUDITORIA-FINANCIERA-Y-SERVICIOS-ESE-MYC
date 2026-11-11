# Control de servicios públicos, combustible y mantenimientos · ESE Moreno y Clavijo

Herramienta interna para cargar mes a mes la información de **servicios públicos, combustible y
mantenimientos**, validarla, conservar su historial por **sede y hospital**, y controlarla con un tablero,
alertas y un informe en Excel.

> **Repositorio público.** Aquí solo hay código, plantillas y un ejemplo con datos ficticios. La base de
> datos, los archivos cargados y el catálogo con cuentas y placas viven en la carpeta `datos/` del servidor
> de la ESE, que está excluida de git (`.gitignore`). No suba archivos reales a este repositorio.

## Qué hace

| Requisito | Dónde está |
|---|---|
| Carga mensual tolerante a formatos (Excel, CSV, PDF) | menú **Cargar datos** · `ese_control/extraccion/` |
| Reporte de lo que no se pudo interpretar (archivo, hoja, fila, motivo) | pantalla de revisión antes de guardar; descargable en Excel |
| Reemplazar o eliminar cargues con trazabilidad | menú **Historial de cargues** · `ese_control/cargues.py` |
| Catálogo configurable de códigos → sede y hospital | menú **Catálogo** (se actualiza subiendo un Excel) · `ese_control/catalogo.py` |
| Plantillas de recolección, una por ítem | menú **Plantillas** · `entregables/plantillas/` |
| Informe Excel con hojas por ítem, por hospital y por sede | menú **Informe en Excel** · `entregables/Informe_ejemplo.xlsx` |
| Tablero interactivo y gráfico de control | menú **Tablero de control** |
| Alertas, recomendaciones y análisis interpretativo | menú **Alertas y análisis** · `ese_control/analisis.py` |
| Permisos por sede y visión consolidada central | usuarios con rol *sede* o *central* · `ese_control/auth.py` |

Alcance deliberadamente acotado: no incluye facturación, nómina ni otros procesos.

## Instalación (una sola vez, en el equipo que hará de servidor)

Requiere Python 3.10 o superior.

```bash
git clone https://github.com/contratacion-ai/-AUDITORIA-FINANCIERA-Y-SERVICIOS-ESE-MYC.git
```

```bash
cd -- -AUDITORIA-FINANCIERA-Y-SERVICIOS-ESE-MYC
```

En Windows basta con hacer doble clic en `iniciar.bat`; en Linux:

```bash
./iniciar.sh
```

El script crea el entorno, instala las dependencias y abre la aplicación. La primera vez la pantalla pide
crear el usuario del **área administrativa central**. Después:

1. **Catálogo → Actualizar catálogo**: suba el Excel con las sedes, los códigos (centros de costo, cuentas de
   energía y acueducto) y los vehículos. El catálogo inicial del repositorio trae solo los nombres de las sedes.
2. **Administración → Usuarios**: cree un usuario de rol *sede* por cada hospital o centro.
3. **Cargar datos**: suba los archivos del mes.

### Acceso desde los demás equipos de la red

La aplicación escucha en el puerto `8501` de todas las interfaces (`.streamlit/config.toml`). Desde cualquier
equipo de la red interna se entra con el navegador a `http://NOMBRE-O-IP-DEL-SERVIDOR:8501`. Pida a sistemas
abrir ese puerto **solo hacia la red interna**; la herramienta no debe publicarse en internet (viaja por HTTP
sin cifrar, lo cual es aceptable únicamente dentro de la red de la ESE).

### Copias de seguridad

Todo lo que hay que respaldar está en la carpeta `datos/`: la base `control_ese.db` y la copia de cada archivo
cargado (`archivos_cargados/`). Para ubicarla en otra unidad, defina la variable de entorno
`ESE_CONTROL_DATOS` antes de iniciar.

### Conocer la herramienta con datos ficticios

```bash
python scripts/generar_demo.py
```

```bash
ESE_CONTROL_DATOS=datos_demo streamlit run app.py
```

### Uso por consola (opcional)

```bash
python -m ese_control cargar "energia_2026.xlsx" --item servicios_publicos --periodo 2026-10
```

```bash
python -m ese_control informe informe_octubre.xlsx --desde 2026-01 --hasta 2026-10
```

Otras órdenes: `iniciar`, `usuario`, `plantillas`, `catalogo archivo.xlsx`.

## Estructura de datos común

Definida en un solo lugar, [`ese_control/esquema.py`](ese_control/esquema.py). De ahí salen las plantillas, el
reconocimiento de encabezados y la validación. Todo archivo, cualquiera sea su formato, termina en la misma
tabla `registros`:

| Campo | Contenido |
|---|---|
| `item` | servicios_publicos · combustible · mantenimiento |
| `categoria` | Energía eléctrica · Acueducto, alcantarillado y aseo · Parque automotor · Plantas eléctricas y equipos · tipo de activo |
| `concepto` | servicio, tipo de combustible o tipo de mantenimiento (preventivo/correctivo) |
| `periodo` | `AAAA-MM` al que **corresponde** el dato (mes facturado o fecha del suministro), no la fecha de recepción |
| `fecha` | fecha del hecho, cuando existe |
| `sede_id` | sede del catálogo (de ella se obtiene el hospital) |
| `referencia` | número de cuenta, placa o equipo |
| `cantidad`, `unidad` | kWh, m3 o galones |
| `valor` | valor del periodo en pesos |
| `total_a_pagar` | total de la factura incluidos saldos anteriores (servicios públicos) |
| `proveedor`, `documento`, `descripcion`, `odometro`, `observacion` | complementarios |
| `cargue_id`, `hoja`, `fila` | de qué archivo, hoja y fila salió el registro |
| `estado` | vigente · reemplazado · eliminado (nada se borra) |

Campos obligatorios por plantilla:

- **Servicios públicos:** periodo, código de sede o n.º de cuenta, servicio, valor facturado del periodo.
- **Combustible:** fecha, destino (parque automotor / plantas y equipos), placa o equipo, tipo de combustible,
  galones, valor total. En vehículos la sede se obtiene de la placa.
- **Mantenimientos:** fecha, código de sede, tipo de activo, tipo de mantenimiento, descripción, valor.

### Formatos que la herramienta ya reconoce

| Fuente | Cómo viene | Cómo se interpreta |
|---|---|---|
| Cuadro anual de energía | una fila por cuenta, bloque de 3 columnas por mes | una fila por cuenta y mes (kWh, facturado, total); ignora totales y notas |
| Cuadro anual de acueducto | una fila por cuenta, 7 columnas por mes, identidad a mitad de la hoja | una fila por concepto (acueducto, alcantarillado, aseo, alumbrado) y mes |
| Formulario de tanqueos | una fila por tanqueo, con placa | sede por placa; corrige años `0026`, litros → galones, cantidades imposibles |
| Control de plantas eléctricas | una fila por suministro, con nombre de sede | sede por nombre o alias; tipo de combustible deducido del equipo |
| Cuadro de desplazamientos | una fila por viaje con kilometraje | kilómetros por vehículo, para calcular km/galón |
| Costos por ambulancia | una hoja por sede, una fila por placa y mes | mantenimiento por vehículo y mes; ignora hojas y tablas resumen |
| Plantillas de la herramienta | hoja `Datos` | lectura directa |
| Factura en PDF | tabla o texto | si no hay tabla, extrae cuenta, periodo, consumo y valores, y **exige revisión en pantalla** |

Para aceptar un encabezado nuevo se añade su sinónimo en `esquema.py`; no hay posiciones de columnas fijas.
Los datos personales de las fuentes (cédula y nombre del conductor) **no se guardan**.

### Reglas de cargue

- **Periodo:** se toma del contenido. Al cargar se elige «solo el periodo indicado» o «todos los periodos del
  archivo» (cuadros acumulados). Si el corte no coincide con el mes calendario, se indican las fechas de corte
  y todo lo que cae en ese rango se asigna al periodo.
- **Reemplazo:** un cargue nuevo sustituye los registros vigentes con la misma clave
  *(ítem, categoría, periodo, sede)*. Por eso una sede renueva solo lo suyo y el archivo de energía no pisa
  el de acueducto. La pantalla muestra qué se va a reemplazar antes de confirmar.
- **Trazabilidad:** de cada cargue quedan el nombre del archivo, su huella SHA-256, una copia del original,
  quién y cuándo lo cargó, los periodos, las filas rechazadas con su motivo y qué cargues reemplazó.
- **Errores y advertencias:** un *error* impide cargar la fila (sede no reconocida, sin valor, sin periodo);
  una *advertencia* la carga pero la deja señalada (fecha corregida, cantidad no plausible, posible duplicado).

## Alertas

Se evalúa el último mes cerrado. Umbrales ajustables en **Administración → Parámetros**.

| Alerta | Regla por defecto |
|---|---|
| Variación frente al mes anterior | ±30 % y al menos $300.000 de diferencia |
| Valor atípico frente al historial | z robusta (mediana/MAD) ≥ 3,5 con 4 o más meses previos |
| Sin reporte en el periodo | la sede o la categoría tenía datos el mes anterior y ahora no |
| Saldo en mora | total a pagar ≥ 1,5 veces lo facturado en el mes |
| Costo unitario atípico | $/kWh o $/m3 a más de 40 % de la mediana entre sedes |
| Cobro sin consumo | energía facturada con 0 kWh |
| Rendimiento bajo | km/galón por debajo del 70 % del esperado del vehículo |
| Combustible sin recorridos / recorridos sin combustible | cruce de tanqueos con desplazamientos |
| Mantenimiento acumulado alto | vehículo con más de 2 veces la mediana de la flota en el año |
| Periodo con cobertura parcial | menos de 25 días con registros: no se compara |

Cada alerta trae su recomendación, y la pestaña **Análisis del periodo** redacta la lectura gerencial
(panorama, impulsores del cambio, saldos, rendimiento de flota, concentración del mantenimiento, prioridades).
Todo es reglas explícitas calculadas localmente: no se envía información a ningún servicio externo.

## Por qué estas tecnologías

- **Python + Pandas:** los archivos fuente son hojas de cálculo desordenadas; Pandas resuelve la limpieza y
  los agregados con poco código, y es la herramienta que un analista de la ESE puede mantener.
- **Streamlit:** una aplicación web interna sin escribir HTML ni JavaScript; se instala con `pip`, corre en un
  PC de oficina y se usa desde el navegador de cualquier equipo de la red. Se prefirió sobre Dash porque el
  flujo principal es de formularios y carga de archivos, no de gráficos enlazados, y el código queda más corto.
- **openpyxl:** genera las plantillas con listas desplegables y el informe con formatos, fórmulas de total y
  gráficos nativos de Excel, que es el formato de trabajo del personal.
- **SQLite:** un solo archivo, sin servidor ni administrador de base de datos; suficiente para decenas de
  usuarios y miles de registros al mes, y la copia de seguridad es copiar una carpeta. Si el uso creciera, el
  acceso a datos está concentrado en `db.py` para migrar a PostgreSQL.
- **Plotly** para gráficos interactivos y **pdfplumber** para leer facturas en PDF.

## Organización del código

```
app.py                      entrada de la aplicación web (ingreso y menú)
ese_control/
  esquema.py                estructura de datos común (plantillas, sinónimos, valores permitidos)
  extraccion/               lectura tolerante: lectura.py, tabular.py, ancho.py, pdf.py, comunes.py
  validacion.py             reglas de validación y normalización
  catalogo.py               sedes, hospitales, códigos y vehículos; resolución de sede
  cargues.py                preparar, confirmar, reemplazar, eliminar; trazabilidad
  analisis.py               indicadores, alertas, recomendaciones y análisis
  plantillas.py · informe.py   salidas en Excel
  auth.py · db.py · config.py  usuarios, base de datos y parámetros
  ui_*.py                   páginas de la interfaz
catalogo_inicial/           sedes y alias iniciales (sin cuentas ni placas)
entregables/                plantillas e informe de ejemplo (datos ficticios)
docs/GUIA_USO.md            guía para el personal administrativo
scripts/generar_demo.py     base de demostración y entregables
tests/                      pruebas automáticas con datos sintéticos
```

Pruebas:

```bash
pip install pytest reportlab
```

```bash
python -m pytest tests
```

## Límites conocidos

- Los PDF escaneados (imagen) no se leen: deben digitarse en la plantilla. Los PDF con texto se leen, pero
  siempre pasan por revisión manual porque cada empresa diseña su factura de forma distinta.
- El ingreso es por usuario y contraseña sobre HTTP: úsese solo dentro de la red interna.
- Las filas con sede no reconocida no se cargan; hay que completar el catálogo y volver a cargar el archivo.

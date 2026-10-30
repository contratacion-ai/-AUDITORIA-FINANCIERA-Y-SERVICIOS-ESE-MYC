# Estructura de entrada

## 1. Formatos RIPS aceptados

| Formato | Cómo se reconoce | Lector |
|---|---|---|
| TXT Res. 3374/2000 | Nombre `TT` + remisión + `.txt` (`AF011210.txt`). `TT` ∈ CT, AF, US, AD, AC, AP, AU, AH, AN, AM, AT | `ingesta/txt3374.py` |
| JSON Res. 2275/2023 | Objeto con `numFactura` y `usuarios` (o lista de ellos; o envoltorio `{"rips": …}`) | `ingesta/json_xlsx.py::leer_json_2275` |
| JSON consolidado | Objeto con `indice` y `hospitales → meses → registros` (herramienta `rips_auditoria`, p. ej. `RIPS_consolidado.json`) | `leer_consolidado_json` |
| XLSX consolidado | Libro con hojas `AF`, `US`, `AC`… y encabezados del Anexo (p. ej. `RIPS_consolidado.xlsx`) | `leer_consolidado_xlsx` |
| ZIP | Se descomprime en memoria, también anidado (hasta 3 niveles), con protección contra rutas inseguras y bombas ZIP | `ingesta/archivos.py` |
| RAR | Se descomprime con `unrar`, `unar`, `bsdtar` o `7z` (debe estar instalado) | `ingesta/archivos.py` |
| Carpeta | Se recorre completa conservando la ruta relativa | `ingesta/archivos.py` |

Los PDF, imágenes y otros archivos se listan como *ignorados* (no son RIPS). Un XLSX que no es
consolidado RIPS se ignora con el mensaje "cárguelo como maestro".

**El formato del archivo (TXT o JSON) nunca define la sede ni el hospital.** Ambos formatos del
mismo envío quedan en el mismo hospital/municipio, deducido del contenido o de las carpetas.

## 2. Campos por archivo (Anexo Técnico No. 1)

El orden, longitud, tipo, obligatoriedad y catálogo de cada campo están en
`config/diccionario_rips_3374.csv` (extraído de la hoja *Diccionario* del consolidado de referencia)
y los catálogos en `config/catalogos_rips.csv`. Los valores que la Res. 3374 no prevé pero la
Res. 2275/2023 sí (por ejemplo tipos de documento PT, CN, SC o finalidades 11–44) se aceptan con
advertencia `VALOR_CATALOGO_EXTENDIDO`.

Equivalencias JSON 2275 → TXT 3374 usadas (resumen):

| JSON 2275 | TXT 3374 |
|---|---|
| `numDocumentoIdObligado`, `numFactura` | AF `num_id_prestador`, `num_factura` |
| `usuarios[].tipoDocumentoIdentificacion/numDocumentoIdentificacion` | US `tipo_id_usuario/num_id_usuario` |
| `consultas[]` (`codConsulta`, `vrServicio`, `valorPagoModerador`) | AC |
| `procedimientos[]` (`codProcedimiento`, `grupoServicios`) | AP (ámbito: 03/04 → 2 hospitalario, 05 → 3 urgencias, otro → 1) |
| `urgencias[]`, `hospitalizacion[]`, `recienNacidos[]` | AU, AH, AN |
| `medicamentos[]` (`codTecnologiaSalud`, `cantidadMedicamento`, `vrUnitMedicamento`) | AM |
| `otrosServicios[]` (`tipoOS`, `codTecnologiaSalud`, `cantidadOS`) | AT |

El JSON 2275 no trae el código de la EPS: se toma de la carpeta (por ejemplo "NUEVA EPS …" → EPS037)
o de la corrección manual.

## 3. Estructura de carpetas y deducción del contexto

```
<Hospital o sede>/<NN. Mes>/<EPS + régimen + modalidad>/<archivos RIPS>
```

| Dato | 1ª fuente | 2ª fuente | 3ª fuente |
|---|---|---|---|
| Hospital | corrección manual | carpeta con palabra HOSPITAL / CENTRO DE SALUD / PUESTO DE SALUD / ESE / IPS (o la anterior a la del mes) | `Prestador <código>` |
| Municipio | corrección manual | 5 primeros dígitos del código de habilitación = código DANE (`config/red_prestadores.yaml`) | nombre de sede o municipio en la ruta |
| Mes | corrección manual | carpeta `NN. Mes` (+ año en la ruta o año por defecto 2026) | mes más frecuente de `fecha_inicio` del AF |
| Modalidad declarada | carpeta de la remisión (CAPITA / EVENTO / PGP) | número de contrato del AF | — |
| EPS | AF `cod_entidad` | corrección manual | carpeta "NUEVA EPS" |

Cada registro conserva `fuente_hospital`, `fuente_municipio` y `fuente_periodo` (carpeta, contenido,
consolidado o manual).

## 4. Validaciones por archivo y remisión

Incidencias con severidad ERROR, ADVERTENCIA o INFO; nunca detienen la carga:

* Archivo: `ZIP_CORRUPTO`, `RAR_SIN_HERRAMIENTA`, `ARCHIVO_ILEGIBLE`, `ARCHIVO_NO_RECONOCIDO`,
  `NOMBRE_ARCHIVO_NO_NORMATIVO`, `ARCHIVO_DUPLICADO`, `ARCHIVO_CORREGIDO_USADO`, `CODIFICACION_NO_UTF8`,
  `TEXTO_REPARADO`, `ARCHIVO_VACIO`, `LINEA_VACIA`.
* Registro: `CANTIDAD_CAMPOS`, `SEPARADOR_FINAL`, `COMILLAS`, `CAMPO_OBLIGATORIO`, `LONGITUD_EXCEDIDA`,
  `FORMATO_FECHA`, `FORMATO_HORA`, `FORMATO_NUMERICO`, `FORMATO_CIE10`, `VALOR_NO_PERMITIDO`,
  `VALOR_CATALOGO_EXTENDIDO`, `REGISTRO_DUPLICADO`, `INCONSISTENCIA_VALOR`, `NOMBRE_O_CODIGO_SERVICIO`.
* Remisión: `ARCHIVO_OBLIGATORIO_FALTANTE` (CT, AF, US), `ENVIO_SIN_SERVICIOS`, `CT_ARCHIVO_NO_ENCONTRADO`,
  `CT_TOTAL_REGISTROS`, `CT_ARCHIVO_NO_DECLARADO`, `FACTURA_DUPLICADA`, `FACTURA_NO_EXISTE_EN_AF`,
  `USUARIO_NO_EXISTE_EN_US`, `USUARIO_DUPLICADO_US`, `FACTURA_SIN_SERVICIOS`, `DIFERENCIA_VALOR_FACTURA`,
  `FACTURA_FUERA_DE_PERIODO`, `NIT_PRESTADOR_DIFERENTE` (≠ 900034131), `EPS_FUERA_DE_ALCANCE`
  (sin EPS037/EPSS37), `PERIODO_NO_DETERMINADO`, `HOSPITAL_NO_DETERMINADO`, `MUNICIPIO_NO_DETERMINADO`.
* Carga: `REMISION_DUPLICADA` (la misma información en dos carpetas) y
  `FACTURA_DUPLICADA_ENTRE_REMISIONES`.

## 5. Separación y deduplicación

Cuando el mismo contenido llega en varios archivos (TXT + JSON, JSON + XLSX consolidado, una carpeta
"RIPS UNIFICADOS" que repite las carpetas por EPS, o la misma remisión guardada en dos meses):

1. Se calcula una llave de contenido por tipo (factura, paciente, código, fecha, cantidad, valor).
2. Se cuentan las repeticiones de cada llave **dentro de cada archivo físico**.
3. Se conserva el número de repeticiones del archivo que más tiene (máximo, no suma): dos líneas
   iguales en el mismo archivo se mantienen (y se marcan `REGISTRO_DUPLICADO`), pero la misma
   información en otro archivo o formato se elimina.
4. Desempate: TXT > JSON 2275 > JSON consolidado > XLSX consolidado; luego la remisión más
   específica; luego la ruta (mes) más antigua.

Los registros eliminados quedan en la hoja `Duplicados_Eliminados` de la validación, con el archivo
donde se conservaron. La misma regla se aplica entre lotes cuando se analizan varios juntos.

## 6. Maestros

Plantillas en `config/plantillas/` (CSV con `,` o `;`, o XLSX con los mismos encabezados):

| Maestro | Columnas | Estado |
|---|---|---|
| `catalogo` | codigo, homologo, descripcion, grupo, subgrupo, estado (OFERTADO/NO OFERTADO), tarifa, uvb | Hoja "MARCACION CUPS CAPITA" (CodigoCUP, Homologo, DescripcionCUP, Grupo, Subgrupo, Ruta, OFERTA 03/09/2026, MARCACION, UVB) |
| `nota_tecnica` | codigo, homologo (CUM para medicamentos), descripcion, grupo, tipo_codigo (CUPS/CUMS), municipio (o TODOS), mes, frecuencia_prevista (mensual), costo_unitario | Hojas "NT COSTOS" + "NOTA TECNICA CAPITA NEPS" |
| `tarifario` | codigo, descripcion, tarifa, vigencia_desde, vigencia_hasta, manual_referencia, observacion | **Pendiente de confirmación** |
| `facturacion` | periodo, hospital, municipio, ambito, modalidad, num_factura, codigo, descripcion, cantidad, valor | **Pendiente de confirmación** |
| `despacho_medicamentos` | periodo, unidades, valor | Opcional (regla de medicamentos del dashboard CAPITA·EVENTO) |

Los montos aceptan `1234567.89` o `1.234.567,89`.

## 7. Base consolidada y actualización mes a mes

* Cada carga (web, escritorio o sincronización de Drive) se valida, se depura y luego **actualiza la
  base** en `datos/base/` por partición **(hospital, mes)**.
* Modo *mes* (por defecto): el hospital-mes cargado reemplaza completo al anterior.
  Modo *carpeta*: reemplaza solo las carpetas EPS cargadas y conserva las demás del mismo mes.
* El análisis une las particiones y vuelve a deduplicar (por ejemplo, una remisión guardada en dos
  meses se cuenta una vez).
* Limpieza: eliminar un hospital-mes, un hospital, un municipio, un mes o vaciar la base (interfaz o
  `cli base eliminar|vaciar`). Los maestros no se borran.

## 8. Carpetas de Google Drive

* Se registran las rutas locales de Google Drive para escritorio (una por hospital o una carpeta que
  contenga varios hospitales).
* La unidad de actualización es la carpeta de mes (`NN. Mes`); su huella combina ruta, tamaño y fecha de
  modificación de cada archivo. **Sincronizar** solo reprocesa las unidades nuevas o con cambios.
* Las carpetas de mes que ya no están en Drive se informan como *ausentes* para que se decida si se
  limpian de la base.

## 9. Red de sedes

Cada carga se asigna a una sede de la red con este orden:

1. Corrección manual.
2. **Alias de la carpeta**: si la ruta contiene uno de los alias de la sede (sin importar tildes ni
   mayúsculas), se usa el nombre oficial de la sede (`fuente_hospital = carpeta (sede)`).
3. **Código de habilitación** del RIPS registrado en la sede (`contenido (sede)`), aunque la carpeta
   tenga otro nombre.
4. Nombre de la carpeta tal como viene, o `Prestador <código>` si no hay carpeta.

Las 29 sedes habilitadas en el REPS según el **Acta de negociación No. 4131** (Nueva EPS, marzo 2026) están
cargadas con su código (código de sede del prestador + número de sede = `cod_prestador` del RIPS):

| Municipio | Código de sede | Sedes |
|---|---|---|
| Arauquita | 8106500206 | 02 Hospital San Lorenzo · 17 C.S. Panamá de Arauca · 19 La Reinera · 20 El Oasis · 21 La Paz · 22 La Pesquera · 24 Aguachica · 25 El Paraíso · 40 Hospital San Ricardo Pampuri de La Esmeralda |
| Cravo Norte | 8122000206 | 04 Hospital San José de Cravo Norte |
| Fortul | 8130000206 | 06 Hospital San Francisco de Fortul · 26 El Salem · 27 Caranal · 30 Indígena Cusay-La Colorada · 44 Caracoles · 45 La 20 |
| Puerto Rondón | 8159100206 | 05 Hospital San Juan de Dios de Rondón |
| Tame | 8179400206 | 03 Hospital San Antonio de Tame · 07 C.S. Juan Jesús Coronel de Pueblo Nuevo · 31 Puerto Gaitán · 32 La Horqueta · 33 Betoyes · 34 Las Malvinas · 35 Puerto Miranda · 36 Puerto Nidia-Juan Carlos Chamat · 37 Mapoy · 38 Alto Cauca · 39 Filipinas · 41 Camame |

Se agregan o corrigen sedes, alias y códigos en *Maestros → Red de sedes* (o en
`config/red_prestadores.yaml`). Para renombrar lo ya cargado, use *Reprocesar todo* en la sincronización.

## 10. Modalidad Nueva EPS (regla de la E.S.E.)

* Para Nueva EPS (EPS037/EPSS37) es **CÁPITA todo lo que NO dice EVENTO** en la carpeta o en el número de
  contrato, tanto subsidiado como contributivo. Lo de evento siempre lo dice (p. ej. "NUEVA EPS SUB EVENTO").
* Excepciones por número de contrato en *Maestros → Contratos Nueva EPS y modalidad* (prioridad máxima).
  Registrado: 02-02-06-00227-2016 = CÁPITA (contrato cápita subsidiado, acta de terminación 0227).
* Alerta automática: si un contrato queda como CÁPITA pero menos del 30 % de sus servicios está en la Nota
  Técnica cápita (por ejemplo traslados, insumos o especialistas), el análisis lo advierte para revisarlo.
* Cápita pactada (actas 4131): afiliados × valor usuario por municipio (subsidiado $40.000 en Arauquita, Fortul y
  Tame; $45.000 en Cravo Norte y Puerto Rondón; contributivo $45.000). Se compara con el costo NT × RIPS en la
  hoja `CAPITA Pactada vs RIPS` y en la pestaña *Cruce Nueva EPS* del dashboard.

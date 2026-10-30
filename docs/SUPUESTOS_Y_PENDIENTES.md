# Supuestos, inconsistencias de los artefactos y pendientes

## Supuestos explícitos

1. **Municipio desde el código de habilitación.** Los 5 primeros dígitos de `cod_prestador` son el
   código DANE del municipio (81794 Tame, 81065 Arauquita, 81001 Arauca, 81300 Fortul, 81220 Cravo Norte, 81591 Puerto
   Rondón). Se verificó con las muestras: 817940020607 (Centro de Salud Jesús Coronel) y 812200020604
   (Hospital San José de Cravo Norte). Si una sede usa otro código, corríjalo en
   `config/red_prestadores.yaml` o a mano.
2. **Mes desde la carpeta `NN. Mes`**; si no existe, mes más frecuente de `fecha_inicio` del AF. Año por
   defecto 2026 cuando la ruta no lo indica (`--anio`).
3. **Nueva EPS = EPS037 y EPSS37.** Las demás EPS se cargan, se validan y se excluyen del análisis
   cápita/evento (opción `--todas-eps` o casilla en la interfaz para incluirlas).
4. **Pacientes únicos del periodo = suma de los únicos mensuales**, como en ambos dashboards (114.923).
   El informe también da los pacientes distintos del periodo completo.
5. **Asistencia = par distinto (paciente, fecha de atención) en el mes.** AM y AT en TXT no traen fecha:
   se asocian a la factura.
6. **Costo/atención = costo / frecuencia RIPS costeada.** Reproduce la columna del dashboard FINAL
   (costo mensual / RIPS CAPITA de la hoja PGP).
7. **Modalidad efectiva = la declarada por la remisión** (carpeta/contrato) cuando existe; la del
   catálogo se usa para auditar diferencias.
8. **Migrables valorados a tarifa EVENTO / meses** (dashboard CAPITA·EVENTO). El "costo estimado
   CAPITA" del dashboard FINAL no tiene fórmula derivable en el HTML.
9. **Ámbito JSON 2275 → 3374**: `grupoServicios` 03/04 → hospitalario, 05 → urgencias, resto → ambulatorio.
10. **Usuarios contratados = 70.003,77** (el dashboard rotula 70.004, pero el giro de $1.813.727.591 sale
    del promedio). Editable en `config/parametros_contrato.yaml`.

## Conciliación del libro INFORME DE SERVICIOS 2026 con los totales de control

Al cargar el libro (`python -m rips_auditoria.cli maestro informe ...`) se genera la conciliación
(hoja `Conciliación Informe` del Excel y pestaña *Referencia* de la interfaz). Resultado con el libro
entregado:

| Indicador | Libro | Dashboard FINAL / control | Estado |
|---|---|---|---|
| Costo CAPITA ene–ago (NT COSTOS) | $8.264.369.795 | $8.053.016.408 | Difiere $211.353.387 |
| Costo CAPITA sin medicamentos | $8.028.274.185 | $8.028.274.185 | Coincide |
| Grupo MEDICAMENTOS | $236.095.610 | $24.742.224 (implícito) | Explica toda la diferencia |
| Participación de los 9 grupos no farmacéuticos | — | GPCT del FINAL | Coinciden (±0,04 pp) |
| CUPS / pacientes únicos / asistencias | 748.058 / 114.923 / 213.814 | igual | Coinciden |
| CUMS | 554.587 | 57.238 | Versión distinta |
| NT mensual por municipio (5) | 94.139,59 / 107.359,72 / 55.767,15 / 2.111,63 / 3.388,34 | igual | Coinciden |
| RIPS vs facturación NT subsidiado | 910.925 / 629.182 | igual | Coinciden |
| RIPS vs facturación NT contributivo | 17.559 / 18.439 | igual | Coinciden |
| Facturación EVENTO (hoja FACTURACION EVENTO) | $7.462.498.070 | igual | Coincide |
| Facturación EVENTO (hoja ESCENARIO MODELO PGP) | $7.499.109.096 | $7.462.498.070 | Inconsistencia interna del libro (mar, jun, jul) |
| PGP-2 (ESCENARIO MODELO PGP) | $3.050.199.907 | $3.045.623.529 | Consecuencia de la fila anterior |
| Giro sin UPC / con UPC | $1.813.727.591 / $2.112.811.270 | igual | Coinciden |

**Conclusión:** el dashboard FINAL se construyó con una versión anterior de las frecuencias de
medicamentos. El motor de la aplicación, aplicado a las frecuencias RIPS y costos unitarios del libro,
reproduce exactamente los $8.264.369.794,92 de NT COSTOS; y, si el grupo MEDICAMENTOS toma los valores
del FINAL, reproduce mes a mes los $8.053.016.408 de control (prueba `tests/test_informe_real.py`).
**Pendiente: definir cuál cifra de medicamentos es la oficial para la negociación.**

Otros hallazgos del libro:

* El segundo municipio de la Nota Técnica es **ARAUQUITA** (código DANE 81065), no Arauca. Se agregó
  a `config/red_prestadores.yaml`; Arauca (81001) se conserva por si alguna sede reporta con ese código.
* 23 códigos de la NT no tienen costo unitario (`COSTO CUPS` vacío), por ejemplo 902216, 903822,
  911017; aparecen en la hoja `Sin costo NT` cuando llegan en RIPS de cápita.
* En la hoja MARCACION el estado OFERTADO/NO OFERTADO está en la columna "OFERTA 03/09/2026"; la
  columna "MARCACION" solo trae una X y "UVB" trae un valor en pesos. El lector detecta la columna del
  estado por su contenido (1.551 códigos: 370 OFERTADOS, 1.177 NO OFERTADOS, 4 sin estado).
* Medicamentos: en la NT el código interno es `MD…` y el CUM (lo que llega en el RIPS AM) es el
  "Homologo"; la clasificación y el costeo cruzan por el homólogo.

## Inconsistencias encontradas en los artefactos de referencia

| # | Artefacto | Hallazgo | Tratamiento |
|---|---|---|---|
| 1 | FINAL, constante `GMON` | La matriz grupo × mes suma $1.006.627.054 (el promedio mensual), no $8.053 M; la columna "Acumulado" por grupo queda a 1/8 del valor real | Se toman como control el total publicado, el detalle mensual `DM` y la participación `GPCT`; los grupos se recalculan desde RIPS |
| 2 | FINAL | Migrables: Esc.2 suma $105.447.145/mes; el texto dice $105.750.000/mes; la tabla SIM suma $105.856.087 | Esc.2 de control usa $105.447.145 (`migrables_mes_referencia`); el análisis con RIPS usa el valor calculado. **Confirmar la cifra oficial** |
| 3 | FINAL | Suma de los 8 costos mensuales = $8.053.016.409 vs total publicado $8.053.016.408 | Redondeo; las pruebas admiten ±$1 |
| 4 | FINAL vs CAPITA·EVENTO | El dashboard CAPITA·EVENTO calcula $8.548.188.101 (base NT $8.264.369.795 con medicamentos por despacho general) frente a $8.053.016.408 del FINAL (diferencia $495,2 M) | El control es el FINAL, como se pidió; la regla de despacho queda como opción. **Confirmar cuál es el valor oficial** |
| 5 | FINAL | Tres conteos distintos de RIPS CAPITA: 676.111 ("RIPS CAPS procesados"), 720.359 (hoja PGP) y 748.058 CUPS (detalle mensual) | Control = 748.058 CUPS; 720.359 se usa como frecuencia costeada para costo/atención |
| 6 | FINAL, tabla PGP | PGP-2 de marzo difiere en $1 del giro UPC + EVENTO | Redondeo por celda; pruebas con ±$1 |
| 7 | FINAL, Top 20 | Incluye insumos y medicamentos (D-0082, G-0105) junto a CUPS | Se replica: el Top 20 no filtra por tipo de código |

## Pendientes de confirmación (estructura configurable ya implementada)

* **Tarifario pactado de evento**: formato exacto no disponible. Plantilla `config/plantillas/tarifario.csv`.
* **Facturación por ámbito**: formato del reporte del sistema de facturación no disponible. Plantilla
  `config/plantillas/facturacion.csv`.
* **Cifra oficial de medicamentos CAPITA** ($236,1 M del libro actual o $24,7 M implícitos en el FINAL).
* "FRE MES SUB CAPITA" suma 678.146 RIPS, distinto de los 910.925 de "FRECUEN NT SUB CAPITA": no se usa
  como fuente de frecuencias.
* **Tope contractual de traslados** (`traslados.tope_mensual`): no aparece en los artefactos.
* **Sedes de Arauquita, Fortul y Puerto Rondón**: agregar sus nombres de carpeta en `config/red_prestadores.yaml`.
* `RIPS_consolidado.json` se describió como truncado, pero se leyó completo (9 meses, índice coincidente
  con el XLSX). Se usa como modelo y para pruebas opcionales.

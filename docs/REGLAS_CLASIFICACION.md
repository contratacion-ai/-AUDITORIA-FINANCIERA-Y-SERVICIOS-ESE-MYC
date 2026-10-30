# Reglas de clasificación y de cálculo

## 1. Modalidad CAPITA / EVENTO

Cada línea de servicio recibe dos modalidades.

**Modalidad contractual** (`modalidad_catalogo`), en este orden (`regla_modalidad`):

| # | Regla | Condición | Resultado |
|---|---|---|---|
| 1 | `CATALOGO_OFERTADO` | Código (o su homólogo) en MARCACION CUPS CAPITA con estado OFERTADO | CAPITA |
| 2 | `CATALOGO_NO_OFERTADO` | En el catálogo con estado NO OFERTADO | EVENTO |
| 3 | `NOTA_TECNICA` | No está en el catálogo pero sí en la Nota Técnica CAPITA | CAPITA |
| 4 | `MODALIDAD_DECLARADA` | Sin marca; la carpeta o el contrato de la remisión dicen CAPITA o EVENTO | la declarada |
| 5 | `POR_DEFECTO` | Sin ningún dato | EVENTO (SIN MARCA), como el dashboard |
| — | `EPISODIO` | AU, AH, AN (sin valor propio) | modalidad de la remisión |

Para medicamentos (AM) se busca también el CUM sin el sufijo de presentación (`20041822-10` → `20041822`).

**Modalidad efectiva** (`modalidad`, usada en costos y facturación): la declarada por la remisión
(así se facturó) cuando existe; si no, la contractual. `fuente_modalidad` = DECLARADA / CLASIFICADA.

Discrepancias que se reportan como hallazgos:

* `OFERTADO_CAPITA_FACTURADO_EVENTO`: código OFERTADO en cápita dentro de una remisión de EVENTO
  (riesgo de glosa por doble pago). Equivale a la "conciliación" del dashboard CAPITA·EVENTO.
* `NO_OFERTADO_EN_CAPITA`: código NO OFERTADO dentro de una remisión de CÁPITA.

## 2. Grupo y subgrupo

Nombres idénticos a los artefactos (`rips_auditoria/constantes.py`): PROMOCION Y MANTENIMIENTO DE
LA SALUD, URGENCIAS, MEDICINA GENERAL, LABORATORIO, ODONTOLOGIA, MEDICAMENTOS, APOYO TERAPEUTICO,
IMAGENOLOGIA, INTERNACION BASICA, PROCEDIMIENTOS, TRASLADOS, CONSULTA MEDICA ESPECIALIZADA,
ATENCION PARTO Y RN, MEDICAMENTOS E INSUMOS, OTROS, COMPLEMENTARIOS, ATENCION DOMICILIARIA, ALBERGUE.
"URGENCIAS CON OBSERVACION" se unifica en URGENCIAS, como en el dashboard.

Orden (`regla_grupo`):

1. `CATALOGO` / `NOTA_TECNICA`: grupo del maestro, pasado por la regla del dashboard
   (`classifyGroup`): toda descripción con "TRASLADO" → TRASLADOS.
2. `TIPO_ARCHIVO_RIPS`: AM → MEDICAMENTOS; AT tipo 1 → MEDICAMENTOS E INSUMOS, 2 → TRASLADOS,
   3 → INTERNACION BASICA, 4 → PROCEDIMIENTOS; AU → URGENCIAS; AH → INTERNACION BASICA; AN → ATENCION PARTO Y RN.
3. `REGLA_DASHBOARD`: réplica de `classifyGroup` (códigos G-/D-/MD → MEDICAMENTOS E INSUMOS;
   INTERNACI → INTERNACION BASICA; DERECHOS DE SALA → PROCEDIMIENTOS; ANTIGENO/SARS/DENGUE/HEPATITIS/
   VIH/SEROLOG/HEMOGRAMA → LABORATORIO).
4. `HEURISTICA` (supuesto, revisar): consultas 890203/890303/890703 → ODONTOLOGIA; 8907xx → URGENCIAS;
   AC 8902/8903/8904 → CONSULTA MEDICA ESPECIALIZADA; AC con finalidad 01–09 → PyMS; otras AC → MEDICINA
   GENERAL; CUPS 90 → LABORATORIO; 87/88 → IMAGENOLOGIA; 23/24/9970/9971 → ODONTOLOGIA; 990/993 o
   finalidad 3–4 → PyMS; 93 → APOYO TERAPEUTICO; 73 → ATENCION PARTO Y RN; resto → PROCEDIMIENTOS.

El subgrupo sale del catálogo; si no, de la heurística. La hoja `Trazabilidad` lista, por código,
modalidad, fuente, regla, grupo y regla de grupo.

## 3. Costo CAPITA (réplica del dashboard)

```
costo CAPITA (mes, grupo) = Σ costo_unitario_NT(código, municipio) × frecuencia RIPS
```

* Costo unitario: (código, municipio) → (código, TODOS) → código sin sufijo. Al cargar NT COSTOS:
  costo unitario = costo acumulado / frecuencia RIPS acumulada de la hoja.
* Solo servicios con modalidad efectiva CAPITA; los que no tienen costo NT van a la hoja `Sin costo NT`.
* Opcional (`reemplazar_medicamentos_por_despacho` en `config/parametros_contrato.yaml`): el grupo
  MEDICAMENTOS se reemplaza por el despacho general (`adj = costNT − medC + despacho`), regla del
  dashboard CAPITA·EVENTO.

Indicadores (mismas fórmulas del dashboard FINAL y del libro):

| Indicador | Fórmula |
|---|---|
| Promedio/mes (Esc.1) | total / n meses |
| Cuatrimestres | primeros n−4 meses vs últimos 4 (w = min(4, n)) |
| Crecimiento | Σ reciente / Σ inicial − 1 |
| Costo/usuario | promedio de (costo mes / pacientes únicos del mes) |
| Costo/atención | promedio de (costo mes / frecuencia RIPS costeada del mes) |
| Pacientes únicos | suma de pacientes distintos de cada mes |
| Asistencias | pares distintos (paciente, fecha de atención) por mes |

## 4. Frecuencias Nota Técnica Nueva EPS vs RIPS

* **Previsto**: frecuencia MENSUAL de la hoja "NOTA TECNICA CAPITA NEPS" por código y municipio
  (Tame, Arauquita, Fortul, Cravo Norte, Puerto Rondón).
* **Observado**: frecuencia RIPS de los servicios con modalidad efectiva CÁPITA de Nueva EPS, del mismo
  municipio y mes (los CUM de medicamentos se cruzan por el homólogo de la NT).
* Solo se comparan municipios con RIPS de cápita cargados (un municipio sin cargar no es subejecución).
* **% de ejecución** = RIPS / previsto × 100. Estados: DENTRO (±10 %), SOBREEJECUCION, SUBEJECUCION,
  SIN_NT (servicio en RIPS que no está en la Nota Técnica).
* **Valor del exceso** = (RIPS − previsto) × costo unitario NT, cuando RIPS supera lo previsto.
* Hojas: `Frecuencias NT Municipio` (municipio × mes), `Frecuencias NT Grupo` (acumulado por grupo),
  `Frecuencias NT Código` (acumulado por servicio, ordenado por desviación) y `Frecuencias Fuera NT`.

## 5. EVENTO: facturación, Top 20 y riesgo

* Facturación mensual EVENTO Nueva EPS desde los RIPS (facturas, servicios, pacientes y valor).
* Top 20 por frecuencia y Pareto (clase A = 80 % del valor, B = hasta 95 %, C = resto).
* Riesgo: traslados (S31307, 601T02, 601T01), parto, colposcopia, etc. (catálogo `RIE` del dashboard,
  ampliable en `config/criterios_evento.yaml`). Si se define `tope_mensual` de traslados en
  `config/parametros_contrato.yaml`, el exceso genera el hallazgo `TRASLADOS_SOBRE_TOPE`.

## 6. Cruces

| Cruce | Llave | Estados |
|---|---|---|
| RIPS vs Nota Técnica | código, municipio (si la NT lo trae), mes | DENTRO, SOBREPASO (> +10 %), SUBEJECUCION (< −10 %), SIN_NT |
| EVENTO vs tarifario | código, mes (tarifa vigente al 1.º del mes) | CONFORME, SOBRE_TARIFA, BAJO_TARIFA (±1 %), SIN_TARIFA |
| RIPS vs facturación por ámbito | periodo, municipio, ámbito, código (las que traiga la facturación) | CONCILIADO, DIFERENCIA, FACTURADO_SIN_RIPS (glosa potencial), RIPS_SIN_FACTURA |
| AF vs servicios | remisión, factura | CONCILIADA, DIFERENCIA, SIN_SERVICIOS |

Tolerancias en `config/parametros_contrato.yaml` (`cruces`).

## 7. Hallazgos de auditoría

Hoja `Hallazgos`, ordenada por severidad y valor en riesgo: `SOBREPASO_FRECUENCIA_NT`,
`NO_OFERTADO_EN_CAPITA`, `OFERTADO_CAPITA_FACTURADO_EVENTO`, `TARIFA_SOBRE_TARIFA`,
`TARIFA_BAJO_TARIFA`, `TARIFA_SIN_TARIFA`, `FACTURADO_SIN_RIPS`, `RIPS_SIN_FACTURA`, `DIFERENCIA`,
`FACTURA_NO_CONCILIA_RIPS`, `REGISTRO_DUPLICADO`, `REMISION_DUPLICADA`, `FACTURA_DUPLICADA*`,
`TRASLADOS_SOBRE_TOPE`. Cada uno trae la acción recomendada.

## 8. Anonimización

Los pacientes se identifican con `PAC-` + SHA-256(sal local | tipo doc | número)[:12]. La sal vive en
`datos/.sal_anonimizacion` (no se versiona) para que el mismo paciente tenga el mismo seudónimo entre
meses. Los informes no contienen nombres ni números de documento.

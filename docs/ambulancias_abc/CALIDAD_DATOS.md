# Calidad de los datos fuente y primeros resultados

La aplicación registra estos hallazgos al cargar los datos (pantalla *Calidad de datos*). Ninguno se corrige con cifras inventadas: se marca, se explica y se decide qué valor usar.

## Informe técnico (fuente principal)

| Ubicación | Hallazgo | Tratamiento |
|---|---|---|
| Anexo A | Reproducido: total/día por placa (±$1 por redondeo), promedio $866.077, flota $17.816.056/día (informe: $17.816.048), $534,5 M/mes, gasolina $962.261 y diésel $787.381/día. | Se usan los valores del Anexo A. |
| Tabla 3 | Rendimiento km/gal mínimo 8,4, máximo 24,3 y promedio 14,9 **no se reproducen** con el Anexo A (dan 7,4 / 50,4 en OKZ362 / 16,2): el combustible es de 27 días certificados y los km de 55 días. | Para asignar el combustible se usa el rendimiento implícito de cada placa (proporcional a km, no altera el reparto). Revisar con el autor. |
| Anexo A OKZ364 | $/hora 2.260.201 vs 2.472.560 recalculado (las horas están redondeadas a 0,2). | Unidad excluida de promedios, como indica el informe. |
| Figura 2 vs tabla 2 | Puerto Jordán: ×3,5 en la figura, 3,15 en la tabla. | Informativo. |
| Num. 2 | Ford Ranger KSM548-KSM552 sin historial. | Registradas como *nuevas sin historial*, sin costeo, en Tame/ACPM hasta confirmar. |

## ANEXOS DE TARIFAS 2026.xlsx, hoja *TRASLADOS Anexo 3*

| Ubicación | Hallazgo | Tratamiento |
|---|---|---|
| L10:O100 | 360 celdas `#REF!` (vigencias 2016-2019). | No se usan. Solo se usa la columna 2026 (P). |
| N8 | Encabezado desalineado: dice "VALOR DEL KILOMETRO $13.000" en la columna del año 2018, mientras P8 = 17.000. | Valor por km = P8 ($17.000). |
| Fila 7 | Sin columnas para 2014-2015 y 2020-2025. | La tarifa 2026 no se puede trazar contra la anterior. |
| A101 | La nota anuncia +30 km por el cierre del puente El Tigre hacia Saravena, pero las tarifas a Saravena siguen con los km originales. | **Severidad alta**: decidir si se ajusta la tarifa. |
| P98:P100 | Fórmula con referencia relativa (`=+C98*P8`). | Valores correctos; fórmula frágil. |
| U82 | Cálculo suelto `=261-288`. | Ignorado. |
| Fila 22, fila 46 | Valores históricos atípicos (2010 = 58.471; 532 $/km). | No afectan 2026. |

## COSTOS AMBULANCIAS.xlsx

| Ubicación | Hallazgo | Tratamiento |
|---|---|---|
| AMBULANCIAS!D9 | Duración del procedimiento vacía; "20 MIN" está como texto en F9. Todas las fórmulas de VALOR PROCEDIMIENTO dan 0. 20 minutos no representa un traslado (3,6 h/día en misión según el informe). | No se usa la duración. |
| AMBULANCIAS!F | Columna VR. PESOS HORA vacía. | No se usa. |
| AMBULANCIAS!G47 | "VR. ACTUAL" $87.450 (valor EPS) sin código CUPS. | Requiere validación; no se usa como tarifa. |
| AMBULANCIAS!I43, I45 | `#DIV/0!`. | No se usan. |
| AMBULANCIAS!E12:E14 | Personal del centro de costos $76,8 M/mes frente a $288,8 M de 21 tripulaciones; planta (E12) = 0. | Las partidas de personal **no** se suman al ABC (doble conteo). Las demás partidas ($21,7 M/mes) quedan como referencia y el administrador decide incluirlas. |
| TABLA TH!H20 | Tripulación/día por tabla TH (7 h × V/H, 191 h/mes) = $504.073 vs $458.467 del informe. | Se usa la del informe; la otra queda como parámetro de contraste. |
| TABLA TH!G17:G19 | 7 h/día por cargo, pero la ambulancia está disponible 24 h con una tripulación. | Si hay turnos, la tripulación estaría subestimada. |
| TABLA TH!C4:F12 | 36 celdas vinculadas a un libro externo no entregado. | Valores guardados, no recalculables. |
| Libro completo | No se encontraron celdas `[object Object]` en la versión recibida. | Informativo. |

## Facturación de traslados, septiembre 2026 (HIS)

617 líneas, $867.175.677. El archivo trae nombres, documentos, teléfonos y direcciones de pacientes: **no se versiona en el repositorio** y la aplicación no guarda esos campos.

| Hallazgo | Líneas |
|---|---|
| Tarifa básica (601T01) y cobro por km en el **mismo ingreso**: posible doble cobro | 102 (56 ingresos) |
| Posible línea duplicada (mismo ingreso, código, km, valor y fecha) | 2 |
| Sin número de factura (solo prefactura) | 54 |
| Fecha de servicio fuera de septiembre (hasta 09/10/2026) | 18 |
| Código con caracteres extra (`601T01.`, `601T01..`) | 6 |
| Valor facturado en cero | 1 |
| Km facturados sin ruta en el Anexo 3: Tame 71 km (54 líneas), Tame 103 km (3), Pto Rondón 147 km (2), Pto Jordán 90 km (1), Cravo Norte 50 km (1) | 61 |
| Sin placa de ambulancia ni destino (todo el archivo) | 617 |
| Columna AMBITO = "Urgencias" en todas las líneas; la apertura útil es UNIDAD FUNCIONAL | 617 |

## Primeros resultados (septiembre 2026, costos estándar del estudio)

| Indicador | Absorción real | Capacidad (TDABC) |
|---|---|---|
| Facturación (fecha de servicio en septiembre) | $842,5 M | $842,5 M |
| Costo total de la flota (21 ambulancias × 30 días) | $534,5 M | $534,5 M |
| Costo asignado a 549 traslados (95.200 km conducidos) | $534,5 M | $367,3 M |
| Capacidad no utilizada | $0 | $167,2 M |
| Margen sobre traslados | $308,1 M (36,6 %) | $475,2 M (56,4 %) |

- Por unidad funcional (capacidad): Urgencias 58 %, Unidad de traslado 56 %, Hospitalización 45 %, Sala de partos 62 %.
- Por recorrido (capacidad): Tame → Saravena es la ruta de mayor facturación ($130,5 M, 60 %); Cravo Norte → Arauca la de menor rentabilidad (18 %).
- Matriz estándar ruta × ambulancia (rutas que salen de la sede de cada unidad): 116 de 253 combinaciones pierden, y todas son de 8 unidades cuyo costo por km facturado (costo/km × 2) supera los $17.000 del manual: las cuatro de Cravo Norte (OEU845, OEU828, KSM545, OYX018), OKZ367, OKZ362, OYX016 y OKZ364. Las otras 13 son rentables en todas sus rutas; las de menor tarifa de equilibrio son OYX014 ($8.062/km), OKZ368 ($9.821) y OEU846 ($10.329). Asignar las rutas largas a estas unidades coincide con la recomendación del informe.

Estas cifras usan el factor ida y vuelta = 2 (supuesto) y costos estándar; cambian al registrar consumos reales y asignar placas.

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

## Modelo ABC corregido por la ESE (COSTOS AMBULANCIAS.xlsx, octubre 2026)

La versión corregida del libro reemplaza la anterior (hoja "AMBULANCIAS") por la hoja **MODELO COSTOS ABC PARAMETRO**. La aplicación la adopta como base del costeo:

| Corrección | Antes | Ahora | Celda |
|---|---|---|---|
| Tripulación por ambulancia/día | $458.467 (13.754.000 ÷ 30) | **$457.592,67**: médico 6,4 h, auxiliar 6,3 h y conductor 6,3 h × valor hora (salario ÷ 191 h) | TABLA DE VALOR TH MYC!G9 |
| Costos indirectos del centro de costos | Solo referencia | **Incluidos**: $21.717.958/mes ÷ 21 ambulancias ÷ 30 días = **$34.473 por ambulancia/día** (la primera versión del libro dividía entre 22: $32.906) | MODELO…!E43, F10, F43 |
| Gastos administrativos | No existían | **6,2 %** sobre el costo total | MODELO…!E44, G44 |
| Horas/día de OKZ364 | 0,2 | **0,333** | MODELO…!M27 |
| Anexo 3 de tarifas | `#REF!` en 2016-2019 y encabezados desalineados | Depurado: una columna 2026, mismas 90 rutas y tarifas | TRASLADOS Anexo 3 |
| Partidas de personal del centro de costos | $76,8 M/mes, no comparables con 21 tripulaciones | Retiradas: la tripulación se costea por la tabla TH | — |

Resultado para OEU846: (700.215 + 294.137 + 457.593 + 34.473) × 1,062 = **$1.578.576/día** (antes $1.452.819), igual a la celda G45 del modelo. Las pruebas automáticas verifican este valor.

Observaciones sobre el modelo (pantalla *Calidad de datos*):

| Ubicación | Observación | Tratamiento |
|---|---|---|
| MODELO…!F10 | La primera versión repartía los indirectos entre 22 ambulancias aunque el informe costea 21. **Corregido** en la versión del 20/10/2026 (TOTAL AMBULANCIAS = 21): todo el centro de costos queda en la flota. | Parámetro `ambulancias_reparto_indirectos` = 21. Si el libro vuelve a traer otro número, se registra un hallazgo. |
| MODELO…!F44 | `=F43*E44+F43` suma la base al recargo, mientras G44 calcula solo el 6,2 %. | Se usa el 6,2 % sobre el total (como G44/G45). |
| TABLA TH | Cada cargo se costea por su jornada (6,3-6,4 h/día), pero la ambulancia está disponible 24 h con una tripulación. | Si hay turnos, la tripulación está subestimada. |
| MODELO…!G46 | La fila UTILIDAD está vacía. | No se aplica utilidad; el margen se calcula contra la tarifa. |

Los hallazgos de la versión anterior del libro (`#DIV/0!`, duración "20 MIN" en texto, VR. ACTUAL sin CUPS, personal del centro de costos) quedaron **resueltos** automáticamente al cargar la versión corregida.

## Resultados de septiembre 2026 con el modelo corregido (costos estándar, sin consumos reales)

| Indicador | Absorción real | Capacidad (TDABC) | Antes de la corrección (absorción real) |
|---|---|---|---|
| Facturación (fecha de servicio en septiembre) | $842,5 M | $842,5 M | $842,5 M |
| Costo total de la flota (21 ambulancias × 30 días) | **$590,1 M** | $590,1 M | $534,5 M |
| — de ello, indirectos del centro de costos | $21,7 M | | — |
| — de ello, gastos administrativos (6,2 %) | $34,5 M | | — |
| Costo asignado a 549 traslados (95.200 km) | $590,1 M | $394,4 M | $534,5 M |
| Capacidad no utilizada | $0 | $195,7 M | $0 |
| Margen sobre traslados | **$252,4 M (30,0 %)** | $448,1 M (53,2 %) | $308,1 M (36,6 %) |

- Costo promedio por ambulancia/día: **$955.456** (informe: $866.077); flota $19.669.968/día. Menor OYX016 ($618.462), mayor OEU846 ($1.578.576).
- Rentabilidad real por sede: Tame 53,1 %, Arauquita 36,8 %, Pto Rondón 35,2 %, Fortul 34,9 %, Pto Jordán 27,7 %, Panamá 15,2 %, La Esmeralda 4,0 %, **Cravo Norte −121,6 %**.
- Matriz ruta × ambulancia: **134 de 253** combinaciones pierden (antes 116). A las 8 unidades de bajo recorrido se suma **OLM779** (Pto Rondón), que ahora pierde en sus 18 rutas. Las de menor costo por km siguen siendo OYX014, OKZ368 y OEU846.

Estas cifras usan el factor ida y vuelta = 2 (supuesto) y costos estándar; cambian al registrar consumos reales y asignar placas.

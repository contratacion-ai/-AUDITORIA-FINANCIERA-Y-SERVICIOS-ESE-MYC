# Parametrización en cuatro niveles

El motor no tiene datos de ninguna institución en el código: lee estos parámetros. Cambiar un parámetro y volver a
ejecutar el motor es suficiente; no se modifica la lógica.

| Nivel | Qué representa | Qué se configura | Quién lo cambia y cuándo |
|---|---|---|---|
| **N1 · Estructura normativa (general)** | Lo común al sector según la Res. DDC-000002/2014 | Elementos (CP, IH, CG, EC), recursos R1…R29, relación cuenta contable → recurso, clases que pueden recibir cada recurso, indicadores de referencia | Costos, al implantar; rara vez |
| **N2 · Estructura institucional** | Cómo está organizada la institución | Unidades de negocio (y su clase), sedes, centros de costos, homologación de los códigos contables/nómina con los centros | Costos, cuando abre/cierra servicios o sedes |
| **N3 · Criterios de distribución y cruces** | Cómo se reparten los costos y cómo se cruzan | Inductores, **reglas de distribución** 1ª–4ª (generales, por unidad de negocio o por centro), % mano de obra permanentes, CUPS por centro y peso, EPS, ámbitos, notas técnicas | Costos / consultor; cuando cambia la operación o los contratos |
| **N4 · Valores del periodo (particular)** | La realidad del mes | Cantidades de inductores del mes (personas, raciones, kilos, solicitudes), producción, % de mano de obra del mes, **reglas de excepción del periodo** | Financiero / costos cada mes |

## Precedencia de reglas (de lo particular a lo general)

Para cada movimiento sin centro (1ª distribución) o cada centro que distribuye (2ª a 4ª) se busca la regla activa aplicable:

1. **N4** – regla con `periodo` = el mes que se calcula.
2. **N3** – regla con `centro origen` = el centro.
3. **N2** – regla con `unidad de negocio` = la unidad del centro.
4. **N1** – regla general de la distribución (en la 1ª: la del recurso).

Gana la más específica; ante empate, la más reciente. La pantalla *Regla efectiva por recurso y centro* muestra el resultado y su nivel.

## Valores de inductores

* `Permanente` (sin periodo): p. ej. m² por centro. Un valor del periodo **reemplaza** al permanente para ese inductor.
* `Centro origen`: cantidades propias de un centro que distribuye (raciones de cocina por servicio, exámenes solicitados al
  laboratorio por cada servicio final). Si existen, se usan en lugar de las generales.
* Tipos de inductor calculados por el sistema: `PRODUCCION` (cantidades producidas por el centro destino, desde el archivo de
  producción o derivadas de los RIPS), `FACTURACION` (valor facturado por centro), `COSTO_DIRECTO` (ponderación por costeo
  directo = resultado de la 1ª distribución; solo desde la 2ª).

## Distribuciones (procesos de asignación)

| Distribución | Origen | Destino permitido | Inductor típico (Anexo 3) |
|---|---|---|---|
| 1ª | Recursos (movimientos) | Cualquier clase según regla y recurso | Directo, % Empleado/OPS, m², personas, kilos, raciones, equipos |
| 2ª | Centros de ESTRUCTURA | Servicios, intermedios, finales | Ponderación por costeo directo, personas, m², facturación, equipos |
| 3ª | Centros de SERVICIOS | Intermedios, finales | Producción (raciones, kilos, horas de mantenimiento, admisiones) |
| 4ª | Centros INTERMEDIOS | Finales | Servicios solicitados por cada centro final |
| CUPS | Centros FINALES | CUPS producidos | Cantidad × peso relativo |

El motor impide destinos de igual o menor nivel (no hay ciclos), reparte en centavos exactos y verifica
`entrada = finales + pendientes`. Lo que no puede asignarse queda en **Pendientes** con su motivo.

> Nota: el informe de referencia describe la 4ª distribución como el cálculo del costo por CUPS. Aquí se sigue la
> Res. 002/2014 (4ª = intermedios → finales) y el costo por CUPS es un paso posterior y explícito.

## Soportes de área y conciliación con el estado financiero

El estado financiero es la fuente oficial del costo. Si para un recurso existen soportes de área (nómina, inventarios,
depreciación, otros), éstos definen **cómo** se reparte el recurso y el total del estado financiero define **cuánto**:
la diferencia se prorratea sobre las asignaciones de área (campo *ajuste de conciliación*) y se informa en la corrida y en el informe.

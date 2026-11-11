# Guía de uso para el personal administrativo

Herramienta de control de **servicios públicos, combustible y mantenimientos** de la ESE Moreno y Clavijo.
Se usa desde el navegador, en la dirección que le indique sistemas (por ejemplo `http://servidor:8501`).

## 1. Quién puede hacer qué

| Rol | Ve | Carga, reemplaza y elimina |
|---|---|---|
| **Sede** (un usuario por hospital o centro) | solo su hospital y sus sedes | solo datos de su hospital |
| **Central** (área administrativa) | toda la ESE, consolidado | todo; además administra catálogo, usuarios y parámetros |

Cambie su contraseña en el panel izquierdo, opción **Cambiar mi contraseña**.

## 2. Rutina mensual

El cierre es el primer día hábil del mes siguiente. Por cada ítem hay un archivo.

1. **Reúna los archivos del mes**: cuadro de energía, cuadro de acueducto, exportación de tanqueos, control de
   plantas eléctricas, cuadro de desplazamientos y cuadro de mantenimientos. Si una sede no tiene un formato
   propio, entréguele la plantilla (menú **Plantillas**).
2. Entre a **Cargar datos** y, para cada archivo:
   - elija el **ítem**;
   - elija el **mes al que corresponde** la información (no el mes en que la recibió);
   - indique si se carga **solo ese mes** o **todos los periodos del archivo** (para cuadros acumulados del año);
   - suba el archivo y pulse **Leer y validar**.
3. **Revise** lo que muestra la pantalla antes de guardar (ver punto 3).
4. Pulse **Confirmar y guardar**.
5. Abra **Alertas y análisis** y revise las alertas de severidad alta.
6. Genere el **Informe en Excel** del periodo para la gerencia.

> Si el corte del mes no coincide con el mes calendario (por ejemplo, del 28 de septiembre al 3 de
> noviembre), abra «El corte de este periodo no coincide con el mes calendario» y escriba las dos fechas.

## 3. Cómo leer la pantalla de revisión

| Indicador | Qué significa |
|---|---|
| Filas leídas | las que tenía el archivo |
| Filas que se cargan | las que pasaron la validación |
| Filas rechazadas | tienen un **error**: no entran hasta corregirlo |
| Advertencias | entran, pero quedan señaladas para revisar |
| Valor a cargar | suma en pesos de lo que se va a guardar |

La tabla de incidencias dice, para cada problema, **hoja, fila, columna y motivo**. Puede descargarla en Excel
y enviarla a quien diligenció el archivo. Los casos más frecuentes:

| Mensaje | Qué hacer |
|---|---|
| *Sede no reconocida: no está en el catálogo…* | El código, la placa o el nombre no está en el catálogo. El área central lo agrega en **Catálogo** y se vuelve a cargar el archivo. |
| *Falta el valor o no es un número* | Corrija la celda en el archivo (por ejemplo, dice «Pendiente»). |
| *Fecha corregida: año digitado como 0026* | Se interpretó como 2026. Verifique y corrija en la fuente para que no se repita. |
| *Cantidad no plausible… se carga el valor y la cantidad queda vacía* | Los galones están mal digitados (por ejemplo, se escribió el kilometraje). Corrija y vuelva a cargar. |
| *Precio por galón fuera de rango* | Galones o valor no cuadran con el soporte. Revise la factura. |
| *El valor facturado no coincide con la suma de los conceptos* | En el cuadro de acueducto hay una celda corrida o mal sumada. |
| *Posible registro duplicado* | Hay dos filas iguales; confirme que no se digitó dos veces. |
| *N filas de periodos distintos no se cargan* | Es normal cuando el archivo trae varios meses y usted pidió cargar uno solo. |

Nada se guarda hasta que usted confirma. Si el archivo tiene muchos errores, es mejor corregirlo y volver a
subirlo.

## 4. Volver a cargar o corregir un mes

- **Reemplazar** (opción recomendada al confirmar): los datos nuevos sustituyen a los vigentes de las
  **mismas sedes y periodos**. La pantalla le avisa cuántos registros y de qué cargues se van a reemplazar.
  Los anteriores no se borran: quedan en el historial como «reemplazados».
- **Complementar**: agrega sin reemplazar. Úselo solo cuando el archivo trae registros adicionales del mismo
  mes (por ejemplo, tanqueos que faltaban).
- **Eliminar un cargue**: en **Historial de cargues**, elija el cargue, escriba el motivo y confirme. Puede
  pedir que se restauren los datos que ese cargue había reemplazado.

En **Historial de cargues** siempre puede consultar qué archivo entró, cuándo, quién lo cargó, a qué periodos
corresponde, qué reemplazó, sus incidencias y descargar el archivo original.

## 5. Plantillas

En **Plantillas** descargue la de cada ítem. Cada una trae:

- hoja **Datos**: se diligencia desde la fila 5, un renglón por registro; las columnas con `*` son obligatorias;
- hoja **Instrucciones**: qué va en cada columna y ejemplos;
- hoja **Catálogo**: los códigos de sede vigentes.

Reglas de oro: no cambie los encabezados, no deje filas en blanco en medio, escriba los valores sin puntos de
miles ni el signo `$`, y use fechas `DD/MM/AAAA`.

## 6. Tablero de control

- Arriba, los filtros: rango de periodos, hospital y sede.
- **Resumen**: gasto mensual por ítem, por hospital y mapa de calor por mes.
- **Servicios públicos**: valor, consumo (kWh, m3), costo unitario y saldos de periodos anteriores.
- **Combustible**: valor y galones por destino, vehículos con mayor gasto y rendimiento (km/galón).
- **Mantenimientos**: por mes, hospital y activo.
- **Gráfico de control**: elija una serie; los puntos fuera de los límites son meses anómalos.
- **Datos**: la tabla detallada, descargable.

El mes en curso se muestra pero no se evalúa, porque está incompleto: los indicadores y las alertas usan el
último mes cerrado.

## 7. Alertas y análisis

Cada alerta dice **qué se observa** y **qué hacer**. Atienda primero las de severidad ▲ alta. La pestaña
**Análisis del periodo** resume en texto el comportamiento del mes y las prioridades de gestión; puede
copiarlo al informe de gerencia. Los umbrales se ajustan en **Administración → Parámetros**.

## 8. Catálogo (área central)

El catálogo dice a qué sede y hospital pertenece cada **código interno, cuenta de servicio o placa**. Cuando
cambie un centro de costo, llegue un vehículo o se abra una sede:

1. **Catálogo → Descargar catálogo vigente**.
2. Edite el Excel: hoja `Sedes`, `Codigos` (tipos: `CENTRO_COSTO`, `CUENTA_ENERGIA`, `CUENTA_ACUEDUCTO`,
   `CUENTA_OTRO`, `ALIAS`) o `Vehiculos`.
3. Súbalo en **Actualizar catálogo**. Si tiene errores, la herramienta los lista y no aplica nada.

Las filas con `validado = NO` fueron deducidas de los archivos: confírmelas y cámbielas a `SI`. Las sedes que
ya no vengan en el Excel quedan inactivas, pero su historial se conserva.

## 9. Informe en Excel

En **Informe en Excel** elija el rango de periodos (y el hospital, si es usuario central). El libro trae:
`Resumen`, `Servicios públicos`, `Combustible`, `Mantenimientos`, una hoja por hospital con sus sedes,
`Alertas`, `Análisis`, `Detalle` y `Trazabilidad`.

## 10. Si algo no funciona

| Situación | Solución |
|---|---|
| «No se reconoció ninguna tabla en el archivo» | Los encabezados deben estar en una sola fila. Use la plantilla del ítem. |
| El PDF no se lee | Si es una imagen escaneada, digite la factura en la plantilla de servicios públicos. |
| No veo datos de otra sede | Su usuario es de rol sede: solo ve su hospital. |
| Olvidé la contraseña | El área central la restablece en **Administración → Usuarios**. |

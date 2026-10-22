# Manual breve de uso · Personal financiero y de costos

## 1. Ingreso

0. **Primera vez**: el instalador no pide contraseña. Al terminar abre la página *Cree su contraseña*, donde el
   administrador define su usuario y su propia contraseña (enlace de un solo uso; también en
   *Inicio › Costos ABC › Crear contraseña del administrador*). Luego el administrador crea los demás usuarios en
   *Usuarios y roles*.
1. Ingrese con su usuario y contraseña. Tras 5 intentos fallidos el acceso se bloquea unos minutos.
2. Seleccione la institución (si tiene varias, p. ej. consultores). El rol aparece junto al nombre de la institución.
3. La sesión se cierra sola tras 30 minutos sin uso.

## 2. Primera vez (implantación)

1. **Parametrización → Cuatro niveles → Cargar catálogo base**: crea elementos, recursos R1–R29, unidades de negocio,
   179 centros de costos, inductores y reglas de la Res. 002/2014.
2. **Centros de costos**: desactive los que la institución no usa, asigne la **sede** y ajuste la clase si un intermedio se
   factura como final.
3. **Homologación centro de costos**: registre cómo se llama cada centro en la contabilidad y la nómina
   (p. ej. `ESE01-TAME-RECURSOS HUMANOS → R05`).
4. **Cuentas contables - Recursos**: verifique los prefijos del plan de cuentas de la institución (el catálogo base trae
   los del Cuadro 2 como punto de partida). Marque como *excluir* lo que no es costo del modelo.
5. **Valores de inductores** permanentes: m² por centro (y por sede), equipos.
6. **CUPS** y **CUPS por centro de costos** (cargue la hoja CUPS de la plantilla), **EPS**, **ámbitos** y **notas técnicas**.
7. **Cargas → Mapeo de columnas**: si sus archivos no siguen la plantilla, cree un mapeo por tipo (ver `PLANTILLA_Y_MAPEO.md`).

## 3. Ciclo mensual

| Paso | Dónde | Quién |
|---|---|---|
| 1. Crear el periodo del mes | Cargas → Periodos | Financiero |
| 2. Cargar estado financiero | Cargas → Nueva carga (ESTADO_FINANCIERO) | Financiero |
| 3. Cargar soportes: nómina, contratistas, inventarios, depreciación | Nueva carga (o libro estándar completo) | Financiero / costos |
| 4. Cargar valores del mes: personas, raciones, kilos, solicitudes; % mano de obra si cambió | Nueva carga o Valores de inductores | Costos |
| 5. Cargar producción (o RIPS), facturación y RIPS | Nueva carga | Facturación / costos |
| 6. Revisar errores de cada carga y corregir | Detalle de la carga (exportable a Excel) | Quien cargó |
| 7. Generar distribuciones 1ª–4ª y costeo CUPS | Motor ABC → Generar | Costos |
| 8. Resolver pendientes y conciliaciones | Detalle de la corrida | Costos |
| 9. Revisar tablero, cruces e informe | Tablero / Cruces / Informe | Costos, gerencia |
| 10. Cerrar el periodo | Cargas → Periodos → Cerrar | Financiero |

**Si se equivoca de archivo**: abra la carga → *Anular carga* (indique el motivo), cargue el correcto y vuelva a ejecutar el motor.
**Si cambia un parámetro**: vuelva a ejecutar el motor; la nueva corrida queda vigente y se conservan las 5 últimas.

## 4. Leer los resultados

* **Detalle de la corrida**: entrada (costo del periodo), costo en finales, pendientes con su motivo, conciliación de
  soportes vs. estado financiero, advertencias. *Conservación: Cuadra* significa que no se perdió ni se duplicó costo.
* **Matrices** (estructura del Anexo Técnico 4): elija la etapa 1ª–4ª; exporte a Excel. Clic en un centro = trazabilidad.
* **Trazabilidad**: muestra de qué archivo, fila, cuenta y por qué ruta (centros e inductores) llegó cada peso al centro.
* **Asignaciones**: cada reparto con su *tasa* (costo por unidad de inductor), cantidad y total del inductor.
* **Costo por CUPS**: costo total y unitario por centro final y CUPS, con su composición por elemento.

## 5. Cruces

* **Facturación por ámbito**: facturado vs. costo ABC y margen por ámbito (ambulatorio, hospitalario, urgencias…) y EPS.
  Si aparece *SIN HOMOLOGAR*, registre el valor en *Homologación de ámbitos*.
* **Costo vs. tarifa**: por nota técnica de evento: tarifa, costo unitario, margen y resultado (*PÉRDIDA*, *CUBRE COSTO*).
* **Cápita / PGP**: valor mensual del contrato frente al costo de lo prestado a la EPS según RIPS.
* **Inconsistencias**: RIPS sin factura y viceversa, cantidades distintas, CUPS fuera del catálogo o sin centro/costo,
  centros finales sin producción, tarifas menores al costo, valor facturado distinto a la tarifa, EPS sin nota técnica,
  ámbitos sin homologar. Exportables a Excel para depuración con facturación.

## 6. Tablero e informe gerencial

* **Tablero**: filtros de periodo, periodo de comparación, unidad de negocio, EPS y ámbito.
* **Informe**: misma estructura del informe de costos ABC institucional (portada, introducción, metodología, análisis por
  elemento y recurso, unidades de negocio finales/intermedias/servicios/estructura, distribución por sede, benchmarking,
  hallazgos, recomendaciones y conclusiones). Los hallazgos se generan con los datos (variaciones sobre el umbral,
  rubros nuevos, indicadores fuera de rango, pendientes, tarifas bajo costo, inconsistencias).
  *Editar textos y parámetros* permite ajustar introducción, conclusiones, recomendaciones, umbral y recursos por sede.
  *Imprimir / guardar PDF* produce el documento; *Exportar Excel* genera las hojas RESUMEN EJECUTIVO, UNIDADES SERVICIO,
  DISTRIBUCIÓN SEDES, BENCHMARKING, HALLAZGOS y RECOMENDACIONES.

## 7. Preguntas frecuentes

* *“La cuenta no está asociada a ningún recurso”* → agregue el prefijo en Cuentas contables - Recursos.
* *“Centro de costos no existe ni está homologado”* → agregue la homologación del código contable.
* *Pendiente “sin regla de 1ª distribución”* → el movimiento no trae centro y el recurso es directo: corrija la fuente o cree una regla.
* *Pendiente “el inductor no tiene valores”* → registre las cantidades del inductor para los centros destino.
* *Centro final sin producción* → cargue producción o asocie sus CUPS al centro (CUPS por centro de costos).

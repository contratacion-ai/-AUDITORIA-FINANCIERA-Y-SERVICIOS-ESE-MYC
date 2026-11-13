# Recomendaciones para mejorar el aplicativo de auditoría (hoja de ruta)

Ordenadas por impacto para la ESE Moreno y Clavijo. Las marcadas ★ son las de mayor retorno con menor esfuerzo.

## 1. Datos y fuentes
1. ★ **Valor glosado real.** Cargar el informe de glosas de Software 360 (o de cartera) con valor objetado, valor aceptado, valor recuperado y causal (códigos del Manual Único de Glosas, Res. 2284/2023). Así el indicador pasa de «valor en facturas glosadas» a **% de glosa real**, **% recuperado** y **causales por EPS y facturador**.
2. ★ **Cartera y recaudo.** Cargar la cartera por edades (30/60/90/180/360) y los pagos por EPS para cerrar el ciclo factura → radicación → glosa → pago. Indicador clave: días de rotación de cartera.
3. **RIPS en JSON (Res. 2275/2023).** Leer directamente los JSON de RIPS y las respuestas del MUV/CUV del Ministerio, en lugar del consolidado en Excel.
4. **Kardex completo de farmacia.** Entradas, salidas, saldos, lotes y vencimientos por bodega/sede. Habilita: medicamentos próximos a vencer, rotación de inventario y costo promedio ponderado real.
5. **Tabla de festivos y plazos configurable** desde la pantalla (hoy en `app/db.py`).

## 2. Análisis
1. ★ **Detección de anomalías.** Alertas automáticas cuando un servicio, sede o EPS se aleja más de 2 desviaciones de su promedio de los últimos 6 meses (z-score) y outliers de cantidad por paciente en medicamentos (posible error de digitación o fuga).
2. **Pronóstico de facturación** por sede y servicio a 3 meses (tendencia + estacionalidad) para el presupuesto y la nota técnica.
3. **Ejecución de la cápita vs UPC.** Cruzar población asignada × UPC con el costo de lo prestado en cápita (incluidos medicamentos e insumos) para medir la suficiencia de cada contrato antes de renegociarlo.
4. **Costeo ABC por CUPS** completo (personal, insumos, depreciación, indirectos) para decidir qué tarifas negociar con cada EPS.
5. **Reingresos y frecuencia de uso** por paciente (con el hash del documento) para auditoría de pertinencia.

## 3. Uso y presentación
1. ★ **Usuarios y roles** (facturación, auditoría médica, farmacia, gerencia) con inicio de sesión y bitácora de quién cargó qué. Hoy no hay autenticación: úsela solo en un equipo o red interna.
2. **Seguimiento de hallazgos.** Que cada alerta (F01–F12, R01–R09, farmacia) se pueda asignar a un responsable, con estado (abierta / en corrección / cerrada) y fecha compromiso: convierte el informe en un plan de mejoramiento medible.
3. **Informe gerencial en Word/PowerPoint** con un clic, además del PDF, para comités y Junta Directiva.
4. **Envío automático mensual** del informe gerencial por correo a gerencia y subgerencias.
5. **Gráficas sin internet:** incluir Chart.js dentro de la carpeta `static/` para que el tablero funcione sin conexión.

## 4. Técnica
1. Pasar de SQLite a PostgreSQL si varias personas usarán la herramienta al mismo tiempo.
2. Integración continua (GitHub Actions) que ejecute `python -m pytest` en cada cambio.
3. Copia de seguridad automática de la carpeta `data/`.
4. Instalador para Windows (un .exe) que no requiera instalar Python.
5. Conexión directa (solo lectura) a la base de datos de Software 360, si el proveedor lo permite, para eliminar la carga manual de Excel.

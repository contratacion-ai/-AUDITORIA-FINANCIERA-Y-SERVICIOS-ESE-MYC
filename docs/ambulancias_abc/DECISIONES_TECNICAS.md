# Decisiones técnicas

| Tema | Decisión | Por qué |
|---|---|---|
| Lenguaje / framework | Python 3.12 + Django 5.1 | Pedido por la ESE; Django trae autenticación, permisos, administrador y migraciones sin código extra. |
| Lógica ABC | Paquete `abc_engine` en Python puro, sin Django | Se prueba y audita aislado (`python -m unittest discover -s abc_engine`); el resto de la app solo le pasa datos. |
| Lectura de archivos | Paquete `flota/importacion` puro (openpyxl) separado de la persistencia | Los lectores devuelven registros + hallazgos; no escriben en la base ni corrigen cifras. |
| Servidor web | waitress | WSGI en Python puro, estable en Windows, sin IIS ni compiladores. |
| Base de datos (red local) | SQLite en WAL, archivo único en el servidor | Cero administración; adecuado para decenas de usuarios por navegador porque solo el servidor accede al archivo. |
| Base de datos (nube / alto volumen) | PostgreSQL (misma aplicación, cambio en `ambulancias.ini`) | Concurrencia real de escrituras y respaldos con `pg_dump`. |
| Concurrencia | Transacciones `IMMEDIATE` + `timeout` (SQLite), bloqueo optimista por `version`, importaciones atómicas | Evita "database is locked" y pérdidas silenciosas cuando dos personas editan lo mismo. |
| Respaldo | API de respaldo en caliente de SQLite / `pg_dump`, tarea diaria con retención | Copia consistente sin detener el servicio. |
| Empaquetado Windows | Python embebido oficial + dependencias + Inno Setup; servidor y respaldo como tareas programadas de Windows (SYSTEM) | Un solo `.exe`, sin instalar Python ni servicios de terceros (NSSM); arranca con Windows y se reinicia si falla. |
| Configuración | `C:\ProgramData\CosteoABCAmbulancias\ambulancias.ini` + variables `ABC_*` | Sobrevive a actualizaciones y desinstalaciones (la sal del hash no puede cambiar). |
| Gráficos | Barras HTML/CSS generadas en el servidor | Funciona en la intranet sin internet (sin CDN) y se imprime bien. |
| Datos personales | Solo hash SHA-256 con sal del documento; no se leen nombres, teléfonos ni direcciones | Ley 1581 de 2012: minimización de datos. El cruce con RIPS sigue siendo posible. |
| Idempotencia | `llave_externa` única por traslado; `update_or_create` en catálogos | Reimportar no duplica; las apps de RIPS y auditoría usan las mismas llaves. |
| Calidad de datos | Modelo `HallazgoCalidad` con celda/fila de origen | Las inconsistencias quedan visibles y trazables, sin reemplazarlas por cifras inventadas. |
| Supuestos | `Parametro.es_supuesto` y aviso en el dashboard | Lo que el estudio no define (factor ida y vuelta, km de traslado básico) es configurable y está señalado. |
| Integración | API REST JSON/CSV versionada (`/api/v1`) con claves por aplicación | Contrato simple para las apps de RIPS y auditoría sin acceso directo a la base. |
| CI | GitHub Actions: pruebas en Linux y compilación del instalador en Windows | Cada cambio produce un instalador probado. |

## Alcance

Se implementó lo solicitado: costeo ABC por ambulancia y recorrido, comparación con tarifas propias, registro de traslados y facturación por ámbito consolidada mensualmente, control de consumos con trazabilidad, dashboard con filtros, operación en red con respaldo, integración RIPS/auditoría e instalador. Los anexos de medicamentos/insumos y vacunación no se usan porque el estudio no los requiere para el costeo de ambulancias.

## Límites conocidos

- La facturación del HIS no trae placa ni destino: la ruta se infiere por sede de origen + km facturados, y la placa debe asignarse (mientras tanto el costo se reparte en la sede).
- Los costos de combustible y mantenimiento son estándar del estudio hasta que se registren consumos reales del mes.
- El instalador se compila en Windows (Inno Setup); en este repositorio se entrega el script y el flujo de CI que lo genera.

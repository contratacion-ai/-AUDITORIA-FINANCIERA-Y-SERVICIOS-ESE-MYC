# Seguridad, protección de datos y auditoría

## Control de acceso

| Permiso | Admin | Financiero | Costos | Auditoría | Gerencia | Consultor |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| Consultar tablero, catálogos, resultados e informes | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Cargar y anular archivos | ✔ | ✔ | ✔ | | | ✔ |
| Parametrizar (catálogos, reglas, inductores, mapeos) | ✔ | | ✔ | | | ✔ |
| Ejecutar el motor | ✔ | | ✔ | | | ✔ |
| Abrir / cerrar periodos | ✔ | ✔ | | | | |
| Bitácora de auditoría | ✔ | | | ✔ | | |
| Usuarios y roles de la institución | ✔ | | | | | |
| Textos del informe | ✔ | | ✔ | | ✔ | ✔ |

* El rol se asigna **por institución** (`Membresia`). Un consultor tiene una membresía por cada institución que atiende y
  solo ve esas. Toda consulta filtra por la institución activa; un identificador de otra institución responde 404.
* Los formularios restringen las listas de selección a la institución activa (no se pueden enlazar datos ajenos).
* Superusuario: solo para soporte técnico (admin de Django).

## Protección de datos (Ley 1581 de 2012, Decreto 1377 de 2013)

* Los datos de salud son sensibles: el **documento del paciente** de RIPS y facturación se reemplaza al cargar por un
  seudónimo HMAC-SHA256 con clave secreta (`PSEUDONIMO_KEY`); no se guardan nombres de pacientes ni diagnósticos más allá
  del código CIE-10 del RIPS. Permite cruzar RIPS con facturación sin conocer la identidad.
* Los documentos de empleados y contratistas se usan solo para la distribución de mano de obra.
* Los archivos originales de RIPS y facturación **no se conservan** (traen documentos de pacientes); solo su huella
  SHA-256. Los demás originales se guardan fuera de la carpeta pública y solo los descargan roles de carga o auditoría
  (queda en bitácora).
* Minimización: el modelo ABC no requiere más datos personales; no agregue columnas de identificación adicionales.

## Trazabilidad y auditoría

* `Bitacora` inmutable: inicio de sesión (y fallidos/bloqueos), selección de institución, cargas (huella SHA-256, filas,
  errores), anulaciones con motivo, cambios de parametrización con valores **antes / después**, valores de inductores,
  apertura/cierre de periodos, corridas del motor, exportaciones de informe y bitácora, asignación/revocación de roles.
* Cada costo final se rastrea a archivo, hoja, fila, periodo, centro e inductor (ver `MODELO_DATOS.md`).
* Cada corrida guarda una huella de los parámetros usados y su verificación de conservación del costo.
* Periodo cerrado = no se permiten cargas, anulaciones, cambios de valores del mes ni nuevas corridas.

## Seguridad técnica

* Contraseñas: validadores de Django (mínimo 10 caracteres, no comunes, no numéricas) y hash PBKDF2.
* Bloqueo temporal tras 5 intentos fallidos por usuario+IP (`LOGIN_MAX_INTENTOS`, `LOGIN_BLOQUEO_SEGUNDOS`).
* Sesión expira tras 30 minutos de inactividad y al cerrar el navegador.
* CSRF en todos los formularios, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`; en producción cookies `Secure`,
  redirección HTTPS y HSTS (`DJANGO_ENV=production`).
* Cargas: lista blanca de extensiones, tamaño máximo (`CARGA_MAX_MB`), lectura con `openpyxl` en modo datos (no ejecuta
  macros ni fórmulas) y `defusedxml`.
* Sin dependencias de internet en tiempo de ejecución: Bootstrap y Chart.js están incluidos en `static/vendor`.
* Secretos solo por variables de entorno (`.env.example`).

## Recomendaciones de operación

* Copia de seguridad diaria de PostgreSQL y del volumen `media` (archivos originales).
* Publicar solo detrás de HTTPS en la red interna; restringir el admin de Django a la red de sistemas.
* Revisar trimestralmente las membresías activas (Auditoría puede exportar la bitácora).

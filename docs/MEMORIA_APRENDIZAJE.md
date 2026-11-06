# Memoria de aprendizaje — Proyectos de auditoría y costos · E.S.E. Moreno y Clavijo

> Documento vivo. Recopila lo aprendido en las sesiones de trabajo (oct–nov 2026) sobre la institución,
> sus datos, las reglas de negocio acordadas, las decisiones técnicas y las preferencias de trabajo.
> Actualícelo al cerrar cada sesión: agregue lo nuevo en la sección correspondiente y en la bitácora (§10).
> Corte de esta versión: **06/11/2026**.

---

## 1. La institución

| Dato | Valor |
|---|---|
| Nombre | E.S.E. Departamental de Primer Nivel **Moreno y Clavijo** (Arauca, Colombia) |
| NIT | 900034131 |
| Municipios | **Tame, Arauquita, Fortul, Cravo Norte, Puerto Rondón** |
| Pagador principal | **Nueva EPS** (códigos EPS037 contributivo / EPSS37 subsidiado) |
| Modalidades de contratación | **CÁPITA** y **EVENTO** (PGP se retiró del alcance el 30/10/2026) |
| Software contable / HIS | **Software 360** (facturación por ámbito, reporte CTRP de estado de resultados) |
| Identidad visual | Colores **verde y vino tinto**, logo de la ESE (usado en instaladores y tableros) |
| Responsable de costos | Profesional de Costos (autor citado en el aplicativo v5) |

### Red de sedes (Acta de negociación 4131 — 29 sedes REPS)
- Hospital San José de **Cravo Norte** (812200020604, verificado con RIPS reales)
- Centro de Salud **Jesús Coronel** — Tame (817940020607, verificado)
- Hospital **San Antonio** — Tame
- Hospital **San Lorenzo** — Arauquita
- Hospital **San Francisco** — Fortul
- Hospital **San Juan de Dios** — Puerto Rondón

**Códigos DANE** (5 primeros dígitos del código de habilitación): 81794 Tame · 81065 Arauquita ·
81300 Fortul · 81220 Cravo Norte · 81591 Puerto Rondón · 81001 Arauca (se conserva por si acaso).
El segundo municipio de la Nota Técnica es **ARAUQUITA**, no Arauca.
Prefijos de factura por hospital: FHJD, FHSL… (se usan para reconocer la sede).

---

## 2. Marco normativo que se aplica

| Norma | Uso |
|---|---|
| Res. **3374/2000** (Anexo Técnico 1) | Estructura RIPS TXT: CT, AF, US, AC, AP, AM, AT, AU, AH, AN |
| Res. **2275/2023** | RIPS en JSON (`grupoServicios` 03/04 → hospitalario, 05 → urgencias, resto ambulatorio) |
| Res. **2284/2023** | Manual Único de Glosas (causales) |
| Res. **DDC-000002/2014** | Jerarquía de costeo ABC: Elementos → Recursos → Unidades de negocio → Centros de costos |
| Ley **1581/2012**, Dec. 1377/2013 | Datos sensibles de salud: seudonimizar documento de paciente, no guardar nombres |

---

## 3. Aplicaciones construidas (ramas del repositorio)

Ninguna está fusionada todavía a `main` (main = commit inicial). PR abiertos: #1 (aplicativo v5) y #2 (cargue RIPS).

| Rama | Aplicación | Tecnología | Puerto |
|---|---|---|---|
| `claude/billing-audit-app-nignte` | **Auditoría de Facturación v5.0**: facturación Software 360, RIPS, costos, notas técnicas; conciliación tri-eje, radicación, hallazgos R01–R07, informe gerencial, Excel 19 hojas | FastAPI + SQLite + Chart.js local | 8000 |
| `claude/intelligent-gates-75q92n` | **Cargue RIPS ESE MyC**: valida RIPS 3374 y JSON, organiza hospital→mes, exporta Excel/JSON homologado, envía al aplicativo v5, tablero `/tablero`, informe por CUPS, depuración | Python + pandas, servidor web local | 8100 |
| `claude/beautiful-pasteur-qzto6q` | **Auditoría RIPS CÁPITA · EVENTO**: base consolidada por (hospital, mes), sincroniza carpetas de Google Drive, frecuencias NT vs RIPS, costo cápita, Top 20/Pareto evento, dashboard, hallazgos Excel/PDF/JSON | Python + pandas | 8765 |
| `claude/ambulancia-abc-costeo-yxrq5c` | **Costeo ABC de ambulancias** (21 unidades): costo por ambulancia y recorrido, tarifas Anexo 3, rentabilidad por ruta, traslados, consumos, auditoría RIPS vs facturación | Django 5.1 + waitress + SQLite WAL/PostgreSQL | 8090 |
| `claude/abc-healthcare-costing-tool-1ufa1b` | **Herramienta de costos ABC** institucional (multi-institución, roles, bitácora) + **homologación CECO** del estado financiero (reporte CTRP) | Django + Bootstrap + Chart.js, PostgreSQL/SQLite, Docker | — |

### Integración entre aplicaciones
- Cargue RIPS → genera `RIPS_AUDITORIA_<AAAA-MM>.xlsx` en la plantilla homologada y lo envía a
  `POST /api/cargas/rips` del aplicativo v5; lee `PARAM CONTRATO` (`/api/parametros`) y la validación de cruce.
- El aplicativo v5 **reemplaza el mes completo** en cada carga: por eso cada archivo mensual lleva todas las sedes acumuladas.
- Ambulancias expone API `/api/v1` (JSON/CSV) con claves por aplicación, para RIPS y auditoría.
- Homologación CECO produce la hoja `CARGA HERRAMIENTA`, que se carga tal cual en la herramienta ABC.

### Llaves comunes de homologación (las 4 fuentes)
Periodo · EPS · Contrato · Modalidad · Sede · Factura (normalizada a mayúsculas sin símbolos) · Código CUPS/CUM · Servicio.

---

## 4. Reglas de negocio acordadas

### 4.1 Modalidad CÁPITA / EVENTO (orden de aplicación)
1. Código (u homólogo) en MARCACIÓN CUPS CÁPITA **OFERTADO** → CÁPITA
2. En catálogo **NO OFERTADO** → EVENTO
3. No está en catálogo pero sí en la **Nota Técnica** → CÁPITA
4. Sin marca: la carpeta/contrato declara la modalidad → la declarada
5. Sin dato → EVENTO (SIN MARCA)
- **Nueva EPS**: es CÁPITA (subsidiado y contributivo) todo lo que **no** dice EVENTO; excepciones por número de
  contrato; `02-02-06-00227-2016` registrado como cápita.
- **Modalidad efectiva** = la declarada por la remisión (así se facturó); la del catálogo se usa para auditar.
- Hallazgos: `OFERTADO_CAPITA_FACTURADO_EVENTO` (riesgo de doble pago/glosa) y `NO_OFERTADO_EN_CAPITA`.
- Alerta si un contrato queda en cápita con **< 30 %** de servicios en la NT.
- El estado OFERTADO está en la columna **"OFERTA 03/09/2026"** del libro (1.551 códigos: 370 ofertados, 1.177 no ofertados).
- Medicamentos: en la NT el código es `MD…` y el CUM es el **"Homologo"**; se cruza por homólogo y sin sufijo (`20041822-10` → `20041822`).

### 4.2 Fórmulas
- **Costo CÁPITA** (mes, grupo) = Σ costo unitario NT (código, municipio) × frecuencia RIPS.
- **Cápita pactada** = afiliados × valor usuario (actas 4131 RS/RC); se compara con costo NT × RIPS por municipio y mes.
- **Pacientes únicos del periodo** = suma de únicos mensuales (114.923). **Asistencia** = par (paciente, fecha) en el mes.
- **Costo por atención** = costo / frecuencia RIPS costeada (promedio $8.626).
- Usuarios contratados = **70.003,77** (editable).

### 4.3 Cifras oficiales de control (decisión 30/10/2026: se usa la cifra ACTUAL del libro)
| Indicador | Valor |
|---|---|
| Costo CÁPITA ene–ago (NT COSTOS) | **$8.264.369.795** |
| Costo CÁPITA sin medicamentos | $8.028.274.185 |
| Grupo medicamentos | $236.095.610 · 554.587 CUMS |
| CUPS / pacientes únicos / asistencias | 748.058 / 114.923 / 213.814 |
| Facturación EVENTO | $7.462.498.070 |
| Giro sin UPC / con UPC | $1.813.727.591 / $2.112.811.270 |
| Dashboard FINAL (histórico, no oficial) | $8.053.016.408 |

Fuente maestra: libro **`INFORME DE SERVICIOS 2026 BASE DE DATOS ANALISIS.xlsx`**.

### 4.4 Ambulancias (modelo ABC corregido por la ESE, oct 2026)
- Flota: **21 ambulancias**; Ford Ranger KSM548–KSM552 nuevas sin historial.
- Tripulación/día **$457.592,67** (médico 6,4 h, auxiliar 6,3 h, conductor 6,3 h; salario ÷ 191 h); valor hora equipo **$72.010**.
- Indirectos $21.717.958/mes ÷ 21 ÷ 30 = **$34.473** por ambulancia/día. Gastos administrativos **6,2 %**.
- Ejemplo verificado OEU846: (700.215 + 294.137 + 457.593 + 34.473) × 1,062 = **$1.578.576/día**.
- Valor km Anexo 3 = **$17.000**; velocidad estándar 34,6 km/h.
- Método **ESTÁNDAR** por recorrido (combustible por unidad; resto por tarifa de flota): 0 de 253 rutas con pérdida
  (margen 13,4 %–45,9 %). La diferencia se reporta como **"sobrecosto por baja utilización"**.
- Hallazgos de facturación sept. 2026 (617 líneas, $867,2 M): 102 líneas con tarifa básica 601T01 + km en el mismo
  ingreso (posible doble cobro), 54 sin número de factura, 61 con km sin ruta en el Anexo 3, ninguna con placa.
- Pendiente de decisión: +30 km a Saravena por cierre del puente El Tigre (severidad alta).

### 4.5 Reglas de auditoría de línea (aplicativo v5)
R01 sin factura · R02 valor ≤ 0 · R03 total ≠ valor × cantidad · R04 cantidad ≤ 0 · R05 sin CUPS/medicamento ·
R06 fecha fuera del mes · R07 factura en varios contratos. Umbrales de diferencia 2 % / 5 %.

### 4.6 Costos ABC institucional / CECO
- Estados del maestro de homologación: ACTIVO / VERIFICAR / NO APLICA / EXCLUIR.
- Maestro: `HOMOLOGACION_CECO_EF_vs_RESOLUCION_ESE_MYC_2026.xlsx`; carga ABC solo clases 5, 6, 7.
- Reporte CTRP editado a mano (sin OidCuentaContable ni OidTercero): la cuenta se toma de la cuenta de detalle que la precede.
- Montos en `Decimal`, repartos en centavos exactos; cada corrida verifica `entrada = finales + pendientes`.
- Roles: Admin, Financiero, Costos, Auditoría, Gerencia, Consultor.

---

## 5. Estructura de los datos fuente

```
Hospital San Jose de Cravo Norte/          (Google Drive para escritorio, G:\Mi unidad\RIPS\...)
  08. Agosto/                              ← el mes sale de "NN. Mes"
    NUEVA EPS CAPITA SUBSIDIADO/   CT AF US AC AP AM AT (TXT 3374)
    NUEVA EPS EVENTO SUBSIDIADO/
    NUEVA SUBS / NUEVA CONTRIB/    ← no declaran modalidad: usar maestro de contratos
    SANITAS EVENTO CONTRIBUTIVO/   ← otras EPS: se cargan y se excluyen del análisis Nueva EPS
```
- Formatos aceptados: TXT 3374, JSON 2275, consolidados JSON/XLSX, ZIP (incluso anidados), RAR.
- Libros de facturación de Software 360 de hasta 500 MB: lector **streaming** del XML del xlsx.
- Una línea repetida entre carpetas se toma una vez (llave por contenido); recargar un hospital-mes lo **reemplaza**, nunca suma.

---

## 6. Inconsistencias conocidas en los artefactos fuente (no corregir con cifras inventadas)
- Dashboard FINAL: matriz `GMON` suma el promedio mensual, no el total; tres conteos distintos de RIPS cápita (676.111 / 720.359 / 748.058).
- 23 códigos de la NT sin costo unitario (p. ej. 902216, 903822, 911017) → hoja "Sin costo NT".
- Libro de servicios: facturación evento difiere entre hojas (mar, jun, jul).
- Anexo 3 de tarifas: 360 celdas `#REF!` (2016–2019), encabezado desalineado.
- COSTOS AMBULANCIAS: duración vacía, `#DIV/0!`, 36 celdas vinculadas a libro externo no entregado.
- Principio: **todo hallazgo se marca, se explica y se deja trazable** (celda/fila de origen); el usuario decide.

---

## 7. Decisiones técnicas que se repiten (patrón de la casa)
1. **Instalador Windows sin permisos de administrador**: NSIS o Inno Setup, manifiesto `asInvoker`, instalación en
   `%LOCALAPPDATA%`, Python 3.12 portátil incluido, sin internet. Evitar "setup"/"install" en el nombre del .exe.
   Advertir de SmartScreen: *Más información → Ejecutar de todas formas*.
2. **Funciona sin internet**: Chart.js 4.4.1 / Bootstrap incluidos localmente; servidor en `127.0.0.1`.
3. **Datos del usuario separados del programa**: se conservan al actualizar o desinstalar.
4. **Primer acceso seguro**: el administrador crea su contraseña en el navegador (token de un solo uso) o se genera clave nueva en TXT.
5. **Privacidad**: documento del paciente → seudónimo HMAC-SHA256 / SHA-256 con sal (`PAC-…`); no se guardan nombres,
   teléfonos ni direcciones; archivos originales con datos personales no se versionan.
6. **Idempotencia**: reemplazo por (hospital, mes); llaves externas únicas; `update_or_create`.
7. **Motor de cálculo puro** (sin framework) y probado aislado; lectores que devuelven registros + hallazgos sin escribir en BD.
8. **Pruebas automáticas que reproducen las cifras oficiales** (p. ej. $8.264.369.794,92 y $1.578.576/día).
9. **Errores legibles**: mensaje en pantalla + "Ver detalle técnico" + `errores.log`.
10. **Salidas**: Excel (openpyxl), PDF, JSON, dashboard HTML; informe gerencial con recomendaciones.
11. **CI**: GitHub Actions; publicar instalador como Release con `[publicar]` en el commit o etiqueta `v*`.
12. SQLite (WAL) para red local; PostgreSQL cuando hay concurrencia alta.

---

## 8. Preferencias de trabajo del usuario
- Idioma: **español**; documentación, interfaz y mensajes de commit en español.
- Quiere herramientas que **mejoren la información**: análisis de datos en salud, análisis de cifras,
  **informes de alta gerencia**, presentaciones que **recomienden acciones** de mejora para la institución.
- Interés en: investigación/comparación con otras entidades de salud, aprendizaje en IA y programas asociados,
  aplicaciones para las áreas de la ESE, y herramientas de diseño para generar recursos económicos propios.
- Usuarios finales no técnicos: todo debe instalarse con un clic, sin administrador y sin Python.
- Preferencia por **trazabilidad** y conciliación contra cifras de control antes de presentar resultados.

---

## 9. Hoja de ruta / pendientes
**Decisiones pendientes del usuario**
- Confirmar códigos de habilitación de las sedes no verificadas (San Antonio, San Lorenzo, San Francisco, San Juan de Dios).
- Ajuste de tarifa a Saravena (+30 km, puente El Tigre).
- Fusionar las ramas a `main` y unificar los dos módulos `rips_auditoria` (intelligent-gates y beautiful-pasteur comparten nombre de paquete).

**Mejoras priorizadas (★ = mayor retorno, menor esfuerzo)**
1. ★ Glosas reales (objetado/aceptado/recuperado, causales Res. 2284/2023) por EPS y facturador.
2. ★ Cartera por edades y recaudo → días de rotación de cartera.
3. ★ Detección de anomalías (z-score > 2 vs últimos 6 meses; outliers de medicamentos).
4. ★ Seguimiento de hallazgos con responsable, estado y fecha compromiso (plan de mejoramiento).
5. Kárdex de farmacia (lotes, vencimientos, rotación, costo promedio ponderado).
6. Pronóstico de facturación a 3 meses por sede y servicio.
7. Suficiencia de la cápita vs UPC por contrato antes de renegociar.
8. Informe gerencial en Word/PowerPoint con un clic y envío mensual automático por correo.
9. Conexión de solo lectura a Software 360.

---

## 10. Bitácora de sesiones
| Fecha | Trabajo |
|---|---|
| 14/10/2026 | Aplicativo de auditoría v4 → v5.0 (facturas, medicamentos e insumos por sede, informe gerencial, instalador .exe). Procesador RIPS 3374 y cargue con instalador. |
| 15/10/2026 | Tablero de análisis RIPS, cargue sin límite de archivos y acceso en red. |
| 16–20/10/2026 | App Django de costeo ABC de ambulancias; modelo corregido por la ESE; reparto entre 21 ambulancias; instalador y Release. |
| 22/10/2026 | RIPS multi-mes y auditoría RIPS vs facturación (ambulancias). Herramienta de costos ABC institucional e instaladores sin administrador. |
| 23–26/10/2026 | Homologación CECO, carga directa del reporte CTRP (incluido el editado a mano), consulta de costos. |
| 27/10/2026 | Informe por CUPS enriquecido, depuración y lectura de RIPS JSON. |
| 30/10/2026 | Auditoría RIPS CÁPITA/EVENTO: libro INFORME DE SERVICIOS 2026, cifras oficiales, retiro de PGP, Drive, red de sedes, regla cápita Nueva EPS. |
| 06/11/2026 | Recopilación de esta memoria de aprendizaje. |

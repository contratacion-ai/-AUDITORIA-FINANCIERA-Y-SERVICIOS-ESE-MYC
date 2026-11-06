# CLAUDE.md — Auditoría financiera y de servicios · E.S.E. Moreno y Clavijo

**Lea primero `docs/MEMORIA_APRENDIZAJE.md`**: contiene la institución, sedes, reglas CÁPITA/EVENTO,
cifras oficiales de control, modelo ABC de ambulancias, decisiones técnicas y pendientes.
Al terminar cada sesión, actualice esa memoria (secciones y bitácora §10).

## Reglas rápidas
- Todo en **español** (interfaz, documentación, commits).
- E.S.E. de primer nivel, NIT 900034131; municipios Tame, Arauquita, Fortul, Cravo Norte, Puerto Rondón; pagador Nueva EPS (EPS037/EPSS37).
- Cifra oficial costo CÁPITA ene–ago 2026: **$8.264.369.795** (libro INFORME DE SERVICIOS 2026). El dashboard FINAL ($8.053.016.408) es solo histórico.
- Nunca reemplazar inconsistencias de los datos fuente con cifras inventadas: marcarlas como hallazgo trazable.
- Privacidad (Ley 1581/2012): seudonimizar documentos de pacientes; no versionar archivos con datos personales.
- Instaladores Windows sin permisos de administrador, con Python portátil y funcionamiento sin internet.
- Pruebas automáticas que reproduzcan las cifras de control antes de entregar.
- Los informes deben terminar en **recomendaciones accionables** para la gerencia.

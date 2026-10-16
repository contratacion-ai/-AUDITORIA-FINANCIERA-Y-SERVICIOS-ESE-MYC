# Costeo ABC de la flota de ambulancias

Aplicación web (Python/Django) para la ESE Moreno y Clavijo: calcula el costo ABC por ambulancia y por recorrido a partir del informe técnico de costos de operación, lo compara con las tarifas propias 2026 (Anexo 3) y analiza la rentabilidad. Incluye registro de traslados y facturación por ámbito consolidada mes a mes, control de consumos por unidad, dashboard con filtros, operación multiusuario en red, API para RIPS y auditoría, e instalador para Windows.

```
ambulancias_abc/
├── abc_engine/          Motor ABC en Python puro (sin Django) + datos del estudio + pruebas
├── flota/
│   ├── importacion/     Lectores de Excel/JSON/CSV (detectan inconsistencias, no escriben en BD)
│   ├── servicios/       Persistencia de importaciones, costeo mensual, respaldo, datos iniciales
│   ├── datos_iniciales/ Tarifas del Anexo 3 y partidas del centro de costos (sin datos personales)
│   ├── api.py           API de integración (/api/v1)
│   └── views.py, templates/  Dashboard, consolidado, ambulancias, rentabilidad por ruta, traslados, consumos
├── config/              Configuración Django (lee ambulancias.ini / variables ABC_*)
└── servidor.py          Servidor de producción (waitress)
```

Inicio rápido (desarrollo):

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py cargar_datos_iniciales
python manage.py crear_admin --clave 'Clave-Desarrollo-1'
python servidor.py            # http://localhost:8000
python manage.py test         # pruebas
```

Documentación en [`docs/ambulancias_abc/`](../docs/ambulancias_abc/):
[modelo y fórmulas](../docs/ambulancias_abc/MODELO_DATOS.md) ·
[instalación y operación](../docs/ambulancias_abc/INSTALACION.md) ·
[integración RIPS/auditoría](../docs/ambulancias_abc/INTEGRACION.md) ·
[calidad de datos y resultados](../docs/ambulancias_abc/CALIDAD_DATOS.md) ·
[decisiones técnicas](../docs/ambulancias_abc/DECISIONES_TECNICAS.md).

Instalador de Windows: [`installer/windows/`](../installer/windows/) (lo compila también el flujo de GitHub Actions).

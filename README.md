# Herramienta de costos ABC para instituciones de salud

Aplicación web para calcular el costo de los servicios de IPS, ESE y redes hospitalarias con costeo basado en actividades,
siguiendo la jerarquía de la Resolución DDC-000002 de 2014 y del manual del sistema web de costos de referencia:
**Elementos → Recursos (↔ cuentas contables) → Unidades de negocio → Centros de costos** (Estructura, Servicios,
Intermedios, Finales), **inductores**, porcentajes de **mano de obra Empleado / OPS** y **cuatro distribuciones**, más el
costeo por **CUPS** y su cruce con **RIPS**, **facturación por ámbito** y **notas técnicas de EPS**.

## Qué incluye

| Requisito | Dónde |
|---|---|
| Carga mensual de Excel/CSV (estado financiero, nómina, contratistas, inventarios, depreciación, inductores, producción, facturación, RIPS, notas técnicas) con validación y limpieza | `cargas/` · Cargas |
| Plantilla estándar + mapeo de columnas configurable por institución | `plantillas/`, Cargas → Mapeo de columnas · [docs/PLANTILLA_Y_MAPEO.md](docs/PLANTILLA_Y_MAPEO.md) |
| Catálogo de centros de costos (4 clases, directos/indirectos, sedes, homologación contable) | Parametrización |
| Inductores, tasas de asignación y trazabilidad de cada asignación | Motor → Asignaciones / Trazabilidad |
| Parametrización en 4 niveles | [docs/PARAMETRIZACION.md](docs/PARAMETRIZACION.md) |
| CUPS editable y costo por CUPS | Motor → Costo por CUPS |
| Análisis por ámbito de facturación | Cruces → Facturación por ámbito |
| Notas técnicas EPS: costo vs. tarifa, cápita/PGP | Cruces |
| Cruce RIPS × CUPS × centros × costos con inconsistencias | Cruces → Inconsistencias |
| Tablero con filtros y comparación de periodos | Tablero |
| Informe con la estructura del informe de referencia (HTML imprimible a PDF y Excel) | Informe |
| Roles, aislamiento por institución, bitácora | [docs/SEGURIDAD_Y_CUMPLIMIENTO.md](docs/SEGURIDAD_Y_CUMPLIMIENTO.md) |
| Modelo entidad-relación | [docs/MODELO_DATOS.md](docs/MODELO_DATOS.md) |
| Manual para el personal financiero | [docs/MANUAL_USUARIO.md](docs/MANUAL_USUARIO.md) |
| Caso de prueba calculado a mano | [docs/CASO_DE_PRUEBA.md](docs/CASO_DE_PRUEBA.md) |

## Decisiones técnicas

* **Django** en lugar de FastAPI: la herramienta es una aplicación web con formularios, autenticación, roles, ORM con
  migraciones y administración; Django lo trae integrado, FastAPI obligaría a construir o ensamblar cada pieza.
* **PostgreSQL** en producción (SQLite para desarrollo y pruebas, sin cambiar código: `DATABASE_URL`).
* **Docker Compose** (aplicación + PostgreSQL) para desplegar en un servidor de la institución; funciona sin internet
  (Bootstrap y Chart.js incluidos localmente), como el sistema de referencia.
* Plantillas de Django + Bootstrap + Chart.js (sin compilación de frontend). Excel con `openpyxl`/`xlrd`.
* Montos en `Decimal`; los repartos se hacen en centavos exactos y cada corrida verifica `entrada = finales + pendientes`.

## Instalador

```bash
bash instalador/construir_instalador_windows.sh   # dist/CostosABC-Setup-<versión>.exe (requiere makensis)
bash instalador/construir_paquete.sh              # dist/CostosABC-<versión>.zip (scripts + dependencias)
```

* **Windows — `CostosABC-Setup.exe`** (recomendado para un equipo): **no requiere permisos de administrador**
  (manifiesto `asInvoker`). Asistente en español que incluye Python 3.12 portátil y todas las dependencias. Instala en
  `%LOCALAPPDATA%\CostosABC`, sirve solo en este equipo (`127.0.0.1`, sin aviso del firewall), inicia al iniciar sesión
  (`HKCU\...\Run`, sin ventana) y programa un respaldo diario del usuario.
* **Windows — `CostosABC-Servidor-Setup.exe`** (servidor de red, requiere administrador): instala en `C:\CostosABC`,
  servicio al encender el equipo (tarea como SYSTEM con reinicio automático), firewall abierto y respaldo diario.
* Ambos piden los datos de la institución y el usuario administrador **sin contraseña**: al terminar se abre la página
  *Cree su contraseña* con un enlace de un solo uso. La configuración la hace `instalador/windows/configurar.py` con el
  Python incluido (sin PowerShell) y verifica que la aplicación responda.
* **Windows — ZIP**: descomprimir y doble clic en `instalador\windows\INSTALAR.bat` (PowerShell).
* **Linux**: `sudo bash instalador/linux/instalar.sh` (servicio systemd) o `--docker` (Docker + PostgreSQL).

Instrucciones para el usuario final: `instalador/LEAME_INSTALACION.txt`. Volver a ejecutar el instalador actualiza la
versión conservando datos y claves.

## Puesta en marcha (desarrollo)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export DJANGO_DEBUG=1
python manage.py migrate
python manage.py cargar_demo --password "Demo-ABC-2025!"   # institución con datos SINTÉTICOS y usuarios por rol
python manage.py createsuperuser                            # opcional: soporte técnico
python manage.py runserver
```

Usuarios de la demo: `admin_demo`, `financiero_demo`, `costos_demo`, `auditoria_demo`, `gerencia_demo`, `consultor_demo`.

Crear una institución real con el catálogo base y su administrador:

```bash
python manage.py crear_institucion 810001234 "ESE HOSPITAL EJEMPLO" --nit 900000000-1 --catalogo-base --admin usuario_admin
```

## Producción

```bash
cp .env.example .env        # defina DJANGO_SECRET_KEY, PSEUDONIMO_KEY, POSTGRES_PASSWORD, hosts
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Ubique un proxy HTTPS delante del puerto 8000. Respalde la base de datos y el volumen `media` (archivos originales).
`python manage.py check --deploy` con `DJANGO_ENV=production` revisa la configuración de seguridad.

## Pruebas

```bash
pytest            # 86 pruebas: limpieza, cargas, motor (caso calculado a mano), cruces, informe, roles y aislamiento
```

## Estructura

```
abc_salud/   configuración          core/      instituciones, roles, periodos, bitácora, demo
modelo/      catálogo y parametrización    cargas/    esquemas, limpieza, lectores, procesador, plantilla
motor/       distribuciones 1ª–4ª, CUPS, trazabilidad   analisis/  consultas, cruces, informe, exportación
seed/        catálogo base Res. 002/2014 (editable)     docs/      documentación
```

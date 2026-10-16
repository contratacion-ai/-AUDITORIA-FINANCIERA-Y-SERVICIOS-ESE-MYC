# Instalación, configuración y operación

## Arquitectura de despliegue

```
 Equipos de la red (navegador)  ──HTTP :8000──►  Servidor Windows (un equipo de la red)
                                                 ├─ waitress + Django (tarea programada al iniciar)
                                                 ├─ C:\ProgramData\CosteoABCAmbulancias\
                                                 │    ambulancias_abc.sqlite3  (base centralizada)
                                                 │    ambulancias.ini, logs, respaldos\
                                                 └─ Respaldo diario 23:00 (tarea programada)
 Nube (opcional): mismo código en Linux + PostgreSQL + proxy HTTPS
```

Nadie abre la base de datos desde otro equipo: todos entran por el navegador y el único proceso que escribe es el servidor. Por eso SQLite es suficiente en la red local; para la nube o muchos usuarios simultáneos se usa PostgreSQL.

## A. Servidor en Windows con el instalador

Requisitos: Windows 10/11 o Server 2016+ de 64 bits, 2 GB de RAM libres, IP fija o nombre de equipo estable en la red. No hace falta instalar Python (el instalador lo incluye).

1. Obtenga `Instalador_CosteoABC_Ambulancias_<versión>.exe`:
   - Desde GitHub: sección **Releases** del repositorio (versión publicada, enlace permanente).
   - O en la pestaña **Actions** › flujo *Costeo ABC Ambulancias* › última ejecución › artefacto *Instalador_CosteoABC_Ambulancias* (se conserva 30 días; viene dentro de un .zip).
   - Para publicar una versión nueva como Release: empuje una etiqueta `v1.2.3` o un commit cuyo mensaje contenga `[publicar]`.
   - O compílelo (ver sección D).
2. Ejecútelo. **No pide usuario ni contraseña de la aplicación.** Al iniciar pregunta cómo instalar:
   - **Para todos los usuarios** (recomendado para el servidor): Windows pide confirmar el permiso de administrador. Deja el servidor funcionando para toda la red, aunque nadie haya iniciado sesión.
   - **Solo para mí**: no pide credenciales de Windows. La aplicación funciona en ese equipo mientras el usuario tenga la sesión abierta. Para que entren los demás equipos, un administrador debe ejecutar luego *Inicio › Costeo ABC Ambulancias › Habilitar acceso desde la red*.
3. El instalador:
   - copia Python embebido y la aplicación (en `C:\Program Files\CosteoABCAmbulancias`, o en la carpeta del usuario si es "solo para mí");
   - crea la carpeta de datos (`C:\ProgramData\CosteoABCAmbulancias`, o `%APPDATA%\CosteoABCAmbulancias`) con la base, `ambulancias.ini` (clave secreta y sal aleatorias), logs y respaldos;
   - carga la flota, los perfiles del estudio, las 90 rutas del Anexo 3, las partidas del centro de costos y los hallazgos de calidad;
   - **crea el usuario `admin` con una clave aleatoria** y la guarda en `CREDENCIALES_ADMIN.txt` en la carpeta de datos (solo lo pueden leer quien instaló, los administradores y el sistema). Al terminar se abre ese archivo; también está en *Inicio › Costeo ABC Ambulancias › Usuario y clave inicial*;
   - para todos los usuarios: abre el puerto en el firewall (perfiles Dominio y Privado) y registra las tareas `CosteoABC-Servidor` (al iniciar Windows) y `CosteoABC-Respaldo` (diaria 23:00); solo para mí: las mismas tareas, ligadas a la sesión del usuario;
   - deja un acceso directo en el escritorio.
4. Abra `http://localhost:8000/` (en el servidor) o `http://NOMBRE-DEL-SERVIDOR:8000/` (desde la red), ingrese con `admin` y la clave del archivo, y cámbiela en **Cambiar mi clave**.

Instalación desatendida (sin ninguna ventana): `Instalador_...exe /VERYSILENT /ALLUSERS` (o `/CURRENTUSER`); `/PUERTO=8080` cambia el puerto.

**Actualizar**: ejecute el instalador de la nueva versión sobre la anterior. Deje la clave en blanco para conservarla. La base, el `.ini` y los respaldos no se tocan; las migraciones se aplican solas.

**Desinstalar**: Panel de control › Programas. Se quitan las tareas y la regla de firewall; `C:\ProgramData\CosteoABCAmbulancias` se conserva.

Registro de la instalación: `C:\ProgramData\CosteoABCAmbulancias\instalacion.log`. Registro de la aplicación: `ambulancias_abc.log` en la misma carpeta.

## B. Primeros pasos después de instalar

1. **Usuarios** (Administración › Usuarios): cree un usuario por persona y asígnele un grupo:
   - *Administrador ABC*: todo, incluidos parámetros, tarifas y partidas indirectas.
   - *Operador*: registra y edita traslados y consumos; importa facturación, RIPS y consumos.
   - *Consulta*: solo lectura (dashboard, reportes).
2. **Sedes**: revise el `codigo_prestador` (código REPS) de cada sede si va a importar RIPS.
3. **Parámetros**: valide los marcados como *supuesto* (`factor_ida_vuelta`, `km_traslado_basico`).
4. **Cargas del mes**: facturación del HIS, RIPS por hospital y tablas de consumos (ver [GUIA_CARGAS_MENSUALES.md](GUIA_CARGAS_MENSUALES.md)).
5. **Asignar placas** a los traslados (Traslados › filtrar *Sin placa* › seleccionar › Asignar).
6. **Registrar consumos** (tanqueos, órdenes de mantenimiento, insumos) o cargarlos con la plantilla CSV. Desde ese momento el costeo usa valores reales en lugar del estándar del estudio.
7. **Claves de API** para RIPS y auditoría: `python\python.exe app\manage.py crear_clave_api auditoria` (desde `C:\Program Files\CosteoABCAmbulancias`) o en Administración › Claves API.

## C. Respaldo y restauración

- Automático: diario a las 23:00 en `C:\ProgramData\CosteoABCAmbulancias\respaldos\` (se conservan los últimos 30; cambie `backup_retencion` en el `.ini`).
- Manual: menú Inicio › *Costeo ABC Ambulancias* › *Respaldar base de datos ahora*, o `manage.py respaldar_bd --destino \\otro-equipo\respaldos`.
- Copie periódicamente la carpeta de respaldos a otro equipo o a la nube institucional.
- Restaurar (SQLite):
  1. `Stop-ScheduledTask CosteoABC-Servidor`
  2. Copie el respaldo elegido sobre `C:\ProgramData\CosteoABCAmbulancias\ambulancias_abc.sqlite3` (borre los archivos `-wal` y `-shm` si existen).
  3. `Start-ScheduledTask CosteoABC-Servidor`
- PostgreSQL: el respaldo usa `pg_dump -Fc`; restaure con `pg_restore -c -d ambulancias_abc archivo.dump`.

## D. Compilar el instalador

En un equipo Windows con PowerShell e [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`):

```powershell
git clone <repositorio> ; cd <repositorio>
powershell -ExecutionPolicy Bypass -File installer\windows\build_installer.ps1 -Version 1.0.0
# Resultado: installer\windows\salida\Instalador_CosteoABC_Ambulancias_1.0.0.exe
```

El script descarga Python 3.12 embebido de python.org, instala las dependencias (`requirements.txt`; con `-ConPostgres` agrega el conector de PostgreSQL), copia la aplicación, ejecuta `manage.py check` y las pruebas del motor ABC, y compila `costeo_abc_ambulancias.iss`. El flujo de GitHub Actions hace lo mismo en cada cambio.

## E. Despliegue en la nube (opcional)

Para acceso fuera de la red local, en un servidor Linux con PostgreSQL:

```bash
cd ambulancias_abc
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-postgres.txt
cp ambulancias.ini.ejemplo ambulancias.ini   # motor = postgresql, https = true,
                                            # allowed_hosts y csrf_trusted_origins con el dominio
python manage.py migrate && python manage.py collectstatic --noinput
python manage.py cargar_datos_iniciales && python manage.py crear_admin --clave '...'
python servidor.py --host 127.0.0.1 --puerto 8000     # detrás de nginx/Caddy con HTTPS
```

Programe `python manage.py respaldar_bd` con cron. Las mismas variables pueden darse por entorno (`ABC_SECRET_KEY`, `ABC_DB_MOTOR=postgresql`, `ABC_DB_HOST`, ...).

La red local y la nube pueden compartir una sola base: instale el servidor de Windows con `motor = postgresql` apuntando al PostgreSQL de la nube (compile con `-ConPostgres`). Exponga la aplicación en internet solo con HTTPS y usuarios con claves robustas.

## F. Desarrollo

```bash
cd ambulancias_abc
pip install -r requirements.txt
python manage.py migrate && python manage.py cargar_datos_iniciales
python manage.py crear_admin --clave 'Clave-Desarrollo-1'
python manage.py runserver          # o: python servidor.py
python manage.py test               # 54 pruebas (motor ABC + aplicación)
python -m unittest discover -s abc_engine -t .   # solo el motor, sin Django
```

## Concurrencia (varios usuarios)

- SQLite en modo WAL: lecturas simultáneas sin bloqueo; escrituras en transacciones `IMMEDIATE` con espera de 30 s.
- Bloqueo optimista: si dos usuarios editan el mismo traslado o consumo, el segundo en guardar recibe un aviso y debe recargar; no se pierden cambios en silencio.
- Las importaciones corren en una sola transacción: o entra todo el archivo o nada.
- Bitácora de cambios (quién, cuándo, qué) en *Parámetros › Ver bitácora* y en Administración.

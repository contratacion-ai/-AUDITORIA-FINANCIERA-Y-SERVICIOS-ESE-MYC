"""Respaldo de la base SQLite, los archivos cargados y el .env, sin depender de Django ni de tareas programadas.

Lo usan `manage.py servir --respaldo-diario` (instalación sin administrador: un hilo del propio servidor respalda
una vez cada 24 horas, sin pedir permisos ni usar el Programador de tareas) y el acceso «Respaldar ahora».
"""
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

DIAS = 30
FORMATO = "%Y%m%d_%H%M"


def ultimo_respaldo(datos):
    """Fecha del respaldo más reciente en datos/respaldos, o None."""
    carpeta = Path(datos) / "respaldos"
    fechas = []
    if carpeta.is_dir():
        for d in carpeta.iterdir():
            try:
                fechas.append(datetime.strptime(d.name, FORMATO))
            except ValueError:
                continue
    return max(fechas) if fechas else None


def respaldar(base, datos=None, dias=DIAS):
    """Copia consistente de db.sqlite3 (API de respaldo de SQLite), media y .env; borra respaldos de más de `dias`."""
    base = Path(base)
    datos = Path(datos) if datos else base / "datos"
    carpeta = datos / "respaldos" / datetime.now().strftime(FORMATO)
    carpeta.mkdir(parents=True, exist_ok=True)
    db = datos / "db.sqlite3"
    if db.exists():
        origen, copia = sqlite3.connect(db), sqlite3.connect(carpeta / "db.sqlite3")
        with copia:
            origen.backup(copia)  # consistente aunque la aplicación esté en uso
        origen.close()
        copia.close()
    if (datos / "media").exists():
        # copyfile (sin metadatos): no depende de CopyFile2 de Windows, que algunos entornos no implementan
        shutil.copytree(datos / "media", carpeta / "media", dirs_exist_ok=True, copy_function=shutil.copyfile)
    if (base / ".env").exists():
        shutil.copyfile(base / ".env", carpeta / "env.txt")
    limite = time.time() - dias * 86400
    for viejo in (datos / "respaldos").iterdir():
        if viejo.is_dir() and viejo.stat().st_mtime < limite:
            shutil.rmtree(viejo, ignore_errors=True)
    return carpeta


def respaldo_pendiente(datos, horas=24, ahora=None):
    ultimo = ultimo_respaldo(datos)
    ahora = ahora or datetime.now()
    return ultimo is None or (ahora - ultimo).total_seconds() >= horas * 3600


def iniciar_respaldo_diario(base, datos, aviso=print, intervalo=3600):
    """Hilo en segundo plano: cada hora revisa si pasaron 24 h desde el último respaldo y, si es así, respalda."""

    def ciclo():
        while True:
            try:
                if respaldo_pendiente(datos):
                    aviso(f"{datetime.now():%Y-%m-%d %H:%M} respaldo en {respaldar(base, datos)}")
            except Exception as exc:  # noqa: BLE001 - un fallo de respaldo nunca detiene el servidor
                aviso(f"{datetime.now():%Y-%m-%d %H:%M} no se pudo respaldar: {exc}")
            time.sleep(intervalo)

    hilo = threading.Thread(target=ciclo, name="respaldo-diario", daemon=True)
    hilo.start()
    return hilo

"""Respaldo diario de Costos ABC en Windows: base SQLite, archivos cargados y .env. Conserva 30 días.

Uso: python respaldar.py C:\\CostosABC   (lo ejecuta la tarea programada CostosABC-Respaldo a las 23:00).
Con PostgreSQL agregue un pg_dump programado aparte.
"""
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

DIAS = 30


def main(destino):
    destino = Path(destino)
    datos = destino / "datos"
    carpeta = datos / "respaldos" / datetime.now().strftime("%Y%m%d_%H%M")
    carpeta.mkdir(parents=True, exist_ok=True)
    base = datos / "db.sqlite3"
    if base.exists():
        origen, copia = sqlite3.connect(base), sqlite3.connect(carpeta / "db.sqlite3")
        with copia:
            origen.backup(copia)  # copia consistente aunque la aplicación esté en uso
        origen.close()
        copia.close()
    if (datos / "media").exists():
        shutil.copytree(datos / "media", carpeta / "media", dirs_exist_ok=True)
    if (destino / ".env").exists():
        shutil.copy2(destino / ".env", carpeta / "env.txt")
    limite = time.time() - DIAS * 86400
    for viejo in (datos / "respaldos").iterdir():
        if viejo.is_dir() and viejo.stat().st_mtime < limite:
            shutil.rmtree(viejo, ignore_errors=True)
    print(f"{datetime.now():%Y-%m-%d %H:%M} respaldo en {carpeta}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[2])

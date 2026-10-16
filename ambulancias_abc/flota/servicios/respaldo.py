"""Respaldo de la base de datos con retención.

* SQLite: API de respaldo en caliente de sqlite3 (consistente aunque haya
  usuarios trabajando; no copia el archivo a mano).
* PostgreSQL: pg_dump en formato custom (debe estar en el PATH).
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import subprocess
from pathlib import Path

from django.conf import settings
from django.db import connection


def respaldar(destino: Path | None = None, retencion: int | None = None) -> Path:
    destino = Path(destino or settings.BACKUP_DIR)
    destino.mkdir(parents=True, exist_ok=True)
    retencion = settings.BACKUP_RETENCION if retencion is None else retencion
    sello = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    db = settings.DATABASES["default"]

    if connection.vendor == "sqlite":
        archivo = destino / f"ambulancias_abc_{sello}.sqlite3"
        connection.ensure_connection()
        copia = sqlite3.connect(archivo)
        with copia:
            connection.connection.backup(copia)
        copia.close()
        patron = "ambulancias_abc_*.sqlite3"
    elif connection.vendor == "postgresql":
        archivo = destino / f"ambulancias_abc_{sello}.dump"
        env = dict(os.environ, PGPASSWORD=db.get("PASSWORD", ""))
        subprocess.run(
            ["pg_dump", "-Fc", "-h", db["HOST"], "-p", str(db["PORT"]), "-U", db["USER"], "-f", str(archivo), db["NAME"]],
            check=True,
            env=env,
        )
        patron = "ambulancias_abc_*.dump"
    else:
        raise RuntimeError(f"Motor no soportado para respaldo: {connection.vendor}")

    if retencion > 0:
        antiguos = sorted(destino.glob(patron))[:-retencion]
        for viejo in antiguos:
            viejo.unlink(missing_ok=True)
    return archivo

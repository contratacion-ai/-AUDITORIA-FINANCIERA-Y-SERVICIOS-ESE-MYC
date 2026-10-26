"""Respaldo de Costos ABC en Windows: base SQLite, archivos cargados y .env. Conserva 30 días.

Uso: python respaldar.py C:\\CostosABC   (acceso «Respaldar ahora»; en el modo servidor, la tarea CostosABC-Respaldo).
En la instalación sin administrador el respaldo diario lo hace el propio servidor (manage.py servir --respaldo-diario).
Con PostgreSQL agregue un pg_dump programado aparte.
"""
import sys
from datetime import datetime
from pathlib import Path


def main(destino):
    destino = Path(destino)
    sys.path.insert(0, str(destino))
    from core.respaldo import respaldar

    carpeta = respaldar(destino, destino / "datos")
    print(f"{datetime.now():%Y-%m-%d %H:%M} respaldo en {carpeta}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[2])

"""Cierra el servidor local de la aplicación."""
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entorno  # noqa: E402

try:
    req = urllib.request.Request(f"http://127.0.0.1:{entorno.PUERTO}/api/apagar", data=b"", method="POST")
    urllib.request.urlopen(req, timeout=3)
    entorno.mensaje("Auditoría RIPS", "La aplicación se cerró.")
except Exception:
    entorno.mensaje("Auditoría RIPS", "La aplicación no estaba abierta.")

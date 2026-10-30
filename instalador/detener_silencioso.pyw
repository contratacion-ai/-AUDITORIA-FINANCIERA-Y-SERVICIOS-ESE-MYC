"""Cierra el servidor local sin mostrar mensajes (lo usa el instalador antes de actualizar)."""
import urllib.request

try:
    urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8765/api/apagar", data=b"", method="POST"), timeout=2)
except Exception:
    pass

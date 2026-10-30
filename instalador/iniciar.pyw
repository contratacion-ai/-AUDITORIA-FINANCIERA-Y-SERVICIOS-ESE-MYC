"""Inicia la aplicación (servidor local 127.0.0.1) y abre el navegador. Sin consola."""
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entorno  # noqa: E402

URL = f"http://127.0.0.1:{entorno.PUERTO}/"


def activo() -> bool:
    try:
        with urllib.request.urlopen(URL + "api/salud", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def abrir_cuando_listo() -> None:
    for _ in range(120):
        if activo():
            webbrowser.open(URL)
            return
        time.sleep(0.5)


if __name__ == "__main__":
    entorno.preparar()
    if activo():                      # ya está corriendo: solo abre el navegador
        webbrowser.open(URL)
        sys.exit(0)
    entorno.registro()
    print(f"--- inicio {time.strftime('%Y-%m-%d %H:%M:%S')} · datos en {entorno.DATOS}")
    threading.Thread(target=abrir_cuando_listo, daemon=True).start()
    try:
        import uvicorn
        uvicorn.run("api.app:app", host="127.0.0.1", port=entorno.PUERTO, log_config=None)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        entorno.mensaje("Auditoría RIPS", f"No se pudo iniciar la aplicación:\n{e}\n\nVer {entorno.LOCAL / 'registro.log'}")

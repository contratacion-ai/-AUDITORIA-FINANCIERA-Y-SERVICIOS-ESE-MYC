"""Inicia la ventana de cargue: ``python -m rips_auditoria.web``.

Usa el primer puerto libre entre 8100 y 8110 (el aplicativo de auditoría usa
8000-8010). Si la ventana ya está abierta, sólo abre el navegador.
"""

import json
import os
import socket
import sys
import threading
import urllib.request
import webbrowser

PUERTOS = range(int(os.environ.get("RIPS_PUERTO", "8100")), 8111)


def _nuestro(puerto: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/api/estado", timeout=2) as r:
            return "carpeta_datos" in json.load(r)
    except Exception:
        return False


def _libre(puerto: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", puerto))
            return True
        except OSError:
            return False


def main() -> int:
    for puerto in PUERTOS:
        if _nuestro(puerto):
            webbrowser.open(f"http://localhost:{puerto}")
            return 0
        if _libre(puerto):
            break
    else:
        print("No hay un puerto libre entre 8100 y 8110. Cierre otros programas y vuelva a intentar.")
        return 1
    url = f"http://localhost:{puerto}"
    print("=" * 66, "  CARGUE DE RIPS (Res. 3374 de 2000) · ESE MORENO Y CLAVIJO",
          f"  Ventana de cargue en: {url}", "  NO cierre esta ventana mientras use el cargue.", "=" * 66, sep="\n")
    if "--sin-navegador" not in sys.argv:
        threading.Timer(2.0, webbrowser.open, args=(url,)).start()
    import uvicorn

    uvicorn.run("rips_auditoria.web.servidor:app", host="127.0.0.1", port=puerto, log_level="warning")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # mostrar el error antes de que se cierre la consola
        print(f"\n[!] Error al iniciar: {type(exc).__name__}: {exc}")
        input("Presione Enter para cerrar…")
        sys.exit(1)

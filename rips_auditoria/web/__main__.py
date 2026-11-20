"""Inicia la ventana de cargue: ``python -m rips_auditoria.web [--red] [--sin-navegador]``.

Usa el primer puerto libre entre 8100 y 8110 (el aplicativo de auditoría usa
8000-8010). Si la ventana ya está abierta, sólo abre el navegador.

``--red`` (o ``red.habilitada: true`` / ``RIPS_RED=1``): atiende también a los
demás equipos de la red con clave de acceso y publica el puente hacia el
aplicativo de auditoría en ``http://<IP>:8000`` (ver ``red.py``).
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


def _libre(puerto: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        try:
            s.bind((host, puerto))
            return True
        except OSError:
            return False


def main() -> int:
    from ..config import cargar_configuracion
    from . import red

    cfg = cargar_configuracion()
    en_red = "--red" in sys.argv or red.red_habilitada(cfg)
    # Modo red: todas las interfaces; los equipos remotos deben dar la clave (red.ProteccionRed).
    host = "0.0.0.0" if en_red else "127.0.0.1"  # noqa: S104
    for puerto in PUERTOS:
        if _nuestro(puerto):
            if en_red:
                print("La ventana de cargue ya está abierta. Si la abrió sin «en red», ciérrela y vuelva a abrir "
                      "«Cargue RIPS ESE MyC (en red)».")
            webbrowser.open(f"http://localhost:{puerto}")
            return 0
        if _libre(puerto, host):
            break
    else:
        print("No hay un puerto libre entre 8100 y 8110. Cierre otros programas y vuelva a intentar.")
        return 1
    url = f"http://localhost:{puerto}"
    lineas = ["=" * 70, "  CARGUE DE RIPS Y TABLERO DE ANALISIS · ESE MORENO Y CLAVIJO",
              f"  En este equipo:        {url}   (tablero: {url}/tablero)"]
    if en_red:
        os.environ["RIPS_RED"] = "1"
        ips = red.ips_locales()
        rc = cfg.get("red") or {}
        puerto_app = int(rc.get("puerto_auditoria") or 8000)
        puente = (red.iniciar_puente(f"http://127.0.0.1:{puerto_app}", puerto_app, ips)
                  if rc.get("puente_auditoria", True) else None)
        lineas += ["  EN LA RED (otros computadores):"] + [f"    Cargue y tablero:    http://{ip}:{puerto}" for ip in ips]
        lineas += [f"    Auditoria (v5):      {u}" for u in (puente or [])]
        lineas += [f"  CLAVE DE ACCESO:       {red.clave_acceso(cfg)}",
                   "  Si otro equipo no conecta, permita el acceso en el Firewall de Windows",
                   "  (menú Inicio > ESE Moreno y Clavijo > Permitir acceso en red)."]
        if not ips:
            lineas.append("  [!] No se encontró una dirección de red en este equipo.")
    lineas += ["  NO cierre esta ventana mientras use el cargue.", "=" * 70]
    print(*lineas, sep="\n")
    if "--sin-navegador" not in sys.argv:
        threading.Timer(2.0, webbrowser.open, args=(url,)).start()
    import uvicorn

    uvicorn.run("rips_auditoria.web.servidor:app", host=host, port=puerto, log_level="warning")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # mostrar el error antes de que se cierre la consola
        print(f"\n[!] Error al iniciar: {type(exc).__name__}: {exc}")
        input("Presione Enter para cerrar…")
        sys.exit(1)

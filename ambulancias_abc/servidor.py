"""Inicia el servidor de producción (waitress) para la red local.

Uso:  python servidor.py            (lee puerto e hilos de ambulancias.ini)
      python servidor.py --puerto 8091

Puerto por defecto 8090 (el 8000 suele estar ocupado por otras aplicaciones
Django del mismo equipo, como la de auditoría de facturación).

Waitress es un servidor WSGI en Python puro que funciona en Windows sin
compiladores ni IIS. Todos los equipos de la red entran con el navegador a
http://<nombre-o-IP-del-servidor>:<puerto>/
"""

import argparse
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.chdir(BASE)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def main():
    from config.settings import cfg

    p = argparse.ArgumentParser()
    p.add_argument("--puerto", type=int, default=int(cfg("puerto", "8090")))
    p.add_argument("--host", default=cfg("host", "0.0.0.0"))
    p.add_argument("--hilos", type=int, default=int(cfg("hilos", "12")))
    a = p.parse_args()

    import socket

    from waitress import serve

    from config.wsgi import application

    # Si otra aplicación ya atiende el puerto, no arrancar: el navegador
    # abriría esa aplicación en lugar de esta.
    with socket.socket() as s:
        s.settimeout(1)
        if s.connect_ex(("127.0.0.1", a.puerto)) == 0:
            raise SystemExit(
                f"El puerto {a.puerto} ya lo usa otra aplicación. Cambie 'puerto' en ambulancias.ini "
                "o ejecute de nuevo scripts\\configurar.ps1 para elegir uno libre."
            )

    print(f"Costeo ABC Ambulancias escuchando en http://{a.host}:{a.puerto}/ ({a.hilos} hilos)")
    serve(application, host=a.host, port=a.puerto, threads=a.hilos, ident="CosteoABC")


if __name__ == "__main__":
    main()

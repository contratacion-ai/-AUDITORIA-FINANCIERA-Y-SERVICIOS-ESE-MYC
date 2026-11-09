"""Lanzador de escritorio de la herramienta de Auditoría ESE Moreno y Clavijo.

* No requiere permisos de administrador: todo vive en la carpeta del usuario.
* No genera conflictos con otras herramientas:
  - usa su propio Python y sus propias librerías (no toca el Python del sistema, el PATH ni el registro);
  - escucha solo en 127.0.0.1 (no se expone en la red ni pide abrir el firewall);
  - busca un puerto libre a partir del 8765 y nunca toma uno ocupado por otro programa;
  - si ya está abierta, reutiliza la instancia en lugar de iniciar otra.
* Se abre en el navegador predeterminado; la dirección se muestra para usarla en cualquier navegador
  (Chrome, Edge, Firefox, Brave, Opera...).

Uso:  python lanzador.py [--puerto 8765] [--sin-navegador] [--datos CARPETA]
"""

from __future__ import annotations

import argparse
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP = Path(__file__).resolve().parent.parent          # carpeta con manage.py
FIRMA_SALUD = "auditoria-ese-myc-ok"
PUERTO_INICIAL = 8765
INTENTOS_PUERTO = 100


def carpeta_datos_por_defecto() -> Path:
    if os.environ.get("AUDITORIA_ESE_DATOS"):
        return Path(os.environ["AUDITORIA_ESE_DATOS"])
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "AuditoriaESE" / "datos"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AuditoriaESE" / "datos"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "auditoria-ese" / "datos"


def es_nuestra_instancia(puerto: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/salud/", timeout=1.5) as r:
            return r.read().decode("utf-8", "ignore").strip() == FIRMA_SALUD
    except Exception:  # noqa: BLE001 - cualquier fallo significa "no es nuestra"
        return False


def puerto_libre(inicial: int) -> int:
    for puerto in range(inicial, inicial + INTENTOS_PUERTO):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", puerto))
                return puerto
            except OSError:
                continue
    raise SystemExit(f"No se encontró un puerto libre entre {inicial} y {inicial + INTENTOS_PUERTO - 1}")


def preparar_entorno(datos: Path) -> None:
    datos.mkdir(parents=True, exist_ok=True)
    archivo_clave = datos / "clave_secreta.txt"
    if not archivo_clave.exists():
        archivo_clave.write_text(secrets.token_urlsafe(50), encoding="utf-8")
    os.environ["DJANGO_SETTINGS_MODULE"] = "ese_auditoria.settings"
    os.environ["DJANGO_DEBUG"] = "0"
    os.environ["DJANGO_SECRET_KEY"] = archivo_clave.read_text(encoding="utf-8").strip()
    os.environ["DJANGO_DB_NAME"] = str(datos / "auditoria.sqlite3")
    os.environ["DJANGO_MEDIA_ROOT"] = str(datos / "archivos")
    os.environ["DJANGO_STATIC_ROOT"] = str(datos / "estaticos")
    os.environ["DJANGO_ALLOWED_HOSTS"] = "127.0.0.1,localhost"
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))


def abrir_navegador(url: str) -> None:
    try:
        webbrowser.open(url, new=2)
    except Exception:  # noqa: BLE001
        pass


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Inicia la herramienta de Auditoría ESE en el navegador")
    p.add_argument("--puerto", type=int, default=PUERTO_INICIAL, help="Puerto inicial a probar (por defecto 8765)")
    p.add_argument("--sin-navegador", action="store_true", help="No abrir el navegador automáticamente")
    p.add_argument("--datos", type=Path, default=None, help="Carpeta de datos (base de datos y archivos cargados)")
    args = p.parse_args(argv)

    datos = args.datos or carpeta_datos_por_defecto()
    archivo_puerto = datos / "puerto_en_uso.txt"

    # ¿Ya hay una instancia abierta? Reutilizarla.
    if archivo_puerto.exists():
        try:
            anterior = int(archivo_puerto.read_text().strip())
        except ValueError:
            anterior = None
        if anterior and es_nuestra_instancia(anterior):
            url = f"http://127.0.0.1:{anterior}/"
            print(f"La herramienta ya está abierta en {url}")
            if not args.sin_navegador:
                abrir_navegador(url)
            return 0

    preparar_entorno(datos)
    import django
    from django.core.management import call_command

    django.setup()
    print("Preparando la base de datos...")
    call_command("migrate", interactive=False, verbosity=0)
    call_command("collectstatic", interactive=False, verbosity=0)

    puerto = puerto_libre(args.puerto)
    url = f"http://127.0.0.1:{puerto}/"
    archivo_puerto.write_text(str(puerto), encoding="utf-8")

    print("=" * 70)
    print(" AUDITORÍA FINANCIERA Y DE SERVICIOS – ESE MORENO Y CLAVIJO")
    print("=" * 70)
    print(f" Abra en cualquier navegador:  {url}")
    print(f" Datos guardados en:           {datos}")
    print(" Para cerrar la herramienta cierre esta ventana (o presione Ctrl+C).")
    print("=" * 70)

    if not args.sin_navegador:
        def esperar_y_abrir():
            for _ in range(60):
                if es_nuestra_instancia(puerto):
                    break
                time.sleep(0.5)
            abrir_navegador(url)

        threading.Thread(target=esperar_y_abrir, daemon=True).start()

    from django.core.wsgi import get_wsgi_application
    from waitress import serve

    try:
        serve(get_wsgi_application(), host="127.0.0.1", port=puerto, threads=8,
              max_request_body_size=2 * 1024 ** 3, channel_timeout=600, ident="AuditoriaESE")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if archivo_puerto.read_text().strip() == str(puerto):
                archivo_puerto.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

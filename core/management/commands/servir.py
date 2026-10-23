import os

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Inicia el servidor de producción (Waitress; funciona en Windows y Linux)."

    def add_arguments(self, parser):
        parser.add_argument("--host", default=os.environ.get("ABC_HOST", "0.0.0.0"))
        parser.add_argument("--puerto", type=int, default=int(os.environ.get("ABC_PUERTO", "8000")))
        parser.add_argument("--hilos", type=int, default=int(os.environ.get("ABC_HILOS", "8")))

    def handle(self, *args, **o):
        from waitress import serve

        from abc_salud.wsgi import application

        datos = settings.DATOS_DIR
        if datos.is_dir():  # el instalador de Windows usa este PID para detener el servidor al actualizar
            (datos / "servidor.pid").write_text(str(os.getpid()))
        self.stdout.write(f"Costos ABC en http://{o['host']}:{o['puerto']} (DEBUG={settings.DEBUG})")
        opciones = {"host": o["host"], "port": o["puerto"], "threads": o["hilos"],
                    "max_request_body_size": (settings.CARGA_MAX_MB + 1) * 1024 * 1024}
        if os.environ.get("ABC_PROXY_CONFIABLE"):  # IP del proxy HTTPS que reenvía X-Forwarded-Proto
            opciones.update(trusted_proxy=os.environ["ABC_PROXY_CONFIABLE"], trusted_proxy_headers={"x-forwarded-proto"})
        serve(application, **opciones)

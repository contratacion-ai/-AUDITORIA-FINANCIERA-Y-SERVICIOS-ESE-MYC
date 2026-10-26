import os

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Inicia el servidor de producción (Waitress; funciona en Windows y Linux)."

    def add_arguments(self, parser):
        parser.add_argument("--host", default=os.environ.get("ABC_HOST", "0.0.0.0"))
        parser.add_argument("--puerto", type=int, default=int(os.environ.get("ABC_PUERTO", "8000")))
        parser.add_argument("--hilos", type=int, default=int(os.environ.get("ABC_HILOS", "8")))
        parser.add_argument("--respaldo-diario", action="store_true",
                            help="Respalda la base y los archivos una vez cada 24 h desde el propio servidor "
                                 "(instalación sin administrador: no usa el Programador de tareas).")

    def handle(self, *args, **o):
        from waitress import serve

        from abc_salud.wsgi import application

        datos = settings.DATOS_DIR
        if datos.is_dir():  # el instalador de Windows usa este PID para detener el servidor al actualizar
            (datos / "servidor.pid").write_text(str(os.getpid()))
        if o["respaldo_diario"] and "sqlite" in settings.DATABASES["default"]["ENGINE"]:
            from core.respaldo import iniciar_respaldo_diario

            iniciar_respaldo_diario(settings.BASE_DIR, datos, aviso=lambda m: self.stdout.write(m))
        self.stdout.write(f"Costos ABC en http://{o['host']}:{o['puerto']} (DEBUG={settings.DEBUG})")
        opciones = {"host": o["host"], "port": o["puerto"], "threads": o["hilos"],
                    "max_request_body_size": (settings.CARGA_MAX_MB + 1) * 1024 * 1024}
        if os.environ.get("ABC_PROXY_CONFIABLE"):  # IP del proxy HTTPS que reenvía X-Forwarded-Proto
            opciones.update(trusted_proxy=os.environ["ABC_PROXY_CONFIABLE"], trusted_proxy_headers={"x-forwarded-proto"})
        serve(application, **opciones)

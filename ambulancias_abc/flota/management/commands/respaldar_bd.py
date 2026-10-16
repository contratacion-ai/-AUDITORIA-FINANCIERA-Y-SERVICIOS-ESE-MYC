from django.core.management.base import BaseCommand

from flota.servicios.respaldo import respaldar


class Command(BaseCommand):
    help = "Crea un respaldo de la base de datos en la carpeta de respaldos (con retención)."

    def add_arguments(self, parser):
        parser.add_argument("--destino", default=None)
        parser.add_argument("--retencion", type=int, default=None, help="Número de respaldos a conservar")

    def handle(self, *args, destino, retencion, **opts):
        archivo = respaldar(destino, retencion)
        self.stdout.write(self.style.SUCCESS(f"Respaldo creado: {archivo}"))

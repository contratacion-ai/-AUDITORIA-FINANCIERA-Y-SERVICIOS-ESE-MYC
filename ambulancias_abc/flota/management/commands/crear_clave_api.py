from django.core.management.base import BaseCommand

from flota.api import crear_clave


class Command(BaseCommand):
    help = "Crea una clave de API para la aplicación de RIPS o de auditoría. La clave se muestra una sola vez."

    def add_arguments(self, parser):
        parser.add_argument("nombre")
        parser.add_argument("--escritura", action="store_true", help="Permite importar RIPS por la API")

    def handle(self, *args, nombre, escritura, **opts):
        _, clave = crear_clave(nombre, escritura)
        self.stdout.write(self.style.SUCCESS(f"Clave para '{nombre}' (guárdela, no se vuelve a mostrar):"))
        self.stdout.write(clave)

from pathlib import Path

from django.core.management.base import BaseCommand

from cargas.plantilla import generar_plantilla


class Command(BaseCommand):
    help = "Genera el archivo de la plantilla estándar de carga mensual."

    def add_arguments(self, parser):
        parser.add_argument("--salida", default="plantillas/Plantilla_Carga_Mensual_ABC.xlsx")

    def handle(self, *args, **o):
        ruta = Path(o["salida"])
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(generar_plantilla())
        self.stdout.write(self.style.SUCCESS(f"Plantilla generada en {ruta}"))

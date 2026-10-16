from django.core.management.base import BaseCommand

from flota.servicios.datos_iniciales import cargar


class Command(BaseCommand):
    help = "Carga sedes, flota, perfiles del estudio, parámetros, tarifas del Anexo 3 y partidas del centro de costos."

    def add_arguments(self, parser):
        parser.add_argument("--sin-tarifas", action="store_true", help="No cargar las tarifas incluidas")
        parser.add_argument("--sin-ceco", action="store_true", help="No cargar las partidas del centro de costos")

    def handle(self, *args, **opts):
        resumen = cargar(incluir_tarifas=not opts["sin_tarifas"], incluir_ceco=not opts["sin_ceco"])
        for k, v in resumen.items():
            self.stdout.write(f"  {k}: {v}")
        self.stdout.write(self.style.SUCCESS("Datos iniciales cargados"))

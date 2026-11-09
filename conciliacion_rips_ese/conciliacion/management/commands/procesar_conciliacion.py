"""Procesa una conciliación desde consola (útil para paquetes grandes).

    python manage.py procesar_conciliacion 3 --hallazgos
"""

from django.core.management.base import BaseCommand, CommandError

from conciliacion import servicios
from conciliacion.models import Conciliacion
from rips_conciliacion.reportes import texto_reporte_general


class Command(BaseCommand):
    help = "Procesa una conciliación Facturación vs RIPS ya creada con sus archivos cargados"

    def add_arguments(self, parser):
        parser.add_argument("conciliacion_id", type=int)
        parser.add_argument("--hallazgos", action="store_true", help="Generar/actualizar hallazgos de la auditoría")

    def handle(self, *args, **opciones):
        try:
            c = Conciliacion.objects.get(pk=opciones["conciliacion_id"])
        except Conciliacion.DoesNotExist as exc:
            raise CommandError("La conciliación no existe") from exc
        resultado = servicios.procesar(c)
        self.stdout.write(texto_reporte_general(resultado))
        if opciones["hallazgos"]:
            r = servicios.generar_hallazgos(c)
            self.stdout.write(self.style.SUCCESS(f"Hallazgos: {r}"))

"""Crea una auditoría de demostración con los archivos de ``ejemplos/`` y la procesa."""

from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand

from auditoria.models import Auditoria
from conciliacion import servicios
from conciliacion.models import ArchivoCargado, Conciliacion


class Command(BaseCommand):
    help = "Carga datos de ejemplo (ZIP 3374, RAR 2275, facturación CSV) y procesa la conciliación"

    def handle(self, *args, **opciones):
        ejemplos = Path(settings.BASE_DIR) / "ejemplos"
        auditoria = Auditoria.objects.create(nombre="DEMO – Auditoría facturación vs RIPS 2024", entidad="EPS PRUEBA",
                                             responsable="Oficina de auditoría")
        c = Conciliacion.objects.create(auditoria=auditoria, nombre="DEMO enero-febrero 2024")

        def archivo(nombre):
            return SimpleUploadedFile(nombre, (ejemplos / nombre).read_bytes())

        servicios.cargar_archivos(c, ArchivoCargado.Tipo.RIPS,
                                  [archivo("RIPS_SEDE_TAME.zip"), archivo("RIPS_SEDE_TAME_FEB.rar")])
        servicios.cargar_archivos(c, ArchivoCargado.Tipo.FACTURACION, [archivo("facturacion_por_ambito.csv")])
        servicios.cargar_archivos(c, ArchivoCargado.Tipo.CATALOGO, [archivo("catalogo_sedes.csv")])
        servicios.procesar(c)
        r = servicios.generar_hallazgos(c)
        self.stdout.write(self.style.SUCCESS(
            f"Demo lista: auditoría {auditoria.pk}, conciliación {c.pk}, hallazgos {r}. "
            f"Abra http://127.0.0.1:8000/conciliacion/{c.pk}/tablero/"))

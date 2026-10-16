from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from flota.servicios import importar


class Command(BaseCommand):
    help = "Importa un archivo: tarifas (Anexo 3), facturacion (HIS), rips (JSON), consumos (CSV) o ceco (COSTOS AMBULANCIAS)."

    def add_arguments(self, parser):
        parser.add_argument("tipo", choices=["tarifas", "facturacion", "rips", "consumos", "ceco"])
        parser.add_argument("archivo")
        parser.add_argument("--vigencia", type=int, default=2026)

    def handle(self, *args, tipo, archivo, vigencia, **opts):
        ruta = Path(archivo)
        if not ruta.exists():
            raise CommandError(f"No existe {ruta}")
        if tipo == "tarifas":
            lote = importar.importar_tarifas(str(ruta), vigencia=vigencia, nombre=ruta.name)
        elif tipo == "facturacion":
            lote = importar.importar_facturacion(str(ruta), nombre=ruta.name)
        elif tipo == "ceco":
            lote = importar.importar_costos_ceco(str(ruta), nombre=ruta.name)
        elif tipo == "rips":
            lote = importar.importar_rips(ruta.read_bytes(), nombre=ruta.name)
        else:
            with ruta.open("rb") as f:
                lote = importar.importar_consumos_csv(f, nombre=ruta.name)
        self.stdout.write(
            self.style.SUCCESS(
                f"{lote.tipo}: {lote.nuevos} nuevos, {lote.actualizados} actualizados/conciliados, "
                f"{lote.omitidos} omitidos, {lote.hallazgos} hallazgos nuevos. {lote.resumen}"
            )
        )

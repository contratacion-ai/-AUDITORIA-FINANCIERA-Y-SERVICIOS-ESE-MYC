from django.core.management.base import BaseCommand, CommandError

from core.models import Institucion
from modelo.importar_maestro import ErrorImportacion, importar_maestro


class Command(BaseCommand):
    help = ("Importa el maestro de homologación CECO (estado financiero → resolución): sedes, centros por llave CECO|SEDE, "
            "homologación de códigos del software contable, reglas y cuentas institucionales.")

    def add_arguments(self, parser):
        parser.add_argument("institucion", help="Código de la institución")
        parser.add_argument("--maestro", help="Archivo HOMOLOGACION_CECO_EF_vs_RESOLUCION (por defecto, el incluido)")
        parser.add_argument("--sin-cuentas", action="store_true", help="No cargar las cuentas institucionales → recurso")
        parser.add_argument("--conservar-base", action="store_true", help="No desactivar los centros base (sin sede)")

    def handle(self, *args, **o):
        inst = Institucion.objects.filter(codigo=o["institucion"]).first()
        if inst is None:
            raise CommandError(f"No existe la institución {o['institucion']}.")
        kwargs = {"ruta": o["maestro"], "desactivar_base": not o["conservar_base"]}
        if o["sin_cuentas"]:
            kwargs["cuentas"] = None
        try:
            resumen = importar_maestro(inst, **kwargs)
        except ErrorImportacion as exc:
            raise CommandError(str(exc)) from exc
        for k, v in resumen.items():
            self.stdout.write(f"  {k}: {v}")

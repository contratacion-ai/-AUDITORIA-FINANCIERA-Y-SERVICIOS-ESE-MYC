from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core.models import Institucion, Membresia, Rol
from modelo.catalogo_base import cargar_catalogo_base


class Command(BaseCommand):
    help = "Crea una institución, opcionalmente con el catálogo base Res. 002/2014 y un administrador."

    def add_arguments(self, parser):
        parser.add_argument("codigo")
        parser.add_argument("nombre")
        parser.add_argument("--nit", default="")
        parser.add_argument("--municipio", default="")
        parser.add_argument("--departamento", default="")
        parser.add_argument("--catalogo-base", action="store_true")
        parser.add_argument("--admin", help="Usuario existente que será administrador de la institución.")

    def handle(self, *args, **o):
        inst, creada = Institucion.objects.get_or_create(codigo=o["codigo"], defaults={
            "nombre": o["nombre"], "nit": o["nit"], "municipio": o["municipio"], "departamento": o["departamento"]})
        self.stdout.write(("Creada " if creada else "Ya existía ") + str(inst))
        if o["catalogo_base"]:
            self.stdout.write(str(cargar_catalogo_base(inst)))
        if o["admin"]:
            u = get_user_model().objects.filter(username=o["admin"]).first()
            if u is None:
                raise CommandError(f"El usuario {o['admin']} no existe (créelo con createsuperuser o desde Usuarios y roles).")
            Membresia.objects.update_or_create(usuario=u, institucion=inst, defaults={"rol": Rol.ADMIN, "activo": True})
            self.stdout.write(f"{u} es administrador de {inst}")

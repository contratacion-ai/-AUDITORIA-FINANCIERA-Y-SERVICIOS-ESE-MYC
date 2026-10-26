from django.core.management.base import BaseCommand, CommandError

from core import primer_acceso


class Command(BaseCommand):
    help = "Genera un nuevo enlace de un solo uso para que el administrador cree su contraseña en el navegador."

    def add_arguments(self, parser):
        parser.add_argument("--puerto", type=int, default=8000)
        parser.add_argument("--host", default="localhost")

    def handle(self, *args, **o):
        usuario = primer_acceso.pendiente()
        if usuario is None:
            raise CommandError("Todos los administradores ya tienen contraseña. Para cambiarla use: "
                               "python manage.py changepassword USUARIO")
        token = primer_acceso.generar()
        self.stdout.write(f"Administrador: {usuario.username}")
        self.stdout.write(primer_acceso.enlace(token, o["puerto"], o["host"]))

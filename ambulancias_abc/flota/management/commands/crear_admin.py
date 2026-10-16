import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Crea o actualiza el usuario administrador (la clave se lee de ABC_ADMIN_CLAVE o --clave)."

    def add_arguments(self, parser):
        parser.add_argument("--usuario", default="admin")
        parser.add_argument("--clave", default=None)
        parser.add_argument("--correo", default="")
        parser.add_argument(
            "--si-no-existe", action="store_true",
            help="No hace nada si el usuario ya existe (para actualizaciones: no cambia la clave)",
        )

    def handle(self, *args, usuario, clave, correo, si_no_existe=False, **opts):
        U = get_user_model()
        if si_no_existe and U.objects.filter(username=usuario).exists():
            self.stdout.write(f"EXISTE: el usuario {usuario} ya existe; no se cambia su clave")
            return
        clave = clave or os.environ.get("ABC_ADMIN_CLAVE")
        if not clave or len(clave) < 8:
            raise CommandError("Indique una clave de al menos 8 caracteres (--clave o ABC_ADMIN_CLAVE)")
        u, creado = U.objects.get_or_create(username=usuario, defaults={"email": correo})
        u.is_staff = u.is_superuser = True
        u.set_password(clave)
        u.save()
        grupo = Group.objects.filter(name="Administrador ABC").first()
        if grupo:
            u.groups.add(grupo)
        self.stdout.write(f"{'CREADO' if creado else 'ACTUALIZADO'}: usuario {usuario}")

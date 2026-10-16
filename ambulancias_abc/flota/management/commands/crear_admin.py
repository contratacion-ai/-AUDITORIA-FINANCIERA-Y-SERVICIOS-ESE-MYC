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

    def handle(self, *args, usuario, clave, correo, **opts):
        clave = clave or os.environ.get("ABC_ADMIN_CLAVE")
        if not clave or len(clave) < 8:
            raise CommandError("Indique una clave de al menos 8 caracteres (--clave o ABC_ADMIN_CLAVE)")
        U = get_user_model()
        u, creado = U.objects.get_or_create(username=usuario, defaults={"email": correo})
        u.is_staff = u.is_superuser = True
        u.set_password(clave)
        u.save()
        grupo = Group.objects.filter(name="Administrador ABC").first()
        if grupo:
            u.groups.add(grupo)
        self.stdout.write(self.style.SUCCESS(f"Usuario {usuario} {'creado' if creado else 'actualizado'}"))

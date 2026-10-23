"""Configuración inicial idempotente usada por los instaladores de Windows y Linux."""
import os

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core import primer_acceso
from core.models import Institucion, Membresia, Rol
from modelo.catalogo_base import cargar_catalogo_base


class Command(BaseCommand):
    help = "Migra la base de datos, publica archivos estáticos, crea el administrador y la institución con el catálogo base."

    def add_arguments(self, parser):
        parser.add_argument("--institucion-codigo", required=True)
        parser.add_argument("--institucion-nombre", required=True)
        parser.add_argument("--nit", default="")
        parser.add_argument("--municipio", default="")
        parser.add_argument("--departamento", default="")
        parser.add_argument("--usuario", required=True, help="Usuario administrador.")
        parser.add_argument("--correo", default="")
        parser.add_argument("--password-env", default="ABC_ADMIN_PASSWORD",
                            help="Variable de entorno con la contraseña (opcional). Sin ella el administrador la crea "
                                 "en el navegador con el enlace de primer acceso.")
        parser.add_argument("--puerto", type=int, default=int(os.environ.get("ABC_PUERTO", "8000")))
        parser.add_argument("--demo", action="store_true", help="Además crea la institución de demostración (datos sintéticos).")

    def handle(self, *args, **o):
        clave = os.environ.get(o["password_env"], "")
        call_command("migrate", interactive=False, verbosity=0)
        self.stdout.write("[OK] Base de datos lista")
        call_command("collectstatic", interactive=False, verbosity=0)
        self.stdout.write("[OK] Archivos estáticos publicados")
        User = get_user_model()
        with transaction.atomic():
            usuario = User.objects.filter(username=o["usuario"]).first()
            if usuario is None:
                if clave:
                    from django.contrib.auth.password_validation import validate_password

                    try:
                        validate_password(clave)
                    except Exception as exc:
                        raise CommandError("Contraseña no válida: " + " ".join(getattr(exc, "messages", [str(exc)]))) from exc
                usuario = User.objects.create_superuser(o["usuario"], o["correo"], clave or None)  # None = sin contraseña
                self.stdout.write(f"[OK] Administrador {usuario.username} creado")
            else:
                self.stdout.write(f"[--] El usuario {usuario.username} ya existía (no se cambió su contraseña)")
            inst, creada = Institucion.objects.get_or_create(codigo=o["institucion_codigo"], defaults={
                "nombre": o["institucion_nombre"], "nit": o["nit"], "municipio": o["municipio"],
                "departamento": o["departamento"]})
            if creada:
                cargar_catalogo_base(inst, usuario=usuario)
                self.stdout.write(f"[OK] Institución {inst.nombre} creada con el catálogo base Res. 002/2014")
            else:
                self.stdout.write(f"[--] La institución {inst.nombre} ya existía")
            Membresia.objects.update_or_create(usuario=usuario, institucion=inst, defaults={"rol": Rol.ADMIN, "activo": True})
        if o["demo"]:
            from core.demo import crear_demo

            demo, _ = crear_demo(usuario)
            Membresia.objects.update_or_create(usuario=usuario, institucion=demo, defaults={"rol": Rol.ADMIN, "activo": True})
            self.stdout.write("[OK] Institución de demostración creada")
        if primer_acceso.pendiente() is not None:
            token = primer_acceso.generar()
            self.stdout.write("[OK] Cree la contraseña del administrador abriendo en el servidor:")
            self.stdout.write("     " + primer_acceso.enlace(token, o["puerto"]))
        self.stdout.write(self.style.SUCCESS("Instalación completada."))

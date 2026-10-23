import os
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from core.demo import crear_demo
from core.models import Membresia, Rol


class Command(BaseCommand):
    help = "Crea la institución de demostración con datos SINTÉTICOS, usuarios por rol y ejecuta el motor."

    def add_arguments(self, parser):
        parser.add_argument("--password", default=os.environ.get("DEMO_PASSWORD"),
                            help="Contraseña de los usuarios demo (por defecto se genera una aleatoria).")

    def handle(self, *args, **opts):
        inst, corridas = crear_demo()
        clave = opts["password"] or secrets.token_urlsafe(10)
        User = get_user_model()
        for rol in Rol.values:
            username = f"{rol.lower()}_demo"
            u, creado = User.objects.get_or_create(username=username)
            if creado or opts["password"]:
                u.set_password(clave)
                u.save()
            Membresia.objects.update_or_create(usuario=u, institucion=inst, defaults={"rol": rol, "activo": True})
        for c in corridas:
            r = c.resumen
            self.stdout.write(f"{c.periodo}: entrada {r['total_entrada']} · finales {r['total_finales']} · "
                              f"pendiente {r['pendiente_asignacion']} · conservación {'OK' if r['conservacion_ok'] else 'ERROR'}")
        self.stdout.write(self.style.SUCCESS(
            f"Institución demo '{inst.nombre}' lista. Usuarios: " + ", ".join(f"{r.lower()}_demo" for r in Rol.values)
            + (f" · contraseña: {clave}" if not opts["password"] else " · contraseña: la indicada")))

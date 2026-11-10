from django.apps import AppConfig


class ConciliacionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "conciliacion"
    verbose_name = "Conciliación Facturación vs RIPS"

    def ready(self):
        from . import signals  # noqa: F401

"""Borra del disco los archivos cuando se eliminan sus registros (limpieza real)."""

from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import ArchivoCargado, Conciliacion


@receiver(post_delete, sender=ArchivoCargado)
def borrar_archivo_fisico(sender, instance, **kwargs):
    if instance.archivo:
        instance.archivo.delete(save=False)


@receiver(post_delete, sender=Conciliacion)
def borrar_resultados_fisicos(sender, instance, **kwargs):
    for campo in (instance.archivo_excel, instance.archivo_csv):
        if campo:
            campo.delete(save=False)

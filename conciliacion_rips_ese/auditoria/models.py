"""Herramienta de auditoría: encargos de auditoría y hallazgos.

La conciliación Facturación vs RIPS es uno de los procedimientos de una
auditoría: cada conciliación pertenece a una ``Auditoria`` y sus
diferencias pueden convertirse en ``Hallazgo`` para su gestión.
"""

from django.db import models
from django.urls import reverse


class Auditoria(models.Model):
    class Estado(models.TextChoices):
        PLANEADA = "PLANEADA", "Planeada"
        EN_EJECUCION = "EN_EJECUCION", "En ejecución"
        INFORME = "INFORME", "En elaboración de informe"
        CERRADA = "CERRADA", "Cerrada"

    nombre = models.CharField(max_length=200)
    tipo = models.CharField(max_length=100, default="Financiera y de servicios")
    periodo_inicio = models.DateField(null=True, blank=True)
    periodo_fin = models.DateField(null=True, blank=True)
    entidad = models.CharField("EPS / entidad auditada", max_length=200, blank=True)
    responsable = models.CharField(max_length=200, blank=True)
    objetivo = models.TextField(blank=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.EN_EJECUCION)
    creada = models.DateTimeField(auto_now_add=True)
    actualizada = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-creada"]
        verbose_name = "auditoría"
        verbose_name_plural = "auditorías"

    def __str__(self):
        return self.nombre

    def get_absolute_url(self):
        return reverse("auditoria:detalle", args=[self.pk])


class Hallazgo(models.Model):
    class Estado(models.TextChoices):
        ABIERTO = "ABIERTO", "Abierto"
        EN_GESTION = "EN_GESTION", "En gestión"
        SUBSANADO = "SUBSANADO", "Subsanado"
        CERRADO = "CERRADO", "Cerrado"
        DESCARTADO = "DESCARTADO", "Descartado"

    class Prioridad(models.TextChoices):
        ALTA = "ALTA", "Alta"
        MEDIA = "MEDIA", "Media"
        BAJA = "BAJA", "Baja"

    auditoria = models.ForeignKey(Auditoria, on_delete=models.CASCADE, related_name="hallazgos")
    conciliacion = models.ForeignKey(
        "conciliacion.Conciliacion", on_delete=models.SET_NULL, null=True, blank=True, related_name="hallazgos"
    )
    origen = models.CharField(max_length=50, default="MANUAL")
    tipo = models.CharField(max_length=60)
    titulo = models.CharField(max_length=250)
    descripcion = models.TextField(blank=True)
    factura = models.CharField(max_length=60, blank=True, db_index=True)
    sede = models.CharField(max_length=120, blank=True)
    hospital = models.CharField(max_length=120, blank=True)
    mes = models.CharField(max_length=10, blank=True)
    servicio = models.CharField(max_length=120, blank=True)
    codigos = models.TextField(blank=True)
    valor_diferencia = models.DecimalField(max_digits=26, decimal_places=6, default=0)
    cantidad_diferencia = models.DecimalField(max_digits=26, decimal_places=6, default=0)
    prioridad = models.CharField(max_length=10, choices=Prioridad.choices, default=Prioridad.MEDIA)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.ABIERTO)
    responsable = models.CharField(max_length=200, blank=True)
    accion_recomendada = models.TextField(blank=True)
    observaciones = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["estado", "-creado"]
        constraints = [
            models.UniqueConstraint(
                fields=["conciliacion", "factura", "tipo"],
                condition=models.Q(conciliacion__isnull=False),
                name="hallazgo_unico_por_factura_tipo",
            )
        ]

    def __str__(self):
        return self.titulo

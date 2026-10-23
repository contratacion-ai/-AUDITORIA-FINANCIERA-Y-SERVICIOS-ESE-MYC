from django.conf import settings
from django.db import models

from cargas.models import MovimientoContable
from core.models import Periodo, TenantModel
from modelo.models import CentroCosto, Cups, Inductor, Recurso, ReglaDistribucion


class Corrida(TenantModel):
    """Ejecución del modelo para un periodo (Generaciones → Distribución 1-4 del manual)."""

    class Estado(models.TextChoices):
        OK = "OK", "Completada"
        CON_PENDIENTES = "PENDIENTES", "Completada con pendientes"
        ERROR = "ERROR", "Error"

    periodo = models.ForeignKey(Periodo, on_delete=models.CASCADE, related_name="corridas")
    estado = models.CharField(max_length=12, choices=Estado.choices)
    vigente = models.BooleanField(default=True, help_text="Última corrida del periodo (la que usan tablero e informes).")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    inicio = models.DateTimeField(auto_now_add=True)
    fin = models.DateTimeField(null=True, blank=True)
    resumen = models.JSONField(default=dict, blank=True)
    huella_parametros = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-inicio"]

    def __str__(self):
        return f"Corrida #{self.pk} · {self.periodo} · {self.get_estado_display()}"


class Asignacion(TenantModel):
    """Una asignación de costo con su trazabilidad completa (origen, destino, inductor, tasa, movimiento fuente)."""

    class Metodo(models.TextChoices):
        DIRECTO = "DIRECTO", "Directo (el movimiento trae centro de costos)"
        MANO_OBRA = "MANO_OBRA", "Porcentaje de mano de obra (Empleado/OPS)"
        INDUCTOR = "INDUCTOR", "Inductor"

    corrida = models.ForeignKey(Corrida, on_delete=models.CASCADE, related_name="asignaciones")
    distribucion = models.PositiveSmallIntegerField(db_index=True)
    recurso = models.ForeignKey(Recurso, on_delete=models.PROTECT, related_name="+")
    centro_origen = models.ForeignKey(CentroCosto, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    centro_destino = models.ForeignKey(CentroCosto, on_delete=models.PROTECT, related_name="+")
    movimiento = models.ForeignKey(MovimientoContable, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    metodo = models.CharField(max_length=10, choices=Metodo.choices)
    regla = models.ForeignKey(ReglaDistribucion, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    inductor = models.ForeignKey(Inductor, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    base = models.DecimalField("valor a distribuir", max_digits=20, decimal_places=2)
    cantidad_inductor = models.DecimalField(max_digits=20, decimal_places=4, default=0)
    total_inductor = models.DecimalField(max_digits=20, decimal_places=4, default=0)
    tasa = models.DecimalField("costo por unidad de inductor", max_digits=24, decimal_places=6, default=0)
    ajuste_conciliacion = models.DecimalField(max_digits=18, decimal_places=2, default=0,
                                              help_text="Diferencia frente al estado financiero prorrateada en esta fila.")
    valor = models.DecimalField(max_digits=20, decimal_places=2)

    class Meta:
        indexes = [
            models.Index(fields=["corrida", "distribucion", "centro_destino"]),
            models.Index(fields=["corrida", "centro_origen"]),
        ]


class Pendiente(TenantModel):
    """Costo que no pudo asignarse (falta regla, inductor sin valores, centro final sin producción...)."""

    corrida = models.ForeignKey(Corrida, on_delete=models.CASCADE, related_name="pendientes")
    distribucion = models.PositiveSmallIntegerField()
    recurso = models.ForeignKey(Recurso, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    centro = models.ForeignKey(CentroCosto, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    movimiento = models.ForeignKey(MovimientoContable, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    valor = models.DecimalField(max_digits=20, decimal_places=2)
    motivo = models.CharField(max_length=300)


class CostoCups(TenantModel):
    """Costo por CUPS en cada centro final (producto de la 4ª distribución + producción)."""

    corrida = models.ForeignKey(Corrida, on_delete=models.CASCADE, related_name="costos_cups")
    centro = models.ForeignKey(CentroCosto, on_delete=models.PROTECT, related_name="+")
    cups = models.ForeignKey(Cups, on_delete=models.PROTECT, related_name="+")
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)
    peso_relativo = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    unidades_ponderadas = models.DecimalField(max_digits=20, decimal_places=4)
    costo_total = models.DecimalField(max_digits=20, decimal_places=2)
    costo_unitario = models.DecimalField(max_digits=20, decimal_places=2)
    costo_por_elemento = models.JSONField(default=dict, blank=True)
    fuente_produccion = models.CharField(max_length=12, default="PRODUCCION")

    class Meta:
        verbose_name = "costo por CUPS"
        verbose_name_plural = "costos por CUPS"
        indexes = [models.Index(fields=["corrida", "cups"])]

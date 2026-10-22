"""Núcleo multi-institución: instituciones, roles, periodos y bitácora de auditoría."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Institucion(models.Model):
    codigo = models.CharField("código (REPS / interno)", max_length=30, unique=True)
    nit = models.CharField("NIT", max_length=20, blank=True)
    nombre = models.CharField(max_length=200)
    pais = models.CharField("país", max_length=60, default="Colombia")
    departamento = models.CharField(max_length=80, blank=True)
    municipio = models.CharField(max_length=80, blank=True)
    moneda = models.CharField(max_length=3, default="COP")
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "institución"
        verbose_name_plural = "instituciones"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Rol(models.TextChoices):
    ADMIN = "ADMIN", "Administrador de la institución"
    FINANCIERO = "FINANCIERO", "Financiero"
    COSTOS = "COSTOS", "Costos"
    AUDITORIA = "AUDITORIA", "Auditoría"
    GERENCIA = "GERENCIA", "Gerencia"
    CONSULTOR = "CONSULTOR", "Consultor externo"


class Membresia(models.Model):
    """Asigna a un usuario una institución con un rol. Un consultor tiene varias membresías."""

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="membresias")
    institucion = models.ForeignKey(Institucion, on_delete=models.CASCADE, related_name="membresias")
    rol = models.CharField(max_length=12, choices=Rol.choices)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "membresía"
        unique_together = [("usuario", "institucion")]

    def __str__(self):
        return f"{self.usuario} · {self.institucion} · {self.get_rol_display()}"


class TenantQuerySet(models.QuerySet):
    def de(self, institucion):
        return self.filter(institucion=institucion)


class TenantModel(models.Model):
    """Base de todo dato que pertenece a una institución (aislamiento de datos)."""

    institucion = models.ForeignKey(Institucion, on_delete=models.CASCADE, related_name="+")

    objects = TenantQuerySet.as_manager()

    class Meta:
        abstract = True


class Periodo(TenantModel):
    class Estado(models.TextChoices):
        ABIERTO = "ABIERTO", "Abierto"
        CERRADO = "CERRADO", "Cerrado"

    anio = models.PositiveSmallIntegerField("año")
    mes = models.PositiveSmallIntegerField()
    estado = models.CharField(max_length=8, choices=Estado.choices, default=Estado.ABIERTO)
    cerrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cerrado_en = models.DateTimeField(null=True, blank=True)

    MESES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
             "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    MESES_CORTOS = ["", "ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]

    class Meta:
        unique_together = [("institucion", "anio", "mes")]
        ordering = ["-anio", "-mes"]

    def clean(self):
        if not 1 <= (self.mes or 0) <= 12:
            raise ValidationError({"mes": "El mes debe estar entre 1 y 12."})
        if not 2000 <= (self.anio or 0) <= 2100:
            raise ValidationError({"anio": "Año fuera de rango."})

    @property
    def cerrado(self):
        return self.estado == self.Estado.CERRADO

    @property
    def etiqueta(self):
        return f"{self.anio}-{self.mes:02d}"

    @property
    def etiqueta_corta(self):
        return f"{self.MESES_CORTOS[self.mes]}-{self.anio}"

    @property
    def nombre_mes(self):
        return self.MESES[self.mes]

    def __str__(self):
        return f"{self.nombre_mes} {self.anio}"


class Bitacora(models.Model):
    """Registro de auditoría de solo inserción (no se edita ni se borra desde la aplicación)."""

    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    institucion = models.ForeignKey(Institucion, null=True, blank=True, on_delete=models.SET_NULL)
    accion = models.CharField(max_length=40, db_index=True)
    modelo = models.CharField(max_length=60, blank=True)
    objeto_id = models.CharField(max_length=40, blank=True)
    descripcion = models.CharField(max_length=300, blank=True)
    detalle = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        verbose_name = "registro de bitácora"
        verbose_name_plural = "bitácora"
        ordering = ["-fecha"]
        indexes = [models.Index(fields=["institucion", "fecha"])]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("La bitácora es inmutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("La bitácora es inmutable.")

    def __str__(self):
        return f"{self.fecha:%Y-%m-%d %H:%M} {self.accion} {self.modelo}"

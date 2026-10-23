from django.conf import settings
from django.db import models

from core.models import Periodo, TenantModel
from modelo.models import Ambito, CentroCosto, Cups, Eps, Recurso, Sede

from .esquemas import TIPOS_CHOICES


class PlantillaMapeo(TenantModel):
    """Mapeo de columnas de un archivo de la institución hacia los campos estándar del tipo de archivo."""

    class Separador(models.TextChoices):
        AUTO = "auto", "Automático"
        COMA = ",", "Coma decimal (1.234,56)"
        PUNTO = ".", "Punto decimal (1,234.56)"

    nombre = models.CharField(max_length=120)
    tipo_archivo = models.CharField(max_length=20, choices=TIPOS_CHOICES)
    hoja = models.CharField(max_length=80, blank=True, help_text="Nombre de la hoja; vacío = la primera.")
    fila_encabezado = models.PositiveSmallIntegerField(default=1, help_text="Fila (1 = primera) donde están los encabezados.")
    mapeo = models.JSONField(default=dict, blank=True, help_text="{campo_estándar: columna_origen}")
    constantes = models.JSONField(default=dict, blank=True, help_text="{campo_estándar: valor fijo para todas las filas}")
    separador_decimal = models.CharField(max_length=4, choices=Separador.choices, default=Separador.AUTO)
    formato_fecha = models.CharField(max_length=30, blank=True, help_text="Ej. %d/%m/%Y. Vacío = detección automática.")
    omitir_duplicados = models.BooleanField(default=True, help_text="Las filas idénticas se reportan y se omiten.")
    es_estandar = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "plantilla de mapeo"
        verbose_name_plural = "plantillas de mapeo"
        unique_together = [("institucion", "nombre")]
        ordering = ["tipo_archivo", "nombre"]

    def __str__(self):
        return f"{self.nombre} ({self.get_tipo_archivo_display()})"


def ruta_carga(instancia, nombre):
    return f"cargas/{instancia.institucion_id}/{instancia.tipo_archivo}/{nombre}"


class Carga(TenantModel):
    class Estado(models.TextChoices):
        APLICADA = "APLICADA", "Aplicada"
        CON_ERRORES = "CON_ERRORES", "Rechazada por errores"
        ANULADA = "ANULADA", "Anulada"

    periodo = models.ForeignKey(Periodo, null=True, blank=True, on_delete=models.PROTECT, related_name="cargas")
    tipo_archivo = models.CharField(max_length=20, choices=TIPOS_CHOICES)
    plantilla = models.ForeignKey(PlantillaMapeo, null=True, blank=True, on_delete=models.SET_NULL, related_name="cargas")
    archivo = models.FileField(upload_to=ruta_carga, blank=True,
                               help_text="Vacío en RIPS y facturación: con datos de pacientes solo se conserva la huella SHA-256.")
    nombre_original = models.CharField(max_length=255)
    hoja = models.CharField(max_length=80, blank=True)
    hash_sha256 = models.CharField(max_length=64, db_index=True)
    tamano = models.PositiveIntegerField(default=0)
    estado = models.CharField(max_length=12, choices=Estado.choices)
    filas_leidas = models.PositiveIntegerField(default=0)
    filas_validas = models.PositiveIntegerField(default=0)
    filas_error = models.PositiveIntegerField(default=0)
    filas_duplicadas = models.PositiveIntegerField(default=0)
    filas_omitidas = models.PositiveIntegerField(default=0)
    valor_total = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    resumen = models.JSONField(default=dict, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    creado = models.DateTimeField(auto_now_add=True)
    anulada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    anulada_en = models.DateTimeField(null=True, blank=True)
    motivo_anulacion = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["-creado"]

    def __str__(self):
        return f"{self.get_tipo_archivo_display()} · {self.nombre_original} ({self.periodo or 'catálogo'})"


class ErrorCarga(models.Model):
    class Nivel(models.TextChoices):
        ERROR = "ERROR", "Error"
        ADVERTENCIA = "ADVERTENCIA", "Advertencia"

    carga = models.ForeignKey(Carga, on_delete=models.CASCADE, related_name="errores")
    fila = models.PositiveIntegerField(null=True, blank=True)
    columna = models.CharField(max_length=80, blank=True)
    valor = models.CharField(max_length=200, blank=True)
    mensaje = models.CharField(max_length=300)
    nivel = models.CharField(max_length=12, choices=Nivel.choices, default=Nivel.ERROR)

    class Meta:
        ordering = ["fila", "id"]


class FilaCargada(TenantModel):
    periodo = models.ForeignKey(Periodo, on_delete=models.CASCADE, related_name="+")
    carga = models.ForeignKey(Carga, on_delete=models.CASCADE, related_name="+")
    fila = models.PositiveIntegerField(help_text="Número de fila en el archivo de origen.")

    class Meta:
        abstract = True


class MovimientoContable(FilaCargada):
    class Fuente(models.TextChoices):
        EF = "EF", "Estado financiero"
        AREA = "AREA", "Soporte de área"

    fuente = models.CharField(max_length=4, choices=Fuente.choices)
    tipo_archivo = models.CharField(max_length=20, choices=TIPOS_CHOICES)
    cuenta = models.CharField(max_length=20, blank=True, db_index=True)
    nombre_cuenta = models.CharField(max_length=200, blank=True)
    recurso = models.ForeignKey(Recurso, on_delete=models.PROTECT, related_name="movimientos")
    centro = models.ForeignKey(CentroCosto, null=True, blank=True, on_delete=models.PROTECT, related_name="movimientos")
    centro_origen = models.CharField("centro de costos (como venía)", max_length=150, blank=True)
    sede = models.ForeignKey(Sede, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    tercero_documento = models.CharField(max_length=20, blank=True, db_index=True)
    tercero_nombre = models.CharField(max_length=150, blank=True)
    descripcion = models.CharField(max_length=200, blank=True)
    valor = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        indexes = [models.Index(fields=["institucion", "periodo", "recurso"])]

    def __str__(self):
        return f"{self.periodo} {self.cuenta} {self.recurso.codigo} {self.valor}"


class Produccion(FilaCargada):
    centro = models.ForeignKey(CentroCosto, on_delete=models.PROTECT, related_name="+")
    cups = models.ForeignKey(Cups, on_delete=models.PROTECT, related_name="+")
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)
    ambito = models.ForeignKey(Ambito, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name_plural = "producción"
        indexes = [models.Index(fields=["institucion", "periodo"])]


class Facturacion(FilaCargada):
    numero_factura = models.CharField(max_length=40, db_index=True)
    fecha = models.DateField(null=True, blank=True)
    eps_codigo = models.CharField(max_length=60)
    eps = models.ForeignKey(Eps, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    cups_codigo = models.CharField(max_length=20)
    cups = models.ForeignKey(Cups, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    cantidad = models.DecimalField(max_digits=14, decimal_places=2)
    valor_unitario = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    valor_total = models.DecimalField(max_digits=18, decimal_places=2)
    ambito_origen = models.CharField(max_length=80, blank=True)
    ambito = models.ForeignKey(Ambito, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    centro = models.ForeignKey(CentroCosto, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    paciente = models.CharField("seudónimo paciente", max_length=40, blank=True)

    class Meta:
        verbose_name_plural = "facturación"
        indexes = [models.Index(fields=["institucion", "periodo", "cups_codigo"])]


class Rips(FilaCargada):
    tipo_registro = models.CharField(max_length=20, blank=True)
    numero_factura = models.CharField(max_length=40, db_index=True)
    fecha_atencion = models.DateField(null=True, blank=True)
    eps_codigo = models.CharField(max_length=60, blank=True)
    eps = models.ForeignKey(Eps, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    paciente = models.CharField("seudónimo paciente", max_length=40, blank=True)
    cups_codigo = models.CharField(max_length=20)
    cups = models.ForeignKey(Cups, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    ambito_origen = models.CharField(max_length=80, blank=True)
    ambito = models.ForeignKey(Ambito, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    finalidad = models.CharField(max_length=20, blank=True)
    diagnostico = models.CharField(max_length=10, blank=True)
    cantidad = models.DecimalField(max_digits=14, decimal_places=2, default=1)
    valor = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        verbose_name = "registro RIPS"
        verbose_name_plural = "RIPS"
        indexes = [models.Index(fields=["institucion", "periodo", "cups_codigo"])]

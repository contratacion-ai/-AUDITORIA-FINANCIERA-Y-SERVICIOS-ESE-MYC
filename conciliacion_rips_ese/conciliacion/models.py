"""Modelos de la conciliación Facturación vs RIPS."""

import hashlib
from pathlib import PurePosixPath

from django.db import models
from django.urls import reverse

from auditoria.models import Auditoria


def ruta_archivo(instancia, nombre):
    return f"conciliaciones/{instancia.conciliacion_id}/{instancia.tipo.lower()}/{nombre}"


class Conciliacion(models.Model):
    class Estado(models.TextChoices):
        BORRADOR = "BORRADOR", "Sin procesar"
        DESACTUALIZADA = "DESACTUALIZADA", "Archivos modificados: debe reprocesarse"
        PROCESADA = "PROCESADA", "Procesada"
        ERROR = "ERROR", "Error al procesar"

    class Separador(models.TextChoices):
        AUTO = "auto", "Automático (por archivo)"
        COMA = ",", "Coma decimal (1.234,56)"
        PUNTO = ".", "Punto decimal (1,234.56)"

    auditoria = models.ForeignKey(Auditoria, on_delete=models.CASCADE, related_name="conciliaciones")
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    separador_decimal = models.CharField(max_length=4, choices=Separador.choices, default=Separador.AUTO)
    ignorar_ceros_factura = models.BooleanField(
        "Ignorar ceros a la izquierda en el número de factura", default=False
    )
    tolerancia_valor = models.DecimalField(
        "Tolerancia de valor (0 = exacto)", max_digits=20, decimal_places=6, default=0
    )
    top_n = models.PositiveIntegerField("Tamaño de rankings de consumo", default=20)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.BORRADOR)
    version_rips = models.CharField(max_length=200, blank=True)
    resumen = models.JSONField(default=list, blank=True)       # reporte general
    tablas = models.JSONField(default=dict, blank=True)        # comparativos y rankings (texto exacto)
    mensaje_error = models.TextField(blank=True)
    archivo_excel = models.FileField(upload_to="resultados/", blank=True)
    archivo_csv = models.FileField(upload_to="resultados/", blank=True)
    procesada = models.DateTimeField(null=True, blank=True)
    creada = models.DateTimeField(auto_now_add=True)
    actualizada = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-creada"]
        verbose_name = "conciliación"
        verbose_name_plural = "conciliaciones"

    def __str__(self):
        return self.nombre

    def get_absolute_url(self):
        return reverse("conciliacion:detalle", args=[self.pk])

    def marcar_desactualizada(self):
        if self.estado in (self.Estado.PROCESADA, self.Estado.ERROR):
            self.estado = self.Estado.DESACTUALIZADA
            self.save(update_fields=["estado", "actualizada"])


class ArchivoCargado(models.Model):
    class Tipo(models.TextChoices):
        RIPS = "RIPS", "RIPS (cualquier formato)"
        FACTURACION = "FACTURACION", "Facturación (cualquier formato)"
        CATALOGO = "CATALOGO", "Catálogo de sedes (código prestador → sede, hospital)"
        EQUIVALENCIAS = "EQUIVALENCIAS", "Equivalencias de códigos (facturación → RIPS)"

    conciliacion = models.ForeignKey(Conciliacion, on_delete=models.CASCADE, related_name="archivos")
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    archivo = models.FileField(upload_to=ruta_archivo, max_length=500)
    nombre_original = models.CharField(max_length=300)
    sede = models.CharField(max_length=120, blank=True, help_text="Asigna la sede a todo el archivo")
    hospital = models.CharField(max_length=120, blank=True, help_text="Asigna el hospital a todo el archivo")
    ambito = models.CharField(max_length=60, blank=True, help_text="Ámbito (solo facturación)")
    sha256 = models.CharField(max_length=64, db_index=True)
    tamano = models.BigIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)
    activo = models.BooleanField(default=True, help_text="Si se desactiva, no se usa al procesar")
    cargado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tipo", "nombre_original"]

    def __str__(self):
        return self.nombre_original

    @property
    def extension(self):
        return PurePosixPath(self.nombre_original).suffix.lower()

    @staticmethod
    def calcular_hash(archivo_subido) -> str:
        h = hashlib.sha256()
        for trozo in archivo_subido.chunks():
            h.update(trozo)
        archivo_subido.seek(0)
        return h.hexdigest()


class HistorialArchivo(models.Model):
    """Trazabilidad de cargas, reemplazos y eliminaciones (requerida en auditoría)."""

    class Accion(models.TextChoices):
        CARGA = "CARGA", "Carga"
        REEMPLAZO = "REEMPLAZO", "Reemplazo"
        ACTUALIZACION = "ACTUALIZACION", "Actualización de datos"
        ELIMINACION = "ELIMINACION", "Eliminación"
        LIMPIEZA = "LIMPIEZA", "Limpieza"

    conciliacion = models.ForeignKey(Conciliacion, on_delete=models.CASCADE, related_name="historial")
    accion = models.CharField(max_length=20, choices=Accion.choices)
    tipo = models.CharField(max_length=20, blank=True)
    nombre = models.CharField(max_length=300)
    sha256 = models.CharField(max_length=64, blank=True)
    detalle = models.TextField(blank=True)
    usuario = models.CharField(max_length=150, blank=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha"]


class Diferencia(models.Model):
    """Una fila del detalle factura-código (incluye las que coinciden)."""

    conciliacion = models.ForeignKey(Conciliacion, on_delete=models.CASCADE, related_name="diferencias")
    estado = models.CharField(max_length=40, db_index=True)
    factura = models.CharField(max_length=60, db_index=True)
    factura_clave = models.CharField(max_length=60)
    mes = models.CharField(max_length=10, db_index=True)
    sede = models.CharField(max_length=120, db_index=True)
    hospital = models.CharField(max_length=120, db_index=True)
    categoria = models.CharField(max_length=40, blank=True, db_index=True)
    servicio = models.CharField(max_length=120, blank=True)
    tipo_codigo = models.CharField(max_length=10, blank=True)
    codigo = models.CharField(max_length=60, db_index=True)
    descripcion = models.CharField(max_length=300, blank=True)
    cantidad_fact = models.DecimalField(max_digits=26, decimal_places=6)
    cantidad_rips = models.DecimalField(max_digits=26, decimal_places=6)
    dif_cantidad = models.DecimalField(max_digits=26, decimal_places=6)
    valor_fact = models.DecimalField(max_digits=26, decimal_places=6)
    valor_rips = models.DecimalField(max_digits=26, decimal_places=6)
    dif_valor = models.DecimalField(max_digits=26, decimal_places=6, db_index=True)
    sede_fact = models.CharField(max_length=120, blank=True)
    sede_rips = models.CharField(max_length=120, blank=True)
    mes_fact = models.CharField(max_length=10, blank=True)
    mes_rips = models.CharField(max_length=10, blank=True)
    campo_codigo_fact = models.CharField(max_length=120, blank=True)
    campo_codigo_rips = models.CharField(max_length=120, blank=True)
    ubicacion_fact = models.CharField(max_length=300, blank=True)
    ubicacion_rips = models.CharField(max_length=300, blank=True)
    archivo_rips = models.CharField(max_length=300, blank=True)
    posible_homologacion = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ["id"]


class Incidencia(models.Model):
    conciliacion = models.ForeignKey(Conciliacion, on_delete=models.CASCADE, related_name="incidencias")
    archivo = models.CharField(max_length=500)
    severidad = models.CharField(max_length=20, db_index=True)
    mensaje = models.TextField()
    linea = models.IntegerField(null=True, blank=True)
    campo = models.CharField(max_length=300, blank=True)
    valor = models.TextField(blank=True)

    class Meta:
        ordering = ["id"]

"""Modelo de datos del costeo ABC de ambulancias. Ver docs/MODELO_DATOS.md."""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F


class ConflictoVersion(Exception):
    """Otro usuario guardó el registro después de que se abrió para edición."""


class VersionadoMixin(models.Model):
    """Bloqueo optimista: cada edición incrementa ``version``.

    ``guardar_con_version(version_leida)`` solo guarda si nadie más modificó
    el registro; si no, lanza ConflictoVersion.
    """

    version = models.PositiveIntegerField(default=1, editable=False)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )

    class Meta:
        abstract = True

    def guardar_con_version(self, version_leida: int, usuario=None):
        with transaction.atomic():
            if self.pk is None:
                self.creado_por = usuario
                self.actualizado_por = usuario
                self.save()
                return
            filas = type(self).objects.filter(pk=self.pk, version=version_leida).update(version=F("version") + 1)
            if filas == 0:
                raise ConflictoVersion(
                    "Otro usuario modificó este registro mientras usted lo editaba. Recargue la página y vuelva a intentarlo."
                )
            self.version = version_leida + 1
            self.actualizado_por = usuario
            self.save()


def primer_dia(fecha: dt.date) -> dt.date:
    return fecha.replace(day=1)


def sumar_meses(mes: dt.date, n: int) -> dt.date:
    total = mes.year * 12 + (mes.month - 1) + n
    return dt.date(total // 12, total % 12 + 1, 1)


def meses_entre_fechas(desde: dt.date, hasta: dt.date) -> int:
    """Número de meses de ``desde`` a ``hasta`` (0 si es el mismo mes)."""
    return (hasta.year - desde.year) * 12 + hasta.month - desde.month


TRASLADO, APH = "TRASLADO", "APH"
CATEGORIAS_SERVICIO = [(TRASLADO, "Traslado asistencial"), (APH, "Atención prehospitalaria (APH)")]


# ---------------------------------------------------------------------------
# Catálogos
# ---------------------------------------------------------------------------


class Sede(models.Model):
    codigo = models.SlugField(max_length=40, unique=True)
    nombre = models.CharField(max_length=120)
    municipio = models.CharField(max_length=80)
    origen_tarifa = models.CharField(
        max_length=120, help_text="Nombre del ORIGEN en el Anexo 3 de tarifas (para inferir rutas)"
    )
    alias = models.TextField(
        blank=True, help_text="Otros nombres con que aparece en archivos (uno por línea), p. ej. 'HOSPITAL SAN ANTONIO'"
    )
    codigo_prestador = models.CharField(max_length=20, blank=True, help_text="Código de habilitación REPS (RIPS)")
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def nombres(self) -> list[str]:
        return [self.nombre, self.municipio, self.codigo, *[a for a in self.alias.splitlines() if a.strip()]]


class Ambulancia(VersionadoMixin):
    GASOLINA, ACPM = "GASOLINA", "ACPM"
    ACTIVA, NUEVA, FUERA = "ACTIVA", "NUEVA_SIN_HISTORIAL", "FUERA_SERVICIO"

    placa = models.CharField(max_length=10, unique=True)
    sede = models.ForeignKey(Sede, on_delete=models.PROTECT, related_name="ambulancias")
    tipo_combustible = models.CharField(max_length=10, choices=[(GASOLINA, "Gasolina"), (ACPM, "Diésel (ACPM)")])
    descripcion = models.CharField(max_length=120, blank=True, help_text="Marca/modelo, tipo TAB")
    estado = models.CharField(
        max_length=20,
        choices=[(ACTIVA, "Activa"), (NUEVA, "Nueva sin historial"), (FUERA, "Fuera de servicio")],
        default=ACTIVA,
    )
    incluir_en_costeo = models.BooleanField(
        default=True, help_text="Si se desmarca, la ambulancia no aporta costo estándar al periodo"
    )
    excluida_promedios = models.BooleanField(default=False)
    motivo_exclusion = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["placa"]

    def __str__(self):
        return self.placa

    @property
    def perfil_vigente(self):
        return self.perfiles.filter(vigente=True).order_by("-fecha_estudio").first()


class PerfilCostoEstudio(models.Model):
    """Costo diario estándar de una ambulancia según un estudio (Anexo A)."""

    ambulancia = models.ForeignKey(Ambulancia, on_delete=models.CASCADE, related_name="perfiles")
    estudio = models.CharField(max_length=150)
    fecha_estudio = models.DateField()
    vigente = models.BooleanField(default=True)
    km_dia = models.DecimalField(max_digits=8, decimal_places=2)
    horas_dia = models.DecimalField(max_digits=8, decimal_places=4)
    combustible_dia = models.DecimalField(max_digits=14, decimal_places=2)
    mantenimiento_dia = models.DecimalField(max_digits=14, decimal_places=2)
    tripulacion_dia = models.DecimalField(max_digits=14, decimal_places=2)
    total_dia_reportado = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    costo_km_reportado = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    costo_hora_reportado = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["ambulancia__placa", "-fecha_estudio"]
        constraints = [models.UniqueConstraint(fields=["ambulancia", "estudio"], name="perfil_unico_por_estudio")]

    def __str__(self):
        return f"{self.ambulancia} · {self.estudio}"

    @property
    def total_dia(self):
        return self.combustible_dia + self.mantenimiento_dia + self.tripulacion_dia


class Parametro(VersionadoMixin):
    """Parámetro configurable. ``es_supuesto`` marca valores que el estudio no define."""

    clave = models.SlugField(max_length=60, unique=True)
    valor = models.DecimalField(max_digits=16, decimal_places=4)
    unidad = models.CharField(max_length=30, blank=True)
    descripcion = models.CharField(max_length=300)
    fuente = models.CharField(max_length=200, blank=True)
    es_supuesto = models.BooleanField(default=False, help_text="Valor no definido por el estudio: revisar")

    class Meta:
        ordering = ["clave"]

    def __str__(self):
        return f"{self.clave} = {self.valor}"


class TarifaRuta(models.Model):
    """Ruta del manual tarifario de tarifas propias (Anexo 3)."""

    codigo = models.SlugField(max_length=160)
    vigencia = models.PositiveIntegerField()
    origen = models.CharField(max_length=120)
    destino = models.CharField(max_length=160)
    km = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    valor_km = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    tarifa = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    ambito_geografico = models.CharField(max_length=20, default="DEPARTAMENTAL")
    fila_fuente = models.PositiveIntegerField(null=True, blank=True)
    observacion = models.CharField(max_length=300, blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["origen", "destino"]
        constraints = [models.UniqueConstraint(fields=["vigencia", "codigo"], name="tarifa_ruta_unica")]

    def __str__(self):
        return f"{self.origen} → {self.destino} ({self.km} km)"


class CodigoTraslado(models.Model):
    """Códigos CUPS/propios de traslado y cómo se facturan."""

    BASE, POR_KM = "BASE", "POR_KM"
    codigo = models.CharField(max_length=20, unique=True)
    descripcion = models.CharField(max_length=250)
    modalidad = models.CharField(
        max_length=10, choices=[(BASE, "Tarifa básica fija"), (POR_KM, "Valor por kilómetro")]
    )
    categoria = models.CharField(
        max_length=10, choices=CATEGORIAS_SERVICIO, default=TRASLADO,
        help_text="Clasificación al extraer de RIPS: traslado o atención prehospitalaria",
    )
    activo = models.BooleanField(default=True)

    def __str__(self):
        return self.codigo


class PartidaCostoIndirecto(models.Model):
    """Partida mensual del centro de costos (COSTOS AMBULANCIAS.xlsx)."""

    categoria = models.CharField(max_length=80)
    concepto = models.CharField(max_length=120)
    valor_mensual = models.DecimalField(max_digits=16, decimal_places=2)
    incluir_en_abc = models.BooleanField(
        default=False, help_text="Si se marca, se reparte entre los traslados por número de traslados"
    )
    fuente = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ["categoria", "concepto"]
        constraints = [models.UniqueConstraint(fields=["categoria", "concepto"], name="partida_unica")]

    def __str__(self):
        return self.concepto


# ---------------------------------------------------------------------------
# Movimientos
# ---------------------------------------------------------------------------


class Traslado(VersionadoMixin):
    MANUAL, HIS, RIPS = "MANUAL", "HIS", "RIPS"

    llave_externa = models.CharField(max_length=200, unique=True, help_text="Llave natural del origen; evita duplicados")
    origen_registro = models.CharField(max_length=10, choices=[(MANUAL, "Manual"), (HIS, "Facturación HIS"), (RIPS, "RIPS")])
    lote = models.ForeignKey("LoteImportacion", null=True, blank=True, on_delete=models.SET_NULL, related_name="traslados")
    lote_rips = models.ForeignKey(
        "LoteImportacion", null=True, blank=True, on_delete=models.SET_NULL, related_name="traslados_rips",
        help_text="Carga de RIPS que contiene (o concilió) esta línea",
    )
    categoria = models.CharField(max_length=10, choices=CATEGORIAS_SERVICIO, default=TRASLADO, db_index=True)

    fecha_servicio = models.DateField(db_index=True)
    mes = models.DateField(db_index=True, editable=False)
    sede = models.ForeignKey(Sede, on_delete=models.PROTECT, related_name="traslados")
    ambulancia = models.ForeignKey(Ambulancia, null=True, blank=True, on_delete=models.SET_NULL, related_name="traslados")
    ruta = models.ForeignKey(TarifaRuta, null=True, blank=True, on_delete=models.SET_NULL, related_name="traslados")
    ruta_inferida = models.BooleanField(default=False, help_text="Ruta deducida por sede de origen y km facturados")

    codigo_cups = models.CharField(max_length=20, db_index=True)
    codigo_cups_original = models.CharField(max_length=30, blank=True)
    descripcion = models.CharField(max_length=250, blank=True)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    km_recorridos = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, help_text="Km conducidos (ida y vuelta) usados como driver"
    )
    valor_unitario = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    valor_facturado = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    ambito = models.CharField(max_length=40, db_index=True, blank=True)
    unidad_funcional = models.CharField(max_length=60, db_index=True, blank=True)
    contrato = models.CharField(max_length=120, blank=True)
    eps_nombre = models.CharField(max_length=150, blank=True)
    eps_codigo = models.CharField(max_length=20, blank=True)
    regimen = models.CharField(max_length=30, blank=True)

    numero_factura = models.CharField(max_length=30, blank=True, db_index=True)
    prefactura = models.CharField(max_length=30, blank=True)
    nro_ingreso = models.CharField(max_length=30, blank=True, db_index=True)
    autorizacion = models.CharField(max_length=60, blank=True)
    rips_consecutivo = models.CharField(max_length=20, blank=True)
    conciliado_rips = models.BooleanField(default=False)

    tipo_documento = models.CharField(max_length=5, blank=True)
    documento_hash = models.CharField(max_length=64, blank=True, db_index=True)
    observaciones_calidad = models.TextField(blank=True)

    class Meta:
        ordering = ["-fecha_servicio", "id"]
        indexes = [models.Index(fields=["mes", "sede"]), models.Index(fields=["mes", "ambulancia"])]

    def __str__(self):
        return f"{self.fecha_servicio} {self.sede} {self.codigo_cups} {self.cantidad}"

    def save(self, *args, **kwargs):
        self.mes = primer_dia(self.fecha_servicio)
        super().save(*args, **kwargs)


class Consumo(VersionadoMixin):
    """Consumo de recursos por ambulancia (trazabilidad por unidad)."""

    COMBUSTIBLE, MANTENIMIENTO, LUBRICANTE, LLANTAS = "COMBUSTIBLE", "MANTENIMIENTO", "LUBRICANTE", "LLANTAS"
    INSUMO, TRIPULACION, OTRO = "INSUMO", "TRIPULACION", "OTRO"
    SEGUROS_IMPUESTOS, IMPREVISTOS = "SEGUROS_IMP", "IMPREVISTOS"
    TIPOS = [
        (COMBUSTIBLE, "Combustible"),
        (MANTENIMIENTO, "Mantenimiento (repuestos y mano de obra)"),
        (LUBRICANTE, "Lubricantes"),
        (LLANTAS, "Llantas"),
        (TRIPULACION, "Personal (nómina de la tripulación)"),
        (SEGUROS_IMPUESTOS, "Seguros e impuestos (SOAT, pólizas, impuesto vehicular, revisión técnico-mecánica)"),
        (IMPREVISTOS, "Imprevistos"),
        (INSUMO, "Insumos y dispositivos médicos"),
        (OTRO, "Otros consumos"),
    ]
    # Tablas de consumo que ve el usuario: clave -> (título, tipos que agrupa, unidad sugerida)
    TABLAS = {
        "combustible": ("Combustible", [COMBUSTIBLE], "gal"),
        "mantenimiento": ("Mantenimiento", [MANTENIMIENTO, LUBRICANTE, LLANTAS], "und"),
        "personal": ("Personal", [TRIPULACION], "mes"),
        "seguros": ("Seguros e impuestos", [SEGUROS_IMPUESTOS], "und"),
        "imprevistos": ("Imprevistos", [IMPREVISTOS], "und"),
        "insumos": ("Insumos", [INSUMO], "und"),
        "otros": ("Otros", [OTRO], "und"),
    }
    # Sinónimos aceptados en la columna 'tipo' de los archivos de carga
    SINONIMOS = {
        "PERSONAL": TRIPULACION, "NOMINA": TRIPULACION, "TRIPULACION": TRIPULACION,
        "SEGUROS": SEGUROS_IMPUESTOS, "IMPUESTOS": SEGUROS_IMPUESTOS, "SEGUROS E IMPUESTOS": SEGUROS_IMPUESTOS,
        "SOAT": SEGUROS_IMPUESTOS, "SEGUROS_IMPUESTOS": SEGUROS_IMPUESTOS, "SEGUROS_IMP": SEGUROS_IMPUESTOS,
        "IMPREVISTO": IMPREVISTOS, "IMPREVISTOS": IMPREVISTOS, "INSUMOS": INSUMO, "LLANTA": LLANTAS,
        "LUBRICANTES": LUBRICANTE, "ACPM": COMBUSTIBLE, "GASOLINA": COMBUSTIBLE,
    }
    # tipo de consumo -> actividad/pool ABC
    POOL = {
        COMBUSTIBLE: "combustible",
        MANTENIMIENTO: "mantenimiento",
        LUBRICANTE: "mantenimiento",
        LLANTAS: "mantenimiento",
        INSUMO: "insumos",
        TRIPULACION: "tripulacion",
        SEGUROS_IMPUESTOS: "otros",
        IMPREVISTOS: "otros",
        OTRO: "otros",
    }

    ambulancia = models.ForeignKey(Ambulancia, on_delete=models.PROTECT, related_name="consumos")
    fecha = models.DateField(db_index=True)
    mes = models.DateField(db_index=True, editable=False)
    tipo = models.CharField(max_length=15, choices=TIPOS)
    descripcion = models.CharField(max_length=200, blank=True)
    cantidad = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    unidad = models.CharField(max_length=20, blank=True, help_text="gal, und, h, ...")
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    odometro = models.PositiveIntegerField(null=True, blank=True)
    documento_soporte = models.CharField(max_length=60, blank=True, help_text="Vale, factura u orden de trabajo")
    proveedor = models.CharField(max_length=120, blank=True)
    meses_prorrateo = models.PositiveSmallIntegerField(
        default=1, help_text="Meses en que se reparte el valor (p. ej. 12 para el SOAT anual). 1 = solo el mes de la fecha",
    )
    lote = models.ForeignKey("LoteImportacion", null=True, blank=True, on_delete=models.SET_NULL, related_name="consumos")

    class Meta:
        ordering = ["-fecha", "ambulancia__placa"]
        indexes = [models.Index(fields=["mes", "ambulancia", "tipo"])]

    def __str__(self):
        return f"{self.fecha} {self.ambulancia} {self.tipo} {self.valor}"

    def clean(self):
        if self.valor is not None and self.valor < 0:
            raise ValidationError({"valor": "El valor no puede ser negativo"})
        if self.tipo == self.COMBUSTIBLE and not self.cantidad:
            raise ValidationError({"cantidad": "Registre los galones del tanqueo"})
        if not 1 <= (self.meses_prorrateo or 1) <= 36:
            raise ValidationError({"meses_prorrateo": "Entre 1 y 36 meses"})

    def valor_en_mes(self, mes: dt.date):
        """Parte del valor que corresponde a ``mes`` según el prorrateo."""
        n = self.meses_prorrateo or 1
        return self.valor / n if 0 <= meses_entre_fechas(self.mes, mes) < n else 0

    def save(self, *args, **kwargs):
        self.mes = primer_dia(self.fecha)
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Trazabilidad y calidad de datos
# ---------------------------------------------------------------------------


class LoteImportacion(models.Model):
    tipo = models.CharField(max_length=30)
    archivo = models.CharField(max_length=255)
    fecha = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    nuevos = models.PositiveIntegerField(default=0)
    actualizados = models.PositiveIntegerField(default=0)
    omitidos = models.PositiveIntegerField(default=0)
    hallazgos = models.PositiveIntegerField(default=0)
    resumen = models.TextField(blank=True)
    mes = models.DateField(null=True, blank=True, help_text="Mes que se declaró al cargar")
    sede = models.ForeignKey("Sede", null=True, blank=True, on_delete=models.SET_NULL, help_text="Hospital que se declaró al cargar")
    detalle = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-fecha"]

    def __str__(self):
        return f"{self.fecha:%Y-%m-%d %H:%M} {self.tipo} {self.archivo}"


class HallazgoCalidad(models.Model):
    SEVERIDADES = [("ALTA", "Alta"), ("MEDIA", "Media"), ("BAJA", "Baja")]
    TIPOS = [
        ("ERROR_FORMULA", "Error de fórmula"),
        ("ENCABEZADO", "Encabezado desalineado"),
        ("FORMULA", "Fórmula frágil"),
        ("INCONSISTENCIA", "Inconsistencia"),
        ("FALTANTE", "Dato faltante"),
        ("DUPLICADO", "Duplicado"),
        ("SUPUESTO", "Supuesto / parámetro sin fuente"),
        ("ESTUDIO", "Cifra del estudio no reproducible"),
    ]
    fuente = models.CharField(max_length=150)
    ubicacion = models.CharField(max_length=200)
    tipo = models.CharField(max_length=20, choices=TIPOS)
    descripcion = models.TextField()
    severidad = models.CharField(max_length=5, choices=SEVERIDADES, default="MEDIA")
    valor = models.CharField(max_length=200, blank=True)
    lote = models.ForeignKey(LoteImportacion, null=True, blank=True, on_delete=models.SET_NULL)
    detectado = models.DateTimeField(auto_now_add=True)
    resuelto = models.BooleanField(default=False)
    nota_resolucion = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["resuelto", "severidad", "fuente", "ubicacion"]
        constraints = [
            models.UniqueConstraint(fields=["fuente", "ubicacion", "tipo", "descripcion"], name="hallazgo_unico")
        ]

    def __str__(self):
        return f"[{self.severidad}] {self.fuente} {self.ubicacion}"


class RegistroAuditoria(models.Model):
    """Bitácora de cambios (quién, cuándo, qué)."""

    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    modelo = models.CharField(max_length=60)
    objeto_id = models.CharField(max_length=60)
    accion = models.CharField(max_length=20)
    cambios = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-fecha"]

    def __str__(self):
        return f"{self.fecha:%Y-%m-%d %H:%M} {self.usuario} {self.accion} {self.modelo}#{self.objeto_id}"


class ClaveAPI(models.Model):
    """Clave para que las aplicaciones de RIPS y auditoría usen la API."""

    nombre = models.CharField(max_length=80, unique=True)
    prefijo = models.CharField(max_length=8, editable=False)
    clave_hash = models.CharField(max_length=64, editable=False)
    puede_escribir = models.BooleanField(default=False, help_text="Permite importar RIPS por la API")
    activa = models.BooleanField(default=True)
    creada = models.DateTimeField(auto_now_add=True)
    ultimo_uso = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.nombre} ({self.prefijo}…)"

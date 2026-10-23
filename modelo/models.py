"""Modelo conceptual ABC (Res. DDC-000002 de 2014 y manual del sistema web de costos).

Jerarquía:  Elemento → Recurso → (Cuentas contables)  ·  Clase → Unidad de negocio → Centro de costos.
Distribución: Inductores + Reglas de distribución (1ª a 4ª) + porcentajes de mano de obra (Empleado/OPS).
Cruces: CUPS, EPS, Ámbitos de facturación, Notas técnicas, Indicadores de referencia.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from core.models import Periodo, TenantModel


class Clase(models.TextChoices):
    """Clases de unidad de negocio (manual, menú Configuración → General)."""

    ESTRUCTURA = "ESTRUCTURA", "Estructura"
    SERVICIOS = "SERVICIOS", "Servicios"
    INTERMEDIOS = "INTERMEDIOS", "Intermedios"
    FINALES = "FINALES", "Finales"


# Orden de cascada: un centro solo distribuye hacia clases de rango mayor (sin ciclos).
RANGO_CLASE = {Clase.ESTRUCTURA: 1, Clase.SERVICIOS: 2, Clase.INTERMEDIOS: 3, Clase.FINALES: 4}
# Distribución en la que se reparte cada clase (la 1ª reparte recursos).
DISTRIBUCION_DE_CLASE = {Clase.ESTRUCTURA: 2, Clase.SERVICIOS: 3, Clase.INTERMEDIOS: 4}
CLASE_DE_DISTRIBUCION = {v: k for k, v in DISTRIBUCION_DE_CLASE.items()}


def lista_clases(texto):
    return [c.strip().upper() for c in (texto or "").split(",") if c.strip()]


class CodigoNombreMixin(models.Model):
    codigo = models.CharField("código", max_length=30)
    nombre = models.CharField(max_length=200)
    activo = models.BooleanField(default=True)

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.codigo} · {self.nombre}"


class Elemento(CodigoNombreMixin, TenantModel):
    """Agrupación de recursos homogéneos: CP, IH, CG, EC (Anexo Técnico 2)."""

    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        unique_together = [("institucion", "codigo")]
        ordering = ["orden", "codigo"]


class Recurso(CodigoNombreMixin, TenantModel):
    class ManoObra(models.TextChoices):
        NO = "", "No aplica"
        EMPLEADO = "EMPLEADO", "Empleado (nómina)"
        OPS = "OPS", "Contratista (OPS)"

    elemento = models.ForeignKey(Elemento, on_delete=models.PROTECT, related_name="recursos")
    orden = models.PositiveSmallIntegerField(default=0)
    inductor_convenido = models.CharField("inductor convenido (texto normativo)", max_length=300, blank=True)
    tipo_mano_obra = models.CharField(
        "distribución de mano de obra", max_length=10, choices=ManoObra.choices, blank=True, default="",
        help_text="Si aplica, los movimientos con documento de tercero se reparten con los porcentajes Empleado/OPS.",
    )
    clases_permitidas = models.CharField(
        max_length=80, blank=True,
        help_text="Clases que pueden recibir este recurso en la 1ª distribución (vacío = todas). "
                  "Ej. insumos hospitalarios: INTERMEDIOS,FINALES",
    )

    class Meta:
        unique_together = [("institucion", "codigo")]
        ordering = ["orden", "codigo"]

    def clean(self):
        invalidas = set(lista_clases(self.clases_permitidas)) - set(Clase.values)
        if invalidas:
            raise ValidationError({"clases_permitidas": f"Clases no válidas: {', '.join(sorted(invalidas))}"})


class CuentaRecurso(TenantModel):
    """Relación cuenta contable → recurso (Cuadro 2). Se aplica el prefijo más largo que coincida."""

    prefijo = models.CharField("cuenta o prefijo", max_length=20)
    recurso = models.ForeignKey(Recurso, null=True, blank=True, on_delete=models.PROTECT, related_name="cuentas")
    excluir = models.BooleanField(default=False, help_text="La cuenta no es costo del modelo (p. ej. vigencias anteriores).")
    descripcion = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "cuenta contable - recurso"
        verbose_name_plural = "cuentas contables - recursos"
        unique_together = [("institucion", "prefijo")]
        ordering = ["prefijo"]

    def clean(self):
        if not self.prefijo.isdigit():
            raise ValidationError({"prefijo": "La cuenta debe contener solo dígitos."})
        if not self.excluir and self.recurso_id is None:
            raise ValidationError({"recurso": "Indique el recurso o marque la cuenta como excluida."})

    def __str__(self):
        return f"{self.prefijo} → {self.recurso.codigo if self.recurso else 'EXCLUIDA'}"


class UnidadNegocio(CodigoNombreMixin, TenantModel):
    clase = models.CharField(max_length=12, choices=Clase.choices)
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "unidad de negocio"
        verbose_name_plural = "unidades de negocio"
        unique_together = [("institucion", "codigo")]
        ordering = ["orden", "codigo"]


class Sede(CodigoNombreMixin, TenantModel):
    municipio = models.CharField(max_length=80, blank=True)

    class Meta:
        unique_together = [("institucion", "codigo")]
        ordering = ["codigo"]


class CentroCosto(CodigoNombreMixin, TenantModel):
    unidad_negocio = models.ForeignKey(UnidadNegocio, on_delete=models.PROTECT, related_name="centros")
    clase = models.CharField(
        max_length=12, choices=Clase.choices, blank=True,
        help_text="Vacío = la clase de la unidad de negocio. Un intermedio que se factura puede marcarse como FINALES.",
    )
    sede = models.ForeignKey(Sede, null=True, blank=True, on_delete=models.PROTECT, related_name="centros")

    class Meta:
        verbose_name = "centro de costos"
        verbose_name_plural = "centros de costos"
        unique_together = [("institucion", "codigo")]
        ordering = ["codigo"]

    def save(self, *args, **kwargs):
        if not self.clase and self.unidad_negocio_id:
            self.clase = self.unidad_negocio.clase
        super().save(*args, **kwargs)

    @property
    def es_directo(self):
        """Productivos (intermedios y finales) = directos; estructura y servicios = indirectos."""
        return self.clase in (Clase.INTERMEDIOS, Clase.FINALES)


class HomologacionCentro(TenantModel):
    """Código de centro de costos usado en la contabilidad / nómina de la institución → centro del modelo."""

    codigo_origen = models.CharField(max_length=150)
    centro = models.ForeignKey(CentroCosto, on_delete=models.CASCADE, related_name="homologaciones")

    class Meta:
        verbose_name = "homologación de centro de costos"
        verbose_name_plural = "homologación de centros de costos"
        unique_together = [("institucion", "codigo_origen")]
        ordering = ["codigo_origen"]

    def __str__(self):
        return f"{self.codigo_origen} → {self.centro.codigo}"


class Inductor(CodigoNombreMixin, TenantModel):
    class Tipo(models.TextChoices):
        CANTIDAD = "CANTIDAD", "Cantidad registrada por centro (m², personas, kilos, %, ...)"
        PRODUCCION = "PRODUCCION", "Producción del centro destino (producción / RIPS)"
        COSTO_DIRECTO = "COSTO_DIRECTO", "Ponderación por costeo directo (resultado 1ª distribución)"
        FACTURACION = "FACTURACION", "Valor facturado por centro destino"

    tipo = models.CharField(max_length=14, choices=Tipo.choices, default=Tipo.CANTIDAD)
    unidad_medida = models.CharField(max_length=40, blank=True)
    descripcion = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "inductores"
        unique_together = [("institucion", "codigo")]
        ordering = ["codigo"]


class ValorInductor(TenantModel):
    """Cantidad del inductor por centro destino. Sin periodo = valor permanente (p. ej. m²).

    `centro_origen` permite cantidades específicas de un centro que distribuye
    (raciones de cocina por servicio, exámenes solicitados al laboratorio por servicio).
    """

    inductor = models.ForeignKey(Inductor, on_delete=models.CASCADE, related_name="valores")
    centro = models.ForeignKey(CentroCosto, on_delete=models.CASCADE, related_name="valores_inductor")
    centro_origen = models.ForeignKey(CentroCosto, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    periodo = models.ForeignKey(Periodo, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    cantidad = models.DecimalField(max_digits=18, decimal_places=4, validators=[MinValueValidator(Decimal("0"))])
    carga = models.ForeignKey("cargas.Carga", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = "valor de inductor"
        verbose_name_plural = "valores de inductores"
        constraints = [
            models.UniqueConstraint(
                fields=["institucion", "inductor", "centro", "centro_origen", "periodo"],
                name="valor_inductor_unico", nulls_distinct=False,
            )
        ]

    def __str__(self):
        return f"{self.inductor.codigo} {self.centro.codigo} = {self.cantidad}"


class ReglaDistribucion(TenantModel):
    """Criterio de distribución. Se aplica la regla más específica (nivel de parametrización mayor).

    1ª distribución: reparte el recurso de los movimientos sin centro de costos.
    2ª/3ª/4ª: reparte el costo acumulado de cada centro de estructura / servicios / intermedios.
    """

    class Distribucion(models.IntegerChoices):
        PRIMERA = 1, "1ª Recursos → centros de costos"
        SEGUNDA = 2, "2ª Estructura → servicios, intermedios y finales"
        TERCERA = 3, "3ª Servicios → intermedios y finales"
        CUARTA = 4, "4ª Intermedios → finales"

    distribucion = models.PositiveSmallIntegerField(choices=Distribucion.choices)
    recurso = models.ForeignKey(Recurso, null=True, blank=True, on_delete=models.CASCADE, related_name="reglas")
    unidad_negocio = models.ForeignKey(UnidadNegocio, null=True, blank=True, on_delete=models.CASCADE, related_name="+",
                                       verbose_name="unidad de negocio origen")
    centro_origen = models.ForeignKey(CentroCosto, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    periodo = models.ForeignKey(Periodo, null=True, blank=True, on_delete=models.CASCADE, related_name="+",
                                help_text="Solo para este periodo (excepción del mes).")
    inductor = models.ForeignKey(Inductor, on_delete=models.PROTECT, related_name="reglas")
    clases_destino = models.CharField(max_length=80, help_text="Separadas por coma. Ej.: SERVICIOS,INTERMEDIOS,FINALES")
    misma_sede = models.BooleanField(default=False, help_text="Repartir solo entre centros de la misma sede del origen.")
    observacion = models.CharField(max_length=200, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "regla de distribución"
        verbose_name_plural = "reglas de distribución"
        ordering = ["distribucion", "recurso__orden", "centro_origen__codigo"]

    @property
    def nivel(self):
        """Nivel de especificidad (ver docs/PARAMETRIZACION.md)."""
        if self.periodo_id:
            return 4
        if self.centro_origen_id:
            return 3
        if self.unidad_negocio_id:
            return 2
        return 1

    def clean(self):
        errores = {}
        destinos = lista_clases(self.clases_destino)
        invalidas = set(destinos) - set(Clase.values)
        if not destinos or invalidas:
            errores["clases_destino"] = "Indique clases válidas: " + ", ".join(Clase.values)
        if self.distribucion == 1:
            if self.recurso_id is None:
                errores["recurso"] = "La 1ª distribución requiere el recurso."
            if self.unidad_negocio_id or self.centro_origen_id:
                errores["centro_origen"] = "En la 1ª distribución el origen es el recurso, no un centro."
            if self.inductor_id and self.inductor.tipo == Inductor.Tipo.COSTO_DIRECTO:
                errores["inductor"] = "La ponderación por costeo directo solo existe desde la 2ª distribución."
        elif self.distribucion in CLASE_DE_DISTRIBUCION:
            if self.recurso_id:
                errores["recurso"] = "Desde la 2ª distribución se reparte el centro completo (sin recurso)."
            origen = CLASE_DE_DISTRIBUCION[self.distribucion]
            for c in destinos:
                if c in RANGO_CLASE and RANGO_CLASE[c] <= RANGO_CLASE[origen]:
                    errores["clases_destino"] = f"La {self.distribucion}ª distribución reparte {origen}; los destinos deben ser de un nivel posterior."
            if self.centro_origen_id and self.centro_origen.clase != origen:
                errores["centro_origen"] = f"El centro origen debe ser de clase {origen}."
            if self.unidad_negocio_id and self.unidad_negocio.clase != origen:
                errores["unidad_negocio"] = f"La unidad de negocio debe ser de clase {origen}."
        if errores:
            raise ValidationError(errores)

    def __str__(self):
        alcance = self.centro_origen or self.unidad_negocio or self.recurso or "general"
        return f"{self.distribucion}ª · {alcance} → {self.inductor.codigo} (N{self.nivel})"


class DistribucionManoObra(TenantModel):
    """Porcentajes por centro de costos para un empleado o contratista OPS (manual: Mano de obra → Distribución)."""

    tipo = models.CharField(max_length=10, choices=[("EMPLEADO", "Empleado"), ("OPS", "OPS")])
    documento = models.CharField(max_length=20, db_index=True)
    nombre = models.CharField(max_length=150, blank=True)
    centro = models.ForeignKey(CentroCosto, on_delete=models.PROTECT, related_name="+")
    porcentaje = models.DecimalField(max_digits=7, decimal_places=4,
                                     validators=[MinValueValidator(Decimal("0"))])
    periodo = models.ForeignKey(Periodo, null=True, blank=True, on_delete=models.CASCADE, related_name="+",
                                help_text="Vacío = vigente para todos los periodos.")
    carga = models.ForeignKey("cargas.Carga", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = "distribución de mano de obra"
        verbose_name_plural = "distribución de mano de obra"
        ordering = ["tipo", "documento", "centro__codigo"]

    def clean(self):
        if self.porcentaje is not None and self.porcentaje > 100:
            raise ValidationError({"porcentaje": "El porcentaje no puede superar 100."})

    def __str__(self):
        return f"{self.tipo} {self.documento} → {self.centro.codigo} {self.porcentaje}%"


class Cups(TenantModel):
    codigo = models.CharField("código CUPS", max_length=12)
    descripcion = models.CharField(max_length=300)
    grupo = models.CharField(max_length=100, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "CUPS"
        verbose_name_plural = "catálogo CUPS"
        unique_together = [("institucion", "codigo")]
        ordering = ["codigo"]

    def __str__(self):
        return f"{self.codigo} · {self.descripcion[:60]}"


class Ambito(TenantModel):
    """Ámbito de facturación (software 360 / RIPS): ambulatorio, hospitalario, urgencias..."""

    codigo = models.CharField(max_length=10)
    nombre = models.CharField(max_length=80)

    class Meta:
        verbose_name = "ámbito"
        unique_together = [("institucion", "codigo")]
        ordering = ["codigo"]

    def __str__(self):
        return f"{self.codigo} · {self.nombre}"


class AmbitoHomologacion(TenantModel):
    valor_origen = models.CharField(max_length=80, help_text="Texto o código tal como llega en el archivo.")
    ambito = models.ForeignKey(Ambito, on_delete=models.CASCADE, related_name="homologaciones")

    class Meta:
        verbose_name = "homologación de ámbito"
        verbose_name_plural = "homologación de ámbitos"
        unique_together = [("institucion", "valor_origen")]

    def __str__(self):
        return f"{self.valor_origen} → {self.ambito.codigo}"


class CupsCentroCosto(TenantModel):
    """Asociación CUPS ↔ centro de costos con peso relativo (UVR, minutos estándar, 1 = igual peso)."""

    cups = models.ForeignKey(Cups, on_delete=models.CASCADE, related_name="centros")
    centro = models.ForeignKey(CentroCosto, on_delete=models.CASCADE, related_name="cups")
    ambito = models.ForeignKey(Ambito, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                               help_text="Vacío = cualquier ámbito.")
    peso_relativo = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("1"),
                                        validators=[MinValueValidator(Decimal("0"))])

    class Meta:
        verbose_name = "CUPS por centro de costos"
        verbose_name_plural = "CUPS por centro de costos"
        constraints = [
            models.UniqueConstraint(fields=["institucion", "cups", "centro", "ambito"], name="cups_centro_unico",
                                    nulls_distinct=False)
        ]

    def __str__(self):
        return f"{self.cups.codigo} → {self.centro.codigo} (peso {self.peso_relativo})"


class Eps(TenantModel):
    codigo = models.CharField("código EPS / ERP", max_length=20)
    nit = models.CharField(max_length=20, blank=True)
    nombre = models.CharField(max_length=200)
    regimen = models.CharField(max_length=30, blank=True, help_text="Contributivo, subsidiado, especial...")
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "EPS / pagador"
        verbose_name_plural = "EPS / pagadores"
        unique_together = [("institucion", "codigo")]
        ordering = ["nombre"]

    def __str__(self):
        return f"{self.codigo} · {self.nombre}"


class NotaTecnica(TenantModel):
    class Modalidad(models.TextChoices):
        EVENTO = "EVENTO", "Evento"
        CAPITA = "CAPITA", "Cápita"
        PGP = "PGP", "Pago global prospectivo"
        PAQUETE = "PAQUETE", "Paquete / canasta"

    eps = models.ForeignKey(Eps, on_delete=models.PROTECT, related_name="notas_tecnicas")
    contrato = models.CharField("número de contrato", max_length=60)
    modalidad = models.CharField(max_length=8, choices=Modalidad.choices, default=Modalidad.EVENTO)
    vigencia_desde = models.DateField()
    vigencia_hasta = models.DateField()
    manual_tarifario = models.CharField(max_length=40, blank=True, help_text="SOAT, ISS 2001, propio...")
    ajuste_porcentaje = models.DecimalField("ajuste sobre manual (%)", max_digits=7, decimal_places=2, default=0)
    valor_mensual = models.DecimalField("valor mensual (cápita/PGP)", max_digits=18, decimal_places=2, default=0)
    poblacion = models.PositiveIntegerField("población / afiliados", default=0)
    condiciones = models.TextField(blank=True)

    class Meta:
        verbose_name = "nota técnica"
        verbose_name_plural = "notas técnicas"
        unique_together = [("institucion", "eps", "contrato")]
        ordering = ["eps__nombre", "-vigencia_desde"]

    def clean(self):
        if self.vigencia_desde and self.vigencia_hasta and self.vigencia_hasta < self.vigencia_desde:
            raise ValidationError({"vigencia_hasta": "La vigencia final es anterior a la inicial."})

    def vigente_en(self, periodo):
        """Vigente si el contrato se cruza con algún día del mes del periodo."""
        import calendar
        from datetime import date

        inicio = date(periodo.anio, periodo.mes, 1)
        fin = date(periodo.anio, periodo.mes, calendar.monthrange(periodo.anio, periodo.mes)[1])
        return self.vigencia_desde <= fin and self.vigencia_hasta >= inicio

    def __str__(self):
        return f"{self.eps.nombre} · {self.contrato} ({self.get_modalidad_display()})"


class NotaTecnicaItem(TenantModel):
    nota = models.ForeignKey(NotaTecnica, on_delete=models.CASCADE, related_name="items")
    cups = models.ForeignKey(Cups, on_delete=models.PROTECT, related_name="+")
    tarifa = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    frecuencia_esperada = models.DecimalField("frecuencia esperada mensual", max_digits=14, decimal_places=4, default=0)
    ambito = models.ForeignKey(Ambito, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    observaciones = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "ítem de nota técnica"
        unique_together = [("institucion", "nota", "cups", "ambito")]

    def __str__(self):
        return f"{self.nota.contrato} · {self.cups.codigo} = {self.tarifa}"


class IndicadorReferencia(TenantModel):
    """Indicador de benchmarking del informe (sección BENCHMARKING NACIONAL)."""

    codigo = models.CharField(max_length=30)
    nombre = models.CharField(max_length=150)
    formula = models.CharField(
        max_length=60,
        help_text="pct_elemento:CP | pct_recurso:R3 | pct_recurso_en_elemento:R3 | pct_un:UN09 | "
                  "pct_un_en_finales:UN02 | valor_recurso:R17 | crecimiento_total",
    )
    minimo = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    maximo = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    es_valor = models.BooleanField(default=False, help_text="El indicador es un valor en pesos y no un porcentaje.")
    fuente = models.CharField(max_length=200, blank=True)
    accion_recomendada = models.CharField(max_length=250, blank=True)
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "indicador de referencia"
        verbose_name_plural = "indicadores de referencia"
        unique_together = [("institucion", "codigo")]
        ordering = ["orden", "codigo"]

    def __str__(self):
        return self.nombre


class ConfiguracionInforme(TenantModel):
    elaborado_por = models.CharField(max_length=150, blank=True)
    cargo = models.CharField(max_length=150, blank=True)
    dirigido_a = models.CharField(max_length=150, blank=True, default="Gerencia General")
    introduccion = models.TextField(blank=True)
    conclusiones = models.TextField(blank=True)
    recomendaciones = models.TextField(blank=True, help_text="Una recomendación por línea: PRIORIDAD | ÁREA | RECOMENDACIÓN | IMPACTO | PLAZO | RESPONSABLE")
    umbral_variacion = models.DecimalField("umbral de variación para hallazgos (%)", max_digits=7, decimal_places=2,
                                           default=Decimal("30"))
    recursos_por_sede = models.CharField(max_length=100, default="R14,R15,R16,R21",
                                         help_text="Recursos que se muestran en la distribución por sede.")

    class Meta:
        verbose_name = "configuración del informe"

    def __str__(self):
        return f"Informe · {self.institucion}"

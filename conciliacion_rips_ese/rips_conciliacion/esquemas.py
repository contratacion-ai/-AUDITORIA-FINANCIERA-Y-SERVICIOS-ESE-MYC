"""Estructuras oficiales de RIPS.

* Resolución 3374 de 2000: archivos planos TXT separados por coma, sin
  encabezado. Los nombres de los campos se toman literalmente del anexo
  técnico de la resolución (los TXT no traen nombres propios).
* Resolución 2275 de 2023: un JSON por factura (FEV-RIPS). Se conservan
  exactamente las llaves del JSON (``numFactura``, ``codConsulta``,
  ``vrServicio``...).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Resolución 3374 de 2000 – archivos TXT
# ---------------------------------------------------------------------------

F_FACTURA = "Número de la factura"
F_PRESTADOR = "Código del prestador de servicios de salud"
F_TIPO_ID = "Tipo de identificación del usuario"
F_NUM_ID = "Número de identificación del usuario en el sistema"
F_AUTORIZACION = "Número de autorización"


@dataclass(frozen=True)
class Esquema3374:
    codigo: str  # prefijo del archivo: AF, US, AC...
    descripcion: str
    campos: tuple[str, ...]
    campos_obligatorios: tuple[str, ...] = ()
    campos_fecha: tuple[str, ...] = ()
    campos_numericos: tuple[str, ...] = ()
    # Para conciliación
    categoria: str | None = None  # CONSULTA, PROCEDIMIENTO, MEDICAMENTO...
    campo_factura: str | None = None
    campo_codigo: str | None = None
    campo_cantidad: str | None = None  # None -> cantidad 1 por registro
    campo_valor: str | None = None
    campo_valor_unitario: str | None = None
    campo_fecha: str | None = None
    campo_prestador: str | None = None
    campo_ambito: str | None = None
    campos_codigos_adicionales: tuple[str, ...] = field(default_factory=tuple)

    @property
    def num_campos(self) -> int:
        return len(self.campos)


CT = Esquema3374(
    codigo="CT",
    descripcion="Archivo de control",
    campos=(
        "Código del prestador de servicios de salud",
        "Fecha de remisión",
        "Código del archivo",
        "Total de registros",
    ),
    campos_obligatorios=("Código del archivo", "Total de registros"),
    campos_fecha=("Fecha de remisión",),
    campos_numericos=("Total de registros",),
)

AF = Esquema3374(
    codigo="AF",
    descripcion="Transacciones (facturas)",
    campos=(
        "Código del prestador de servicios de salud",
        "Razón social o apellidos y nombre del prestador",
        "Tipo de identificación del prestador",
        "Número de identificación del prestador",
        "Número de la factura",
        "Fecha de expedición de la factura",
        "Fecha de inicio",
        "Fecha final",
        "Código entidad administradora",
        "Nombre entidad administradora",
        "Número del contrato",
        "Plan de beneficios",
        "Número de la póliza",
        "Valor total del pago compartido (copago)",
        "Valor de la comisión",
        "Valor total de descuentos",
        "Valor neto a pagar por la entidad contratante",
    ),
    campos_obligatorios=("Número de la factura",),
    campos_fecha=(
        "Fecha de expedición de la factura",
        "Fecha de inicio",
        "Fecha final",
    ),
    campos_numericos=(
        "Valor total del pago compartido (copago)",
        "Valor de la comisión",
        "Valor total de descuentos",
        "Valor neto a pagar por la entidad contratante",
    ),
    campo_factura="Número de la factura",
    campo_valor="Valor neto a pagar por la entidad contratante",
    campo_fecha="Fecha de expedición de la factura",
    campo_prestador="Código del prestador de servicios de salud",
)

US = Esquema3374(
    codigo="US",
    descripcion="Usuarios",
    campos=(
        "Tipo de identificación del usuario",
        "Número de identificación del usuario en el sistema",
        "Código entidad administradora",
        "Tipo de usuario",
        "Primer apellido del usuario",
        "Segundo apellido del usuario",
        "Primer nombre del usuario",
        "Segundo nombre del usuario",
        "Edad",
        "Unidad de medida de la edad",
        "Sexo",
        "Código del departamento de residencia habitual",
        "Código del municipio de residencia habitual",
        "Zona de residencia habitual",
    ),
    campos_obligatorios=(
        "Tipo de identificación del usuario",
        "Número de identificación del usuario en el sistema",
    ),
    campos_numericos=("Edad",),
)

AC = Esquema3374(
    codigo="AC",
    descripcion="Consultas",
    campos=(
        F_FACTURA,
        "Código del prestador de servicios de salud",
        F_TIPO_ID,
        F_NUM_ID,
        "Fecha de la consulta",
        F_AUTORIZACION,
        "Código de consulta",
        "Finalidad de la consulta",
        "Causa externa",
        "Código del diagnóstico principal",
        "Código del diagnóstico relacionado No. 1",
        "Código del diagnóstico relacionado No. 2",
        "Código del diagnóstico relacionado No. 3",
        "Tipo de diagnóstico principal",
        "Valor de la consulta",
        "Valor de la cuota moderadora",
        "Valor neto a pagar",
    ),
    campos_obligatorios=(F_FACTURA, "Código de consulta"),
    campos_fecha=("Fecha de la consulta",),
    campos_numericos=(
        "Valor de la consulta",
        "Valor de la cuota moderadora",
        "Valor neto a pagar",
    ),
    categoria="CONSULTA",
    campo_factura=F_FACTURA,
    campo_codigo="Código de consulta",
    campo_valor="Valor de la consulta",
    campo_fecha="Fecha de la consulta",
    campo_prestador="Código del prestador de servicios de salud",
    campos_codigos_adicionales=("Código del diagnóstico principal",),
)

AP = Esquema3374(
    codigo="AP",
    descripcion="Procedimientos",
    campos=(
        F_FACTURA,
        "Código del prestador de servicios de salud",
        F_TIPO_ID,
        F_NUM_ID,
        "Fecha del procedimiento",
        F_AUTORIZACION,
        "Código del procedimiento",
        "Ámbito de realización del procedimiento",
        "Finalidad del procedimiento",
        "Personal que atiende",
        "Diagnóstico principal",
        "Código del diagnóstico relacionado",
        "Código de diagnóstico de la complicación",
        "Forma de realización del acto quirúrgico",
        "Valor del procedimiento",
    ),
    campos_obligatorios=(F_FACTURA, "Código del procedimiento"),
    campos_fecha=("Fecha del procedimiento",),
    campos_numericos=("Valor del procedimiento",),
    categoria="PROCEDIMIENTO",
    campo_factura=F_FACTURA,
    campo_codigo="Código del procedimiento",
    campo_valor="Valor del procedimiento",
    campo_fecha="Fecha del procedimiento",
    campo_prestador="Código del prestador de servicios de salud",
    campo_ambito="Ámbito de realización del procedimiento",
    campos_codigos_adicionales=("Diagnóstico principal",),
)

AU = Esquema3374(
    codigo="AU",
    descripcion="Urgencias con observación",
    campos=(
        F_FACTURA,
        "Código del prestador de servicios de salud",
        F_TIPO_ID,
        F_NUM_ID,
        "Fecha de ingreso del usuario a observación",
        "Hora de ingreso del usuario a observación",
        F_AUTORIZACION,
        "Causa externa",
        "Diagnóstico de la salida",
        "Diagnóstico relacionado No. 1 a la salida",
        "Diagnóstico relacionado No. 2 a la salida",
        "Diagnóstico relacionado No. 3 a la salida",
        "Destino del usuario a la salida de observación",
        "Estado a la salida",
        "Causa básica de muerte en urgencias",
        "Fecha de la salida del usuario en observación",
        "Hora de la salida del usuario de observación",
    ),
    campos_obligatorios=(F_FACTURA,),
    campos_fecha=(
        "Fecha de ingreso del usuario a observación",
        "Fecha de la salida del usuario en observación",
    ),
    categoria="URGENCIAS",
    campo_factura=F_FACTURA,
    campo_fecha="Fecha de ingreso del usuario a observación",
    campo_prestador="Código del prestador de servicios de salud",
)

AH = Esquema3374(
    codigo="AH",
    descripcion="Hospitalización",
    campos=(
        F_FACTURA,
        "Código del prestador de servicios de salud",
        F_TIPO_ID,
        F_NUM_ID,
        "Vía de ingreso a la institución",
        "Fecha de ingreso del usuario a la institución",
        "Hora de ingreso del usuario a la institución",
        F_AUTORIZACION,
        "Causa externa",
        "Diagnóstico principal de ingreso",
        "Diagnóstico principal de egreso",
        "Diagnóstico relacionado No. 1 de egreso",
        "Diagnóstico relacionado No. 2 de egreso",
        "Diagnóstico relacionado No. 3 de egreso",
        "Diagnóstico de la complicación",
        "Estado a la salida",
        "Diagnóstico de la causa básica de muerte",
        "Fecha de egreso del usuario a la institución",
        "Hora de egreso del usuario de la institución",
    ),
    campos_obligatorios=(F_FACTURA,),
    campos_fecha=(
        "Fecha de ingreso del usuario a la institución",
        "Fecha de egreso del usuario a la institución",
    ),
    categoria="HOSPITALIZACION",
    campo_factura=F_FACTURA,
    campo_fecha="Fecha de ingreso del usuario a la institución",
    campo_prestador="Código del prestador de servicios de salud",
)

AN = Esquema3374(
    codigo="AN",
    descripcion="Recién nacidos",
    campos=(
        F_FACTURA,
        "Código del prestador de servicios de salud",
        "Tipo de identificación de la madre",
        "Número de identificación de la madre en el sistema",
        "Fecha de nacimiento del recién nacido",
        "Hora de nacimiento",
        "Edad gestacional",
        "Control prenatal",
        "Sexo",
        "Peso",
        "Diagnóstico del recién nacido",
        "Causa básica de muerte",
        "Fecha de muerte del recién nacido",
        "Hora de muerte del recién nacido",
    ),
    campos_obligatorios=(F_FACTURA,),
    campos_fecha=("Fecha de nacimiento del recién nacido",),
    categoria="RECIEN_NACIDO",
    campo_factura=F_FACTURA,
    campo_fecha="Fecha de nacimiento del recién nacido",
    campo_prestador="Código del prestador de servicios de salud",
)

AM = Esquema3374(
    codigo="AM",
    descripcion="Medicamentos",
    campos=(
        F_FACTURA,
        "Código del prestador de servicios de salud",
        F_TIPO_ID,
        F_NUM_ID,
        F_AUTORIZACION,
        "Código del medicamento",
        "Tipo de medicamento",
        "Nombre genérico del medicamento",
        "Forma farmacéutica",
        "Concentración del medicamento",
        "Unidad de medida del medicamento",
        "Número de unidades",
        "Valor unitario de medicamento",
        "Valor total de medicamento",
    ),
    campos_obligatorios=(F_FACTURA, "Código del medicamento"),
    campos_numericos=(
        "Número de unidades",
        "Valor unitario de medicamento",
        "Valor total de medicamento",
    ),
    categoria="MEDICAMENTO",
    campo_factura=F_FACTURA,
    campo_codigo="Código del medicamento",
    campo_cantidad="Número de unidades",
    campo_valor="Valor total de medicamento",
    campo_valor_unitario="Valor unitario de medicamento",
    campo_prestador="Código del prestador de servicios de salud",
)

AT = Esquema3374(
    codigo="AT",
    descripcion="Otros servicios",
    campos=(
        F_FACTURA,
        "Código del prestador de servicios de salud",
        F_TIPO_ID,
        F_NUM_ID,
        F_AUTORIZACION,
        "Tipo de servicio",
        "Código del servicio",
        "Nombre del servicio",
        "Cantidad",
        "Valor unitario del material e insumo",
        "Valor total del material e insumo",
    ),
    campos_obligatorios=(F_FACTURA, "Código del servicio"),
    campos_numericos=(
        "Cantidad",
        "Valor unitario del material e insumo",
        "Valor total del material e insumo",
    ),
    categoria="OTRO_SERVICIO",
    campo_factura=F_FACTURA,
    campo_codigo="Código del servicio",
    campo_cantidad="Cantidad",
    campo_valor="Valor total del material e insumo",
    campo_valor_unitario="Valor unitario del material e insumo",
    campo_prestador="Código del prestador de servicios de salud",
)

ESQUEMAS_3374: dict[str, Esquema3374] = {
    e.codigo: e for e in (CT, AF, US, AC, AP, AU, AH, AN, AM, AT)
}

# Tipos de archivo que aportan líneas conciliables (tienen código y valor)
TIPOS_CON_VALOR_3374 = ("AC", "AP", "AM", "AT")

# ---------------------------------------------------------------------------
# Resolución 2275 de 2023 – JSON
# ---------------------------------------------------------------------------

LLAVES_RAIZ_2275 = (
    "numDocumentoIdObligado",
    "numFactura",
    "tipoNota",
    "numNota",
    "usuarios",
)
LLAVES_USUARIO_2275 = (
    "tipoDocumentoIdentificacion",
    "numDocumentoIdentificacion",
    "tipoUsuario",
    "fechaNacimiento",
    "codSexo",
    "codPaisResidencia",
    "codMunicipioResidencia",
    "codZonaTerritorialResidencia",
    "incapacidad",
    "consecutivo",
    "codPaisOrigen",
    "servicios",
)


@dataclass(frozen=True)
class Esquema2275:
    servicio: str  # llave en "servicios": consultas, procedimientos...
    categoria: str
    campos_obligatorios: tuple[str, ...]
    campo_codigo: str | None = None
    campo_valor: str | None = None
    campo_cantidad: str | None = None
    campo_valor_unitario: str | None = None
    campo_fecha: str | None = None
    campos_numericos: tuple[str, ...] = ()
    campos_codigos_adicionales: tuple[str, ...] = ()


ESQUEMAS_2275: dict[str, Esquema2275] = {
    "consultas": Esquema2275(
        servicio="consultas",
        categoria="CONSULTA",
        campos_obligatorios=(
            "codPrestador",
            "fechaInicioAtencion",
            "codConsulta",
            "vrServicio",
        ),
        campo_codigo="codConsulta",
        campo_valor="vrServicio",
        campo_fecha="fechaInicioAtencion",
        campos_numericos=("vrServicio", "valorPagoModerador"),
        campos_codigos_adicionales=("codServicio", "codDiagnosticoPrincipal"),
    ),
    "procedimientos": Esquema2275(
        servicio="procedimientos",
        categoria="PROCEDIMIENTO",
        campos_obligatorios=(
            "codPrestador",
            "fechaInicioAtencion",
            "codProcedimiento",
            "vrServicio",
        ),
        campo_codigo="codProcedimiento",
        campo_valor="vrServicio",
        campo_fecha="fechaInicioAtencion",
        campos_numericos=("vrServicio", "valorPagoModerador"),
        campos_codigos_adicionales=("codServicio", "codDiagnosticoPrincipal"),
    ),
    "medicamentos": Esquema2275(
        servicio="medicamentos",
        categoria="MEDICAMENTO",
        campos_obligatorios=(
            "codPrestador",
            "codTecnologiaSalud",
            "cantidadMedicamento",
            "vrServicio",
        ),
        campo_codigo="codTecnologiaSalud",
        campo_valor="vrServicio",
        campo_cantidad="cantidadMedicamento",
        campo_valor_unitario="vrUnitMedicamento",
        campo_fecha="fechaDispensAdmon",
        campos_numericos=(
            "cantidadMedicamento",
            "vrUnitMedicamento",
            "vrServicio",
            "valorPagoModerador",
        ),
        campos_codigos_adicionales=("codDiagnosticoPrincipal",),
    ),
    "otrosServicios": Esquema2275(
        servicio="otrosServicios",
        categoria="OTRO_SERVICIO",
        campos_obligatorios=(
            "codPrestador",
            "codTecnologiaSalud",
            "cantidadOS",
            "vrServicio",
        ),
        campo_codigo="codTecnologiaSalud",
        campo_valor="vrServicio",
        campo_cantidad="cantidadOS",
        campo_valor_unitario="vrUnitOS",
        campo_fecha="fechaSuministroTecnologia",
        campos_numericos=("cantidadOS", "vrUnitOS", "vrServicio", "valorPagoModerador"),
    ),
    "urgencias": Esquema2275(
        servicio="urgencias",
        categoria="URGENCIAS",
        campos_obligatorios=("codPrestador", "fechaInicioAtencion"),
        campo_fecha="fechaInicioAtencion",
        campos_codigos_adicionales=("codDiagnosticoPrincipal",),
    ),
    "hospitalizacion": Esquema2275(
        servicio="hospitalizacion",
        categoria="HOSPITALIZACION",
        campos_obligatorios=("codPrestador", "fechaInicioAtencion"),
        campo_fecha="fechaInicioAtencion",
        campos_codigos_adicionales=("codDiagnosticoPrincipal",),
    ),
    "recienNacidos": Esquema2275(
        servicio="recienNacidos",
        categoria="RECIEN_NACIDO",
        campos_obligatorios=("codPrestador", "fechaNacimiento"),
        campo_fecha="fechaNacimiento",
    ),
}

# Equivalencia de categorías entre versiones (para reportes comparables)
CATEGORIA_POR_TIPO_3374 = {
    k: v.categoria for k, v in ESQUEMAS_3374.items() if v.categoria
}
CATEGORIA_POR_SERVICIO_2275 = {k: v.categoria for k, v in ESQUEMAS_2275.items()}

# Categorías que corresponden a farmacia (dispensación de medicamentos)
CATEGORIAS_FARMACIA = ("MEDICAMENTO",)

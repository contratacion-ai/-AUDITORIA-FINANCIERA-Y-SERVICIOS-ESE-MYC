"""Especificación de los archivos RIPS según el Anexo Técnico No. 1 de la
Resolución 3374 de 2000 (numeral 2: archivos que transfiere la IPS a la
entidad administradora de planes de beneficios).

Este módulo es la ÚNICA fuente de verdad sobre estructura: orden de campos,
longitudes máximas, tipo de dato, obligatoriedad y valores permitidos. El
parser, el validador y las plantillas Excel se generan a partir de aquí, de
modo que un ajuste normativo se hace en un solo lugar.

Decisiones tomadas ante vacíos del Anexo (documentadas):

* Obligatoriedad: el Anexo no trae una columna "obligatorio". Se marcó como
  obligatorio todo campo cuya descripción no diga "en blanco cuando...",
  "cuando aplique", "cuando se requiera", "no es obligatorio" o
  "diligenciable sólo cuando...". Los campos "diligenciables obligatoriamente
  cuando los servicios se hayan prestado a población vinculada" (archivo US)
  se modelan como obligatorios condicionados a tipo de usuario = 3.
* Tipos de dato: el Anexo sólo indica longitud y formato de fecha/hora/valor.
  Se clasificaron como: A (alfanumérico), E (entero), V (valor con punto
  decimal), F (fecha dd/mm/aaaa), H (hora hh:mm), C (código de catálogo) y
  X (código CIE-10, 4 caracteres).
* Códigos con o sin cero a la izquierda ("1" vs "01") se consideran
  equivalentes al comparar con el catálogo (ver ``validacion.py``).
* Catálogos extendidos: los RIPS reales de la ESE ya usan códigos de normas
  posteriores (p. ej. tipo de documento CN/PT, finalidad 11-44 y causa
  21-49 de la Resolución 2275 de 2023). Un valor que no está en la tabla de
  la 3374 pero sí en el catálogo extendido se reporta como ADVERTENCIA y no
  como ERROR. Fuente del catálogo extendido: hoja "Listas" de la plantilla
  FEV-RIPS v1.0 entregada por el usuario.
* Archivo AD (descripción agrupada): se incluye por completitud normativa,
  aunque no aparece en el material de ejemplo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Catálogos de la Resolución 3374 de 2000
# ---------------------------------------------------------------------------

TIPO_ID_USUARIO = {
    "CC": "Cédula de ciudadanía",
    "CE": "Cédula de extranjería",
    "PA": "Pasaporte",
    "RC": "Registro civil",
    "TI": "Tarjeta de identidad",
    "AS": "Adulto sin identificación",
    "MS": "Menor sin identificación",
    "NU": "Número único de identificación",
}
TIPO_ID_PRESTADOR = {
    "NI": "NIT",
    "CC": "Cédula de ciudadanía",
    "CE": "Cédula de extranjería",
    "PA": "Pasaporte",
}
TIPO_USUARIO = {
    "1": "Contributivo",
    "2": "Subsidiado",
    "3": "Vinculado",
    "4": "Particular",
    "5": "Otro",
}
UNIDAD_EDAD = {"1": "Años", "2": "Meses", "3": "Días"}
SEXO = {"M": "Masculino", "F": "Femenino"}
ZONA = {"U": "Urbana", "R": "Rural"}
CONCEPTO_AD = {
    "01": "Consultas",
    "02": "Procedimientos de diagnósticos",
    "03": "Procedimientos terapéuticos no quirúrgicos",
    "04": "Procedimientos terapéuticos quirúrgicos",
    "05": "Procedimientos de promoción y prevención",
    "06": "Estancias",
    "07": "Honorarios",
    "08": "Derechos de sala",
    "09": "Materiales e insumos",
    "10": "Banco de sangre",
    "11": "Prótesis y órtesis",
    "12": "Medicamentos POS",
    "13": "Medicamentos no POS",
    "14": "Traslado de pacientes",
}
FINALIDAD_CONSULTA = {
    "01": "Atención del parto (puerperio)",
    "02": "Atención del recién nacido",
    "03": "Atención en planificación familiar",
    "04": "Detección de alteraciones de crecimiento y desarrollo del menor de diez años",
    "05": "Detección de alteración del desarrollo joven",
    "06": "Detección de alteraciones del embarazo",
    "07": "Detección de alteraciones del adulto",
    "08": "Detección de alteraciones de agudeza visual",
    "09": "Detección de enfermedad profesional",
    "10": "No aplica",
}
CAUSA_EXTERNA = {
    "01": "Accidente de trabajo",
    "02": "Accidente de tránsito",
    "03": "Accidente rábico",
    "04": "Accidente ofídico",
    "05": "Otro tipo de accidente",
    "06": "Evento catastrófico",
    "07": "Lesión por agresión",
    "08": "Lesión auto infligida",
    "09": "Sospecha de maltrato físico",
    "10": "Sospecha de abuso sexual",
    "11": "Sospecha de violencia sexual",
    "12": "Sospecha de maltrato emocional",
    "13": "Enfermedad general",
    "14": "Enfermedad profesional",
    "15": "Otra",
}
TIPO_DIAGNOSTICO = {
    "1": "Impresión diagnóstica",
    "2": "Confirmado nuevo",
    "3": "Confirmado repetido",
}
AMBITO_PROCEDIMIENTO = {"1": "Ambulatorio", "2": "Hospitalario", "3": "En urgencias"}
FINALIDAD_PROCEDIMIENTO = {
    "1": "Diagnóstico",
    "2": "Terapéutico",
    "3": "Protección específica",
    "4": "Detección temprana de enfermedad general",
    "5": "Detección temprana de enfermedad profesional",
}
PERSONAL_ATIENDE = {
    "1": "Médico (a) especialista",
    "2": "Médico (a) general",
    "3": "Enfermera (o)",
    "4": "Auxiliar de enfermería",
    "5": "Otro",
}
FORMA_ACTO_QX = {
    "1": "Único o unilateral",
    "2": "Múltiple o bilateral, misma vía, diferente especialidad",
    "3": "Múltiple o bilateral, misma vía, igual especialidad",
    "4": "Múltiple o bilateral, diferente vía, diferente especialidad",
    "5": "Múltiple o bilateral, diferente vía, igual especialidad",
}
DESTINO_URGENCIAS = {
    "1": "Alta de urgencias",
    "2": "Remisión a otro nivel de complejidad",
    "3": "Hospitalización",
}
ESTADO_SALIDA = {"1": "Vivo (a)", "2": "Muerto (a)"}
VIA_INGRESO = {
    "1": "Urgencias",
    "2": "Consulta externa o programada",
    "3": "Remitido",
    "4": "Nacido en la institución",
}
CONTROL_PRENATAL = {"1": "Sí", "2": "No"}
TIPO_MEDICAMENTO = {"1": "Medicamento POS", "2": "Medicamento no POS"}
TIPO_SERVICIO_AT = {
    "1": "Materiales e insumos",
    "2": "Traslados",
    "3": "Estancias",
    "4": "Honorarios",
}

# ---------------------------------------------------------------------------
# Catálogos extendidos (normas posteriores; ver docstring del módulo)
# ---------------------------------------------------------------------------

EXT_TIPO_ID_USUARIO = {
    "CD": "Carné diplomático",
    "CN": "Certificado de nacido vivo",
    "DE": "Documento de identificación extranjero",
    "NI": "NIT",
    "NV": "Certificado de nacido vivo (código anterior)",
    "PE": "Permiso especial de permanencia",
    "PT": "Permiso por protección temporal",
    "SC": "Salvoconducto",
    "SI": "Sin identificación",
}
EXT_FINALIDAD_2275 = {f"{n:02d}": "Finalidad tecnología en salud (Res. 2275/2023)" for n in range(11, 45)}
EXT_CAUSA_2275 = {f"{n:02d}": "Causa motivo de atención (Res. 2275/2023)" for n in range(21, 50)}


# ---------------------------------------------------------------------------
# Modelo de especificación
# ---------------------------------------------------------------------------

TIPOS_DATO = {
    "A": "Alfanumérico",
    "E": "Entero",
    "V": "Valor (decimal con punto)",
    "F": "Fecha dd/mm/aaaa",
    "H": "Hora hh:mm",
    "C": "Código de catálogo",
    "X": "Código CIE-10",
}


@dataclass(frozen=True)
class Campo:
    """Un campo (columna) de un registro RIPS."""

    nombre: str  # identificador técnico (snake_case) usado en DataFrames/JSON
    etiqueta: str  # nombre del dato tal como aparece en el Anexo Técnico
    longitud: int  # longitud máxima según el Anexo
    tipo: str  # ver TIPOS_DATO
    obligatorio: bool = True
    valores: dict[str, str] | None = None  # catálogo de la Res. 3374
    valores_ext: dict[str, str] | None = None  # catálogo extendido (advertencia)
    # Obligatoriedad condicionada: (campo, valores) => obligatorio si el campo
    # indicado toma alguno de esos valores.
    obligatorio_si: tuple[str, tuple[str, ...]] | None = None


@dataclass(frozen=True)
class EspecArchivo:
    """Estructura de un tipo de archivo RIPS."""

    codigo: str  # CT, AF, US, ...
    nombre: str
    numeral: str  # numeral del Anexo Técnico No. 1
    campos: tuple[Campo, ...] = field(default_factory=tuple)

    @property
    def num_campos(self) -> int:
        return len(self.campos)

    @property
    def columnas(self) -> list[str]:
        return [c.nombre for c in self.campos]

    def campo(self, nombre: str) -> Campo:
        for c in self.campos:
            if c.nombre == nombre:
                return c
        raise KeyError(f"{self.codigo}: campo inexistente {nombre!r}")


def _c(nombre, etiqueta, longitud, tipo, obligatorio=True, valores=None, valores_ext=None, obligatorio_si=None):
    return Campo(nombre, etiqueta, longitud, tipo, obligatorio, valores, valores_ext, obligatorio_si)


# Campos comunes a los archivos de servicios
_FACTURA = _c("num_factura", "Número de la factura", 20, "A")
_PRESTADOR = _c("cod_prestador", "Código del prestador de servicios de salud", 10, "A")
_TIPO_ID = _c("tipo_id_usuario", "Tipo de identificación del usuario", 2, "C", valores=TIPO_ID_USUARIO, valores_ext=EXT_TIPO_ID_USUARIO)
_NUM_ID = _c("num_id_usuario", "Número de identificación del usuario en el Sistema", 20, "A")
_AUTORIZACION = _c("num_autorizacion", "Número de autorización", 15, "A", obligatorio=False)

_VINCULADO = ("tipo_usuario", ("3",))

# Nota sobre la longitud del código del prestador: el Anexo fija 10 posiciones,
# pero el REPS asigna códigos de 12 dígitos (10 + 2 de sede), que son los que
# usan todos los RIPS reales. Se acepta hasta 12 y se documenta aquí.
LONGITUD_COD_PRESTADOR = 12


def _ajustar_prestador(campos: tuple[Campo, ...]) -> tuple[Campo, ...]:
    out = []
    for c in campos:
        if c.nombre == "cod_prestador":
            c = Campo(**{**c.__dict__, "longitud": LONGITUD_COD_PRESTADOR})
        out.append(c)
    return tuple(out)


ESPECIFICACIONES: dict[str, EspecArchivo] = {}


def _registrar(codigo, nombre, numeral, campos):
    ESPECIFICACIONES[codigo] = EspecArchivo(codigo, nombre, numeral, _ajustar_prestador(tuple(campos)))


_registrar("CT", "Archivo de control", "2.1", [
    _PRESTADOR,
    _c("fecha_remision", "Fecha de remisión", 10, "F"),
    _c("codigo_archivo", "Código del archivo", 8, "A"),
    _c("total_registros", "Total de registros", 10, "E"),
])

_registrar("AF", "Archivo de las transacciones", "2.2", [
    _PRESTADOR,
    _c("razon_social", "Razón social o apellidos y nombre del prestador", 60, "A"),
    _c("tipo_id_prestador", "Tipo de identificación del prestador", 2, "C", valores=TIPO_ID_PRESTADOR),
    _c("num_id_prestador", "Número de identificación del prestador", 20, "A"),
    _FACTURA,
    _c("fecha_expedicion", "Fecha de expedición de la factura", 10, "F"),
    _c("fecha_inicio", "Fecha de inicio del período de facturación", 10, "F"),
    _c("fecha_final", "Fecha final del período de facturación", 10, "F"),
    _c("cod_entidad", "Código entidad administradora", 6, "A", obligatorio=False),
    _c("nombre_entidad", "Nombre entidad administradora", 30, "A", obligatorio=False),
    _c("num_contrato", "Número del contrato", 15, "A", obligatorio=False),
    _c("plan_beneficios", "Plan de beneficios", 30, "A", obligatorio=False),
    _c("num_poliza", "Número de la póliza", 10, "A", obligatorio=False),
    _c("valor_copago", "Valor total del pago compartido (copago)", 15, "V", obligatorio=False),
    _c("valor_comision", "Valor de la comisión", 15, "V", obligatorio=False),
    _c("valor_descuentos", "Valor total de descuentos", 15, "V", obligatorio=False),
    _c("valor_neto", "Valor neto a pagar por la entidad contratante", 15, "V"),
])

_registrar("US", "Archivo de usuarios de los servicios de salud", "2.4", [
    _TIPO_ID,
    _NUM_ID,
    _c("cod_entidad", "Código entidad administradora", 6, "A", obligatorio=False),
    _c("tipo_usuario", "Tipo de usuario", 1, "C", valores=TIPO_USUARIO),
    _c("primer_apellido", "Primer apellido del usuario", 30, "A", obligatorio=False, obligatorio_si=_VINCULADO),
    _c("segundo_apellido", "Segundo apellido del usuario", 30, "A", obligatorio=False),
    _c("primer_nombre", "Primer nombre del usuario", 20, "A", obligatorio=False, obligatorio_si=_VINCULADO),
    _c("segundo_nombre", "Segundo nombre del usuario", 20, "A", obligatorio=False),
    _c("edad", "Edad", 3, "E", obligatorio=False, obligatorio_si=_VINCULADO),
    _c("unidad_edad", "Unidad de medida de la edad", 1, "C", obligatorio=False, valores=UNIDAD_EDAD, obligatorio_si=_VINCULADO),
    _c("sexo", "Sexo", 1, "C", obligatorio=False, valores=SEXO, obligatorio_si=_VINCULADO),
    _c("cod_departamento", "Código del departamento de residencia habitual", 2, "A", obligatorio=False, obligatorio_si=_VINCULADO),
    _c("cod_municipio", "Código del municipio de residencia habitual", 3, "A", obligatorio=False, obligatorio_si=_VINCULADO),
    _c("zona", "Zona de residencia habitual", 1, "C", obligatorio=False, valores=ZONA, obligatorio_si=_VINCULADO),
])

_registrar("AD", "Archivo de descripción agrupada de los servicios de salud prestados", "2.3", [
    _FACTURA,
    _PRESTADOR,
    _c("cod_concepto", "Código del concepto", 2, "C", valores=CONCEPTO_AD),
    _c("cantidad", "Cantidad", 15, "E"),
    _c("valor_unitario", "Valor unitario", 15, "V", obligatorio=False),
    _c("valor_total", "Valor total por concepto", 15, "V"),
])

_registrar("AC", "Archivo de consulta", "2.4", [
    _FACTURA, _PRESTADOR, _TIPO_ID, _NUM_ID,
    _c("fecha_consulta", "Fecha de la consulta", 10, "F"),
    _AUTORIZACION,
    _c("cod_consulta", "Código de consulta", 8, "A"),
    _c("finalidad", "Finalidad de la consulta", 2, "C", valores=FINALIDAD_CONSULTA, valores_ext=EXT_FINALIDAD_2275),
    _c("causa_externa", "Causa externa", 2, "C", valores=CAUSA_EXTERNA, valores_ext=EXT_CAUSA_2275),
    _c("dx_principal", "Código del diagnóstico principal", 4, "X"),
    _c("dx_relacionado1", "Código del diagnóstico relacionado No. 1", 4, "X", obligatorio=False),
    _c("dx_relacionado2", "Código del diagnóstico relacionado No. 2", 4, "X", obligatorio=False),
    _c("dx_relacionado3", "Código del diagnóstico relacionado No. 3", 4, "X", obligatorio=False),
    _c("tipo_dx_principal", "Tipo de diagnóstico principal", 1, "C", valores=TIPO_DIAGNOSTICO),
    _c("valor_consulta", "Valor de la consulta", 15, "V"),
    _c("valor_cuota_moderadora", "Valor de la cuota moderadora", 15, "V", obligatorio=False),
    _c("valor_neto", "Valor neto a pagar", 15, "V"),
])

_registrar("AP", "Archivo de procedimientos", "2.4", [
    _FACTURA, _PRESTADOR, _TIPO_ID, _NUM_ID,
    _c("fecha_procedimiento", "Fecha del procedimiento", 10, "F"),
    _AUTORIZACION,
    _c("cod_procedimiento", "Código del procedimiento", 8, "A"),
    _c("ambito", "Ámbito de realización del procedimiento", 1, "C", valores=AMBITO_PROCEDIMIENTO),
    _c("finalidad", "Finalidad del procedimiento", 1, "C", valores=FINALIDAD_PROCEDIMIENTO, valores_ext=EXT_FINALIDAD_2275),
    _c("personal_atiende", "Personal que atiende", 1, "C", obligatorio=False, valores=PERSONAL_ATIENDE),
    _c("dx_principal", "Diagnóstico principal", 4, "X", obligatorio=False),
    _c("dx_relacionado", "Diagnóstico relacionado", 4, "X", obligatorio=False),
    _c("dx_complicacion", "Complicación", 4, "X", obligatorio=False),
    _c("forma_acto_qx", "Forma de realización del acto quirúrgico", 1, "C", obligatorio=False, valores=FORMA_ACTO_QX),
    _c("valor", "Valor del procedimiento", 15, "V"),
])

_registrar("AU", "Archivo de urgencias con observación", "2.4", [
    _FACTURA, _PRESTADOR, _TIPO_ID, _NUM_ID,
    _c("fecha_ingreso", "Fecha de ingreso del usuario a observación", 10, "F"),
    _c("hora_ingreso", "Hora de ingreso del usuario a observación", 5, "H"),
    _AUTORIZACION,
    _c("causa_externa", "Causa externa", 2, "C", valores=CAUSA_EXTERNA, valores_ext=EXT_CAUSA_2275),
    _c("dx_salida", "Diagnóstico a la salida", 4, "X"),
    _c("dx_relacionado1", "Diagnóstico relacionado No. 1 a la salida", 4, "X", obligatorio=False),
    _c("dx_relacionado2", "Diagnóstico relacionado No. 2 a la salida", 4, "X", obligatorio=False),
    _c("dx_relacionado3", "Diagnóstico relacionado No. 3 a la salida", 4, "X", obligatorio=False),
    _c("destino_salida", "Destino del usuario a la salida de observación", 1, "C", valores=DESTINO_URGENCIAS),
    _c("estado_salida", "Estado a la salida", 1, "C", valores=ESTADO_SALIDA),
    _c("causa_muerte", "Causa básica de muerte en urgencias", 4, "X", obligatorio=False, obligatorio_si=("estado_salida", ("2",))),
    _c("fecha_salida", "Fecha de salida del usuario de observación", 10, "F"),
    _c("hora_salida", "Hora de salida del usuario de observación", 5, "H"),
])

_registrar("AH", "Archivo de hospitalización", "2.4", [
    _FACTURA, _PRESTADOR, _TIPO_ID, _NUM_ID,
    _c("via_ingreso", "Vía de ingreso a la institución", 1, "C", valores=VIA_INGRESO),
    _c("fecha_ingreso", "Fecha de ingreso del usuario a la institución", 10, "F"),
    _c("hora_ingreso", "Hora de ingreso del usuario a la institución", 5, "H"),
    _AUTORIZACION,
    _c("causa_externa", "Causa externa", 2, "C", valores=CAUSA_EXTERNA, valores_ext=EXT_CAUSA_2275),
    _c("dx_ingreso", "Diagnóstico principal de ingreso", 4, "X"),
    _c("dx_egreso", "Diagnóstico principal de egreso", 4, "X"),
    _c("dx_relacionado1", "Diagnóstico relacionado No. 1 de egreso", 4, "X", obligatorio=False),
    _c("dx_relacionado2", "Diagnóstico relacionado No. 2 de egreso", 4, "X", obligatorio=False),
    _c("dx_relacionado3", "Diagnóstico relacionado No. 3 de egreso", 4, "X", obligatorio=False),
    _c("dx_complicacion", "Diagnóstico de la complicación", 4, "X", obligatorio=False),
    _c("estado_salida", "Estado a la salida", 1, "C", valores=ESTADO_SALIDA),
    _c("causa_muerte", "Diagnóstico de la causa básica de muerte", 4, "X", obligatorio=False, obligatorio_si=("estado_salida", ("2",))),
    _c("fecha_egreso", "Fecha de egreso del usuario a la institución", 10, "F"),
    _c("hora_egreso", "Hora de egreso del usuario a la institución", 5, "H"),
])

_registrar("AN", "Archivo de recién nacidos", "2.4", [
    _FACTURA, _PRESTADOR,
    _c("tipo_id_madre", "Tipo de identificación de la madre", 2, "C", valores=TIPO_ID_USUARIO, valores_ext=EXT_TIPO_ID_USUARIO),
    _c("num_id_madre", "Número de identificación de la madre", 20, "A"),
    _c("fecha_nacimiento", "Fecha de nacimiento del recién nacido", 10, "F"),
    _c("hora_nacimiento", "Hora de nacimiento", 5, "H"),
    _c("edad_gestacional", "Edad gestacional (semanas)", 2, "E"),
    _c("control_prenatal", "Control prenatal", 1, "C", valores=CONTROL_PRENATAL),
    _c("sexo", "Sexo", 1, "C", valores=SEXO),
    _c("peso", "Peso (gramos)", 4, "E"),
    _c("dx_recien_nacido", "Diagnóstico del recién nacido", 4, "X"),
    _c("causa_muerte", "Causa básica de muerte", 4, "X", obligatorio=False),
    _c("fecha_muerte", "Fecha de muerte del recién nacido", 10, "F", obligatorio=False),
    _c("hora_muerte", "Hora de muerte del recién nacido", 5, "H", obligatorio=False),
])

_NO_POS = ("tipo_medicamento", ("2",))
_registrar("AM", "Archivo de medicamentos", "2.4", [
    _FACTURA, _PRESTADOR, _TIPO_ID, _NUM_ID,
    _AUTORIZACION,
    _c("cod_medicamento", "Código del medicamento", 20, "A", obligatorio=False, obligatorio_si=_NO_POS),
    _c("tipo_medicamento", "Tipo de medicamento", 1, "C", valores=TIPO_MEDICAMENTO),
    _c("nombre_generico", "Nombre genérico del medicamento", 30, "A"),
    _c("forma_farmaceutica", "Forma farmacéutica", 20, "A", obligatorio=False, obligatorio_si=_NO_POS),
    _c("concentracion", "Concentración del medicamento", 20, "A", obligatorio=False, obligatorio_si=_NO_POS),
    _c("unidad_medida", "Unidad de medida del medicamento", 20, "A", obligatorio=False, obligatorio_si=_NO_POS),
    _c("num_unidades", "Número de unidades", 5, "V"),
    _c("valor_unitario", "Valor unitario de medicamento", 15, "V"),
    _c("valor_total", "Valor total de medicamento", 15, "V"),
])

_registrar("AT", "Archivo de otros servicios", "2.4", [
    _FACTURA, _PRESTADOR, _TIPO_ID, _NUM_ID,
    _AUTORIZACION,
    _c("tipo_servicio", "Tipo de servicio", 1, "C", valores=TIPO_SERVICIO_AT),
    _c("cod_servicio", "Código del servicio", 20, "A", obligatorio=False),
    # "Si no existe el código del servicio, es obligatorio diligenciar el
    # nombre": se valida como regla cruzada en validacion.py.
    _c("nombre_servicio", "Nombre del servicio", 60, "A", obligatorio=False),
    _c("cantidad", "Cantidad", 5, "V"),
    _c("valor_unitario", "Valor unitario", 15, "V", obligatorio=False),
    _c("valor_total", "Valor total", 15, "V"),
])

# Orden canónico de presentación (control, transacciones, usuarios, servicios)
ORDEN_ARCHIVOS = ["CT", "AF", "US", "AD", "AC", "AP", "AU", "AH", "AN", "AM", "AT"]
ARCHIVOS_SERVICIO = ["AC", "AP", "AU", "AH", "AN", "AM", "AT"]
# Archivos que toda remisión debe contener (Anexo, numeral 1: control,
# transacciones y usuarios son la base de cualquier envío).
ARCHIVOS_OBLIGATORIOS = ["CT", "AF", "US"]

# Campos que identifican al usuario en cada archivo de servicio.
CLAVE_USUARIO = {t: ("tipo_id_usuario", "num_id_usuario") for t in ["AC", "AP", "AU", "AH", "AM", "AT"]}
CLAVE_USUARIO["AN"] = ("tipo_id_madre", "num_id_madre")

# Campo de fecha de atención por archivo (para validar período y conciliación)
FECHA_SERVICIO = {
    "AC": "fecha_consulta",
    "AP": "fecha_procedimiento",
    "AU": "fecha_ingreso",
    "AH": "fecha_ingreso",
    "AN": "fecha_nacimiento",
}

# Campo de valor facturado por archivo, usado en la conciliación con AF.
# AU, AH y AN no tienen valor en la Res. 3374 (se facturan vía AP/AT/AM).
VALOR_SERVICIO = {
    "AC": "valor_neto",
    "AP": "valor",
    "AM": "valor_total",
    "AT": "valor_total",
}


def especificacion(codigo: str) -> EspecArchivo:
    try:
        return ESPECIFICACIONES[codigo.upper()]
    except KeyError as exc:
        raise KeyError(f"Tipo de archivo RIPS desconocido: {codigo!r}") from exc

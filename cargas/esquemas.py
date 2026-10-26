"""Esquemas de los tipos de archivo admitidos.

Cada tipo define sus campos destino (canónicos). La plantilla estándar usa como encabezado la
`etiqueta` de cada campo; las plantillas de mapeo de cada institución asocian sus propias
columnas a estos campos, de modo que el motor nunca depende del formato de origen.
"""
from dataclasses import dataclass, field

TEXTO, NUMERO, FECHA, CODIGO = "texto", "numero", "fecha", "codigo"


@dataclass(frozen=True)
class Campo:
    nombre: str
    etiqueta: str
    tipo: str = TEXTO
    requerido: bool = False
    ayuda: str = ""
    sinonimos: tuple = ()
    ejemplo: object = ""


@dataclass(frozen=True)
class Esquema:
    codigo: str
    nombre: str
    descripcion: str
    campos: tuple
    periodico: bool = True
    fuente: str = ""  # EF | AREA para movimientos contables
    claves_duplicado: tuple = field(default_factory=tuple)

    def campo(self, nombre):
        return next(c for c in self.campos if c.nombre == nombre)

    @property
    def requeridos(self):
        return [c for c in self.campos if c.requerido]


C = Campo

_MOV_COMUNES = (
    C("cuenta", "CUENTA", CODIGO, ayuda="Cuenta contable (solo dígitos). Determina el recurso por prefijo.",
      sinonimos=("CODIGO CUENTA", "CUENTA CONTABLE", "CODIGOCUENTA", "AUXILIAR", "CTA"), ejemplo="51010101"),
    C("recurso", "RECURSO", CODIGO, ayuda="Código del recurso (R1…R29). Si se indica, prevalece sobre la cuenta.",
      sinonimos=("COD RECURSO",), ejemplo=""),
    C("centro_costo", "CENTRO DE COSTOS", CODIGO,
      ayuda="Código del modelo (A10) o código homologado de la contabilidad.",
      sinonimos=("CENTRO COSTO", "CENTRO DE COSTO", "CECO", "COD CENTRO", "NOMBRECENTROCOSTO", "CENTRO DE COSTOS MYC",
                 "CODIGO CECO"), ejemplo="A10"),
    C("sede", "SEDE", CODIGO, ayuda="Código de sede; permite repartir por sede (energía, agua, aseo).",
      sinonimos=("MUNICIPIO", "PUNTO DE ATENCION"), ejemplo=""),
)

ESQUEMAS = {}


def _registrar(esquema):
    ESQUEMAS[esquema.codigo] = esquema
    return esquema


_registrar(Esquema(
    "ESTADO_FINANCIERO", "Estado financiero / balance de prueba",
    "Saldos o movimientos del mes por cuenta contable (clase 5, 6 y 7). Es la fuente oficial del costo.",
    (
        _MOV_COMUNES[0],
        C("nombre_cuenta", "NOMBRE CUENTA", sinonimos=("NOMBRECUENTACONTABLE", "DESCRIPCION CUENTA", "NOMBRE"),
          ejemplo="SUELDOS DEL PERSONAL"),
        *_MOV_COMUNES[1:],
        C("documento_tercero", "DOCUMENTO TERCERO", CODIGO, sinonimos=("DOCUMENTOTERCERO", "NIT", "CEDULA", "IDENTIFICACION")),
        C("nombre_tercero", "NOMBRE TERCERO", sinonimos=("NOMBRETERCERO", "TERCERO", "RAZON SOCIAL")),
        C("descripcion", "DESCRIPCION", sinonimos=("DETALLE", "CONCEPTO")),
        C("valor", "VALOR", NUMERO, ayuda="Valor del costo del mes. Alternativamente mapee DEBITO y CREDITO.",
          sinonimos=("SALDO", "VALORNUEVOSALDO", "VALOR NUEVO SALDO", "MOVIMIENTO", "VALOR MES", "TOTAL"), ejemplo=1500000),
        C("debito", "DEBITO", NUMERO, sinonimos=("DEBE", "DÉBITO")),
        C("credito", "CREDITO", NUMERO, sinonimos=("HABER", "CRÉDITO")),
    ),
    fuente="EF",
))

_registrar(Esquema(
    "NOMINA", "Nómina (personal de planta / contrato directo)",
    "Detalle de nómina por persona y centro de costos. Distribuye el recurso de personal; "
    "el total se concilia contra el estado financiero.",
    (
        C("documento_tercero", "DOCUMENTO", CODIGO, True, sinonimos=("DOCUMENTOTERCERO", "CEDULA", "IDENTIFICACION"),
          ejemplo="1000000001"),
        C("nombre_tercero", "NOMBRE", sinonimos=("NOMBRETERCERO", "EMPLEADO", "NOMBRE EMPLEADO"), ejemplo="EMPLEADO EJEMPLO"),
        C("cargo", "CARGO"),
        *_MOV_COMUNES,
        C("descripcion", "CONCEPTO", sinonimos=("CONCEPTO NOMINA", "DEVENGO")),
        C("valor", "VALOR", NUMERO, True, sinonimos=("TOTAL", "TOTAL DEVENGADO", "COSTO TOTAL"), ejemplo=4500000),
    ),
    fuente="AREA",
))

_registrar(Esquema(
    "CONTRATISTAS", "Contratistas (OPS / contrato con terceros)",
    "Pagos a contratistas por documento. Si no trae centro de costos se reparte con la distribución OPS.",
    (
        C("documento_tercero", "DOCUMENTO", CODIGO, True, sinonimos=("DOCUMENTOTERCERO", "NIT", "CEDULA")),
        C("nombre_tercero", "NOMBRE", sinonimos=("NOMBRETERCERO", "CONTRATISTA", "RAZON SOCIAL")),
        *_MOV_COMUNES,
        C("descripcion", "OBJETO", sinonimos=("OBJETO CONTRATO", "CONCEPTO")),
        C("valor", "VALOR", NUMERO, True, sinonimos=("VALOR PAGADO", "TOTAL", "VALOR CAUSADO"), ejemplo=3200000),
    ),
    fuente="AREA",
))

_registrar(Esquema(
    "INVENTARIOS", "Inventarios / consumos de almacén y farmacia",
    "Salidas de almacén y farmacia por centro de costos (medicamentos, dispositivos, reactivos, papelería).",
    (
        *_MOV_COMUNES,
        C("descripcion", "ARTICULO", sinonimos=("PRODUCTO", "DESCRIPCION", "ELEMENTO")),
        C("cantidad", "CANTIDAD", NUMERO),
        C("valor", "VALOR", NUMERO, True, sinonimos=("COSTO", "VALOR TOTAL", "TOTAL"), ejemplo=250000),
    ),
    fuente="AREA",
))

_registrar(Esquema(
    "DEPRECIACION", "Depreciaciones y amortizaciones",
    "Depreciación / amortización del mes por activo y centro de costos.",
    (
        *_MOV_COMUNES,
        C("descripcion", "ACTIVO", sinonimos=("PLACA", "DESCRIPCION ACTIVO", "BIEN")),
        C("valor", "VALOR", NUMERO, True, sinonimos=("DEPRECIACION", "VALOR DEPRECIACION", "TOTAL")),
    ),
    fuente="AREA",
))

_registrar(Esquema(
    "OTROS_AREA", "Otros soportes de área (servicios públicos por sede, mantenimiento, etc.)",
    "Valores por centro o por sede que soportan la asignación de un recurso.",
    (
        *_MOV_COMUNES,
        C("descripcion", "DESCRIPCION", sinonimos=("DETALLE", "CONCEPTO")),
        C("valor", "VALOR", NUMERO, True, sinonimos=("VALOR FACTURADO", "TOTAL", "CUENTA MENSUAL")),
    ),
    fuente="AREA",
))

_registrar(Esquema(
    "VALORES_INDUCTOR", "Valores de inductores por centro de costos",
    "Cantidades del inductor por centro destino (m², No. personas, kilos de ropa, raciones, exámenes solicitados).",
    (
        C("inductor", "INDUCTOR", CODIGO, True, ejemplo="PERSONAS"),
        C("centro_costo", "CENTRO DE COSTOS", CODIGO, True, sinonimos=_MOV_COMUNES[2].sinonimos, ejemplo="A10"),
        C("centro_origen", "CENTRO ORIGEN", CODIGO, ayuda="Opcional: centro que distribuye (cocina, laboratorio…)."),
        C("cantidad", "CANTIDAD", NUMERO, True, sinonimos=("VALOR", "AREA", "AREAS", "NO PERSONAS", "TOTAL"), ejemplo=12),
        C("permanente", "PERMANENTE", ayuda="SI = valor vigente para todos los periodos (p. ej. m²)."),
    ),
))

_registrar(Esquema(
    "MANO_OBRA", "Distribución de mano de obra (Empleado / OPS)",
    "Porcentaje de dedicación de cada persona a los centros de costos. Debe sumar 100 por persona.",
    (
        C("tipo", "TIPO", True, ayuda="EMPLEADO u OPS", ejemplo="EMPLEADO"),
        C("documento", "DOCUMENTO", CODIGO, True, ejemplo="1000000001"),
        C("nombre", "NOMBRE"),
        C("centro_costo", "CENTRO DE COSTOS", CODIGO, True, sinonimos=_MOV_COMUNES[2].sinonimos, ejemplo="A10"),
        C("porcentaje", "PORCENTAJE", NUMERO, True, sinonimos=("%", "PORC", "PARTICIPACION"), ejemplo=60),
    ),
))

_registrar(Esquema(
    "PRODUCCION", "Producción por centro de costos y CUPS",
    "Cantidades producidas por centro y CUPS en el mes. Si no se carga, la producción se deriva de los RIPS.",
    (
        C("centro_costo", "CENTRO DE COSTOS", CODIGO, True, sinonimos=_MOV_COMUNES[2].sinonimos, ejemplo="A10"),
        C("cups", "CUPS", CODIGO, True, sinonimos=("CODIGO CUPS", "COD CUPS", "PROCEDIMIENTO"), ejemplo="890201"),
        C("cantidad", "CANTIDAD", NUMERO, True, sinonimos=("PRODUCCION", "TOTAL", "ACTIVIDADES"), ejemplo=120),
        C("ambito", "AMBITO", sinonimos=("ÁMBITO",)),
    ),
))

_PACIENTE = (
    C("tipo_documento_paciente", "TIPO DOCUMENTO PACIENTE", sinonimos=("TIPO DOC", "TIPODOCUMENTO", "TIPO ID")),
    C("documento_paciente", "DOCUMENTO PACIENTE", CODIGO,
      ayuda="Se seudonimiza (HMAC-SHA256) al cargar; nunca se almacena en claro.",
      sinonimos=("NUMERO DOCUMENTO", "NUM DOC", "IDENTIFICACION PACIENTE", "NUMDOCUMENTOIDENTIFICACION")),
)

_registrar(Esquema(
    "FACTURACION", "Facturación (software 360 u otro HIS)",
    "Detalle facturado por factura, EPS, CUPS y ámbito.",
    (
        C("numero_factura", "NUMERO FACTURA", CODIGO, True, sinonimos=("FACTURA", "NO FACTURA", "NUMFACTURA", "PREFIJO FACTURA"),
          ejemplo="FE-1001"),
        C("fecha", "FECHA", FECHA, sinonimos=("FECHA FACTURA", "FECHA EXPEDICION"), ejemplo="15/09/2025"),
        C("eps", "EPS", CODIGO, True, sinonimos=("ENTIDAD", "ASEGURADORA", "PAGADOR", "CODIGO EPS", "ERP"), ejemplo="EPS001"),
        C("cups", "CUPS", CODIGO, True, sinonimos=("CODIGO CUPS", "COD SERVICIO", "CODIGO PROCEDIMIENTO"), ejemplo="890201"),
        C("cantidad", "CANTIDAD", NUMERO, True, ejemplo=1),
        C("valor_unitario", "VALOR UNITARIO", NUMERO, sinonimos=("VR UNITARIO", "TARIFA"), ejemplo=42018),
        C("valor_total", "VALOR TOTAL", NUMERO, True, sinonimos=("VR TOTAL", "VALOR", "TOTAL"), ejemplo=42018),
        C("ambito", "AMBITO", sinonimos=("ÁMBITO", "AMBITO FACTURACION", "TIPO ATENCION"), ejemplo="1"),
        C("centro_costo", "CENTRO DE COSTOS", CODIGO, sinonimos=_MOV_COMUNES[2].sinonimos),
        *_PACIENTE,
    ),
))

_registrar(Esquema(
    "RIPS", "RIPS (consultas, procedimientos, urgencias, hospitalización)",
    "Registros individuales de prestación de servicios (Res. 3374/2000 y Res. 2275/2023) en formato plano.",
    (
        C("tipo_registro", "TIPO REGISTRO", ayuda="AC, AP, AU, AH, AN, AM, AT o consulta/procedimiento…"),
        C("numero_factura", "NUMERO FACTURA", CODIGO, True, sinonimos=("NUMFACTURA", "FACTURA", "NUM FACTURA"),
          ejemplo="FE-1001"),
        C("fecha_atencion", "FECHA ATENCION", FECHA, sinonimos=("FECHA", "FECHAINICIOATENCION", "FECHA CONSULTA",
                                                                 "FECHA PROCEDIMIENTO")),
        C("eps", "EPS", CODIGO, sinonimos=("CODIGO EPS", "ENTIDAD ADMINISTRADORA", "ERP")),
        *_PACIENTE,
        C("cups", "CUPS", CODIGO, True, sinonimos=("CODCONSULTA", "CODPROCEDIMIENTO", "CODIGO CONSULTA",
                                                  "CODIGO PROCEDIMIENTO", "COD SERVICIO"), ejemplo="890201"),
        C("ambito", "AMBITO", sinonimos=("ÁMBITO", "AMBITO REALIZACION", "MODALIDAD")),
        C("finalidad", "FINALIDAD", sinonimos=("FINALIDADTECNOLOGIASALUD", "FINALIDAD CONSULTA")),
        C("diagnostico", "DIAGNOSTICO PRINCIPAL", CODIGO, sinonimos=("CODDIAGNOSTICOPRINCIPAL", "DX PRINCIPAL", "CIE10")),
        C("cantidad", "CANTIDAD", NUMERO, ayuda="Vacío = 1.", ejemplo=1),
        C("valor", "VALOR", NUMERO, sinonimos=("VRSERVICIO", "VALOR CONSULTA", "VALOR PROCEDIMIENTO")),
    ),
))

_registrar(Esquema(
    "NOTA_TECNICA", "Nota técnica por EPS",
    "Tarifas y condiciones contratadas por EPS, contrato y CUPS.",
    (
        C("eps", "EPS", CODIGO, True, ejemplo="EPS001"),
        C("nombre_eps", "NOMBRE EPS", ejemplo="EPS EJEMPLO"),
        C("contrato", "CONTRATO", CODIGO, True, ejemplo="CT-2025-01"),
        C("modalidad", "MODALIDAD", ayuda="EVENTO, CAPITA, PGP o PAQUETE", requerido=True, ejemplo="EVENTO"),
        C("vigencia_desde", "VIGENCIA DESDE", FECHA, True, ejemplo="01/01/2025"),
        C("vigencia_hasta", "VIGENCIA HASTA", FECHA, True, ejemplo="31/12/2025"),
        C("cups", "CUPS", CODIGO, True, ejemplo="890201"),
        C("tarifa", "TARIFA", NUMERO, True, ejemplo=42018),
        C("frecuencia_esperada", "FRECUENCIA ESPERADA", NUMERO, ayuda="Mensual (cápita / PGP)."),
        C("ambito", "AMBITO"),
        C("valor_mensual", "VALOR MENSUAL", NUMERO, ayuda="Valor mensual del contrato (cápita / PGP)."),
        C("poblacion", "POBLACION", NUMERO),
        C("manual_tarifario", "MANUAL TARIFARIO", ejemplo="SOAT"),
        C("condiciones", "CONDICIONES"),
    ),
    periodico=False,
))

_registrar(Esquema(
    "CUPS", "Catálogo CUPS y asociación a centros de costos",
    "Códigos CUPS de la institución; opcionalmente el centro de costos que los produce y su peso relativo.",
    (
        C("codigo", "CUPS", CODIGO, True, sinonimos=("CODIGO CUPS", "CODIGO"), ejemplo="890201"),
        C("descripcion", "DESCRIPCION", requerido=True, sinonimos=("NOMBRE", "DESCRIPCION CUPS"),
          ejemplo="CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL"),
        C("grupo", "GRUPO"),
        C("centro_costo", "CENTRO DE COSTOS", CODIGO, sinonimos=_MOV_COMUNES[2].sinonimos, ejemplo="A10"),
        C("peso_relativo", "PESO RELATIVO", NUMERO, sinonimos=("UVR", "PESO", "MINUTOS")),
        C("ambito", "AMBITO"),
    ),
    periodico=False,
))

_registrar(Esquema(
    "CENTROS_COSTO", "Catálogo de centros de costos",
    "Centros de costos de la institución con su unidad de negocio, sede y código homologado contable.",
    (
        C("codigo", "CODIGO", CODIGO, True, ejemplo="A10"),
        C("nombre", "NOMBRE", requerido=True, ejemplo="MEDICINA GENERAL"),
        C("unidad_negocio", "UNIDAD DE NEGOCIO", CODIGO, True, ejemplo="UN01"),
        C("clase", "CLASE", ayuda="ESTRUCTURA, SERVICIOS, INTERMEDIOS o FINALES (vacío = la de la unidad)."),
        C("sede", "SEDE", CODIGO),
        C("codigo_origen", "CODIGO CONTABLE", ayuda="Código/nombre usado en la contabilidad (homologación)."),
    ),
    periodico=False,
))

_registrar(Esquema(
    "CUENTAS_RECURSO", "Cuentas contables - recursos",
    "Relación de cuentas (o prefijos) del plan de cuentas con los recursos del modelo.",
    (
        C("cuenta", "CUENTA", CODIGO, True, ejemplo="510101"),
        C("recurso", "RECURSO", CODIGO, ejemplo="R1"),
        C("excluir", "EXCLUIR", ayuda="SI = la cuenta no es costo del modelo."),
        C("descripcion", "DESCRIPCION"),
    ),
    periodico=False,
))

TIPOS_CHOICES = [(e.codigo, e.nombre) for e in ESQUEMAS.values()]
TIPOS_MOVIMIENTO = {k for k, e in ESQUEMAS.items() if e.fuente}


def esquema(codigo):
    return ESQUEMAS[codigo]

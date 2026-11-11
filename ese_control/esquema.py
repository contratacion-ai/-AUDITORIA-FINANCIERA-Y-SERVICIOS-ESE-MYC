"""Estructura de datos común.

Este módulo es la única fuente de verdad sobre *qué columnas* tiene cada ítem:

* las plantillas de Excel se generan a partir de ``PLANTILLAS``;
* la lectura tolerante reconoce encabezados con los ``sinonimos`` de cada campo;
* la validación exige los campos ``obligatorio`` y los valores de ``opciones``;
* todo termina en la tabla ``registros`` con las columnas de ``COLUMNAS_REGISTRO``.

Para aceptar un encabezado nuevo que traiga un sistema o una sede, basta con añadir
su expresión regular a ``sinonimos`` (se compara contra el encabezado en minúsculas,
sin tildes y sin signos).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import COMBUSTIBLE, DESPLAZAMIENTOS, MANTENIMIENTO, SERVICIOS

# --------------------------------------------------------------------------- #
# Columnas crudas: lo que produce cualquier extractor antes de validar.
# --------------------------------------------------------------------------- #
COLUMNAS_CRUDAS = [
    "periodo",        # texto o fecha; se interpreta en la validación
    "fecha",          # fecha del hecho (factura, tanqueo, mantenimiento)
    "marca_temporal",  # fecha de diligenciamiento (formularios); respaldo de "fecha"
    "codigo",         # código de sede, centro de costo o número de cuenta
    "sede",           # nombre de sede/hospital tal como viene
    "referencia",     # placa, equipo, activo o cuenta
    "categoria",      # agrupación del ítem (ver CATEGORIAS)
    "concepto",       # servicio, tipo de combustible o tipo de mantenimiento
    "descripcion",
    "cantidad",       # consumo (kWh, m3) o galones
    "unidad",
    "valor",          # valor del periodo en pesos
    "total_a_pagar",  # total de la factura incluidos saldos anteriores (servicios)
    "proveedor",      # empresa de servicios, estación o taller
    "documento",      # número de factura, orden o enlace al soporte
    "odometro",
    "litros",
    "km_inicial", "km_final", "origen", "destino",   # solo desplazamientos
    "observacion",
]

# Columnas de la tabla ``registros`` (estructura normalizada y validada).
COLUMNAS_REGISTRO = [
    "item", "categoria", "concepto", "periodo", "fecha", "sede_id", "referencia",
    "descripcion", "cantidad", "unidad", "valor", "total_a_pagar", "proveedor",
    "documento", "odometro", "observacion", "hoja", "fila",
]

# --------------------------------------------------------------------------- #
# Valores controlados
# --------------------------------------------------------------------------- #
CAT_ENERGIA = "Energía eléctrica"
CAT_AGUA = "Acueducto, alcantarillado y aseo"
CAT_OTROS_SP = "Otros servicios"
CAT_VEHICULOS = "Parque automotor"
CAT_PLANTAS = "Plantas eléctricas y equipos"

SERVICIOS_OPCIONES = ["Energía eléctrica", "Acueducto", "Alcantarillado", "Aseo", "Alumbrado público",
                      "Gas", "Internet y telefonía", "Otro"]
CATEGORIA_DE_SERVICIO = {
    "Energía eléctrica": CAT_ENERGIA,
    "Acueducto": CAT_AGUA, "Alcantarillado": CAT_AGUA, "Aseo": CAT_AGUA,
    "Alumbrado público": CAT_AGUA, "Acueducto y aseo (sin discriminar)": CAT_AGUA,
    "Gas": CAT_OTROS_SP, "Internet y telefonía": CAT_OTROS_SP, "Otro": CAT_OTROS_SP,
}
UNIDAD_DE_SERVICIO = {"Energía eléctrica": "kWh", "Acueducto": "m3", "Gas": "m3"}

DESTINOS_COMBUSTIBLE = [CAT_VEHICULOS, CAT_PLANTAS]
COMBUSTIBLES = ["Diésel (ACPM)", "Gasolina corriente", "Gasolina extra", "Urea (AdBlue)", "Otro"]

ACTIVOS_MANTENIMIENTO = [CAT_VEHICULOS, CAT_PLANTAS, "Equipo biomédico", "Infraestructura",
                         "Equipo de cómputo y comunicaciones", "Otro"]
TIPOS_MANTENIMIENTO = ["Preventivo", "Correctivo", "Sin clasificar"]

# Clave de reemplazo: un cargue nuevo sustituye los registros vigentes que coincidan en
# (ítem, categoría, periodo, sede). Así una sede renueva solo lo suyo y el archivo
# de energía no pisa el de acueducto.


# --------------------------------------------------------------------------- #
# Plantillas de recolección
# --------------------------------------------------------------------------- #
@dataclass
class Campo:
    nombre: str                 # nombre interno (columna cruda)
    titulo: str                 # encabezado en la plantilla
    tipo: str                   # texto | numero | moneda | fecha | periodo | lista
    obligatorio: bool = False
    ayuda: str = ""
    opciones: list = field(default_factory=list)
    ancho: int = 18
    sinonimos: list = field(default_factory=list)   # expresiones regulares


def _campos_servicios() -> list[Campo]:
    return [
        Campo("periodo", "Periodo (AAAA-MM)", "periodo", True,
              "Mes de consumo facturado, no el mes en que llegó la factura. Ejemplo: 2026-10.", ancho=16,
              sinonimos=[r"^periodo", r"^mes( facturado| de consumo)?$", r"periodo facturado"]),
        Campo("codigo", "Código de sede o N.º de cuenta", "texto", True,
              "Código de sede del catálogo (hoja Catálogo), centro de costo o número de cuenta del servicio.",
              ancho=24, sinonimos=[r"^cod(igo)?( de)?( la)?( sede| cuenta| centro.*| usuario| suscriptor)?$",
                                   r"centro de costo", r"^c ?costo", r"^ceco$", r"^n(umero|ro|o)? ?(de )?cuenta",
                                   r"^cuenta( contrato)?$", r"^niu$", r"^nic$", r"^codigo de sede"]),
        Campo("sede", "Sede (nombre)", "texto", False,
              "Opcional si diligenció el código. Sirve de respaldo para ubicar la sede.", ancho=34,
              sinonimos=[r"^sede", r"^hospital", r"^usuario$", r"^suscriptor", r"^punto de atencion", r"^nombre( de la)? sede"]),
        Campo("concepto", "Servicio", "lista", True, "Un renglón por servicio facturado.",
              opciones=SERVICIOS_OPCIONES, ancho=22, sinonimos=[r"^servicio", r"^tipo( de)? servicio", r"^concepto"]),
        Campo("cantidad", "Consumo", "numero", False,
              "Consumo del periodo: kWh en energía, m3 en acueducto. Deje vacío si no aplica (aseo, alumbrado).",
              ancho=12, sinonimos=[r"^consumo", r"^kw", r"^m ?3$", r"^cantidad"]),
        Campo("unidad", "Unidad", "lista", False, "kWh, m3 o N/A.", opciones=["kWh", "m3", "N/A"], ancho=10,
              sinonimos=[r"^unidad"]),
        Campo("valor", "Valor facturado del periodo ($)", "moneda", True,
              "Solo el consumo/cargo de este mes, sin saldos anteriores.", ancho=24,
              sinonimos=[r"^v(alor|r)? ?fact", r"^valor( del)? (periodo|mes|servicio)", r"^valor$", r"^valor consumo"]),
        Campo("total_a_pagar", "Total a pagar de la factura ($)", "moneda", False,
              "Total de la factura incluidos saldos en mora. Escríbalo una sola vez por factura.", ancho=24,
              sinonimos=[r"^total( a pagar| factura| de la factura)?$", r"^total a pagar", r"^t factura", r"^v(alor)? ?t(otal)?( f)?$",
                         r"^valor total"]),
        Campo("proveedor", "Empresa prestadora", "texto", False, "", ancho=24,
              sinonimos=[r"^empresa", r"^proveedor", r"^prestador", r"^operador"]),
        Campo("documento", "N.º de factura", "texto", False, "", ancho=16,
              sinonimos=[r"^n( ?o| ?ro|umero)? ?(de )?factura", r"^factura"]),
        Campo("fecha", "Fecha de la factura", "fecha", False, "DD/MM/AAAA.", ancho=16,
              sinonimos=[r"^fecha( de)?( la)? (factura|expedicion|emision)", r"^fecha$"]),
        Campo("observacion", "Observaciones", "texto", False, "", ancho=36, sinonimos=[r"^observa", r"^nota"]),
    ]


def _campos_combustible() -> list[Campo]:
    return [
        Campo("fecha", "Fecha del suministro", "fecha", True,
              "DD/MM/AAAA. El periodo se toma de esta fecha (o de las fechas de corte indicadas al cargar).",
              ancho=18, sinonimos=[r"^fecha( de| del)? (suministro|registro|tanqueo|consumo|despacho|venta)", r"^fecha$"]),
        Campo("codigo", "Código de sede", "texto", False,
              "Código de sede del catálogo o centro de costo. Para vehículos puede dejarlo vacío: "
              "la placa ubica la sede.", ancho=18,
              sinonimos=[r"^cod(igo)?( de)?( la)?( sede| centro.*)?$", r"centro de costo", r"^c ?costo", r"^ceco$"]),
        Campo("sede", "Sede (nombre)", "texto", False, "Opcional; respaldo del código.", ancho=30,
              sinonimos=[r"^sede", r"^hospital", r"^punto de atencion"]),
        Campo("categoria", "Destino del combustible", "lista", True, "", opciones=DESTINOS_COMBUSTIBLE, ancho=28,
              sinonimos=[r"^destino", r"^uso del combustible"]),
        Campo("referencia", "Placa o equipo", "texto", True,
              "Placa del vehículo (sin espacios ni guiones) o nombre del equipo: planta eléctrica, guadaña, canoa…",
              ancho=22, sinonimos=[r"^placa", r"^equipo", r"^vehiculo$", r"^maquina"]),
        Campo("concepto", "Tipo de combustible", "lista", True, "", opciones=COMBUSTIBLES, ancho=20,
              sinonimos=[r"^tipo( de)? combustible", r"^combustible$", r"^producto"]),
        Campo("cantidad", "Galones", "numero", True, "Cantidad suministrada en galones (use coma o punto decimal).",
              ancho=12, sinonimos=[r"galon", r"^cantidad( suministrada)?$", r"^gal$"]),
        Campo("valor", "Valor total ($)", "moneda", True, "Valor pagado por el suministro.", ancho=18,
              sinonimos=[r"^valor( total)?( del)?( tanqueo| suministro)?$", r"^total$", r"^vr total", r"^importe"]),
        Campo("odometro", "Kilometraje (odómetro)", "numero", False, "Solo vehículos.", ancho=20,
              sinonimos=[r"^kilometraje( actual)?", r"odometro", r"^km( actual)?$"]),
        Campo("proveedor", "Estación de servicio", "texto", False, "", ancho=26,
              sinonimos=[r"^estacion", r"^eds", r"^proveedor"]),
        Campo("documento", "N.º de factura o soporte", "texto", False, "Número de factura o enlace al soporte.",
              ancho=24, sinonimos=[r"factura", r"soporte", r"comprobante", r"^recibo"]),
        Campo("observacion", "Observaciones", "texto", False, "", ancho=36, sinonimos=[r"^observa", r"^nota"]),
    ]


def _campos_mantenimiento() -> list[Campo]:
    return [
        Campo("fecha", "Fecha del mantenimiento", "fecha", True,
              "DD/MM/AAAA. Si solo conoce el mes, use el día 1.", ancho=20,
              sinonimos=[r"^fecha( de| del)?( la)? (mantenimiento|servicio|orden|factura|ejecucion|intervencion)",
                         r"^fecha$"]),
        Campo("codigo", "Código de sede", "texto", True, "Código de sede del catálogo o centro de costo.",
              ancho=18, sinonimos=[r"^cod(igo)?( de)?( la)?( sede| centro.*)?$", r"centro de costo", r"^c ?costo", r"^ceco$"]),
        Campo("sede", "Sede (nombre)", "texto", False, "Opcional; respaldo del código.", ancho=30,
              sinonimos=[r"^sede", r"^hospital", r"^punto de atencion"]),
        Campo("categoria", "Tipo de activo", "lista", True, "", opciones=ACTIVOS_MANTENIMIENTO, ancho=30,
              sinonimos=[r"^tipo( de)? activo", r"^clase( de)? (activo|equipo)", r"^grupo"]),
        Campo("referencia", "Placa o identificación del activo", "texto", False,
              "Placa del vehículo, serie o placa de inventario del equipo.", ancho=26,
              sinonimos=[r"^placa", r"^activo", r"^serie", r"^identificacion"]),
        Campo("concepto", "Tipo de mantenimiento", "lista", True, "", opciones=TIPOS_MANTENIMIENTO, ancho=22,
              sinonimos=[r"^tipo( de)? (mantenimiento|mtto|servicio|intervencion)", r"^clase( de)? mantenimiento"]),
        Campo("descripcion", "Descripción del trabajo", "texto", True, "Qué se hizo y sobre qué equipo.", ancho=44,
              sinonimos=[r"^descripcion", r"^detalle", r"^trabajo", r"^actividad", r"^vehiculo$", r"^equipo$"]),
        Campo("valor", "Valor ($)", "moneda", True, "Valor total con IVA.", ancho=16,
              sinonimos=[r"^valor( total)?$", r"^costo( total)?$", r"^mantenimiento$", r"^total$", r"^vr total"]),
        Campo("proveedor", "Proveedor o taller", "texto", False, "", ancho=26,
              sinonimos=[r"^proveedor", r"^taller", r"^contratista"]),
        Campo("documento", "N.º de orden o factura", "texto", False, "", ancho=20,
              sinonimos=[r"factura", r"^orden", r"^ot$", r"^n(umero|ro|o)? ?(de )?orden"]),
        Campo("observacion", "Observaciones", "texto", False, "", ancho=36, sinonimos=[r"^observa", r"^nota"]),
    ]


PLANTILLAS: dict[str, list[Campo]] = {
    SERVICIOS: _campos_servicios(),
    COMBUSTIBLE: _campos_combustible(),
    MANTENIMIENTO: _campos_mantenimiento(),
}

VERSION_PLANTILLA = "1"

# --------------------------------------------------------------------------- #
# Perfiles de detección de tablas "largas" (una fila por hecho).
# Además de los campos de plantilla se reconocen columnas propias de exportaciones.
# ``requeridos``: grupos de columnas; debe aparecer al menos una de cada grupo para
# que una fila se considere encabezado de ese perfil.
# --------------------------------------------------------------------------- #
_EXTRA = {
    "marca_temporal": [r"^marca temporal", r"^timestamp", r"^fecha( de)? (diligenciamiento|envio)"],
    "periodo": [r"^periodo", r"^mes$"],
    "litros": [r"litros"],
    "km_inicial": [r"^k(ilometraje|m) inicial"],
    "km_final": [r"^k(ilometraje|m) final"],
    "origen": [r"^municipio( de)? salida", r"^origen"],
    "destino": [r"^municipio( de)? destino", r"^destino$"],
    "descripcion_motivo": [r"^motivo"],
}


def _sinonimos(item: str) -> dict[str, list[str]]:
    return {c.nombre: list(c.sinonimos) for c in PLANTILLAS[item]}


def _perfil_combustible() -> dict:
    s = _sinonimos(COMBUSTIBLE)
    s["marca_temporal"] = _EXTRA["marca_temporal"]
    s["periodo"] = _EXTRA["periodo"]
    s["litros"] = _EXTRA["litros"]
    # En las exportaciones del formulario la sede viene como "HOSPITALES Y CENTROS DE SALUD".
    s["sede"] = s["sede"] + [r"^hospitales y centros"]
    # "Cantidad suministrada (litros)" no debe tomarse como galones.
    return {"item": COMBUSTIBLE, "sinonimos": s, "excluir": {"cantidad": [r"litro"], "odometro": [r"inicial", r"final"]},
            "requeridos": [["fecha", "periodo", "marca_temporal"], ["cantidad"], ["valor"], ["referencia", "sede", "codigo"]]}


def _perfil_mantenimiento() -> dict:
    s = _sinonimos(MANTENIMIENTO)
    s["periodo"] = _EXTRA["periodo"]
    return {"item": MANTENIMIENTO, "sinonimos": s, "excluir": {"valor": [r"combustible"]},
            "requeridos": [["fecha", "periodo"], ["valor"]]}


def _perfil_servicios() -> dict:
    return {"item": SERVICIOS, "sinonimos": _sinonimos(SERVICIOS), "excluir": {},
            "requeridos": [["periodo"], ["concepto"], ["valor"], ["codigo", "sede"]]}


def _perfil_desplazamientos() -> dict:
    s = {"fecha": [r"^fecha( del)? desplazamiento", r"^fecha$"], "marca_temporal": _EXTRA["marca_temporal"],
         "referencia": [r"^placa"], "km_inicial": _EXTRA["km_inicial"], "km_final": _EXTRA["km_final"],
         "descripcion": _EXTRA["descripcion_motivo"], "origen": _EXTRA["origen"], "destino": _EXTRA["destino"]}
    return {"item": DESPLAZAMIENTOS, "sinonimos": s, "excluir": {},
            "requeridos": [["fecha", "marca_temporal"], ["referencia"], ["km_inicial"], ["km_final"]]}


PERFILES = [_perfil_desplazamientos(), _perfil_combustible(), _perfil_servicios(), _perfil_mantenimiento()]

# Una tabla de mantenimiento solo se acepta sin la palabra "mantenimiento" en el
# encabezado si el usuario indicó el ítem; evita confundir cualquier tabla con valores.
PISTA_MANTENIMIENTO = r"mantenimiento|mtto|taller|orden de trabajo"

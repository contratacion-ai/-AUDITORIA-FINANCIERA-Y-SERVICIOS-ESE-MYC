"""Utilidades compartidas: parámetros, bitácora, hallazgos y resolución de catálogos."""

from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError, transaction

from ..importacion import normalizar
from ..models import Ambulancia, HallazgoCalidad, Parametro, RegistroAuditoria, Sede, TarifaRuta

PARAMETROS_DEFECTO = {
    # clave: (valor, unidad, descripción, fuente, es_supuesto)
    "precio_gasolina": (23650, "$/gal", "Precio de contrato del galón de gasolina", "Informe técnico num. 4.2 (contrato 003-0045)", False),
    "precio_acpm": (18500, "$/gal", "Precio de contrato del galón de ACPM", "Informe técnico num. 4.2 (contrato 003-0045)", False),
    "tripulacion_dia": (
        457592.6702, "$/día",
        "Tripulación por ambulancia y día: valor hora × horas/día (médico 6,4 h, auxiliar 6,3 h, conductor 6,3 h)",
        "Modelo ABC corregido, COSTOS AMBULANCIAS.xlsx, TABLA DE VALOR TH MYC!G9", False,
    ),
    "tripulacion_dia_informe": (
        458467, "$/día", "Tripulación del informe técnico (13.754.000 ÷ 30). Solo contraste, no se usa en el cálculo",
        "Informe técnico num. 4.4", False,
    ),
    "tasa_gastos_admin": (
        0.062, "fracción", "Gastos administrativos sobre el costo total (0,062 = 6,2 %)",
        "Modelo ABC corregido, MODELO COSTOS ABC PARAMETRO!E44", False,
    ),
    "ambulancias_reparto_indirectos": (
        21, "ambulancias", "Ambulancias entre las que se reparten los costos indirectos del centro de costos",
        "Modelo ABC corregido, MODELO COSTOS ABC PARAMETRO!F10", False,
    ),
    "velocidad_flota_kmh": (34.6, "km/h", "Velocidad efectiva media de la flota (para unidades sin perfil)", "Informe técnico num. 4.5", False),
    "valor_km_tarifa": (17000, "$/km", "Valor del kilómetro, manual tarifario propio 2026", "ANEXOS DE TARIFAS 2026.xlsx, Anexo 3!P8", False),
    "tarifa_traslado_basico": (459300, "$", "Traslado básico asistencial (tarifa fija)", "ANEXOS DE TARIFAS 2026.xlsx, Anexo 3!P9", False),
    "factor_ida_vuelta": (
        2, "factor", "Km conducidos por cada km facturado. El anexo factura un sentido; la ambulancia regresa a su sede",
        "Supuesto: el estudio mide recorridos de ida y regreso (num. 4.1). Ajustar si se factura ida y vuelta", True,
    ),
    "km_traslado_basico": (
        30, "km", "Km conducidos (ida y vuelta) de un traslado con tarifa básica sin km facturados",
        "Supuesto basado en el num. 4.1 (traslados a veredas valorados en 30 km ida y vuelta)", True,
    ),
    "tolerancia_km_ruta": (0, "km", "Diferencia máxima entre km facturados y km de la ruta para inferirla", "Configuración", True),
}


# Valores por defecto que cambiaron con el modelo ABC corregido: (anterior, nuevo).
# En una actualización solo se cambian si el parámetro conserva el valor anterior
# (es decir, si nadie lo modificó a mano).
CORRECCIONES_PARAMETROS = {
    "tripulacion_dia": (Decimal("458467"), Decimal("457592.6702")),
    "ambulancias_reparto_indirectos": (Decimal("22"), Decimal("21")),
}


def parametro(clave: str) -> Decimal:
    try:
        return Parametro.objects.get(clave=clave).valor
    except Parametro.DoesNotExist:
        return Decimal(str(PARAMETROS_DEFECTO[clave][0]))


def parametros() -> dict[str, Decimal]:
    valores = {k: Decimal(str(v[0])) for k, v in PARAMETROS_DEFECTO.items()}
    valores.update({p.clave: p.valor for p in Parametro.objects.all()})
    return valores


def registrar(usuario, obj, accion: str, cambios: dict | None = None):
    RegistroAuditoria.objects.create(
        usuario=usuario if getattr(usuario, "is_authenticated", False) else None,
        modelo=type(obj).__name__,
        objeto_id=str(obj.pk),
        accion=accion,
        cambios=cambios or {},
    )


def guardar_hallazgos(lista: list[dict], lote=None) -> int:
    nuevos = 0
    for h in lista:
        try:
            with transaction.atomic():
                _, creado = HallazgoCalidad.objects.get_or_create(
                    fuente=h["fuente"][:150],
                    ubicacion=h["ubicacion"][:200],
                    tipo=h["tipo"],
                    descripcion=h["descripcion"],
                    defaults={"severidad": h.get("severidad", "MEDIA"), "valor": h.get("valor", "")[:200], "lote": lote},
                )
            nuevos += int(creado)
        except IntegrityError:
            pass
    return nuevos


class ResolutorCatalogos:
    """Resuelve nombres de sede, placas y rutas de forma tolerante (tildes, espacios)."""

    def __init__(self):
        self.sedes = {}
        for s in Sede.objects.all():
            for n in s.nombres():
                self.sedes[normalizar(n)] = s
        self.placas = {a.placa: a for a in Ambulancia.objects.select_related("sede")}
        self.rutas: dict[str, list[TarifaRuta]] = {}
        for r in TarifaRuta.objects.filter(activa=True).order_by("-vigencia"):
            self.rutas.setdefault(normalizar(r.origen), []).append(r)

    def sede(self, nombre):
        return self.sedes.get(normalizar(nombre))

    def ambulancia(self, placa):
        return self.placas.get(normalizar(placa).replace(" ", "").replace("-", ""))

    def ruta_por_km(self, sede: Sede, km, tolerancia=Decimal(0)):
        """Ruta del anexo con el mismo origen que la sede y los mismos km.

        Devuelve (ruta, motivo). Si hay varias candidatas o ninguna, ruta=None.
        """
        if sede is None or km is None:
            return None, "sin sede o sin km"
        candidatas = [
            r for r in self.rutas.get(normalizar(sede.origen_tarifa), []) if r.km is not None and abs(r.km - km) <= tolerancia
        ]
        vigencia = max((r.vigencia for r in candidatas), default=None)
        candidatas = [r for r in candidatas if r.vigencia == vigencia]
        if len(candidatas) == 1:
            return candidatas[0], ""
        if not candidatas:
            return None, f"no hay ruta del anexo desde {sede.origen_tarifa} con {km} km"
        return None, f"{len(candidatas)} rutas desde {sede.origen_tarifa} con {km} km: " + ", ".join(r.destino for r in candidatas)

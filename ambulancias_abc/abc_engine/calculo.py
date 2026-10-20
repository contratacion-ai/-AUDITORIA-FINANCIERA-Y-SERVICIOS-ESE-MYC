"""Fórmulas ABC.

Recursos (informe técnico, num. 4):
    C_total/día = C_combustible + C_mantenimiento + C_tripulación

Actividades y drivers usados para asignar los recursos a cada traslado:

    Actividad                    Recurso que consume        Driver
    ---------------------------  -------------------------  ------------------------
    Desplazamiento (rodamiento)  Combustible                Galones (km / rendimiento)
    Desgaste del vehículo        Mantenimiento              Kilómetros recorridos
    Atención en misión           Tripulación                Horas en misión (km / velocidad)
    Disponibilidad / soporte     Indirectos del centro de   Horas en misión (como la tripulación:
                                 costos por ambulancia-día  es un costo fijo diario)
    Atención clínica             Insumos (consumos reales)  Número de traslados
    Soporte / otros              Otros consumos e indirectos Número de traslados

Dos métodos de asignación:

* ``REAL`` (absorción total): la tasa de cada actividad es el costo del
  recurso en el periodo dividido por la suma del driver de los traslados de
  la ambulancia. Todo el costo queda en traslados; si la ambulancia no tuvo
  traslados, su costo queda como capacidad no utilizada.
* ``CAPACIDAD`` (tasas estándar del estudio): combustible a precio de
  contrato por galón, mantenimiento a $/km del estudio, tripulación a costo de
  disponibilidad por hora (C_tripulación/24 h). La diferencia entre el costo
  del periodo y lo asignado es la capacidad no utilizada (o sobreconsumo si
  es negativa).

Gastos administrativos (modelo corregido de la ESE, 6,2 %): recargo
porcentual sobre todo lo anterior; se aplica igual a lo asignado y a lo no
utilizado, por lo que no altera la conservación.

En ambos casos se cumple la identidad de conservación:

    sum(costo asignado a traslados) + sum(no utilizado) = costo total de la flota
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

GASOLINA = "GASOLINA"
ACPM = "ACPM"

METODO_REAL = "REAL"
METODO_CAPACIDAD = "CAPACIDAD"
# Costo por recorrido con tasas de flota: lo que cuesta el recorrido en sí, sin
# cargarle el tiempo ocioso de la ambulancia (ver TasasFlota).
METODO_ESTANDAR = "ESTANDAR"

ACTIVIDADES = ("combustible", "mantenimiento", "tripulacion", "indirectos", "insumos", "otros")
COMPONENTES = ACTIVIDADES + ("administracion",)


@dataclass(frozen=True)
class Precios:
    """Precios de contrato 003-0045 de 2026 ($/galón)."""

    gasolina: float = 23650.0
    acpm: float = 18500.0

    def precio(self, tipo_combustible: str) -> float:
        return self.gasolina if tipo_combustible == GASOLINA else self.acpm


@dataclass(frozen=True)
class PerfilAmbulancia:
    """Perfil diario de una ambulancia según el Anexo A del informe."""

    placa: str
    sede: str
    tipo_combustible: str
    km_dia: float
    horas_dia: float
    combustible_dia: float
    mantenimiento_dia: float
    tripulacion_dia: float
    excluida_promedios: bool = False
    indirectos_dia: float = 0.0  # centro de costos ÷ ambulancias ÷ 30
    tasa_admin: float = 0.0  # gastos administrativos (0,062 = 6,2 %)

    @property
    def base_dia(self) -> float:
        return self.combustible_dia + self.mantenimiento_dia + self.tripulacion_dia + self.indirectos_dia

    @property
    def administracion_dia(self) -> float:
        return self.base_dia * self.tasa_admin

    @property
    def total_dia(self) -> float:
        return self.base_dia + self.administracion_dia

    def galones_dia(self, precios: Precios) -> float:
        return self.combustible_dia / precios.precio(self.tipo_combustible)

    def rendimiento_km_gal(self, precios: Precios) -> float | None:
        gal = self.galones_dia(precios)
        return self.km_dia / gal if gal > 0 else None

    @property
    def velocidad_kmh(self) -> float | None:
        return self.km_dia / self.horas_dia if self.horas_dia > 0 else None

    @property
    def costo_km(self) -> float | None:
        return self.total_dia / self.km_dia if self.km_dia > 0 else None

    @property
    def costo_hora_operacion(self) -> float | None:
        return self.total_dia / self.horas_dia if self.horas_dia > 0 else None

    @property
    def costo_hora_disponibilidad(self) -> float:
        return self.total_dia / 24.0


@dataclass(frozen=True)
class CostoRecorrido:
    km: float
    galones: float
    horas: float
    combustible: float
    mantenimiento: float
    tripulacion: float
    indirectos: float = 0.0
    administracion: float = 0.0

    @property
    def total(self) -> float:
        return self.combustible + self.mantenimiento + self.tripulacion + self.indirectos + self.administracion


@dataclass(frozen=True)
class TasasFlota:
    """Tasas por driver comunes a toda la flota (método ESTANDAR).

    Con REAL, un recorrido de una ambulancia que anda poco por día absorbe
    días enteros de tripulación, mantenimiento e indirectos (p. ej. OKZ364:
    0,33 h/día → $1,37 M por hora de tripulación). Estas tasas cobran al
    recorrido solo los recursos que consume:

    * tripulación: valor hora del equipo (Σ salario ÷ horas mes, tabla TH);
    * mantenimiento: $/km de la flota (Σ mantenimiento/día ÷ Σ km/día);
    * indirectos: $/hora de operación de la flota (Σ indirectos ÷ Σ horas/día);
    * horas del recorrido: km ÷ velocidad efectiva de la flota.

    La diferencia con REAL es el sobrecosto por baja utilización de cada
    unidad: existe, pero es capacidad ociosa y no costo del recorrido.
    """

    mantenimiento_km: float
    tripulacion_hora: float
    indirectos_hora: float
    velocidad_kmh: float


def tasas_flota(perfiles, tripulacion_hora: float, velocidad_kmh: float) -> TasasFlota:
    """Tasas de flota con las unidades que entran en los promedios del informe."""
    base = [p for p in perfiles if not p.excluida_promedios and p.km_dia > 0 and p.horas_dia > 0]
    km = sum(p.km_dia for p in base)
    horas = sum(p.horas_dia for p in base)
    if not km or not horas:
        raise ValueError("No hay ambulancias con km y horas para calcular las tasas de flota")
    return TasasFlota(
        mantenimiento_km=sum(p.mantenimiento_dia for p in base) / km,
        tripulacion_hora=tripulacion_hora,
        indirectos_hora=sum(p.indirectos_dia for p in base) / horas,
        velocidad_kmh=velocidad_kmh,
    )


def costo_recorrido(
    perfil: PerfilAmbulancia,
    km: float,
    precios: Precios,
    metodo: str = METODO_REAL,
    tasas: TasasFlota | None = None,
) -> CostoRecorrido:
    """Costo estándar de un recorrido de ``km`` kilómetros con una ambulancia.

    Con ``REAL`` la tripulación se absorbe en las horas en misión (costo por
    hora de operación del informe); con ``CAPACIDAD`` se cobra la hora de
    disponibilidad (C_tripulación / 24 h); con ``ESTANDAR`` se usan las tasas
    de flota (``tasas``) y solo el combustible depende de la ambulancia.
    """
    rend = perfil.rendimiento_km_gal(precios)
    vel = perfil.velocidad_kmh
    if not rend or not vel or perfil.km_dia <= 0:
        raise ValueError(f"La ambulancia {perfil.placa} no tiene km/horas/combustible suficientes")
    galones = km / rend
    combustible = galones * precios.precio(perfil.tipo_combustible)
    if metodo == METODO_ESTANDAR:
        if tasas is None:
            raise ValueError("El método ESTANDAR requiere las tasas de flota")
        horas = km / tasas.velocidad_kmh
        mantenimiento = km * tasas.mantenimiento_km
        tripulacion = horas * tasas.tripulacion_hora
        indirectos = horas * tasas.indirectos_hora
        subtotal = combustible + mantenimiento + tripulacion + indirectos
        return CostoRecorrido(km, galones, horas, combustible, mantenimiento, tripulacion, indirectos, subtotal * perfil.tasa_admin)
    horas = km / vel
    mantenimiento = km * perfil.mantenimiento_dia / perfil.km_dia
    # Tripulación e indirectos son costos fijos por día: se absorben por hora en
    # misión (REAL) o por hora de disponibilidad de 24 h (CAPACIDAD).
    horas_base = 24.0 if metodo == METODO_CAPACIDAD else perfil.horas_dia
    tripulacion = horas * perfil.tripulacion_dia / horas_base
    indirectos = horas * perfil.indirectos_dia / horas_base
    subtotal = combustible + mantenimiento + tripulacion + indirectos
    return CostoRecorrido(km, galones, horas, combustible, mantenimiento, tripulacion, indirectos, subtotal * perfil.tasa_admin)


# ---------------------------------------------------------------------------
# Indicadores de flota (reproducen las tablas 3 y 4 del informe)
# ---------------------------------------------------------------------------


def indicadores_flota(perfiles: list[PerfilAmbulancia], precios: Precios) -> dict:
    """Mínimos, máximos y promedios de la flota excluyendo las unidades marcadas.

    El promedio ponderado de costo por hora es costo total / horas totales,
    como en el numeral 5.4 del informe.
    """
    base = [p for p in perfiles if not p.excluida_promedios]
    if not base:
        return {}

    def resumen(fn):
        vals = [(fn(p), p.placa) for p in base if fn(p) is not None]
        if not vals:
            return None
        mn = min(vals)
        mx = max(vals)
        return {
            "min": mn[0],
            "min_placa": mn[1],
            "max": mx[0],
            "max_placa": mx[1],
            "promedio": mean(v for v, _ in vals),
        }

    total_horas = sum(p.horas_dia for p in base)
    por_tipo = {}
    for tipo in (GASOLINA, ACPM):
        grupo = [p for p in base if p.tipo_combustible == tipo]
        if not grupo:
            continue
        horas = sum(p.horas_dia for p in grupo)
        km = sum(p.km_dia for p in grupo)
        por_tipo[tipo] = {
            "n": len(grupo),
            "km_dia": mean(p.km_dia for p in grupo),
            "combustible_dia": mean(p.combustible_dia for p in grupo),
            "mantenimiento_dia": mean(p.mantenimiento_dia for p in grupo),
            "total_dia": mean(p.total_dia for p in grupo),
            "combustible_km": sum(p.combustible_dia for p in grupo) / km if km else None,
            "costo_hora_ponderado": sum(p.total_dia for p in grupo) / horas if horas else None,
        }

    return {
        "n_ambulancias": len(base),
        "n_total": len(perfiles),
        "km_dia": resumen(lambda p: p.km_dia),
        "galones_dia": resumen(lambda p: p.galones_dia(precios)),
        "rendimiento_km_gal": resumen(lambda p: p.rendimiento_km_gal(precios)),
        "combustible_dia": resumen(lambda p: p.combustible_dia),
        "mantenimiento_dia": resumen(lambda p: p.mantenimiento_dia),
        "tripulacion_dia": resumen(lambda p: p.tripulacion_dia),
        "total_dia": resumen(lambda p: p.total_dia),
        "costo_km": resumen(lambda p: p.costo_km),
        "horas_dia": resumen(lambda p: p.horas_dia),
        "costo_hora_operacion": resumen(lambda p: p.costo_hora_operacion),
        "costo_hora_disponibilidad": resumen(lambda p: p.costo_hora_disponibilidad),
        "costo_hora_ponderado": (sum(p.total_dia for p in base) / total_horas) if total_horas else None,
        "costo_flota_dia": sum(p.total_dia for p in perfiles),
        "por_tipo": por_tipo,
    }


# ---------------------------------------------------------------------------
# Asignación de un periodo (mes) a los traslados
# ---------------------------------------------------------------------------


@dataclass
class PoolAmbulancia:
    """Costo de los recursos de una ambulancia en el periodo."""

    placa: str
    sede: str
    combustible: float = 0.0
    mantenimiento: float = 0.0
    tripulacion: float = 0.0
    indirectos: float = 0.0
    insumos: float = 0.0
    otros: float = 0.0

    @property
    def total(self) -> float:
        """Total sin gastos administrativos (el recargo se aplica en la asignación)."""
        return sum(getattr(self, a) for a in ACTIVIDADES)


@dataclass(frozen=True)
class TrasladoDriver:
    """Traslado a costear. ``placa`` puede ser None: se reparte en la sede."""

    id: str
    sede: str
    km: float
    placa: str | None = None
    horas: float | None = None


@dataclass
class _Parte:
    traslado_id: str
    placa: str
    peso: float  # fracción del traslado que hace esta ambulancia
    km: float
    galones: float
    horas: float


@dataclass
class ResultadoAsignacion:
    metodo: str
    por_traslado: dict[str, dict[str, float]] = field(default_factory=dict)
    placa_traslado: dict[str, dict[str, float]] = field(default_factory=dict)
    # (traslado, placa) -> costo por actividad de la parte que hizo esa ambulancia
    costo_parte: dict[tuple[str, str], dict[str, float]] = field(default_factory=dict)
    por_ambulancia: dict[str, dict[str, float]] = field(default_factory=dict)
    indirectos: float = 0.0
    indirectos_no_utilizados: float = 0.0
    avisos: list[str] = field(default_factory=list)

    @property
    def total_pool(self) -> float:
        return sum(a["pool_total"] for a in self.por_ambulancia.values()) + self.indirectos

    @property
    def total_asignado(self) -> float:
        return sum(t["total"] for t in self.por_traslado.values())

    @property
    def total_no_utilizado(self) -> float:
        return sum(a["no_utilizado_total"] for a in self.por_ambulancia.values()) + self.indirectos_no_utilizados


def _vacio() -> dict[str, float]:
    return {a: 0.0 for a in COMPONENTES} | {"total": 0.0}


def asignar_periodo(
    pools: list[PoolAmbulancia],
    traslados: list[TrasladoDriver],
    perfiles: dict[str, PerfilAmbulancia],
    precios: Precios,
    metodo: str = METODO_REAL,
    indirectos: float = 0.0,
    velocidad_flota_kmh: float = 34.6,
    tipos_combustible: dict[str, str] | None = None,
    tasa_admin: float = 0.0,
) -> ResultadoAsignacion:
    """Asigna el costo de los recursos del periodo a los traslados.

    ``perfiles`` aporta rendimiento (km/gal) y velocidad efectiva por placa.
    Si una placa no tiene perfil se usan el promedio de la flota por tipo de
    combustible y ``velocidad_flota_kmh`` (34,6 km/h, num. 4.5 del informe).

    ``indirectos``: monto de la flota (no de una ambulancia) que se reparte por
    número de traslados. ``tasa_admin``: recargo de gastos administrativos.
    """
    res = ResultadoAsignacion(metodo=metodo, indirectos=indirectos * (1 + tasa_admin))
    tipos_combustible = tipos_combustible or {}
    pools_por_placa = {p.placa: p for p in pools}

    validos = [p for p in perfiles.values() if not p.excluida_promedios and p.km_dia > 0]

    def rend_defecto(tipo):
        vals = [p.rendimiento_km_gal(precios) for p in validos if p.tipo_combustible == tipo]
        vals = [v for v in vals if v]
        return mean(vals) if vals else 1.0

    def parametros(placa):
        perfil = perfiles.get(placa)
        tipo = perfil.tipo_combustible if perfil else tipos_combustible.get(placa, ACPM)
        rend = perfil.rendimiento_km_gal(precios) if perfil else None
        vel = perfil.velocidad_kmh if perfil else None
        if not rend:
            rend = rend_defecto(tipo)
        if not vel:
            vel = velocidad_flota_kmh
        return tipo, rend, vel

    # 1. Repartir cada traslado entre ambulancias (partes con peso).
    por_sede: dict[str, list[str]] = {}
    for p in pools:
        por_sede.setdefault(p.sede, []).append(p.placa)

    def peso_km(placa):
        perfil = perfiles.get(placa)
        return perfil.km_dia if perfil and perfil.km_dia > 0 else 1.0

    partes: list[_Parte] = []
    for t in traslados:
        if t.placa and t.placa in pools_por_placa:
            asignacion = [(t.placa, 1.0)]
        else:
            if t.placa:
                res.avisos.append(f"Traslado {t.id}: placa {t.placa} sin costo en el periodo; se reparte en la sede")
            candidatas = por_sede.get(t.sede) or [p.placa for p in pools]
            if not candidatas:
                res.avisos.append(f"Traslado {t.id}: no hay ambulancias con costo en el periodo")
                continue
            pesos = [peso_km(c) for c in candidatas]
            s = sum(pesos)
            asignacion = [(c, w / s) for c, w in zip(candidatas, pesos)]
        for placa, peso in asignacion:
            _, rend, vel = parametros(placa)
            km = t.km * peso
            horas = (t.horas * peso) if t.horas is not None else km / vel
            partes.append(_Parte(t.id, placa, peso, km, km / rend, horas))

    # 2. Totales de driver por ambulancia.
    drivers: dict[str, dict[str, float]] = {}
    for parte in partes:
        d = drivers.setdefault(parte.placa, {"galones": 0.0, "km": 0.0, "horas": 0.0, "conteo": 0.0})
        d["galones"] += parte.galones
        d["km"] += parte.km
        d["horas"] += parte.horas
        d["conteo"] += parte.peso

    # 3. Tasas por actividad.
    tasas: dict[str, dict[str, float]] = {}
    for pool in pools:
        d = drivers.get(pool.placa, {"galones": 0.0, "km": 0.0, "horas": 0.0, "conteo": 0.0})
        tipo, _, _ = parametros(pool.placa)
        perfil = perfiles.get(pool.placa)

        def real(valor, base):
            return valor / base if base > 0 else 0.0

        if metodo == METODO_CAPACIDAD:
            tasa_mant = (
                perfil.mantenimiento_dia / perfil.km_dia
                if perfil and perfil.km_dia > 0
                else real(pool.mantenimiento, d["km"])
            )
            tasa_trip = perfil.tripulacion_dia / 24.0 if perfil else real(pool.tripulacion, d["horas"])
            tasa_ind_amb = perfil.indirectos_dia / 24.0 if perfil else real(pool.indirectos, d["horas"])
            tasas[pool.placa] = {
                "combustible": precios.precio(tipo),
                "mantenimiento": tasa_mant,
                "tripulacion": tasa_trip,
                "indirectos": tasa_ind_amb,
                "insumos": real(pool.insumos, d["conteo"]),
                "otros": real(pool.otros, d["conteo"]),
            }
        else:
            tasas[pool.placa] = {
                "combustible": real(pool.combustible, d["galones"]),
                "mantenimiento": real(pool.mantenimiento, d["km"]),
                "tripulacion": real(pool.tripulacion, d["horas"]),
                "indirectos": real(pool.indirectos, d["horas"]),
                "insumos": real(pool.insumos, d["conteo"]),
                "otros": real(pool.otros, d["conteo"]),
            }

    driver_de = {
        "combustible": "galones",
        "mantenimiento": "km",
        "tripulacion": "horas",
        "indirectos": "horas",
        "insumos": "conteo",
        "otros": "conteo",
    }

    # 4. Costo por parte -> traslado y ambulancia.
    total_conteo = sum(p.peso for p in partes)
    tasa_ind = indirectos / total_conteo if total_conteo > 0 else 0.0
    for parte in partes:
        tr = res.por_traslado.setdefault(parte.traslado_id, _vacio())
        cp = res.costo_parte.setdefault((parte.traslado_id, parte.placa), _vacio())
        pt = res.placa_traslado.setdefault(parte.traslado_id, {})
        pt[parte.placa] = pt.get(parte.placa, 0.0) + parte.peso
        valores = {"galones": parte.galones, "km": parte.km, "horas": parte.horas, "conteo": parte.peso}
        costos = {act: tasas[parte.placa][act] * valores[driver_de[act]] for act in ACTIVIDADES}
        costos["indirectos"] += tasa_ind * parte.peso  # indirectos de flota por número de traslados
        subtotal = sum(costos.values())
        costos["administracion"] = subtotal * tasa_admin
        for destino in (tr, cp):
            for act, costo in costos.items():
                destino[act] += costo
            destino["total"] += subtotal + costos["administracion"]

    # 5. Resumen por ambulancia con capacidad no utilizada.
    for pool in pools:
        d = drivers.get(pool.placa, {"galones": 0.0, "km": 0.0, "horas": 0.0, "conteo": 0.0})
        fila = {"pool_total": pool.total, "asignado_total": 0.0, "no_utilizado_total": 0.0}
        fila.update({f"driver_{k}": v for k, v in d.items()})
        for act in ACTIVIDADES:
            valor_pool = getattr(pool, act)
            asignado = tasas[pool.placa][act] * d[driver_de[act]]
            fila[f"pool_{act}"] = valor_pool
            fila[f"asignado_{act}"] = asignado
            fila[f"no_utilizado_{act}"] = valor_pool - asignado
            fila["asignado_total"] += asignado
            fila["no_utilizado_total"] += valor_pool - asignado
            fila[f"tasa_{act}"] = tasas[pool.placa][act]
        fila["indirectos_flota_asignados"] = tasa_ind * d["conteo"]
        # Gastos administrativos: mismo recargo sobre el costo, lo asignado y lo no utilizado.
        fila["pool_administracion"] = pool.total * tasa_admin
        fila["asignado_administracion"] = fila["asignado_total"] * tasa_admin
        fila["no_utilizado_administracion"] = fila["no_utilizado_total"] * tasa_admin
        fila["pool_total"] += fila["pool_administracion"]
        fila["asignado_total"] += fila["asignado_administracion"]
        fila["no_utilizado_total"] += fila["no_utilizado_administracion"]
        fila["tasa_administracion"] = tasa_admin
        if fila["no_utilizado_total"] < -0.5:
            res.avisos.append(
                f"{pool.placa}: lo asignado a tasa estándar supera el costo del periodo "
                f"en {-fila['no_utilizado_total']:,.0f} (sobreuso de capacidad)"
            )
        res.por_ambulancia[pool.placa] = fila

    res.indirectos_no_utilizados = res.indirectos if total_conteo == 0 else 0.0
    return res

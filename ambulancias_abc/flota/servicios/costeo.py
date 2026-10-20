"""Adaptador entre la base de datos y el motor ABC (abc_engine).

* ``calcular_mes``: arma los costos (pools) de cada ambulancia en el mes con
  los consumos reales registrados o, si no hay, con el estándar del estudio
  (costo/día × días del mes), y los asigna a los traslados del mes.
* ``consolidar``: recorre varios meses y aplica filtros para el dashboard.
* ``matriz_rutas``: costo estándar de cada ruta del manual por ambulancia
  frente a la tarifa (margen y rentabilidad por recorrido).
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from django.db.models import Sum

from abc_engine.calculo import (
    METODO_CAPACIDAD,
    METODO_ESTANDAR,
    METODO_REAL,
    PerfilAmbulancia,
    PoolAmbulancia,
    Precios,
    TasasFlota,
    TrasladoDriver,
    asignar_periodo,
    costo_recorrido,
    tasas_flota,
)

from ..models import (
    Ambulancia,
    Consumo,
    PartidaCostoIndirecto,
    PerfilCostoEstudio,
    Sede,
    TarifaRuta,
    Traslado,
    meses_entre_fechas,
    sumar_meses,
)
from .comunes import parametros

METODOS = {METODO_REAL: "Absorción real", METODO_CAPACIDAD: "Capacidad (tasas estándar)"}
# Métodos para el costo de un recorrido (matriz de rutas y costo estándar por sede).
METODOS_RECORRIDO = {
    METODO_ESTANDAR: "Costo del recorrido (tasas de flota)",
    METODO_REAL: "Absorción del día de la ambulancia",
    METODO_CAPACIDAD: "Capacidad 24 h",
}


def tasas_vigentes(pars=None, perfiles=None) -> TasasFlota:
    pars = pars or parametros()
    perfiles = perfiles or perfiles_vigentes(pars)
    return tasas_flota(perfiles.values(), float(pars["tripulacion_valor_hora"]), float(pars["velocidad_flota_kmh"]))


def precios_vigentes(pars=None) -> Precios:
    pars = pars or parametros()
    return Precios(gasolina=float(pars["precio_gasolina"]), acpm=float(pars["precio_acpm"]))


def indirectos_mes_incluidos() -> float:
    """Partidas del centro de costos marcadas para el ABC ($/mes, toda la flota)."""
    return float(PartidaCostoIndirecto.objects.filter(incluir_en_abc=True).aggregate(v=Sum("valor_mensual"))["v"] or 0)


def indirectos_por_ambulancia_mes(pars=None) -> float:
    """Modelo corregido: partidas incluidas ÷ número de ambulancias del reparto."""
    pars = pars or parametros()
    n = float(pars["ambulancias_reparto_indirectos"]) or 1.0
    return indirectos_mes_incluidos() / n


def perfiles_vigentes(pars=None) -> dict[str, PerfilAmbulancia]:
    pars = pars or parametros()
    indirectos_dia = indirectos_por_ambulancia_mes(pars) / 30.0
    tasa_admin = float(pars["tasa_gastos_admin"])
    perfiles = {}
    qs = PerfilCostoEstudio.objects.filter(vigente=True).select_related("ambulancia__sede").order_by("ambulancia__placa", "-fecha_estudio")
    for p in qs:
        a = p.ambulancia
        if a.placa in perfiles:
            continue
        perfiles[a.placa] = PerfilAmbulancia(
            placa=a.placa,
            sede=a.sede.codigo,
            tipo_combustible=a.tipo_combustible,
            km_dia=float(p.km_dia),
            horas_dia=float(p.horas_dia),
            combustible_dia=float(p.combustible_dia),
            mantenimiento_dia=float(p.mantenimiento_dia),
            # la tripulación se toma del parámetro vigente (un solo valor para la flota)
            tripulacion_dia=float(pars["tripulacion_dia"]),
            excluida_promedios=a.excluida_promedios,
            indirectos_dia=indirectos_dia,
            tasa_admin=tasa_admin,
        )
    return perfiles


def meses_entre(desde: dt.date, hasta: dt.date) -> list[dt.date]:
    meses, m = [], desde.replace(day=1)
    while m <= hasta:
        meses.append(m)
        m = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return meses


def consumos_del_mes(mes: dt.date, placa: str | None = None) -> dict[str, dict[str, float]]:
    """placa -> actividad ABC -> valor del mes, aplicando el prorrateo de cada consumo."""
    reales = defaultdict(lambda: defaultdict(float))
    qs = Consumo.objects.filter(mes__lte=mes, mes__gte=sumar_meses(mes, -35))
    if placa:
        qs = qs.filter(ambulancia__placa=placa)
    for c in qs.values("ambulancia__placa", "tipo", "valor", "mes", "meses_prorrateo"):
        n = c["meses_prorrateo"] or 1
        if meses_entre_fechas(c["mes"], mes) < n:
            reales[c["ambulancia__placa"]][Consumo.POOL[c["tipo"]]] += float(c["valor"]) / n
    return reales


@dataclass
class ResultadoMes:
    mes: dt.date
    metodo: str
    dias: int
    filas: list[dict] = field(default_factory=list)  # una por (traslado, ambulancia)
    ambulancias: dict[str, dict] = field(default_factory=dict)
    fuente_pool: dict[str, dict[str, str]] = field(default_factory=dict)  # placa -> actividad -> REAL/ESTANDAR
    indirectos: float = 0.0  # suma de los indirectos de las ambulancias (informativo)
    tasa_admin: float = 0.0
    total_pool: float = 0.0
    total_asignado: float = 0.0
    total_no_utilizado: float = 0.0
    avisos: list[str] = field(default_factory=list)


def calcular_mes(mes: dt.date, metodo: str = METODO_REAL) -> ResultadoMes:
    mes = mes.replace(day=1)
    pars = parametros()
    precios = precios_vigentes(pars)
    perfiles = perfiles_vigentes(pars)
    dias = calendar.monthrange(mes.year, mes.month)[1]
    res = ResultadoMes(mes=mes, metodo=metodo, dias=dias)

    reales = consumos_del_mes(mes)
    indirectos_amb = indirectos_por_ambulancia_mes(pars)

    pools, tipos = [], {}
    for amb in Ambulancia.objects.select_related("sede").exclude(estado=Ambulancia.FUERA):
        tipos[amb.placa] = amb.tipo_combustible
        real = reales.get(amb.placa, {})
        perfil = perfiles.get(amb.placa)
        if not amb.incluir_en_costeo and not real:
            continue
        pool = PoolAmbulancia(placa=amb.placa, sede=amb.sede.codigo)
        fuentes = {}
        estandar = {
            "combustible": perfil.combustible_dia * dias if perfil else 0.0,
            "mantenimiento": perfil.mantenimiento_dia * dias if perfil else 0.0,
            "tripulacion": float(pars["tripulacion_dia"]) * dias,
            "indirectos": indirectos_amb,
            "insumos": 0.0,
            "otros": 0.0,
        }
        for act, valor_estandar in estandar.items():
            if act in real:
                setattr(pool, act, real[act])
                fuentes[act] = "REAL"
            elif amb.incluir_en_costeo:
                setattr(pool, act, valor_estandar)
                fuentes[act] = ("CENTRO_COSTOS" if act == "indirectos" else "ESTANDAR") if valor_estandar else "-"
            else:
                fuentes[act] = "-"
        pools.append(pool)
        res.fuente_pool[amb.placa] = fuentes

    traslados = list(
        Traslado.objects.filter(mes=mes).select_related("sede", "ambulancia", "ruta").order_by("id")
    )
    drivers = [
        TrasladoDriver(id=str(t.pk), sede=t.sede.codigo, placa=t.ambulancia.placa if t.ambulancia_id else None, km=float(t.km_recorridos))
        for t in traslados
    ]
    # Indirectos ya incluidos en el costo de cada ambulancia (modelo corregido).
    res.indirectos = sum(p.indirectos for p in pools)
    res.tasa_admin = float(pars["tasa_gastos_admin"])
    r = asignar_periodo(
        pools,
        drivers,
        perfiles,
        precios,
        metodo,
        velocidad_flota_kmh=float(pars["velocidad_flota_kmh"]),
        tipos_combustible=tipos,
        tasa_admin=res.tasa_admin,
    )
    res.avisos = r.avisos
    res.total_pool = r.total_pool
    res.total_asignado = r.total_asignado
    res.total_no_utilizado = r.total_no_utilizado
    res.ambulancias = r.por_ambulancia

    for t in traslados:
        reparto = r.placa_traslado.get(str(t.pk)) or {None: 1.0}
        for placa, peso in reparto.items():
            costo = r.costo_parte.get((str(t.pk), placa), {})
            res.filas.append(
                {
                    "traslado_id": t.pk,
                    "mes": mes,
                    "placa": placa,
                    "peso": peso,
                    "asignada": bool(t.ambulancia_id),
                    "sede": t.sede.nombre,
                    "sede_codigo": t.sede.codigo,
                    "ruta_id": t.ruta_id,
                    "ruta": str(t.ruta) if t.ruta_id else "Sin ruta del anexo",
                    "ambito": t.ambito or "SIN ÁMBITO",
                    "unidad_funcional": t.unidad_funcional or "SIN UNIDAD",
                    "codigo_cups": t.codigo_cups,
                    "categoria": t.categoria,
                    "km": float(t.km_recorridos) * peso,
                    "traslado": peso if t.km_recorridos > 0 else 0.0,
                    "linea": peso,
                    "ingreso": float(t.valor_facturado) * peso,
                    "costo": costo.get("total", 0.0),
                    **{f"costo_{k}": costo.get(k, 0.0) for k in ("combustible", "mantenimiento", "tripulacion", "indirectos", "administracion", "insumos", "otros")},
                }
            )
    return res


def _sumar(destino: dict, fila: dict):
    for k in ("km", "traslado", "linea", "ingreso", "costo", "costo_combustible", "costo_mantenimiento",
              "costo_tripulacion", "costo_insumos", "costo_otros", "costo_indirectos", "costo_administracion"):
        destino[k] = destino.get(k, 0.0) + fila.get(k, 0.0)


def _cerrar(d: dict) -> dict:
    d["margen"] = d.get("ingreso", 0.0) - d.get("costo", 0.0)
    d["rentabilidad"] = (d["margen"] / d["ingreso"] * 100) if d.get("ingreso") else None
    d["costo_km"] = d["costo"] / d["km"] if d.get("km") else None
    d["costo_traslado"] = d["costo"] / d["traslado"] if d.get("traslado") else None
    d["costo_otros_total"] = d.get("costo_insumos", 0.0) + d.get("costo_otros", 0.0)
    d["costo_ind_admin"] = d.get("costo_indirectos", 0.0) + d.get("costo_administracion", 0.0)
    return d


def consolidar(desde: dt.date, hasta: dt.date, metodo=METODO_REAL, filtros: dict | None = None) -> dict:
    """Indicadores del dashboard para un rango de meses y filtros opcionales.

    filtros: placa, sede (código), ambito, unidad_funcional, ruta_id.
    La capacidad no utilizada solo se puede atribuir a ambulancia/mes; si hay
    filtros por ámbito, unidad o ruta se informa aparte y no se resta.
    """
    filtros = {k: v for k, v in (filtros or {}).items() if v not in (None, "")}
    filtra_actividad = any(k in filtros for k in ("ambito", "unidad_funcional", "ruta_id", "categoria"))
    total = {}
    por_mes, por_amb, por_ruta, por_ambito, por_unidad = {}, {}, {}, {}, {}
    no_utilizado = 0.0
    pool_total = 0.0
    avisos = []
    fuentes = defaultdict(set)

    sede_de = dict(Ambulancia.objects.values_list("placa", "sede__codigo"))

    def incluye_amb(placa):
        if filtros.get("placa") and placa != filtros["placa"]:
            return False
        return not filtros.get("sede") or sede_de.get(placa) == filtros["sede"]

    for mes in meses_entre(desde, hasta):
        r = calcular_mes(mes, metodo)
        avisos.extend(f"{mes:%Y-%m}: {a}" for a in r.avisos)
        m = por_mes.setdefault(mes, {"mes": mes, "pool": 0.0, "no_utilizado": 0.0})
        for placa, fila in r.ambulancias.items():
            if not incluye_amb(placa):
                continue
            pool_total += fila["pool_total"]
            no_utilizado += fila["no_utilizado_total"]
            m["pool"] += fila["pool_total"]
            m["no_utilizado"] += fila["no_utilizado_total"]
            a = por_amb.setdefault(placa, {"placa": placa, "pool": 0.0, "no_utilizado": 0.0})
            a["pool"] += fila["pool_total"]
            a["no_utilizado"] += fila["no_utilizado_total"]
            for act, f in r.fuente_pool.get(placa, {}).items():
                fuentes[placa].add(f"{act}:{f}")
        for fila in r.filas:
            if filtros.get("placa") and fila["placa"] != filtros["placa"]:
                continue
            if filtros.get("sede") and fila["sede_codigo"] != filtros["sede"]:
                continue
            if filtros.get("ambito") and fila["ambito"] != filtros["ambito"]:
                continue
            if filtros.get("unidad_funcional") and fila["unidad_funcional"] != filtros["unidad_funcional"]:
                continue
            if filtros.get("categoria") and fila["categoria"] != filtros["categoria"]:
                continue
            if filtros.get("ruta_id") and str(fila["ruta_id"]) != str(filtros["ruta_id"]):
                continue
            _sumar(total, fila)
            _sumar(m, fila)
            _sumar(por_amb.setdefault(fila["placa"] or "SIN ASIGNAR", {"placa": fila["placa"] or "SIN ASIGNAR", "pool": 0.0, "no_utilizado": 0.0}), fila)
            r_ = por_ruta.setdefault(fila["ruta"], {"ruta": fila["ruta"], "ruta_id": fila["ruta_id"]})
            _sumar(r_, fila)
            _sumar(por_ambito.setdefault(fila["ambito"], {"ambito": fila["ambito"]}), fila)
            _sumar(por_unidad.setdefault(fila["unidad_funcional"], {"unidad_funcional": fila["unidad_funcional"]}), fila)

    for d in [total, *por_mes.values(), *por_amb.values(), *por_ruta.values(), *por_ambito.values(), *por_unidad.values()]:
        for k in ("km", "traslado", "linea", "ingreso", "costo"):
            d.setdefault(k, 0.0)
        _cerrar(d)
    for placa, a in por_amb.items():
        a["fuentes"] = sorted(fuentes.get(placa, []))
        a["margen_con_capacidad"] = a["margen"] - a["no_utilizado"]
    total["no_utilizado"] = no_utilizado
    total["pool"] = pool_total
    total["margen_con_capacidad"] = total["margen"] - (0.0 if filtra_actividad else no_utilizado)
    return {
        "total": total,
        "por_mes": sorted(por_mes.values(), key=lambda d: d["mes"]),
        "por_ambulancia": sorted(por_amb.values(), key=lambda d: -d["costo"]),
        "por_ruta": sorted(por_ruta.values(), key=lambda d: -d["ingreso"]),
        "por_ambito": sorted(por_ambito.values(), key=lambda d: -d["ingreso"]),
        "por_unidad": sorted(por_unidad.values(), key=lambda d: -d["ingreso"]),
        "filtra_actividad": filtra_actividad,
        "avisos": avisos[:50],
        "n_avisos": len(avisos),
    }


def matriz_rutas(metodo=METODO_ESTANDAR, sede_codigo=None, placa=None, solo_origen=True, vigencia=None) -> list[dict]:
    """Costo estándar ABC de cada ruta del manual por ambulancia y su margen.

    ``sobrecosto_unidad`` = costo con absorción del día (REAL) − costo del
    recorrido con tasas de flota: lo que la baja utilización de esa ambulancia
    agregaría si se cargara al recorrido (capacidad ociosa, no costo de la ruta).
    """
    pars = parametros()
    precios = precios_vigentes(pars)
    perfiles = perfiles_vigentes(pars)
    tasas = tasas_vigentes(pars, perfiles)
    factor = float(pars["factor_ida_vuelta"])
    rutas = TarifaRuta.objects.filter(activa=True)
    vigencia = vigencia or rutas.order_by("-vigencia").values_list("vigencia", flat=True).first()
    rutas = rutas.filter(vigencia=vigencia)
    ambs = Ambulancia.objects.select_related("sede").filter(placa__in=perfiles.keys())
    if sede_codigo:
        ambs = ambs.filter(sede__codigo=sede_codigo)
    if placa:
        ambs = ambs.filter(placa=placa)
    from ..importacion import normalizar

    filas = []
    for amb in ambs:
        perfil = perfiles[amb.placa]
        if perfil.km_dia <= 0 or perfil.horas_dia <= 0:
            continue
        for ruta in rutas:
            if ruta.km is None:
                continue
            if solo_origen and normalizar(ruta.origen) != normalizar(amb.sede.origen_tarifa):
                continue
            km_cond = float(ruta.km) * factor
            c = costo_recorrido(perfil, km_cond, precios, metodo, tasas)
            absorcion = costo_recorrido(perfil, km_cond, precios, METODO_REAL)
            estandar = c if metodo == METODO_ESTANDAR else costo_recorrido(perfil, km_cond, precios, METODO_ESTANDAR, tasas)
            tarifa = float(ruta.tarifa) if ruta.tarifa is not None else None
            margen = tarifa - c.total if tarifa is not None else None
            filas.append(
                {
                    "ruta": ruta,
                    "placa": amb.placa,
                    "sede": amb.sede.nombre,
                    "tipo_combustible": amb.tipo_combustible,
                    "excluida": amb.excluida_promedios,
                    "km_conducidos": c.km,
                    "horas": c.horas,
                    "galones": c.galones,
                    "combustible": c.combustible,
                    "mantenimiento": c.mantenimiento,
                    "tripulacion": c.tripulacion,
                    "indirectos": c.indirectos,
                    "administracion": c.administracion,
                    "ind_admin": c.indirectos + c.administracion,
                    "costo": c.total,
                    "sobrecosto_unidad": absorcion.total - estandar.total,
                    "tarifa": tarifa,
                    "margen": margen,
                    "rentabilidad": (margen / tarifa * 100) if tarifa else None,
                    "tarifa_km_equilibrio": (c.total / float(ruta.km)) if ruta.km else None,
                }
            )
    filas.sort(key=lambda f: (f["ruta"].origen, f["ruta"].destino, f["costo"]))
    return filas


# ---------------------------------------------------------------------------
# Tablas: recorridos por sede, facturación por ámbito y consumos por categoría
# ---------------------------------------------------------------------------


def recorridos_por_sede(desde: dt.date, hasta: dt.date, metodo=METODO_REAL, sede_codigo=None, categoria=None,
                        metodo_recorrido=METODO_ESTANDAR) -> list[dict]:
    """Por cada sede: sus recorridos del Anexo 3 con traslados, facturación, costo ABC
    asignado (real del periodo) y costo estándar del recorrido, con márgenes.

    El costo estándar es el promedio de las ambulancias de la sede (con perfil y
    no excluidas) para un viaje de ida y vuelta por esa ruta, con
    ``metodo_recorrido`` (por defecto tasas de flota). ``metodo`` es el de la
    asignación del periodo (costo real).
    """
    from ..importacion import normalizar

    pars = parametros()
    precios = precios_vigentes(pars)
    perfiles = perfiles_vigentes(pars)
    tasas = tasas_vigentes(pars, perfiles)
    factor = float(pars["factor_ida_vuelta"])
    sedes = Sede.objects.filter(activa=True).order_by("nombre")
    if sede_codigo:
        sedes = sedes.filter(codigo=sede_codigo)
    vigencia = TarifaRuta.objects.order_by("-vigencia").values_list("vigencia", flat=True).first()

    # Real del periodo: una sola pasada por mes para todas las sedes.
    real = defaultdict(lambda: defaultdict(float))  # (sede, ruta_id|None) -> métricas
    flota_sede = defaultdict(lambda: {"pool": 0.0, "no_utilizado": 0.0})
    sede_de = dict(Ambulancia.objects.values_list("placa", "sede__codigo"))
    for mes in meses_entre(desde, hasta):
        r = calcular_mes(mes, metodo)
        for placa, a in r.ambulancias.items():
            flota_sede[sede_de.get(placa)]["pool"] += a["pool_total"]
            flota_sede[sede_de.get(placa)]["no_utilizado"] += a["no_utilizado_total"]
        for f in r.filas:
            if categoria and f["categoria"] != categoria:
                continue
            d = real[(f["sede_codigo"], f["ruta_id"])]
            for k in ("traslado", "linea", "km", "ingreso", "costo"):
                d[k] += f[k]
            if f["categoria"] == "APH":
                d["aph"] += f["linea"]

    salida = []
    for sede in sedes:
        ambs = [
            perfiles[a.placa]
            for a in Ambulancia.objects.filter(sede=sede, placa__in=perfiles.keys())
            if not perfiles[a.placa].excluida_promedios and perfiles[a.placa].km_dia > 0 and perfiles[a.placa].horas_dia > 0
        ]
        filas = []
        for ruta in TarifaRuta.objects.filter(activa=True, vigencia=vigencia).order_by("destino"):
            if normalizar(ruta.origen) != normalizar(sede.origen_tarifa):
                continue
            d = real.get((sede.codigo, ruta.pk), {})
            costos = [costo_recorrido(p, float(ruta.km) * factor, precios, metodo_recorrido, tasas).total for p in ambs] if ruta.km else []
            estandar = sum(costos) / len(costos) if costos else None
            tarifa = float(ruta.tarifa) if ruta.tarifa is not None else None
            fila = {
                "ruta": ruta,
                "km": float(ruta.km) if ruta.km else None,
                "tarifa": tarifa,
                "costo_estandar": estandar,
                "costo_estandar_min": min(costos) if costos else None,
                "costo_estandar_max": max(costos) if costos else None,
                "margen_estandar": (tarifa - estandar) if tarifa is not None and estandar is not None else None,
                "traslados": d.get("traslado", 0.0),
                "lineas": d.get("linea", 0.0),
                "km_conducidos": d.get("km", 0.0),
                "facturado": d.get("ingreso", 0.0),
                "costo_real": d.get("costo", 0.0),
            }
            fila["rent_estandar"] = (fila["margen_estandar"] / tarifa * 100) if fila["margen_estandar"] is not None and tarifa else None
            fila["margen_real"] = fila["facturado"] - fila["costo_real"]
            fila["rent_real"] = (fila["margen_real"] / fila["facturado"] * 100) if fila["facturado"] else None
            filas.append(fila)
        sin = real.get((sede.codigo, None), {})
        otras = {
            "traslados": sin.get("traslado", 0.0), "lineas": sin.get("linea", 0.0), "aph": sin.get("aph", 0.0),
            "km_conducidos": sin.get("km", 0.0), "facturado": sin.get("ingreso", 0.0), "costo_real": sin.get("costo", 0.0),
        }
        otras["margen_real"] = otras["facturado"] - otras["costo_real"]
        otras["rent_real"] = (otras["margen_real"] / otras["facturado"] * 100) if otras["facturado"] else None
        tot = {k: sum(f[k] for f in filas) + otras.get(k, 0.0) for k in ("traslados", "lineas", "km_conducidos", "facturado", "costo_real")}
        tot["margen_real"] = tot["facturado"] - tot["costo_real"]
        tot["rent_real"] = (tot["margen_real"] / tot["facturado"] * 100) if tot["facturado"] else None
        tot.update(flota_sede.get(sede.codigo, {"pool": 0.0, "no_utilizado": 0.0}))
        tot["resultado_con_capacidad"] = tot["facturado"] - tot["pool"] if not categoria else None
        filas.sort(key=lambda f: (-f["facturado"], f["ruta"].destino))
        salida.append({"sede": sede, "filas": filas, "otras": otras, "total": tot, "n_ambulancias": len(ambs)})
    return salida


def facturacion_por_ambito(desde: dt.date, hasta: dt.date, sede_codigo=None) -> list[dict]:
    from django.db.models import Count, Q

    qs = Traslado.objects.filter(mes__gte=desde, mes__lte=hasta)
    if sede_codigo:
        qs = qs.filter(sede__codigo=sede_codigo)
    return list(
        qs.values("mes", "sede__nombre", "ambito", "unidad_funcional", "categoria")
        .annotate(
            lineas=Count("id"),
            traslados=Count("id", filter=Q(km_recorridos__gt=0)),
            facturado=Sum("valor_facturado"),
            km=Sum("km_recorridos"),
            sin_factura=Count("id", filter=Q(numero_factura="")),
            conciliados=Count("id", filter=Q(conciliado_rips=True)),
        )
        .order_by("mes", "sede__nombre", "ambito", "unidad_funcional", "categoria")
    )


def tabla_consumos(clave: str, desde: dt.date, hasta: dt.date, placa=None) -> dict:
    """Matriz ambulancia × mes de una tabla de consumos (con prorrateo) y comparación
    con el estándar del estudio cuando existe (combustible, mantenimiento, personal)."""
    titulo, tipos, _ = Consumo.TABLAS[clave]
    meses = meses_entre(desde, hasta)
    pars = parametros()
    perfiles = perfiles_vigentes(pars)
    qs = Consumo.objects.filter(tipo__in=tipos, mes__lte=hasta, mes__gte=sumar_meses(desde, -35)).select_related("ambulancia")
    if placa:
        qs = qs.filter(ambulancia__placa=placa)
    matriz = defaultdict(lambda: defaultdict(float))
    cantidades = defaultdict(lambda: defaultdict(float))
    odometros = defaultdict(list)
    for c in qs:
        n = c.meses_prorrateo or 1
        for m in meses:
            if 0 <= meses_entre_fechas(c.mes, m) < n:
                matriz[c.ambulancia.placa][m] += float(c.valor) / n
                cantidades[c.ambulancia.placa][m] += float(c.cantidad) / n
        if c.odometro and desde <= c.mes <= hasta:
            odometros[c.ambulancia.placa].append((c.fecha, c.odometro, float(c.cantidad)))
    campo_estandar = {"combustible": "combustible_dia", "mantenimiento": "mantenimiento_dia", "personal": "tripulacion_dia"}.get(clave)
    filas = []
    placas = sorted(set(matriz) | (set(perfiles) if campo_estandar and not placa else set()) | ({placa} if placa else set()))
    for p in placas:
        valores = [matriz[p].get(m, 0.0) for m in meses]
        total = sum(valores)
        estandar = None
        if campo_estandar and p in perfiles:
            # Con registros: estándar solo de los meses que tienen registro (comparable).
            # Sin registros: estándar de todo el periodo, como referencia.
            dia = getattr(perfiles[p], campo_estandar)
            con_dato = [m for m, v in zip(meses, valores) if v] or meses
            estandar = sum(dia * calendar.monthrange(m.year, m.month)[1] for m in con_dato)
        fila = {"placa": p, "valores": valores, "total": total, "cantidad": sum(cantidades[p].get(m, 0.0) for m in meses),
                "estandar": estandar, "diferencia": (total - estandar) if estandar is not None and total else None}
        lecturas = sorted(odometros.get(p, []))
        if clave == "combustible" and len(lecturas) >= 2:
            km = lecturas[-1][1] - lecturas[0][1]
            galones = sum(g for _, _, g in lecturas[1:])  # galones después de la primera lectura
            fila["km_odometro"] = km
            fila["rendimiento"] = km / galones if galones else None
        filas.append(fila)
    totales_mes = [sum(f["valores"][i] for f in filas) for i in range(len(meses))]
    return {
        "titulo": titulo, "tipos": tipos, "meses": meses, "filas": filas, "totales_mes": totales_mes,
        "total": sum(totales_mes), "con_estandar": bool(campo_estandar),
    }

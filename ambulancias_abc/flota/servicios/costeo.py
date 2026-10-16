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
    METODO_REAL,
    PerfilAmbulancia,
    PoolAmbulancia,
    Precios,
    TrasladoDriver,
    asignar_periodo,
    costo_recorrido,
)

from ..models import Ambulancia, Consumo, PartidaCostoIndirecto, PerfilCostoEstudio, TarifaRuta, Traslado
from .comunes import parametros

METODOS = {METODO_REAL: "Absorción real", METODO_CAPACIDAD: "Capacidad (tasas estándar)"}


def precios_vigentes(pars=None) -> Precios:
    pars = pars or parametros()
    return Precios(gasolina=float(pars["precio_gasolina"]), acpm=float(pars["precio_acpm"]))


def perfiles_vigentes(pars=None) -> dict[str, PerfilAmbulancia]:
    pars = pars or parametros()
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
        )
    return perfiles


def meses_entre(desde: dt.date, hasta: dt.date) -> list[dt.date]:
    meses, m = [], desde.replace(day=1)
    while m <= hasta:
        meses.append(m)
        m = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return meses


@dataclass
class ResultadoMes:
    mes: dt.date
    metodo: str
    dias: int
    filas: list[dict] = field(default_factory=list)  # una por (traslado, ambulancia)
    ambulancias: dict[str, dict] = field(default_factory=dict)
    fuente_pool: dict[str, dict[str, str]] = field(default_factory=dict)  # placa -> actividad -> REAL/ESTANDAR
    indirectos: float = 0.0
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

    reales = defaultdict(lambda: defaultdict(float))
    for fila in Consumo.objects.filter(mes=mes).values("ambulancia__placa", "tipo").annotate(v=Sum("valor")):
        reales[fila["ambulancia__placa"]][Consumo.POOL[fila["tipo"]]] += float(fila["v"])

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
            "insumos": 0.0,
            "otros": 0.0,
        }
        for act, valor_estandar in estandar.items():
            if act in real:
                setattr(pool, act, real[act])
                fuentes[act] = "REAL"
            elif amb.incluir_en_costeo:
                setattr(pool, act, valor_estandar)
                fuentes[act] = "ESTANDAR" if valor_estandar else "-"
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
    res.indirectos = float(
        PartidaCostoIndirecto.objects.filter(incluir_en_abc=True).aggregate(v=Sum("valor_mensual"))["v"] or 0
    )
    r = asignar_periodo(
        pools,
        drivers,
        perfiles,
        precios,
        metodo,
        indirectos=res.indirectos,
        velocidad_flota_kmh=float(pars["velocidad_flota_kmh"]),
        tipos_combustible=tipos,
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
                    "km": float(t.km_recorridos) * peso,
                    "traslado": peso if t.km_recorridos > 0 else 0.0,
                    "linea": peso,
                    "ingreso": float(t.valor_facturado) * peso,
                    "costo": costo.get("total", 0.0),
                    **{f"costo_{k}": costo.get(k, 0.0) for k in ("combustible", "mantenimiento", "tripulacion", "insumos", "otros", "indirectos")},
                }
            )
    return res


def _sumar(destino: dict, fila: dict):
    for k in ("km", "traslado", "linea", "ingreso", "costo", "costo_combustible", "costo_mantenimiento",
              "costo_tripulacion", "costo_insumos", "costo_otros", "costo_indirectos"):
        destino[k] = destino.get(k, 0.0) + fila.get(k, 0.0)


def _cerrar(d: dict) -> dict:
    d["margen"] = d.get("ingreso", 0.0) - d.get("costo", 0.0)
    d["rentabilidad"] = (d["margen"] / d["ingreso"] * 100) if d.get("ingreso") else None
    d["costo_km"] = d["costo"] / d["km"] if d.get("km") else None
    d["costo_traslado"] = d["costo"] / d["traslado"] if d.get("traslado") else None
    d["costo_otros_total"] = d.get("costo_insumos", 0.0) + d.get("costo_otros", 0.0) + d.get("costo_indirectos", 0.0)
    return d


def consolidar(desde: dt.date, hasta: dt.date, metodo=METODO_REAL, filtros: dict | None = None) -> dict:
    """Indicadores del dashboard para un rango de meses y filtros opcionales.

    filtros: placa, sede (código), ambito, unidad_funcional, ruta_id.
    La capacidad no utilizada solo se puede atribuir a ambulancia/mes; si hay
    filtros por ámbito, unidad o ruta se informa aparte y no se resta.
    """
    filtros = {k: v for k, v in (filtros or {}).items() if v not in (None, "")}
    filtra_actividad = any(k in filtros for k in ("ambito", "unidad_funcional", "ruta_id"))
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
        if not filtros.get("placa") and not filtros.get("sede"):
            pool_total += r.indirectos
            m["pool"] += r.indirectos
        for fila in r.filas:
            if filtros.get("placa") and fila["placa"] != filtros["placa"]:
                continue
            if filtros.get("sede") and fila["sede_codigo"] != filtros["sede"]:
                continue
            if filtros.get("ambito") and fila["ambito"] != filtros["ambito"]:
                continue
            if filtros.get("unidad_funcional") and fila["unidad_funcional"] != filtros["unidad_funcional"]:
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


def matriz_rutas(metodo=METODO_REAL, sede_codigo=None, placa=None, solo_origen=True, vigencia=None) -> list[dict]:
    """Costo estándar ABC de cada ruta del manual por ambulancia y su margen."""
    pars = parametros()
    precios = precios_vigentes(pars)
    perfiles = perfiles_vigentes(pars)
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
            c = costo_recorrido(perfil, float(ruta.km) * factor, precios, metodo)
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
                    "costo": c.total,
                    "tarifa": tarifa,
                    "margen": margen,
                    "rentabilidad": (margen / tarifa * 100) if tarifa else None,
                    "tarifa_km_equilibrio": (c.total / float(ruta.km)) if ruta.km else None,
                }
            )
    filas.sort(key=lambda f: (f["ruta"].origen, f["ruta"].destino, f["costo"]))
    return filas

"""Validación contra la estructura común y el catálogo.

Recibe las tablas crudas de la extracción y devuelve registros normalizados más la
lista de incidencias (archivo, hoja, fila, columna y motivo). Criterio:

* **Error**: la fila no se carga (sin sede reconocida, sin valor, sin periodo…).
* **Advertencia**: la fila se carga pero queda señalada (fecha corregida, cantidad
  no plausible, posible duplicado…).
* **Información**: lo que se omitió a propósito (otros periodos, filas en cero).
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

import pandas as pd

from . import esquema
from .catalogo import Catalogo
from .config import COMBUSTIBLE, DESPLAZAMIENTOS, GALON_EN_LITROS, MANTENIMIENTO, SERVICIOS
from .extraccion.comunes import (ADVERTENCIA, ERROR, INFO, Incidencia, Tabla, a_fecha, a_numero, a_periodo,
                                 normalizar, normalizar_codigo, periodo_de_fecha, periodo_valido, vacio)


@dataclass
class Opciones:
    periodo: str | None = None            # periodo declarado al cargar (AAAA-MM)
    alcance: str = "periodo"              # 'periodo' = solo el declarado | 'todos' = lo que traiga el archivo
    corte_desde: dt.date | None = None    # fechas de corte del periodo (cuando no coincide con el mes calendario)
    corte_hasta: dt.date | None = None
    hospital: str | None = None           # ámbito del usuario de sede; None = central
    hoy: dt.date = field(default_factory=dt.date.today)


@dataclass
class Resultado:
    item: str
    registros: pd.DataFrame
    incidencias: list
    filas_leidas: int = 0
    omitidas_periodo: int = 0
    omitidas_cero: int = 0
    omitidas_ambito: int = 0
    valor_rechazado: float = 0.0

    rechazadas: int = 0

    @property
    def advertencias(self) -> int:
        return sum(1 for i in self.incidencias if i.severidad == ADVERTENCIA)


_SERVICIO = [(r"energia|electric|kwh|luz", "Energía eléctrica"), (r"alcantarillado", "Alcantarillado"),
             (r"sin discriminar", "Acueducto y aseo (sin discriminar)"),
             (r"acueducto|agua", "Acueducto"), (r"aseo|basura", "Aseo"), (r"alumbrado", "Alumbrado público"),
             (r"gas\b", "Gas"), (r"internet|telefon|celular|datos", "Internet y telefonía")]
_COMBUSTIBLE = [(r"urea|adblue", "Urea (AdBlue)"), (r"extra|premium", "Gasolina extra"),
                (r"gasolina|corriente", "Gasolina corriente"), (r"d[ie]+sel|desel|acpm", "Diésel (ACPM)")]
_ACTIVO = [(r"vehic|automotor|ambulanc|placa", esquema.CAT_VEHICULOS), (r"planta|generador", esquema.CAT_PLANTAS),
           (r"biomed", "Equipo biomédico"), (r"infraestr|locativ|edific", "Infraestructura"),
           (r"computo|sistemas|comunicac", "Equipo de cómputo y comunicaciones")]
_TIPO_MTTO = [(r"prevent", "Preventivo"), (r"correct", "Correctivo")]


def _clasificar(texto, reglas, defecto):
    t = normalizar(texto)
    for patron, valor in reglas:
        if t and re.search(patron, t):
            return valor
    return defecto


def _texto(v, largo=300):
    if vacio(v):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()[:largo]


def validar(tabla: Tabla, cat: Catalogo, params: dict, op: Opciones) -> Resultado:
    inc: list[Incidencia] = []
    item = tabla.item
    filas = []
    omit_periodo = omit_cero = omit_ambito = rechazadas = 0
    valor_rechazado = 0.0
    cache: dict = {}
    anio_def = int(op.periodo[:4]) if op.periodo else None
    periodo_tope = periodo_de_fecha(op.hoy)

    def err(fila, motivo, columna="", valor="", sev=ERROR):
        inc.append(Incidencia(sev, motivo, hoja=fila["_hoja"], fila=int(fila["_fila"]), columna=columna,
                              valor="" if vacio(valor) else str(valor)[:80]))

    for f in tabla.datos.to_dict("records"):
        n_inc = len(inc)
        valor = a_numero(f.get("valor"))

        # ---- periodo y fecha -------------------------------------------------
        fecha, periodo = None, None
        if not vacio(f.get("fecha")) or not vacio(f.get("marca_temporal")):
            fecha = _fecha(f, op, err)
        if not vacio(f.get("periodo")):
            periodo = a_periodo(f["periodo"], f.get("_anio") or anio_def)
            if periodo is None:
                err(f, "Periodo no reconocido. Use AAAA-MM (ejemplo: 2026-10).", "periodo", f["periodo"])
        elif fecha is not None:
            periodo = periodo_de_fecha(fecha)
            if op.corte_desde and op.corte_hasta and op.periodo:
                # Con fechas de corte, lo que cae en el rango pertenece al periodo declarado.
                periodo = op.periodo if op.corte_desde <= fecha <= op.corte_hasta else "fuera"
        elif vacio(f.get("fecha")) and vacio(f.get("marca_temporal")):
            if op.periodo and item != COMBUSTIBLE and item != DESPLAZAMIENTOS:
                periodo = op.periodo     # p. ej. factura en PDF sin periodo legible
                err(f, f"La fila no trae periodo ni fecha; se asigna el periodo del cargue ({op.periodo}).",
                    "periodo", sev=ADVERTENCIA)
            else:
                err(f, "Falta la fecha (o el periodo): no se puede ubicar el mes al que corresponde.", "fecha")
        if periodo and periodo != "fuera" and periodo_valido(periodo) and periodo > periodo_tope:
            err(f, f"El periodo {periodo} es posterior al mes actual.", "periodo", periodo)
            periodo = None
        if periodo == "fuera" or (periodo and op.alcance == "periodo" and op.periodo and periodo != op.periodo):
            omit_periodo += 1
            del inc[n_inc:]     # incidencias de una fila que de todos modos no se carga
            continue

        # ---- filas sin valor que no son un hecho ------------------------------
        if item == MANTENIMIENTO and valor == 0:
            omit_cero += 1
            del inc[n_inc:]
            continue

        # ---- sede --------------------------------------------------------------
        sede_id = _sede(f, tabla, cat, cache, err)
        if sede_id and op.hospital and cat.hospital_de.get(sede_id) != op.hospital:
            # Un usuario de sede puede subir el archivo consolidado: solo entra lo de su hospital.
            omit_ambito += 1
            del inc[n_inc:]
            continue

        # ---- valor -------------------------------------------------------------
        if item != DESPLAZAMIENTOS:
            if valor is None:
                err(f, "Falta el valor o no es un número.", "valor", f.get("valor"))
            elif valor < 0:
                err(f, "Valor negativo: se carga como nota crédito o ajuste; verifique.", "valor", valor, ADVERTENCIA)
            elif valor == 0 and item == COMBUSTIBLE:
                err(f, "El suministro no tiene valor.", "valor", f.get("valor"))

        reg = {"item": item, "periodo": periodo, "fecha": fecha.isoformat() if fecha else None, "sede_id": sede_id,
               "valor": valor, "hoja": f["_hoja"], "fila": int(f["_fila"]),
               "descripcion": _texto(f.get("descripcion")), "proveedor": _texto(f.get("proveedor"), 120),
               "documento": _texto(f.get("documento"), 200), "observacion": _texto(f.get("observacion")),
               "cantidad": None, "unidad": None, "total_a_pagar": None, "odometro": None,
               "referencia": _texto(f.get("referencia"), 80), "categoria": None, "concepto": None}
        if item == SERVICIOS:
            _servicios(f, reg, err)
        elif item == COMBUSTIBLE:
            _combustible(f, reg, params, err)
        elif item == MANTENIMIENTO:
            _mantenimiento(f, reg, cat)
        else:
            _desplazamiento(f, reg, params, err)

        if any(i.severidad == ERROR for i in inc[n_inc:]):
            valor_rechazado += valor or 0
            rechazadas += 1
            continue
        filas.append(reg)

    df = pd.DataFrame(filas)
    if len(df):
        _duplicados(df, inc, item)
    if omit_periodo:
        donde = (f"fuera de las fechas de corte ({op.corte_desde:%d/%m/%Y} a {op.corte_hasta:%d/%m/%Y})"
                 if op.corte_desde and op.corte_hasta else f"de periodos distintos a {op.periodo}")
        inc.append(Incidencia(INFO, f"{omit_periodo} filas {donde} no se cargan en este cargue.", hoja=tabla.hoja))
    if omit_ambito:
        inc.append(Incidencia(INFO, f"{omit_ambito} filas pertenecen a otros hospitales y no se cargan: su usuario "
                                    f"solo carga datos de «{op.hospital}».", hoja=tabla.hoja))
    if omit_cero:
        inc.append(Incidencia(INFO, f"{omit_cero} filas con valor cero no se cargan (meses sin mantenimiento).",
                              hoja=tabla.hoja))
    return Resultado(item, df, inc, filas_leidas=len(tabla.datos), omitidas_periodo=omit_periodo,
                     omitidas_cero=omit_cero, omitidas_ambito=omit_ambito, valor_rechazado=valor_rechazado, rechazadas=rechazadas)


def _fecha(f, op: Opciones, err) -> dt.date | None:
    fecha, nota = a_fecha(f.get("fecha"))
    marca, _ = a_fecha(f.get("marca_temporal"))
    if nota and fecha is None and marca is None:
        err(f, f"Fecha no válida ({nota}).", "fecha", f.get("fecha"))
        return None
    if nota and fecha is not None:
        err(f, f"Fecha corregida: {nota}.", "fecha", f.get("fecha"), ADVERTENCIA)
    if fecha is None:
        if marca is not None and not vacio(f.get("fecha")):
            err(f, f"Fecha no válida; se usa la fecha de diligenciamiento ({marca:%d/%m/%Y}).", "fecha",
                f.get("fecha"), ADVERTENCIA)
        return marca
    limite = (marca or op.hoy) + dt.timedelta(days=1)
    if fecha > limite or fecha.year < 2015:
        if marca is not None:
            err(f, f"La fecha {fecha:%d/%m/%Y} es posterior al diligenciamiento o no es plausible; se usa la "
                   f"fecha de diligenciamiento ({marca:%d/%m/%Y}).", "fecha", f.get("fecha"), ADVERTENCIA)
            return marca
        err(f, f"La fecha {fecha:%d/%m/%Y} está en el futuro o no es plausible.", "fecha", f.get("fecha"))
        return None
    return fecha


def _sede(f, tabla: Tabla, cat: Catalogo, cache: dict, err) -> str | None:
    item = tabla.item
    es_vehiculo = item == DESPLAZAMIENTOS or item == MANTENIMIENTO or (
        item == COMBUSTIBLE and _clasificar(f.get("categoria"), _ACTIVO, None) == esquema.CAT_VEHICULOS)
    placa = f.get("referencia") if es_vehiculo else None
    if tabla.formato == "matriz_energia":
        tipos = ("CUENTA_ENERGIA", "CENTRO_COSTO")
    elif tabla.formato == "matriz_acueducto":
        tipos = ("CUENTA_ACUEDUCTO", "CENTRO_COSTO")
    elif item == SERVICIOS:
        es_en = _clasificar(f.get("concepto"), _SERVICIO, "") == "Energía eléctrica"
        tipos = ("CENTRO_COSTO", "CUENTA_ENERGIA" if es_en else "CUENTA_ACUEDUCTO", "CUENTA_OTRO")
    else:
        tipos = ("CENTRO_COSTO",)
    hoja = f.get("_sede_hoja") if tabla.formato not in ("matriz_energia", "matriz_acueducto") else None
    clave = (normalizar_codigo(f.get("codigo")), normalizar_codigo(f.get("sede")), normalizar_codigo(placa),
             normalizar_codigo(hoja), tipos)
    if clave not in cache:
        cache[clave] = cat.resolver(f.get("codigo"), f.get("sede"), placa, hoja, tipos)
    sede_id, motivo = cache[clave]
    if sede_id is None:
        err(f, "Sede no reconocida: " + motivo + ". Actualice el catálogo y vuelva a cargar.",
            "codigo" if not vacio(f.get("codigo")) else ("placa" if placa else "sede"),
            f.get("codigo") if not vacio(f.get("codigo")) else (placa or f.get("sede")))
    return sede_id


def _servicios(f, reg, err):
    concepto = _clasificar(f.get("concepto"), _SERVICIO, None)
    if concepto is None:
        if vacio(f.get("concepto")):
            err(f, "Falta el servicio (energía, acueducto, aseo…).", "servicio")
        else:
            concepto = "Otro"
            err(f, "Servicio no reconocido; se clasifica como «Otro».", "servicio", f.get("concepto"), ADVERTENCIA)
    reg["concepto"] = concepto
    reg["categoria"] = esquema.CATEGORIA_DE_SERVICIO.get(concepto, esquema.CAT_OTROS_SP)
    cant = a_numero(f.get("cantidad"))
    if cant is None and not vacio(f.get("cantidad")) and normalizar(f.get("cantidad")) not in ("n a", "na"):
        err(f, "El consumo no es un número; se deja vacío.", "consumo", f.get("cantidad"), ADVERTENCIA)
    if cant is not None and cant < 0:
        err(f, "Consumo negativo.", "consumo", cant)
    reg["cantidad"] = cant
    unidad = _texto(f.get("unidad"), 10)
    reg["unidad"] = None if cant is None else (unidad if unidad and normalizar(unidad) not in ("n a", "na")
                                               else esquema.UNIDAD_DE_SERVICIO.get(concepto))
    total = a_numero(f.get("total_a_pagar"))
    if total is None and not vacio(f.get("total_a_pagar")):
        err(f, "El total a pagar no es un número; se deja vacío.", "total_a_pagar", f.get("total_a_pagar"), ADVERTENCIA)
    reg["total_a_pagar"] = total
    reg["referencia"] = _texto(f.get("referencia") if not vacio(f.get("referencia")) else f.get("codigo"), 80)


def _combustible(f, reg, p, err):
    cat_txt = f.get("categoria")
    reg["categoria"] = (esquema.CAT_VEHICULOS if _clasificar(cat_txt, _ACTIVO, None) == esquema.CAT_VEHICULOS
                        else esquema.CAT_PLANTAS)
    es_vehiculo = reg["categoria"] == esquema.CAT_VEHICULOS
    if vacio(f.get("referencia")):
        err(f, "Falta la placa del vehículo o el nombre del equipo.", "placa_o_equipo")
    else:
        reg["referencia"] = (normalizar_codigo(f["referencia"]) if es_vehiculo
                             else re.sub(r"\s+", " ", str(f["referencia"]).strip().upper())[:80])
    reg["concepto"] = _clasificar(f.get("concepto"), _COMBUSTIBLE, "Otro" if es_vehiculo else "Sin especificar")
    es_fluido = reg["concepto"] == "Urea (AdBlue)"

    gal = a_numero(f.get("cantidad"))
    litros = a_numero(f.get("litros"))
    valor = reg["valor"] or 0
    if gal in (None, 0) and litros and 0 < litros <= p["galones_max_tanqueo"] * GALON_EN_LITROS:
        gal = round(litros / GALON_EN_LITROS, 3)
        err(f, f"Sin galones; se calcularon a partir de los litros ({litros:g} L = {gal:g} gal).", "galones",
            f.get("litros"), ADVERTENCIA)
    if gal in (None, 0):
        err(f, "Falta la cantidad de galones; se carga solo el valor.", "galones", f.get("cantidad"), ADVERTENCIA)
        gal = None
    elif gal < 0 or gal > p["galones_max_tanqueo"]:
        err(f, f"Cantidad no plausible ({gal:,.0f} galones en un suministro; máximo {p['galones_max_tanqueo']:g}). "
               "Se carga el valor y la cantidad queda vacía para corregir.", "galones", f.get("cantidad"), ADVERTENCIA)
        gal = None
    elif valor > 0 and not es_fluido:
        precio = valor / gal
        if not p["precio_galon_min"] <= precio <= p["precio_galon_max"]:
            err(f, f"Precio por galón fuera de rango (${precio:,.0f}; plausible entre ${p['precio_galon_min']:,.0f} "
                   f"y ${p['precio_galon_max']:,.0f}). Revise galones y valor contra el soporte.",
                "valor/galones", f"{gal:g} gal / ${valor:,.0f}", ADVERTENCIA)
    reg["cantidad"] = gal
    reg["unidad"] = "gal" if gal is not None else None
    odo = a_numero(f.get("odometro"))
    if es_vehiculo and odo is None and not vacio(f.get("odometro")):
        err(f, "Kilometraje no numérico; se deja vacío.", "odometro", f.get("odometro"), ADVERTENCIA)
    reg["odometro"] = odo if es_vehiculo else None


def _mantenimiento(f, reg, cat: Catalogo):
    ref = normalizar_codigo(f.get("referencia"))
    es_placa = bool(re.fullmatch(r"[A-Z]{3}\d{3}", ref)) or ref in cat.vehiculos
    defecto = esquema.CAT_VEHICULOS if es_placa else "Otro"
    reg["categoria"] = _clasificar(f.get("categoria"), _ACTIVO, defecto)
    if reg["categoria"] == esquema.CAT_VEHICULOS and ref:
        reg["referencia"] = ref
    reg["concepto"] = _clasificar(f.get("concepto"), _TIPO_MTTO, "Sin clasificar")


def _desplazamiento(f, reg, p, err):
    reg["referencia"] = normalizar_codigo(f.get("referencia"))
    if not reg["referencia"]:
        err(f, "Falta la placa del vehículo.", "placa")
    ki, kf = a_numero(f.get("km_inicial")), a_numero(f.get("km_final"))
    km = None
    if ki is None or kf is None:
        err(f, "Kilometraje inicial o final sin dato; el viaje se registra sin kilómetros.", "kilometraje",
            f"{f.get('km_inicial')} / {f.get('km_final')}", ADVERTENCIA)
    elif kf < ki or kf - ki > p["km_max_desplazamiento"]:
        err(f, f"Kilometraje no plausible (inicial {ki:,.0f}, final {kf:,.0f}); el viaje se registra sin kilómetros.",
            "kilometraje", f"{ki:g} / {kf:g}", ADVERTENCIA)
    else:
        km = kf - ki
    reg.update(km_inicial=ki, km_final=kf, km=km, motivo=reg.pop("descripcion"),
               origen=_texto(f.get("origen"), 80), destino=_texto(f.get("destino"), 80), placa=reg["referencia"])


def _duplicados(df: pd.DataFrame, inc: list, item: str):
    if item == DESPLAZAMIENTOS:
        cols = ["fecha", "placa", "km_inicial", "km_final"]
        df2 = df.dropna(subset=["km_inicial"])
    elif item == SERVICIOS:
        cols = ["periodo", "sede_id", "referencia", "concepto"]
        df2 = df
    else:
        cols = ["fecha", "periodo", "sede_id", "referencia", "valor", "cantidad", "documento"]
        df2 = df.dropna(subset=["fecha"]) if item == COMBUSTIBLE else df[df["documento"].notna()]
    dup = df2[df2.duplicated(cols, keep="first")]
    for _, r in dup.iterrows():
        inc.append(Incidencia(ADVERTENCIA, "Posible registro duplicado: coincide con otra fila del archivo en "
                                           + ", ".join(cols) + ". Verifique que no se haya digitado dos veces.",
                              hoja=r["hoja"], fila=int(r["fila"])))

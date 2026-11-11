"""Indicadores, alertas, recomendaciones y análisis interpretativo.

Todo se calcula sobre los registros **vigentes**. Las reglas son explícitas y sus
umbrales están en la tabla ``parametros`` (página Configuración), para que el área
administrativa pueda afinarlas sin tocar código. No se usa ningún servicio externo.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from . import db, esquema
from .config import COMBUSTIBLE, ITEMS, MANTENIMIENTO, SERVICIOS
from .extraccion.comunes import nombre_periodo, periodo_anterior, periodo_de_fecha

COLUMNAS_ALERTA = ["severidad", "item", "tipo", "hospital", "sede", "periodo", "indicador", "valor", "referencia",
                   "mensaje", "recomendacion"]
ORDEN_SEVERIDAD = {"Alta": 0, "Media": 1, "Baja": 2}

RECOMENDACION = {
    "Variación frente al mes anterior": "Comparar la factura o los soportes del mes con los del anterior; confirmar "
        "lecturas del medidor, cambios de servicio (equipos nuevos, obras, brigadas) y descartar cobros duplicados.",
    "Valor atípico frente al historial": "Revisar el soporte del periodo: un valor tan alejado del comportamiento "
        "habitual suele ser un error de digitación, una lectura estimada o un evento puntual que conviene documentar.",
    "Sin reporte en el periodo": "Solicitar a la sede la factura o el reporte del periodo. Si el servicio fue "
        "suspendido o la cuenta cancelada, marcar la sede o el código como inactivo en el catálogo.",
    "Saldo en mora": "Conciliar la cuenta con la empresa prestadora, verificar pagos no aplicados y programar el pago "
        "o un acuerdo: los saldos acumulados generan intereses y riesgo de suspensión del servicio.",
    "Costo unitario atípico": "Verificar la tarifa aplicada (estrato, clase de uso, contribución) y la lectura; si la "
        "cuenta está mal clasificada, pedir la reliquidación a la empresa.",
    "Cobro sin consumo": "Confirmar si la sede está en uso. Si está desocupada, evaluar la suspensión temporal o la "
        "cancelación de la cuenta para no pagar cargos fijos.",
    "Rendimiento bajo": "Revisar el vehículo (mantenimiento, inyección, presión de llantas), contrastar tanqueos con "
        "el odómetro y verificar que todos los recorridos queden en el cuadro de desplazamientos.",
    "Combustible sin recorridos registrados": "Exigir el registro de desplazamientos del vehículo: sin kilómetros no "
        "es posible justificar el consumo de combustible.",
    "Recorridos sin combustible registrado": "Verificar si hubo tanqueos que no se reportaron en el formulario de "
        "combustible o si el vehículo se abasteció con cargo a otra sede.",
    "Mantenimiento acumulado alto": "Evaluar el costo-beneficio de seguir reparando frente a reponer el vehículo, y "
        "revisar si las intervenciones repetidas corresponden a garantías no reclamadas.",
    "Periodo con cobertura parcial": "Cargar el reporte completo del mes antes de comparar: las variaciones de un "
        "periodo incompleto no son concluyentes.",
    "Datos incompletos": "Completar en la fuente la cantidad de galones de cada suministro; sin ella no se puede "
        "calcular precio por galón ni rendimiento.",
    "Catálogo por validar": "Revisar en la página Catálogo las filas marcadas validado = NO y confirmar a qué sede "
        "corresponde cada código o vehículo.",
}


# --------------------------------------------------------------------------- #
# Datos
# --------------------------------------------------------------------------- #
def datos(ruta=None, hospital: str | None = None) -> pd.DataFrame:
    """Registros vigentes con el nombre de sede y hospital."""
    sql = """SELECT r.*, s.hospital, s.sede, s.municipio, s.tipo AS tipo_sede
             FROM registros r JOIN sedes s USING(sede_id) WHERE r.estado='vigente'"""
    df = db.consulta(sql + (" AND s.hospital=?" if hospital else ""), (hospital,) if hospital else (), ruta)
    df["item_nombre"] = df["item"].map(ITEMS)
    return df


def desplazamientos(ruta=None, hospital: str | None = None) -> pd.DataFrame:
    sql = """SELECT d.*, s.hospital, s.sede FROM desplazamientos d JOIN sedes s USING(sede_id)
             WHERE d.estado='vigente'"""
    return db.consulta(sql + (" AND s.hospital=?" if hospital else ""), (hospital,) if hospital else (), ruta)


def vehiculos(ruta=None) -> pd.DataFrame:
    return db.consulta("SELECT v.*, s.hospital FROM vehiculos v JOIN sedes s USING(sede_id)", ruta=ruta)


def periodos(df: pd.DataFrame) -> list[str]:
    return sorted(df["periodo"].unique()) if len(df) else []


def periodo_de_control(df: pd.DataFrame, hoy: dt.date | None = None) -> str | None:
    """Último periodo cerrado con datos (el mes en curso está incompleto y no se evalúa)."""
    actual = periodo_de_fecha(hoy or dt.date.today())
    cerrados = [p for p in periodos(df) if p < actual]
    return cerrados[-1] if cerrados else (periodos(df)[-1] if len(df) else None)


def cobertura_combustible(df: pd.DataFrame) -> pd.DataFrame:
    """Días con registros por periodo y destino del combustible (detecta meses a medias)."""
    c = df[(df["item"] == COMBUSTIBLE) & df["fecha"].notna()]
    if c.empty:
        return pd.DataFrame(columns=["categoria", "periodo", "desde", "hasta", "dias"])
    g = c.groupby(["categoria", "periodo"])["fecha"].agg(desde="min", hasta="max").reset_index()
    g["dias"] = (pd.to_datetime(g["hasta"]) - pd.to_datetime(g["desde"])).dt.days + 1
    return g


def cobertura_desplazamientos(despl: pd.DataFrame) -> dict[str, int]:
    """Días cubiertos por el cuadro de desplazamientos en cada periodo."""
    d = despl[despl["fecha"].notna()] if len(despl) else despl
    if d.empty:
        return {}
    g = d.groupby("periodo")["fecha"].agg(["min", "max"])
    return ((pd.to_datetime(g["max"]) - pd.to_datetime(g["min"])).dt.days + 1).to_dict()


def rendimiento(df: pd.DataFrame, despl: pd.DataFrame, veh: pd.DataFrame) -> pd.DataFrame:
    """Kilómetros, galones y km/galón por vehículo y periodo, frente al rendimiento esperado."""
    c = df[(df["item"] == COMBUSTIBLE) & (df["categoria"] == esquema.CAT_VEHICULOS)
           & (df["concepto"] != "Urea (AdBlue)")]
    g = c.groupby(["periodo", "referencia"]).agg(galones=("cantidad", "sum"), valor=("valor", "sum"),
                                                 tanqueos=("valor", "size"),
                                                 sin_galones=("cantidad", lambda s: int(s.isna().sum()))).reset_index()
    g = g.rename(columns={"referencia": "placa"})
    k = (despl.groupby(["periodo", "placa"]).agg(km=("km", "sum"), viajes=("km", "size")).reset_index()
         if len(despl) else pd.DataFrame(columns=["periodo", "placa", "km", "viajes"]))
    r = g.merge(k, on=["periodo", "placa"], how="outer")
    r = r.merge(veh[["placa", "hospital", "descripcion", "rendimiento_esperado"]], on="placa", how="left")
    for col in ("galones", "valor", "km", "tanqueos", "viajes", "sin_galones"):
        r[col] = r[col].fillna(0.0)
    r["km_por_galon"] = np.where((r["galones"] > 0) & (r["km"] > 0), r["km"] / r["galones"].replace(0, np.nan), np.nan)
    r["pct_esperado"] = r["km_por_galon"] / r["rendimiento_esperado"] * 100
    return r.sort_values(["periodo", "hospital", "placa"]).reset_index(drop=True)


def mora_servicios(df: pd.DataFrame) -> pd.DataFrame:
    """Saldo acumulado por sede y grupo de servicio: total a pagar menos lo facturado en el mes."""
    s = df[(df["item"] == SERVICIOS)]
    if s.empty:
        return pd.DataFrame(columns=["periodo", "hospital", "sede", "sede_id", "categoria", "valor", "total_a_pagar", "saldo"])
    g = s.groupby(["periodo", "hospital", "sede", "sede_id", "categoria"]).agg(
        valor=("valor", "sum"), total_a_pagar=("total_a_pagar", lambda x: x.sum(min_count=1))).reset_index()
    g["saldo"] = (g["total_a_pagar"] - g["valor"]).clip(lower=0)
    return g


# --------------------------------------------------------------------------- #
# Alertas
# --------------------------------------------------------------------------- #
def _n(v: float) -> str:
    """Número entero con separador de miles colombiano."""
    return f"{v:,.0f}".replace(",", ".")


def _d(v: float, dec: int = 1, signo: bool = False) -> str:
    """Decimal con coma (convención colombiana)."""
    return f"{v:{'+' if signo else ''}.{dec}f}".replace(".", ",")


def _fmt(v: float) -> str:
    return "$" + _n(v)


def alertas(df: pd.DataFrame, despl: pd.DataFrame, veh: pd.DataFrame, params: dict, periodo: str | None,
            catalogo_pendiente: int = 0) -> pd.DataFrame:
    out: list[dict] = []
    if df.empty or not periodo:
        return pd.DataFrame(columns=COLUMNAS_ALERTA)
    previo = periodo_anterior(periodo)
    P, np_, npv = periodo, nombre_periodo(periodo), nombre_periodo(previo)

    def alerta(sev, item, tipo, hospital, sede, indicador, valor, ref, mensaje):
        out.append(dict(severidad=sev, item=ITEMS.get(item, item), tipo=tipo, hospital=hospital, sede=sede, periodo=P,
                        indicador=indicador, valor=valor, referencia=ref, mensaje=mensaje,
                        recomendacion=RECOMENDACION.get(tipo, "")))

    cob = cobertura_combustible(df)
    parcial = {(r.categoria, r.periodo): r for r in cob.itertuples() if r.dias < params["dias_cobertura_minima"]}

    # Serie mensual por (ítem, categoría, sede).
    serie = df.groupby(["item", "categoria", "hospital", "sede", "sede_id", "periodo"]).agg(
        valor=("valor", "sum"), cantidad=("cantidad", lambda s: s.sum(min_count=1))).reset_index()
    hay_datos = set(zip(df["item"], df["categoria"], df["periodo"]))

    for (item, cat, hosp, sede, sid), g in serie.groupby(["item", "categoria", "hospital", "sede", "sede_id"]):
        g = g.set_index("periodo")
        v, vp = g["valor"].get(P), g["valor"].get(previo)
        if item == MANTENIMIENTO:
            continue    # el mantenimiento es irregular por naturaleza: se evalúa por acumulado, no mes a mes
        incompleto = item == COMBUSTIBLE and ((cat, P) in parcial or (cat, previo) in parcial)
        # A. Variación frente al mes anterior
        marcado = False
        if v is not None and vp and vp > 0 and not incompleto:
            dif, pct = v - vp, (v - vp) / vp * 100
            if abs(pct) >= params["alerta_variacion_pct"] and abs(dif) >= params["alerta_variacion_min_valor"]:
                sev = "Alta" if pct >= 2 * params["alerta_variacion_pct"] else ("Media" if pct > 0 else "Baja")
                sentido = "aumentó" if pct > 0 else "disminuyó"
                extra = "" if pct > 0 else " Una caída fuerte también puede indicar subregistro."
                alerta(sev, item, "Variación frente al mes anterior", hosp, sede, f"{cat}: valor", v, vp,
                       f"{cat} {sentido} {_d(abs(pct), 0)}% frente a {npv}: de {_fmt(vp)} a {_fmt(v)} "
                       f"({'+' if dif > 0 else '-'}{_fmt(abs(dif))}).{extra}")
                marcado = True
        # B. Atípico frente al historial propio
        hist = g.loc[g.index < P, "valor"]
        if v is not None and not marcado and not incompleto and len(hist) >= params["alerta_meses_historia"]:
            med = hist.median()
            mad = (hist - med).abs().median()
            if mad > 0:
                z = 0.6745 * (v - med) / mad
                if abs(z) >= params["alerta_z_robusto"] and abs(v - med) >= params["alerta_variacion_min_valor"]:
                    alerta("Media" if v > med else "Baja", item, "Valor atípico frente al historial", hosp, sede,
                           f"{cat}: valor", v, med,
                           f"{cat}: {_fmt(v)} en {np_} frente a una mediana de {_fmt(med)} en los "
                           f"{len(hist)} meses anteriores.")
        # C. Sin reporte
        if v is None and vp and vp > 0 and (item, cat, P) in hay_datos and item == SERVICIOS:
            alerta("Media", item, "Sin reporte en el periodo", hosp, sede, f"{cat}: valor", None, vp,
                   f"No hay registro de {cat.lower()} en {np_}; en {npv} se facturaron {_fmt(vp)}.")

    # Categorías completas sin cargar en el periodo
    for (item, cat) in {(i, c) for i, c, p in hay_datos if p == previo and i != MANTENIMIENTO}:
        if (item, cat, P) not in hay_datos:
            alerta("Alta", item, "Sin reporte en el periodo", "Toda la ESE", "", f"{cat}", None, None,
                   f"No se ha cargado información de {cat.lower()} para {np_}, aunque sí existe para {npv}.")
    for (cat, per), r in parcial.items():
        if per == P:
            alerta("Baja", COMBUSTIBLE, "Periodo con cobertura parcial", "Toda la ESE", "", cat, r.dias, None,
                   f"{cat}: los registros de {np_} solo cubren {r.dias} días "
                   f"({pd.Timestamp(r.desde):%d/%m} a {pd.Timestamp(r.hasta):%d/%m}); no se comparan contra el mes anterior.")

    actual = df[df["periodo"] == P]
    # D. Costo unitario de servicios
    for concepto, unidad, minimo in (("Energía eléctrica", "kWh", 50), ("Acueducto", "m3", 5)):
        s = actual[(actual["concepto"] == concepto) & (actual["cantidad"] >= minimo) & (actual["valor"] > 0)]
        s = s.groupby(["hospital", "sede"]).agg(valor=("valor", "sum"), cantidad=("cantidad", "sum")).reset_index()
        if len(s) >= 5:
            s["unit"] = s["valor"] / s["cantidad"]
            med = s["unit"].median()
            for r in s.itertuples():
                desv = (r.unit - med) / med * 100
                if abs(desv) >= params["alerta_costo_unitario_pct"]:
                    alerta("Media" if desv > 0 else "Baja", SERVICIOS, "Costo unitario atípico", r.hospital, r.sede,
                           f"{concepto}: $/{unidad}", r.unit, med,
                           f"{concepto}: {_fmt(r.unit)} por {unidad} frente a una mediana de {_fmt(med)} entre las "
                           f"sedes ({_d(desv, 0, True)}%).")
    # E. Mora
    m = mora_servicios(actual)
    for r in m.itertuples():
        if pd.notna(r.total_a_pagar) and r.saldo >= params["alerta_variacion_min_valor"] and (
                r.valor == 0 or r.total_a_pagar >= params["alerta_mora_factor"] * r.valor):
            meses = f" (equivale a {_d(r.saldo / r.valor, 1)} meses de facturación)" if r.valor > 0 else ""
            sev = "Alta" if r.valor == 0 or r.total_a_pagar >= 3 * r.valor else "Media"
            alerta(sev, SERVICIOS, "Saldo en mora", r.hospital, r.sede, f"{r.categoria}: saldo acumulado", r.saldo,
                   r.valor, f"{r.categoria}: total a pagar {_fmt(r.total_a_pagar)} frente a {_fmt(r.valor)} "
                            f"facturados en {np_}; saldo anterior acumulado {_fmt(r.saldo)}{meses}.")
    # F. Cobro sin consumo
    e = actual[(actual["concepto"] == "Energía eléctrica")].groupby(["hospital", "sede"]).agg(
        valor=("valor", "sum"), cantidad=("cantidad", lambda s: s.sum(min_count=1))).reset_index()
    for r in e[(e["valor"] > 0) & (e["cantidad"] == 0)].itertuples():
        alerta("Baja", SERVICIOS, "Cobro sin consumo", r.hospital, r.sede, "Energía: valor sin kWh", r.valor, 0,
               f"Se facturaron {_fmt(r.valor)} de energía con consumo de 0 kWh en {np_}.")

    # G. Rendimiento de vehículos
    rend = rendimiento(df, despl, veh)
    rend = rend[rend["periodo"] == P]
    comb_parcial = (esquema.CAT_VEHICULOS, P) in parcial
    hay_despl = len(despl) and (despl["periodo"] == P).any()
    dias_despl = cobertura_desplazamientos(despl).get(P, 0)
    if hay_despl and dias_despl < params["dias_cobertura_minima"]:
        alerta("Baja", COMBUSTIBLE, "Periodo con cobertura parcial", "Toda la ESE", "", "Desplazamientos", dias_despl,
               None, f"El cuadro de desplazamientos de {np_} solo cubre {dias_despl} días; el rendimiento (km/galón) "
                     "no se evalúa en este periodo porque compararía el combustible de todo el mes con parte de los recorridos.")
        rend = rend.iloc[0:0]
    for r in rend.itertuples():
        hosp = r.hospital if isinstance(r.hospital, str) else "Sin asignar"
        if hay_despl and r.galones > 0 and r.km == 0:
            alerta("Media", COMBUSTIBLE, "Combustible sin recorridos registrados", hosp, "", "Galones sin km",
                   r.galones, 0, f"Vehículo {r.placa}: {_n(r.galones)} galones ({_fmt(r.valor)}) en {np_} y ningún "
                                 "kilómetro en el cuadro de desplazamientos.")
        elif r.km > 0 and r.tanqueos == 0 and not comb_parcial and (COMBUSTIBLE, esquema.CAT_VEHICULOS, P) in hay_datos:
            alerta("Baja", COMBUSTIBLE, "Recorridos sin combustible registrado", hosp, "", "Km sin galones", r.km, 0,
                   f"Vehículo {r.placa}: {_n(r.km)} km recorridos en {np_} sin ningún tanqueo reportado.")
        elif pd.notna(r.pct_esperado) and r.pct_esperado < params["alerta_rendimiento_pct"] and r.galones >= 20:
            sev = "Alta" if r.pct_esperado < params["alerta_rendimiento_pct"] / 2 else "Media"
            alerta(sev, COMBUSTIBLE, "Rendimiento bajo", hosp, "", "km/galón", r.km_por_galon,
                   r.rendimiento_esperado,
                   f"Vehículo {r.placa}: {_d(r.km_por_galon, 1)} km/gal en {np_} frente a {_d(r.rendimiento_esperado, 1)} "
                   f"esperados ({_d(r.pct_esperado, 0)}%); {_n(r.km)} km con {_n(r.galones)} galones.")
    sin_gal = actual[(actual["item"] == COMBUSTIBLE) & actual["cantidad"].isna()]
    if len(sin_gal):
        alerta("Baja", COMBUSTIBLE, "Datos incompletos", "Toda la ESE", "", "Suministros sin galones", len(sin_gal),
               None, f"{len(sin_gal)} suministros de {np_} por {_fmt(sin_gal['valor'].sum())} no tienen una cantidad "
                     "de galones válida.")

    # H. Mantenimiento acumulado por vehículo (año del periodo)
    mt = df[(df["item"] == MANTENIMIENTO) & (df["periodo"].str[:4] == P[:4]) & (df["periodo"] <= P)
            & (df["categoria"] == esquema.CAT_VEHICULOS) & df["referencia"].notna()]
    ac = mt.groupby(["hospital", "referencia"])["valor"].sum().reset_index()
    if len(ac) >= 5:
        med = ac["valor"].median()
        for r in ac[ac["valor"] >= 2 * med].sort_values("valor", ascending=False).itertuples():
            alerta("Alta" if r.valor >= 3 * med else "Media", MANTENIMIENTO, "Mantenimiento acumulado alto",
                   r.hospital, "", "Acumulado del año", r.valor, med,
                   f"Vehículo {r.referencia}: {_fmt(r.valor)} en mantenimiento acumulado a {np_}, "
                   f"{_d(r.valor / med, 1)} veces la mediana de la flota ({_fmt(med)}).")
    if catalogo_pendiente:
        alerta("Baja", "Catálogo", "Catálogo por validar", "Toda la ESE", "", "Filas por validar", catalogo_pendiente,
               None, f"El catálogo tiene {catalogo_pendiente} sedes, códigos o vehículos marcados como «por validar»; "
                     "los datos asociados pueden estar asignados a una sede equivocada.")

    a = pd.DataFrame(out, columns=COLUMNAS_ALERTA)
    if len(a):
        a["_o"] = a["severidad"].map(ORDEN_SEVERIDAD)
        a["_m"] = pd.to_numeric(a["valor"], errors="coerce").fillna(0)
        a = a.sort_values(["_o", "item", "tipo", "_m"], ascending=[True, True, True, False]).drop(columns=["_o", "_m"])
    return a.reset_index(drop=True)


def pendientes_catalogo(ruta=None) -> int:
    q = ("SELECT (SELECT COUNT(*) FROM sedes WHERE validado=0 AND activo=1) + (SELECT COUNT(*) FROM codigos WHERE "
         "validado=0) + (SELECT COUNT(*) FROM vehiculos WHERE validado=0) AS n")
    return int(db.consulta(q, ruta=ruta)["n"].iloc[0])


# --------------------------------------------------------------------------- #
# Análisis interpretativo
# --------------------------------------------------------------------------- #
def analisis_texto(df: pd.DataFrame, despl: pd.DataFrame, veh: pd.DataFrame, al: pd.DataFrame, periodo: str | None
                   ) -> list[tuple[str, list[str]]]:
    """Secciones (título, párrafos) con la lectura gerencial del periodo."""
    if df.empty or not periodo:
        return [("Sin datos", ["Aún no hay información cargada para analizar."])]
    P, previo = periodo, periodo_anterior(periodo)
    np_, npv = nombre_periodo(P), nombre_periodo(previo)
    act, ant = df[df["periodo"] == P], df[df["periodo"] == previo]
    secciones = []

    # 1. Panorama
    tot, tot_ant = act["valor"].sum(), ant["valor"].sum()
    por_item = act.groupby("item")["valor"].sum()
    p = [f"En {np_} el gasto controlado sumó {_fmt(tot)}: "
         + "; ".join(f"{ITEMS[i].lower()} {_fmt(v)} ({_d(v / tot * 100, 0)}%)" for i, v in
                     por_item.sort_values(ascending=False).items()) + "."]
    comunes = [i for i in por_item.index if i in set(ant["item"])]
    if comunes and tot_ant > 0:
        a1, a0 = act[act["item"].isin(comunes)]["valor"].sum(), ant[ant["item"].isin(comunes)]["valor"].sum()
        p.append(f"Frente a {npv}, y comparando solo los ítems con datos en ambos meses, el gasto "
                 f"{'subió' if a1 >= a0 else 'bajó'} {_d(abs(a1 - a0) / a0 * 100, 1)}% ({_fmt(a0)} → {_fmt(a1)}).")
    faltan = [ITEMS[i] for i in ITEMS if i not in por_item.index]
    if faltan:
        p.append("Sin información cargada en el periodo para: " + ", ".join(faltan).lower()
                 + ". El total anterior no los incluye.")
    hosp = act.groupby("hospital")["valor"].sum().sort_values(ascending=False)
    if len(hosp) > 1:
        top = hosp.head(3)
        p.append("Mayor gasto por hospital: " + "; ".join(f"{h} {_fmt(v)} ({_d(v / tot * 100, 0)}%)" for h, v in top.items())
                 + f". Los tres concentran el {_d(top.sum() / tot * 100, 0)}% del total.")
    secciones.append((f"Panorama de {np_}", p))

    # 2. Servicios públicos
    s, s0 = act[act["item"] == SERVICIOS], ant[ant["item"] == SERVICIOS]
    if len(s):
        p = []
        for cat, g in s.groupby("categoria"):
            v, v0 = g["valor"].sum(), s0[s0["categoria"] == cat]["valor"].sum()
            t = f"{cat}: {_fmt(v)}"
            if v0 > 0:
                t += f" ({_d((v - v0) / v0 * 100, 1, True)}% frente a {npv})"
            p.append(t + "." + _impulsores(g, s0[s0["categoria"] == cat]))
        en = s[(s["concepto"] == "Energía eléctrica") & (s["cantidad"] > 0)]
        if len(en):
            p.append(f"Energía: {_n(en['cantidad'].sum())} kWh a un costo medio de "
                     f"{_fmt(en['valor'].sum() / en['cantidad'].sum())} por kWh.")
        m = mora_servicios(s)
        saldo = m["saldo"].sum()
        if saldo > 0:
            peor = m.sort_values("saldo", ascending=False).head(3)
            p.append(f"Los totales a pagar superan lo facturado en el mes en {_fmt(saldo)}: es saldo de periodos "
                     f"anteriores ({_d(saldo / s['valor'].sum(), 1)} veces la facturación del mes). Mayores saldos: "
                     + "; ".join(f"{r.sede} ({r.categoria.split(',')[0].lower()}) {_fmt(r.saldo)}" for r in peor.itertuples())
                     + ". Conviene conciliar estas cuentas con las empresas prestadoras: si el cuadro de origen "
                       "acumula valores de meses ya pagados, la cifra está sobrestimada.")
        secciones.append(("Servicios públicos", p))

    # 3. Combustible
    c, c0 = act[act["item"] == COMBUSTIBLE], ant[ant["item"] == COMBUSTIBLE]
    if len(c):
        p = []
        gal = c["cantidad"].sum()
        t = f"Se registraron {len(c)} suministros por {_fmt(c['valor'].sum())}"
        if gal > 0:
            con_gal = c[c["cantidad"].notna()]
            t += f" y {_n(gal)} galones (precio medio {_fmt(con_gal['valor'].sum() / gal)} por galón)"
        p.append(t + "." + _impulsores(c, c0))
        cob = cobertura_combustible(df)
        cb = cob[(cob["periodo"] == P)]
        if len(cb) and cb["dias"].min() < 25:
            p.append("Atención: los registros del periodo no cubren el mes completo ("
                     + "; ".join(f"{r.categoria.lower()}: {r.dias} días" for r in cb.itertuples())
                     + "), por lo que las comparaciones son parciales.")
        r = rendimiento(df, despl, veh)
        r = r[(r["periodo"] == P) & r["km_por_galon"].notna()]
        dias_despl = cobertura_desplazamientos(despl).get(P, 0)
        if len(r) and dias_despl < 25:
            p.append(f"El cuadro de desplazamientos solo cubre {dias_despl} días del periodo, por lo que aún no es "
                     "válido calcular el rendimiento (km por galón) del mes.")
        elif len(r):
            flota = r["km"].sum() / r["galones"].sum()
            con_esp = r[r["rendimiento_esperado"].notna()]
            t = (f"Rendimiento de la flota con datos completos ({len(r)} vehículos): {_d(flota, 1)} km por galón, "
                 f"{_n(r['km'].sum())} km recorridos")
            if len(con_esp):
                esperado = con_esp["km"].sum() / (con_esp["km"] / con_esp["rendimiento_esperado"]).sum()
                bajos = (con_esp["pct_esperado"] < 70).sum()
                t += (f"; el esperado para esos recorridos es {_d(esperado, 1)} km/gal. {bajos} de {len(con_esp)} "
                      "vehículos rinden menos del 70% de lo esperado")
            p.append(t + ". Un rendimiento muy inferior al esperado suele reflejar recorridos sin registrar, ralentí "
                         "prolongado o consumo no asociado al vehículo, más que una falla mecánica.")
        secciones.append(("Combustible", p))

    # 4. Mantenimiento
    mt = df[(df["item"] == MANTENIMIENTO) & (df["periodo"].str[:4] == P[:4]) & (df["periodo"] <= P)]
    if len(mt):
        m_act = mt[mt["periodo"] == P]["valor"].sum()
        meses = mt["periodo"].nunique()
        p = [f"Mantenimiento de {np_}: {_fmt(m_act)}. Acumulado del año a {np_}: {_fmt(mt['valor'].sum())} en {meses} "
             f"meses con registros (promedio {_fmt(mt['valor'].sum() / meses)} por mes)."]
        h = mt.groupby("hospital")["valor"].sum().sort_values(ascending=False)
        p.append("Acumulado por hospital: " + "; ".join(f"{k} {_fmt(v)}" for k, v in h.head(4).items()) + ".")
        vref = mt[mt["referencia"].notna()].groupby("referencia")["valor"].sum().sort_values(ascending=False)
        if len(vref) >= 4:
            n = max(1, round(len(vref) * 0.2))
            p.append(f"Concentración: {n} de {len(vref)} activos ({', '.join(vref.head(n).index)}) explican el "
                     f"{_d(vref.head(n).sum() / vref.sum() * 100, 0)}% del gasto acumulado. Son los candidatos a "
                     "evaluar reposición o garantía.")
        secciones.append(("Mantenimientos", p))

    # 5. Prioridades
    if len(al):
        altas = al[al["severidad"] == "Alta"]
        p = [f"El periodo generó {len(al)} alertas: {len(altas)} de severidad alta, "
             f"{(al['severidad'] == 'Media').sum()} media y {(al['severidad'] == 'Baja').sum()} baja."]
        for tipo, g in al[al["severidad"].isin(["Alta", "Media"])].groupby("tipo", sort=False):
            p.append(f"{tipo} ({len(g)}): {RECOMENDACION.get(tipo, '')}")
        secciones.append(("Prioridades de gestión", p[:7]))
    return secciones


def _impulsores(act: pd.DataFrame, ant: pd.DataFrame) -> str:
    """Frase con los hospitales que más explican el cambio frente al mes anterior."""
    if ant.empty:
        return ""
    d = (act.groupby("hospital")["valor"].sum().sub(ant.groupby("hospital")["valor"].sum(), fill_value=0))
    d = d[d.abs() > 0].sort_values()
    if d.empty:
        return ""
    partes = []
    if d.iloc[-1] > 0:
        partes.append(f"mayor aumento en {d.index[-1]} (+{_fmt(d.iloc[-1])})")
    if d.iloc[0] < 0:
        partes.append(f"mayor reducción en {d.index[0]} (-{_fmt(abs(d.iloc[0]))})")
    return " Cambio explicado sobre todo por: " + " y ".join(partes) + "." if partes else ""

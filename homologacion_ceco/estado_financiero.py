"""Estado financiero adaptado y homologado.

* **Por cuenta y sede/IPS**: la estructura del estado financiero de origen (clase → grupo → cuenta → subcuenta →
  auxiliar) con una columna por IPS (primer token de la sede, igual que la llave CECO|SEDE) y el total.
  Debajo se muestran las partidas NO cargadas por estado del CECO, para que el total cuadre con lo leído.
* **Por CECO de resolución y sede/IPS**: tipo de centro → CECO de resolución, con una columna por IPS.

Solo se incluyen registros CARGADOS (CECO ACTIVO) en el cuerpo; los valores se suman en centavos exactos.
"""
from collections import defaultdict

import pandas as pd

from . import config as C
from .procesador import CARGADO, RETENIDO

TOTAL = "TOTAL"


def orden_sedes(sedes):
    preferido = [C.sede_corta(s["sede"]) for s in C.sedes_y_puc()["sedes"]]
    unicos = []
    for s in preferido:
        if s not in unicos:
            unicos.append(s)
    presentes = set(sedes)
    return [s for s in unicos if s in presentes] + sorted(presentes - set(unicos))


def nombres_sede(resultado):
    """Sigla → nombre largo de la sede principal (para el encabezado)."""
    nombres = {}
    for s in C.sedes_y_puc()["sedes"]:
        nombres.setdefault(C.sede_corta(s["sede"]), s["sede"])
    for s in resultado.referencia.maestro["sede"].drop_duplicates():
        nombres.setdefault(C.sede_corta(s), s)
    return nombres


def _nombre_nivel(prefijo, nombres_cuenta):
    puc = C.sedes_y_puc()["nombres_cuenta"]
    return nombres_cuenta.get(prefijo) or puc.get(prefijo) or ""


def por_cuenta(resultado):
    """{periodo: DataFrame(nivel, cuenta, nombre, <sedes>, TOTAL)} con subtotales jerárquicos."""
    reg = resultado.registros
    niveles = list(C.sedes_y_puc()["niveles_cuenta"])
    salida = {}
    for periodo in sorted(p for p in reg["periodo"].dropna().unique() if p):
        rp = reg[reg["periodo"] == periodo]
        car = rp[rp["destino"] == CARGADO]
        ret = rp[rp["destino"] == RETENIDO]
        sedes = orden_sedes(set(rp["sede_ips"].dropna()) - {""})
        if (ret["sede_ips"].fillna("") == "").any():
            sedes.append("SIN SEDE")
        valores = defaultdict(lambda: defaultdict(int))   # prefijo → sede → centavos
        hojas = set()
        for r in car.itertuples(index=False):
            cta = r.cuenta
            hojas.add(cta)
            for n in niveles:
                if len(cta) > n:
                    valores[cta[:n]][r.sede_ips] += r.valor_cent
            valores[cta][r.sede_ips] += r.valor_cent
        filas = []
        for prefijo in sorted(valores, key=lambda p: (p, len(p))):
            nivel = next((i + 1 for i, n in enumerate(niveles) if len(prefijo) == n), len(niveles) + 1)
            if prefijo in hojas and len(prefijo) not in niveles:
                nivel = len(niveles) + 1
            nombre = _nombre_nivel(prefijo, resultado.nombres_cuenta)
            filas.append(_fila(nivel, prefijo, nombre, valores[prefijo], sedes))
        tot = defaultdict(int)
        for r in car.itertuples(index=False):
            tot[r.sede_ips] += r.valor_cent
        filas.append(_fila(0, "", "TOTAL COSTOS Y GASTOS HOMOLOGADOS (CARGADOS)", tot, sedes))
        if len(ret):
            filas.append(_fila(-1, "", "PARTIDAS NO CARGADAS POR ESTADO DEL CECO", {}, sedes, vacia=True))
            tot_ret = defaultdict(int)
            for estado, g in ret.groupby("estado"):
                v = defaultdict(int)
                for r in g.itertuples(index=False):
                    v[r.sede_ips or "SIN SEDE"] += r.valor_cent or 0
                    tot_ret[r.sede_ips or "SIN SEDE"] += r.valor_cent or 0
                filas.append(_fila(-2, "", estado, v, sedes))
            todo = defaultdict(int)
            for k, v in list(tot.items()) + list(tot_ret.items()):
                todo[k] += v
            filas.append(_fila(0, "", "TOTAL LEÍDO VÁLIDO (CARGADO + NO CARGADO)", todo, sedes))
        columnas = ["nivel", "cuenta", "nombre"] + sedes + [TOTAL]
        salida[periodo] = pd.DataFrame(filas).reindex(columns=columnas)
    return salida


def por_ceco(resultado):
    """{periodo: DataFrame(nivel, tipo/código, nombre, <sedes>, TOTAL)} de los CECO de resolución cargados."""
    reg = resultado.registros
    salida = {}
    for periodo in sorted(p for p in reg["periodo"].dropna().unique() if p):
        car = reg[(reg["periodo"] == periodo) & (reg["destino"] == CARGADO)]
        sedes = orden_sedes(set(car["sede_ips"].dropna()) - {""})
        filas, total = [], defaultdict(int)
        tipos = list(C.TIPOS) + sorted(set(car["tipo"].dropna()) - set(C.TIPOS))
        for tipo in tipos:
            g = car[car["tipo"] == tipo]
            if g.empty:
                continue
            sub = defaultdict(int)
            detalle = []
            for (cod, nombre), gg in g.groupby(["cod_res", "nombre_res"], sort=True):
                v = defaultdict(int)
                for r in gg.itertuples(index=False):
                    v[r.sede_ips] += r.valor_cent
                    sub[r.sede_ips] += r.valor_cent
                    total[r.sede_ips] += r.valor_cent
                detalle.append(_fila(2, cod, nombre, v, sedes))
            filas.append(_fila(1, "", tipo, sub, sedes))
            filas.extend(detalle)
        filas.append(_fila(0, "", "TOTAL CARGADO", total, sedes))
        salida[periodo] = pd.DataFrame(filas).reindex(columns=["nivel", "cuenta", "nombre"] + sedes + [TOTAL])
    return salida


def _fila(nivel, codigo, nombre, valores, sedes, vacia=False):
    fila = {"nivel": nivel, "cuenta": codigo, "nombre": nombre}
    if vacia:
        return fila
    for s in sedes:
        fila[s] = valores.get(s, 0) / 100
    fila[TOTAL] = sum(valores.values()) / 100
    return fila

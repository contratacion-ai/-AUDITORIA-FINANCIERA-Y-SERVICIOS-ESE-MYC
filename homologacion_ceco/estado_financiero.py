"""Estado de resultados adaptado y homologado.

* **Por cuenta y sede/IPS**: la estructura del estado financiero de origen (clase → grupo → cuenta → subcuenta →
  auxiliar) con una columna por IPS (primer token de la sede, igual que la llave CECO|SEDE) y el total. Los
  ingresos (clase 4) se muestran en positivo y se calcula el resultado del periodo. Debajo se muestran los gastos
  y costos NO cargados (por estado del CECO y sin centro de costo), para que el total cuadre con el reporte.
* **Por CECO de resolución y sede/IPS**: gastos y costos por tipo de centro → CECO de resolución, por IPS.

Solo se incluyen registros CARGADOS (CECO ACTIVO) en el cuerpo; los valores se suman en centavos exactos.
"""
from collections import defaultdict

import pandas as pd

from . import config as C
from .procesador import CARGADO, RECHAZADO, RETENIDO

TOTAL = "TOTAL"
SIN_SEDE = "SIN SEDE"
RECHAZADO_ETIQUETA = "SIN CENTRO DE COSTO O CON ERROR (RECHAZADO)"


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


def signo(cuenta):
    """+1 para cuentas de naturaleza débito (gastos y costos), −1 para crédito (ingresos): se muestran en positivo."""
    return -1 if str(cuenta)[:1] in C.sedes_y_puc().get("clases_naturaleza_credito", []) else 1


def es_costo(cuenta):
    return str(cuenta)[:1] in C.sedes_y_puc()["clases_costo"]


def por_cuenta(resultado):
    """{periodo: DataFrame(nivel, cuenta, nombre, <sedes>, TOTAL)} con subtotales jerárquicos.

    Los ingresos (naturaleza crédito) se muestran en positivo. Al final: total de ingresos, total de gastos y costos,
    resultado del periodo y las partidas no cargadas (por estado del CECO y sin centro de costo), para que el total
    de gastos y costos cuadre con el reporte de origen.
    """
    reg = resultado.registros
    niveles = list(C.sedes_y_puc()["niveles_cuenta"])
    salida = {}
    for periodo in sorted(p for p in reg["periodo"].dropna().unique() if p):
        rp = reg[reg["periodo"] == periodo]
        car = rp[rp["destino"] == CARGADO]
        ret = rp[rp["destino"] == RETENIDO]
        rech = rp[(rp["destino"] == RECHAZADO) & rp["valor_cent"].notna() & rp["cuenta"].fillna("").map(bool)]
        sedes = orden_sedes(set(rp["sede_ips"].dropna()) - {""})
        if (ret["sede_ips"].fillna("") == "").any() or len(rech):
            sedes.append(SIN_SEDE)
        valores = defaultdict(lambda: defaultdict(int))   # prefijo → sede → centavos (con signo de presentación)
        hojas = set()
        ingresos, costos = defaultdict(int), defaultdict(int)
        for r in car.itertuples(index=False):
            cta, v = r.cuenta, r.valor_cent * signo(r.cuenta)
            hojas.add(cta)
            for n in niveles:
                if len(cta) > n:
                    valores[cta[:n]][r.sede_ips] += v
            valores[cta][r.sede_ips] += v
            (ingresos if signo(cta) < 0 else costos)[r.sede_ips] += v
        filas = []
        for prefijo in sorted(valores, key=lambda p: (p, len(p))):
            nivel = next((i + 1 for i, n in enumerate(niveles) if len(prefijo) == n), len(niveles) + 1)
            if prefijo in hojas and len(prefijo) not in niveles:
                nivel = len(niveles) + 1
            filas.append(_fila(nivel, prefijo, _nombre_nivel(prefijo, resultado.nombres_cuenta), valores[prefijo], sedes))
        if ingresos:
            filas.append(_fila(0, "", "TOTAL INGRESOS (CARGADOS)", ingresos, sedes))
        filas.append(_fila(0, "", "TOTAL GASTOS Y COSTOS HOMOLOGADOS (CARGADOS)", costos, sedes))
        if ingresos:
            resultado_sede = defaultdict(int)
            for k, v in ingresos.items():
                resultado_sede[k] += v
            for k, v in costos.items():
                resultado_sede[k] -= v
            filas.append(_fila(0, "", "RESULTADO DEL PERIODO (INGRESOS − GASTOS Y COSTOS)", resultado_sede, sedes))

        no_cargado = defaultdict(int)
        bloques = []
        for estado, g in ret.groupby("estado"):
            v = defaultdict(int)
            for r in g.itertuples(index=False):
                if es_costo(r.cuenta) and r.valor_cent is not None:
                    v[r.sede_ips or SIN_SEDE] += r.valor_cent
                    no_cargado[r.sede_ips or SIN_SEDE] += r.valor_cent
            if v:
                bloques.append(_fila(-2, "", estado, v, sedes))
        v = defaultdict(int)
        for r in rech.itertuples(index=False):
            if es_costo(r.cuenta):
                v[SIN_SEDE] += int(r.valor_cent)
                no_cargado[SIN_SEDE] += int(r.valor_cent)
        if v:
            bloques.append(_fila(-2, "", RECHAZADO_ETIQUETA, v, sedes))
        if bloques:
            filas.append(_fila(-1, "", "GASTOS Y COSTOS NO CARGADOS", {}, sedes, vacia=True))
            filas.extend(bloques)
            todo = defaultdict(int)
            for k, val in list(costos.items()) + list(no_cargado.items()):
                todo[k] += val
            filas.append(_fila(0, "", "TOTAL GASTOS Y COSTOS LEÍDOS (CARGADO + NO CARGADO)", todo, sedes))
        columnas = ["nivel", "cuenta", "nombre"] + sedes + [TOTAL]
        salida[periodo] = pd.DataFrame(filas).reindex(columns=columnas)
    return salida


def por_ceco(resultado):
    """{periodo: DataFrame(nivel, tipo/código, nombre, <sedes>, TOTAL)}: gastos y costos (clases de costo) por CECO."""
    reg = resultado.registros
    salida = {}
    for periodo in sorted(p for p in reg["periodo"].dropna().unique() if p):
        car = reg[(reg["periodo"] == periodo) & (reg["destino"] == CARGADO) & reg["cuenta"].fillna("").map(es_costo)]
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
        filas.append(_fila(0, "", "TOTAL GASTOS Y COSTOS CARGADOS", total, sedes))
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

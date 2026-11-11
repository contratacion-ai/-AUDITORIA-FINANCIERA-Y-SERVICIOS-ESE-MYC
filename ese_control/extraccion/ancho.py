"""Extractor de matrices "anchas" de servicios públicos.

Es el formato de los cuadros anuales de energía y de acueducto: una fila por cuenta
o sede y un bloque de columnas por mes (consumo, valor facturado, total…). El
extractor ubica la fila con los nombres de los meses, la de subencabezados y las
columnas que identifican la cuenta (CODIGO, USUARIO, M/PIO), estén donde estén.
"""
from __future__ import annotations

import re

import pandas as pd

from .. import esquema
from ..config import MESES, SERVICIOS
from .comunes import (ADVERTENCIA, ERROR, INFO, Incidencia, Tabla, a_numero, buscar_anio, mes_de_texto,
                      normalizar, vacio)
from .lectura import Rejilla

# subencabezado normalizado -> medida
_MEDIDAS = [
    (r"^kw", "kwh"),
    (r"^m ?3$|^consumo", "m3"),
    (r"^agua$|^acueducto", "acueducto"),
    (r"^alcantarillado", "alcantarillado"),
    (r"^aseo", "aseo"),
    (r"^alumbrado", "alumbrado"),
    (r"^v(alor)? ?fact", "facturado"),
    (r"^t factura|^v(alor)? ?t(otal)?( f)?$|^total", "total"),
]
_IDENTIDAD = [
    (r"^cod(igo)?", "codigo"),
    (r"^usuario|^sede|^nombre|^suscriptor", "sede"),
    (r"^m pio|^municipio", "municipio"),
    (r"^sector|^zona", "sector"),
]


def _medida(texto) -> str | None:
    t = normalizar(texto)
    for patron, m in _MEDIDAS:
        if t and re.search(patron, t):
            return m
    return None


def detectar(rejilla: Rejilla):
    """Fila de meses: la primera con al menos dos nombres de mes distintos."""
    for i, fila in enumerate(rejilla.filas[:25]):
        meses = {}
        for j, c in enumerate(fila):
            m = mes_de_texto(c) if isinstance(c, str) else None
            if m:
                meses.setdefault(j, m)
        if len(set(meses.values())) >= 2 and i + 1 < len(rejilla.filas):
            sub = rejilla.filas[i + 1]
            if sum(1 for c in sub if _medida(c)) >= 2:
                return i, meses
    return None


def extraer(rejilla: Rejilla, nombre_archivo: str = "") -> tuple[list[Tabla], list[Incidencia]]:
    det = detectar(rejilla)
    if det is None:
        return [], []
    inc: list[Incidencia] = []
    i_mes, meses_col = det
    fila_mes, sub = rejilla.filas[i_mes], rejilla.filas[i_mes + 1]
    hoja = rejilla.hoja
    n_cols = max(len(f) for f in rejilla.filas)

    titulos = [c for fila in rejilla.filas[:i_mes + 1] for c in fila if isinstance(c, str)]
    anio = buscar_anio(titulos + [hoja, nombre_archivo])

    # Columnas de identidad (pueden estar al inicio o en medio de la hoja).
    ident: dict[str, int] = {}
    for j, c in enumerate(sub):
        t = normalizar(c)
        for patron, nombre in _IDENTIDAD:
            if t and re.search(patron, t) and nombre not in ident:
                ident[nombre] = j
    if "codigo" not in ident and "sede" not in ident:
        return [], [Incidencia(ERROR, "La hoja tiene meses en columnas pero no se encontró la columna CODIGO ni "
                                      "USUARIO/SEDE que identifique cada fila.", hoja=hoja, fila=i_mes + 2)]

    # Bloques: cada mes abarca desde su primera columna hasta el siguiente mes o columna de identidad.
    inicios = sorted({j for j in meses_col
                      if j == 0 or mes_de_texto(fila_mes[j - 1]) != meses_col[j]})
    cortes = sorted(set(inicios) | set(ident.values()) | {n_cols})
    bloques = []   # (mes, {medida: columna})
    for j0 in inicios:
        j1 = next(c for c in cortes if c > j0)
        medidas = {}
        for j in range(j0, min(j1, len(sub))):
            m = _medida(sub[j])
            if m and m not in medidas:
                medidas[m] = j
        if medidas:
            bloques.append((meses_col[j0], medidas))
    es_energia = any("kwh" in m for _, m in bloques)

    # Filas de datos.
    registros = []
    i0 = i_mes + 2
    vacias = 0
    fin = None
    for i in range(i0, len(rejilla.filas)):
        fila = rejilla.filas[i] + [None] * (n_cols - len(rejilla.filas[i]))
        if all(vacio(v) for v in fila):
            vacias += 1
            if vacias >= 2 and registros:
                fin = i
                break
            continue
        vacias = 0
        codigo = fila[ident["codigo"]] if "codigo" in ident else None
        sede = fila[ident["sede"]] if "sede" in ident else None
        tiene_datos = any(a_numero(fila[j]) not in (None, 0) for _, med in bloques for j in med.values())
        if vacio(codigo) and vacio(sede):
            if tiene_datos and registros:
                fin = i     # fila de totales: fin de la tabla
                break
            continue
        registros.append((i + 1, codigo, sede, fila))
    if fin is not None:
        resto = sum(1 for f in rejilla.filas[fin:] if not all(vacio(v) for v in f))
        inc.append(Incidencia(INFO, f"La tabla termina en la fila {fin}. Las {resto} filas siguientes (totales o "
                                    "notas) no se cargan.", hoja=hoja, fila=fin + 1))

    # Meses repetidos (p. ej. dos bloques SEPTIEMBRE): se usa el que tenga datos.
    def _suma_bloque(med):
        return sum(abs(a_numero(f[j]) or 0) for _, _, _, f in registros for j in med.values())

    por_mes: dict[int, list] = {}
    for mes, med in bloques:
        por_mes.setdefault(mes, []).append(med)
    elegidos = {}
    for mes, lista in por_mes.items():
        con_datos = [m for m in lista if _suma_bloque(m) > 0]
        if len(con_datos) > 1:
            inc.append(Incidencia(ERROR, f"El mes de {MESES[mes - 1]} aparece en {len(con_datos)} bloques de columnas "
                                         "con datos; no se puede saber cuál es el correcto. Deje uno solo.",
                                  hoja=hoja, fila=i_mes + 1, columna=MESES[mes - 1]))
        elif con_datos:
            elegidos[mes] = con_datos[0]
    meses_vacios = [MESES[m - 1] for m in por_mes if m not in elegidos and len(
        [x for x in por_mes[m] if _suma_bloque(x) > 0]) == 0]
    if meses_vacios:
        inc.append(Incidencia(INFO, "Meses sin datos, no se cargan: " + ", ".join(meses_vacios) + ".", hoja=hoja))
    if anio is None:
        inc.append(Incidencia(ADVERTENCIA, "No se encontró el año en la hoja ni en el nombre del archivo; se usará "
                                           "el año del periodo indicado al cargar.", hoja=hoja))

    out = []
    vistos = set()
    for n_fila, codigo, sede, fila in registros:
        for mes, med in sorted(elegidos.items()):
            v = {m: a_numero(fila[j]) for m, j in med.items()}
            no_num = [(m, fila[j]) for m, j in med.items() if v[m] is None and not vacio(fila[j])]
            for m, crudo in no_num:
                inc.append(Incidencia(ADVERTENCIA, "Valor no numérico; se toma como vacío.", hoja=hoja, fila=n_fila,
                                      columna=f"{MESES[mes - 1]} / {sub[med[m]]}", valor=str(crudo)[:60]))
            if all(x in (None, 0) for x in v.values()):
                continue
            clave = (str(codigo), mes)
            if not vacio(codigo) and clave in vistos:
                inc.append(Incidencia(ERROR, f"El código {codigo} aparece más de una vez con datos en "
                                             f"{MESES[mes - 1]}; se conserva la primera fila.", hoja=hoja, fila=n_fila,
                                      columna=MESES[mes - 1], valor=str(codigo)))
                continue
            vistos.add(clave)
            base = {"periodo": f"{MESES[mes - 1]}" + (f" {anio}" if anio else ""), "codigo": codigo, "sede": sede,
                    "referencia": codigo, "_hoja": hoja, "_fila": n_fila, "_anio": anio}
            out.extend(_filas_energia(base, v) if es_energia else _filas_agua(base, v, inc, mes))

    df = pd.DataFrame(out)
    for c in esquema.COLUMNAS_CRUDAS:
        if c not in df.columns:
            df[c] = None
    servicio = "energía" if es_energia else "acueducto, alcantarillado y aseo"
    tabla = Tabla(item=SERVICIOS, formato="matriz_energia" if es_energia else "matriz_acueducto", hoja=hoja, datos=df,
                  descripcion=f"matriz anual de {servicio}: {len(registros)} cuentas, meses con datos: "
                              + (", ".join(MESES[m - 1] for m in sorted(elegidos)) or "ninguno"),
                  columnas_usadas={k: str(sub[j]) for k, j in ident.items()})
    return [tabla], inc


def _filas_energia(base: dict, v: dict) -> list[dict]:
    return [dict(base, concepto="Energía eléctrica", cantidad=v.get("kwh"), unidad="kWh",
                 valor=v.get("facturado") or 0, total_a_pagar=v.get("total"))]


def _filas_agua(base: dict, v: dict, inc: list, mes: int) -> list[dict]:
    """Una fila por concepto de la factura; el total a pagar va una sola vez (en la primera)."""
    partes = [("Acueducto", "acueducto"), ("Alcantarillado", "alcantarillado"), ("Aseo", "aseo"),
              ("Alumbrado público", "alumbrado")]
    filas = []
    for nombre, clave in partes:
        if v.get(clave) not in (None, 0) or (clave == "acueducto" and v.get("m3") not in (None, 0)):
            filas.append(dict(base, concepto=nombre, valor=v.get(clave) or 0,
                              cantidad=v.get("m3") if clave == "acueducto" else None,
                              unidad="m3" if clave == "acueducto" else None))
    suma = sum(f["valor"] for f in filas)
    fact = v.get("facturado")
    if not filas and fact not in (None, 0):
        filas.append(dict(base, concepto="Acueducto y aseo (sin discriminar)", valor=fact,
                          cantidad=v.get("m3"), unidad="m3"))
    elif not filas and v.get("total") not in (None, 0):
        filas.append(dict(base, concepto="Acueducto y aseo (sin discriminar)", valor=0))
    elif fact not in (None, 0) and abs(fact - suma) > max(100, 0.01 * abs(fact)):
        inc.append(Incidencia(ADVERTENCIA, f"En {MESES[mes - 1]} el valor facturado ({fact:,.0f}) no coincide con la "
                                           f"suma de los conceptos ({suma:,.0f}). Se cargan los conceptos; revise la "
                                           "digitación.", hoja=base["_hoja"], fila=base["_fila"],
                              columna=MESES[mes - 1], valor=f"{fact:,.0f}"))
    if filas:
        filas[0]["total_a_pagar"] = v.get("total")
    return filas

"""Extractor de tablas "largas": una fila por factura, tanqueo, mantenimiento o viaje.

Sirve para las plantillas de la herramienta, las exportaciones de formularios y de
sistemas, y las hojas de cálculo que lleva cada sede. No depende de la posición de
las columnas ni de la fila donde empieza la tabla: busca la fila de encabezados
comparando cada celda con los sinónimos de ``esquema.PERFILES``.
"""
from __future__ import annotations

import re

import pandas as pd

from .. import esquema
from ..config import COMBUSTIBLE, DESPLAZAMIENTOS, MANTENIMIENTO
from .comunes import INFO, Incidencia, Tabla, buscar_anio, es_fila_total, normalizar, vacio
from .lectura import Rejilla

MAX_FILAS_ENCABEZADO = 60


def _mapear(fila: list, perfil: dict) -> dict[str, int]:
    """campo -> índice de columna, para una fila candidata a encabezado."""
    mapa: dict[str, int] = {}
    usados: set[int] = set()
    # Dos pasadas: primero los patrones más específicos (los que aparecen antes).
    for campo, patrones in perfil["sinonimos"].items():
        for patron in patrones:
            for j, celda in enumerate(fila):
                if j in usados or not isinstance(celda, str):
                    continue
                t = normalizar(celda)
                if not t or len(t) > 70:
                    continue
                if any(re.search(x, t) for x in perfil["excluir"].get(campo, [])):
                    continue
                if re.search(patron, t):
                    mapa[campo] = j
                    usados.add(j)
                    break
            if campo in mapa:
                break
    return mapa


def _cumple(mapa: dict, perfil: dict) -> bool:
    return all(any(c in mapa for c in grupo) for grupo in perfil["requeridos"])


def detectar_encabezado(rejilla: Rejilla, item_forzado: str | None = None, desde: int = 0):
    """Devuelve (índice de fila, perfil, mapa) del mejor encabezado, o None."""
    mejor = None
    for i in range(desde, min(len(rejilla.filas), desde + MAX_FILAS_ENCABEZADO)):
        fila = rejilla.filas[i]
        if sum(isinstance(c, str) for c in fila) < 2:
            continue
        for perfil in esquema.PERFILES:
            if item_forzado and perfil["item"] != item_forzado and not (
                    item_forzado == COMBUSTIBLE and perfil["item"] == DESPLAZAMIENTOS):
                continue
            mapa = _mapear(fila, perfil)
            if not _cumple(mapa, perfil):
                continue
            if perfil["item"] == MANTENIMIENTO and item_forzado != MANTENIMIENTO:
                texto = " ".join(normalizar(c) for c in fila if isinstance(c, str)) + " " + normalizar(rejilla.hoja)
                if not re.search(esquema.PISTA_MANTENIMIENTO, texto):
                    continue
            puntaje = len(mapa)
            if mejor is None or puntaje > mejor[3]:
                mejor = (i, perfil, mapa, puntaje)
        if mejor and mejor[0] == i:
            break   # el primer encabezado válido manda; lo que haya debajo son datos
    return mejor[:3] if mejor else None


def extraer(rejilla: Rejilla, item_forzado: str | None = None, nombre_archivo: str = ""
            ) -> tuple[list[Tabla], list[Incidencia]]:
    inc: list[Incidencia] = []
    det = detectar_encabezado(rejilla, item_forzado)
    if det is None:
        return [], inc
    i_enc, perfil, mapa = det
    encabezado = rejilla.filas[i_enc]
    item = perfil["item"]
    titulos = [c for fila in rejilla.filas[:i_enc] for c in fila if isinstance(c, str)]
    anio = buscar_anio(titulos + [rejilla.hoja, nombre_archivo])

    filas_out = []
    vacias_seguidas = 0
    fin = None
    for i in range(i_enc + 1, len(rejilla.filas)):
        fila = rejilla.filas[i]
        n_fila = rejilla.fila_inicial + i
        valores = {c: (fila[j] if j < len(fila) else None) for c, j in mapa.items()}
        if all(vacio(v) for v in fila):
            vacias_seguidas += 1
            continue
        if es_fila_total(fila):
            fin = i   # después del total solo pueden venir resúmenes
            break
        if vacias_seguidas >= 2 and len(filas_out) and _parece_otro_encabezado(fila, perfil):
            fin = i
            break
        vacias_seguidas = 0
        if all(vacio(v) for v in valores.values()):
            continue
        valores["_hoja"] = rejilla.hoja
        valores["_fila"] = n_fila
        filas_out.append(valores)

    if fin is not None:
        resto = sum(1 for f in rejilla.filas[fin + 1:] if not all(vacio(v) for v in f))
        if resto:
            inc.append(Incidencia(INFO, f"Se leyó la tabla hasta la fila {rejilla.fila_inicial + fin} (totales). "
                                        f"Las {resto} filas siguientes son resúmenes o notas y no se cargan.",
                                  hoja=rejilla.hoja, fila=rejilla.fila_inicial + fin))

    df = pd.DataFrame(filas_out)
    for c in esquema.COLUMNAS_CRUDAS:
        if c not in df.columns:
            df[c] = None
    df["_anio"] = anio
    df["_sede_hoja"] = rejilla.hoja   # respaldo: una hoja por sede

    formato = "tabla"
    if item == COMBUSTIBLE and len(df):
        formato = _completar_combustible(df, encabezado, mapa)
    elif item == MANTENIMIENTO and len(df):
        formato = "mantenimiento"
    elif item == DESPLAZAMIENTOS:
        formato = "desplazamientos"

    usadas = {c: str(encabezado[j]).strip() for c, j in mapa.items()}
    ignoradas = [str(c).strip() for j, c in enumerate(encabezado)
                 if isinstance(c, str) and c.strip() and j not in mapa.values()]
    tabla = Tabla(item=item, formato=formato, hoja=rejilla.hoja, datos=df,
                  descripcion=f"encabezados en la fila {rejilla.fila_inicial + i_enc}, {len(df)} filas con datos",
                  columnas_usadas=usadas, columnas_ignoradas=ignoradas)
    return [tabla], inc


def _parece_otro_encabezado(fila: list, perfil: dict) -> bool:
    return len(_mapear(fila, perfil)) >= 2 and not any(isinstance(c, (int, float)) for c in fila)


def _completar_combustible(df: pd.DataFrame, encabezado: list, mapa: dict) -> str:
    """Deduce destino (vehículo o planta/equipo) y tipo de combustible cuando no vienen."""
    titulo_ref = normalizar(encabezado[mapa["referencia"]]) if "referencia" in mapa else ""
    es_vehiculo = "placa" in titulo_ref or "vehiculo" in titulo_ref
    if "categoria" not in mapa:
        df["categoria"] = esquema.CAT_VEHICULOS if es_vehiculo else esquema.CAT_PLANTAS
    if "concepto" not in mapa:
        # "PLANTA ELECTRICA DESEL", "PLANTA ELECTRICA GASOLINA ESTATICA"...
        df["concepto"] = df["referencia"]
    return "combustible_vehiculos" if es_vehiculo else "combustible_equipos"

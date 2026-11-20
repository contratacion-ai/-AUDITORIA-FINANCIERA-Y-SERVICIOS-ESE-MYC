"""Lectura de notas técnicas de capitación en sus distintos formatos.

Formatos reconocidos (en cualquier hoja del libro; la fila de encabezados se
busca en las primeras 40 filas):

* **Lista simple** (Nueva EPS): ``CodigoCUP``, ``Homologo``, ``ESTADO CUPS``,
  ``DescripcionCUP``.
* **Una hoja por municipio** (Sanitas «FORMATO CAPITA TAME»…): datos del
  contrato arriba (``CONTRATISTA:``, ``CONSECUTIVO DEL CONTRATO:``,
  ``MUNICIPIO DE COBERTURA:``, ``POBLACIÓN PROYECTADA:``, ``REGIMEN:``) y la
  tabla ``Agrupador``, ``CUPS``, ``CODIGO NT``, ``DESCRIPCIÓN``,
  ``Actividades mes`` (meta mensual).
* **Lista con municipio por fila** (Coosalud «NT_SERVICIOS SUB»):
  ``MUNICIPIO``, ``REGIMEN``, ``CodigoCUPS``, ``DescripcionCUPS``, ``Agrupador``.
* **Matriz de PyM por actividad** (Coosalud «NT 2025 PYM»): una actividad por
  fila con varios códigos en la celda «Código CUPS o REPS» (separados por
  saltos de línea, comas o guiones, o en rangos «990201 al 990206») y el curso
  de vida en «Población». Cada código se agrega con la actividad como
  descripción; la meta de la actividad no se reparte entre sus códigos.

Las hojas auxiliares «Hoja1», «Hoja2»… se omiten si hay otras hojas con tabla.
Si un código se repite en el mismo municipio se conserva el primero (las
listas de servicios antes que la matriz de PyM).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pandas as pd

from .catalogo_cups import normalizar_codigo

COLUMNAS = ["codigo", "homologo", "estado", "descripcion", "agrupador", "meta_mes", "ambito", "contrato",
            "contratista", "poblacion", "regimen", "hoja"]
_META = {  # etiqueta (normalizada, al inicio de la celda) -> campo
    "MUNICIPIO DE COBERTURA": "ambito", "NOMBRE DEL DEPARTAMENTO MUNICIPIO": "ambito",
    "CONTRATISTA": "contratista", "NOMBRE DE LA IPS": "contratista",
    "CONSECUTIVO DEL CONTRATO": "contrato", "NUMERO DEL CONTRATO": "contrato",
    "POBLACION PROYECTADA": "poblacion", "TOTAL POBLACION ASIGNADA": "poblacion", "REGIMEN": "regimen",
}


def _n(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto if texto is not None else "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", " ", t).strip()


_COD_FUERTE = {"CUPS", "CODIGOCUP", "CODIGOCUPS", "CODIGO CUP", "CODIGO CUPS", "COD CUPS", "CODCUPS"}


def _es_codigo_col(n: str) -> bool:
    return n in _COD_FUERTE or n in {"CODIGO", "COD"} or n.startswith("CODIGO CUPS")


def _celda(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def codigos_de_celda(texto) -> list[str]:
    """'890201\\n890301' / '997310 -997001' / '990201 al 990206' -> lista de códigos."""
    t = _celda(texto).upper()
    if not t:
        return []
    salida = []
    for a, b in re.findall(r"(\d{6})\s*AL\s*(\d{6})", t):  # rangos «A al B»
        ini, fin = int(a), int(b)
        if 0 < fin - ini <= 60:
            salida += [str(x).zfill(6) for x in range(ini, fin + 1)]
    t = re.sub(r"(\d{6})\s*AL\s*(\d{6})", " ", t)
    for tok in re.split(r"[\s,;/]+", t):
        tok = tok.strip("-.() ")
        if tok.isdigit() and 3 <= len(tok) < 6:  # numérico que Excel dejó sin ceros a la izquierda
            tok = tok.zfill(6)
        if re.fullmatch(r"[0-9A-Z]{5,12}(-\d{1,3})?", tok) and len(re.sub(r"\D", "", tok)) >= 2:
            salida.append(normalizar_codigo(tok))
    return list(dict.fromkeys(salida))


def _metadatos(filas: list[list]) -> dict:
    meta = {}
    for fila in filas:
        for j, v in enumerate(fila):
            n = _n(v)
            campo = next((c for k, c in _META.items() if n.startswith(k)), None)
            if campo and campo not in meta:
                valor = next((_celda(x) for x in fila[j + 1:] if _celda(x)), "")
                if valor:
                    meta[campo] = valor
    if "contrato" in meta:
        meta["contrato"] = re.sub(r"\s+", "", meta["contrato"])
    return meta


def _leer_hoja(nombre: str, df: pd.DataFrame) -> pd.DataFrame | None:
    filas = df.values.tolist()
    for i, fila in enumerate(filas[:40]):
        ns = [_n(v) for v in fila]
        c_cod = next((j for j, n in enumerate(ns) if _es_codigo_col(n)), None)
        if c_cod is None:
            continue

        def col(*claves, excluir=(), ns=ns, c_cod=c_cod):  # la primera clave que aparezca gana
            for k in claves:
                j = next((j for j, n in enumerate(ns) if j != c_cod and (n.startswith(k) or k in n)
                          and not any(e in n for e in excluir)), None)
                if j is not None:
                    return j
            return None

        c_des = col("DESCRIP")
        c_grupo = col("GRUPO") if c_des is None else None  # matriz de PyM: la actividad
        if c_des is None and c_grupo is None and ns[c_cod] not in _COD_FUERTE:
            continue
        c_hom, c_est = col("HOMOLOG"), col("ESTADO")
        c_agr = col("AGRUPADOR", "MODULO")
        c_meta = col("ACTIVIDADES MES", "ACTIVIDADES AL MES")
        c_mun = col("MUNICIPIO")
        c_reg = col("REGIMEN")
        c_curso = col("POBACION", "POBLACION", excluir=("TOTAL",)) if c_grupo is not None else None
        meta = _metadatos(filas[:i])
        registros = []
        for fila in filas[i + 1:]:
            codigos = codigos_de_celda(fila[c_cod])
            if not codigos:
                continue
            desc = _celda(fila[c_des]) if c_des is not None else _celda(fila[c_grupo]) if c_grupo is not None else ""
            if c_curso is not None and _celda(fila[c_curso]):
                desc = f"{desc} · {_celda(fila[c_curso])}"
            meta_mes = pd.to_numeric(_celda(fila[c_meta]), errors="coerce") if c_meta is not None and len(codigos) == 1 else None
            for cod in codigos:
                registros.append({
                    "codigo": cod,
                    "homologo": normalizar_codigo(_celda(fila[c_hom])) if c_hom is not None and len(codigos) == 1 else cod,
                    "estado": (_celda(fila[c_est]).upper() if c_est is not None else "") or "ACTIVO",
                    "descripcion": re.sub(r"\s+", " ", desc),
                    "agrupador": (_celda(fila[c_agr]) if c_agr is not None else "") or
                                 ("PROMOCION Y MANTENIMIENTO (matriz PYM)" if c_grupo is not None else ""),
                    "meta_mes": meta_mes,
                    "ambito": _n(_celda(fila[c_mun]) if c_mun is not None and _celda(fila[c_mun]) else meta.get("ambito", "")),
                    "contrato": meta.get("contrato", ""), "contratista": meta.get("contratista", ""),
                    "poblacion": meta.get("poblacion", ""),
                    "regimen": (_celda(fila[c_reg]) if c_reg is not None else "") or meta.get("regimen", ""),
                    "hoja": nombre,
                })
        if registros:
            out = pd.DataFrame(registros, columns=COLUMNAS)
            out.attrs["matriz_pym"] = c_grupo is not None
            return out
    return None


def leer_nota(ruta: str | Path) -> pd.DataFrame:
    """Todas las tablas de códigos del libro (o CSV) en un solo DataFrame con ``COLUMNAS``."""
    ruta = Path(ruta)
    if ruta.suffix.lower() == ".csv":
        hojas = {ruta.stem: pd.read_csv(ruta, dtype=object, sep=None, engine="python", header=None)}
    else:
        hojas = pd.read_excel(ruta, sheet_name=None, dtype=object, header=None)
    leidas = [(n, t) for n, t in ((n, _leer_hoja(n, df)) for n, df in hojas.items()) if t is not None]
    principales = [x for x in leidas if not re.fullmatch(r"(?i)hoja\s*\d+|sheet\s*\d+", x[0].strip())]
    leidas = principales or leidas
    if not leidas:
        raise ValueError("No se encontró una tabla con columna de código CUPS (CodigoCUP, CUPS o CodigoCUPS) y "
                         "descripción en ninguna hoja.")
    leidas.sort(key=lambda x: x[1].attrs.get("matriz_pym", False))  # listas de servicios antes que la matriz de PyM
    return pd.concat([t for _n_, t in leidas], ignore_index=True)


__all__ = ["codigos_de_celda", "leer_nota"]

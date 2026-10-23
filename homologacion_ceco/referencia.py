"""Lectura y control de calidad del archivo de referencia (fuente de verdad de la homologación).

El archivo HOMOLOGACION_CECO_EF_vs_RESOLUCION trae fórmulas (nombre del CECO de resolución, ¿en lista
utilizados?, llave CECO|SEDE). No se confía en los valores guardados de esas fórmulas: se recalculan aquí
con la misma lógica, de modo que si el usuario cambia la columna D (código homologado) el resultado se
actualiza aunque el libro no se haya recalculado en Excel.
"""
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import pandas as pd

from . import config as C
from .normalizar import clave, codigo, codigo_ceco, texto


class ErrorReferencia(Exception):
    """El archivo de referencia no tiene la estructura esperada."""


# Encabezados esperados del maestro (hoja "C COSTOS ESTADO FINANCIERO"), por clave normalizada.
COLUMNAS_MAESTRO = {
    "cod_ef": ("CODIGO",),
    "nombre_ef": ("NOMBRE",),
    "cod_res": ("CENTRO DE COSTOS RESOLUCION HOMOLOGADO", "CENTRO DE COSTOS RESOLUCION", "CODIGO RESOLUCION"),
    "tipo": ("TIPO CECO", "TIPO"),
    "sede": ("SEDE IPS", "SEDE"),
    "estado": ("ESTADO PARA COSTEO", "ESTADO"),
    "confianza": ("CONFIANZA",),
    "criterio": ("CRITERIO DE HOMOLOGACION", "CRITERIO"),
    "metodo": ("METODO",),
}
OBLIGATORIAS = ("cod_ef", "nombre_ef", "cod_res", "tipo", "sede", "estado")


def _tipo(valor):
    """Normaliza el tipo de CECO: 'PRODUCTIVO', 'Final', 'apoyo asistencial', 'LOGISTICO'…"""
    palabras = set(clave(valor).split())
    if palabras & {"PRODUCTIVO", "FINAL"}:
        return C.PRODUCTIVO
    if palabras & {"ASISTENCIAL", "INTERMEDIO"}:
        return C.APOYO_ASISTENCIAL
    if "LOGISTICO" in palabras:
        return C.APOYO_LOGISTICO
    if "ADMINISTRATIVO" in palabras:
        return C.ADMINISTRATIVO
    return texto(valor)


def _estado(valor):
    k = clave(valor)
    for e in C.ESTADOS:
        if k == clave(e):
            return e
    return texto(valor).upper()


def _hoja(libro, *palabras, excluir=()):
    for ws in libro.worksheets:
        k = clave(ws.title)
        if all(p in k for p in palabras) and not any(x in k for x in excluir):
            return ws
    raise ErrorReferencia(f"No se encontró la hoja con {' / '.join(palabras)} en el archivo de referencia.")


def _encabezado(ws, requeridos, max_filas=15):
    """Fila (1-based) y {clave: índice} del encabezado: la primera fila que contiene todas las claves requeridas."""
    for n, fila in enumerate(ws.iter_rows(min_row=1, max_row=max_filas, values_only=True), start=1):
        claves = [clave(v) for v in fila]
        if all(any(c == r for c in claves) for r in requeridos):
            return n, claves
    raise ErrorReferencia(f"La hoja '{ws.title}' no tiene encabezados {', '.join(requeridos)}.")


def _ubicar(claves, opciones):
    for op in opciones:
        if op in claves:
            return claves.index(op)
    for op in opciones:
        for i, c in enumerate(claves):
            if c.startswith(op):
                return i
    return None


def _catalogo(ws):
    fila, claves = _encabezado(ws, ("CODIGO CECO", "NOMBRE CENTROS DE COSTOS"))
    i_cod, i_nom = claves.index("CODIGO CECO"), claves.index("NOMBRE CENTROS DE COSTOS")
    datos = []
    for r in ws.iter_rows(min_row=fila + 1, values_only=True):
        cod = codigo(r[i_cod] if i_cod < len(r) else None)
        if cod:
            datos.append({"cod_res": cod, "nombre_res": texto(r[i_nom])})
    return pd.DataFrame(datos, columns=["cod_res", "nombre_res"]).drop_duplicates("cod_res")


@dataclass
class Referencia:
    ruta: Path
    huella: str
    maestro: pd.DataFrame
    resolucion: pd.DataFrame
    utilizados: pd.DataFrame
    hallazgos: pd.DataFrame = field(default=None)
    _indice: dict = field(default_factory=dict, repr=False)
    _utilizados: set = field(default_factory=set, repr=False)

    def buscar(self, cod_ef):
        """Fila del maestro (dict) para el código de CECO del estado financiero, o None."""
        return self._indice.get(cod_ef)

    def nombre_resolucion(self, cod_res):
        fila = self.resolucion.loc[self.resolucion["cod_res"] == cod_res, "nombre_res"]
        return fila.iloc[0] if len(fila) else None

    def en_utilizados(self, cod_res):
        return cod_res in self._utilizados

    def control_calidad(self):
        """Indicadores equivalentes a la hoja RESUMEN HOMOLOGACIÓN, recalculados."""
        m = self.maestro
        total = len(m)
        filas = [("Estado para costeo", e, int((m["estado"] == e).sum()), _pct(int((m["estado"] == e).sum()), total))
                 for e in C.ESTADOS]
        otros = int((~m["estado"].isin(C.ESTADOS)).sum())
        if otros:
            filas.append(("Estado para costeo", "ESTADO NO VÁLIDO (se trata como VERIFICAR)", otros, _pct(otros, total)))
        filas.append(("Estado para costeo", "TOTAL CECO E.F.", total, 1.0 if total else 0))
        activos = m["estado"] == C.ACTIVO
        cnt = self.hallazgos["tipo"].value_counts() if len(self.hallazgos) else {}
        filas += [
            ("Control de calidad", "CECO E.F. en catálogo origen", total, None),
            ("Control de calidad", "CECO con código homologado", int((m["cod_res"] != "").sum()), None),
            ("Control de calidad", "Códigos homologados no válidos en resolución", int(cnt.get("CODIGO NO VALIDO", 0)), None),
            ("Control de calidad", 'ACTIVOS asignados a CECO fuera de "Utilizados"',
             int((activos & ~m["en_utilizados"]).sum()), None),
            ("Control de calidad", 'CECO de resolución "Utilizados" sin ningún CECO E.F. asignado',
             int(cnt.get("UTILIZADO SIN ASIGNAR", 0)), None),
            ("Control de calidad", "Códigos E.F. duplicados en el maestro", int(cnt.get("CODIGO DUPLICADO", 0)), None),
        ]
        filas += [("Por tipo de CECO (solo ACTIVOS)", t, int((activos & (m["tipo"] == t)).sum()), None) for t in C.TIPOS]
        return pd.DataFrame(filas, columns=["seccion", "indicador", "valor", "porcentaje"])


def _pct(n, total):
    return round(n / total, 4) if total else 0


def cargar_referencia(ruta=None):
    """Lee el archivo de referencia y devuelve una `Referencia` validada."""
    ruta = Path(ruta or C.REFERENCIA_POR_DEFECTO)
    if not ruta.is_file():
        raise ErrorReferencia(f"No existe el archivo de referencia: {ruta}")
    contenido = ruta.read_bytes()
    try:
        import io
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # extensión de validación de datos de Excel no soportada por openpyxl
            libro = openpyxl.load_workbook(io.BytesIO(contenido), data_only=False, read_only=False)
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de lectura es un error de referencia
        raise ErrorReferencia(f"No se pudo abrir el archivo de referencia ({exc.__class__.__name__}: {exc}).") from exc

    resolucion = _catalogo(_hoja(libro, "RESOLUCION", excluir=("HOMOLOGACION",)))
    utilizados = _catalogo(_hoja(libro, "UTILIZADOS"))
    ws = _hoja(libro, "ESTADO FINANCIERO")
    fila_enc, claves = _encabezado(ws, ("CODIGO", "NOMBRE"))
    idx = {campo: _ubicar(claves, ops) for campo, ops in COLUMNAS_MAESTRO.items()}
    faltan = [c for c in OBLIGATORIAS if idx[c] is None]
    if faltan:
        raise ErrorReferencia(f"La hoja '{ws.title}' no tiene las columnas: {', '.join(faltan)}.")

    nombres_res = dict(zip(resolucion["cod_res"], resolucion["nombre_res"]))
    set_util = set(utilizados["cod_res"])
    registros, hallazgos, vistos = [], [], {}
    for n, r in enumerate(ws.iter_rows(min_row=fila_enc + 1, values_only=True), start=fila_enc + 1):
        def v(campo):
            i = idx[campo]
            return r[i] if i is not None and i < len(r) else None

        cod_ef = codigo_ceco(v("cod_ef")) if v("cod_ef") is not None else ""
        if not cod_ef:
            continue
        cod_res = codigo(v("cod_res"))
        sede = texto(v("sede"))
        reg = {
            "cod_ef": cod_ef,
            "nombre_ef": texto(v("nombre_ef")),
            "cod_res": cod_res,
            "nombre_res": nombres_res.get(cod_res, "CÓDIGO NO VÁLIDO"),
            "en_utilizados": cod_res in set_util,
            "llave": C.llave(cod_res, sede),
            "tipo": _tipo(v("tipo")),
            "sede": sede,
            "estado": _estado(v("estado")),
            "confianza": texto(v("confianza")),
            "criterio": texto(v("criterio")),
            "metodo": texto(v("metodo")),
            "fila_referencia": n,
        }
        if cod_ef in vistos:
            hallazgos.append(("CODIGO DUPLICADO", cod_ef, f"Repetido en las filas {vistos[cod_ef]} y {n}; se usa la fila {vistos[cod_ef]}"))
            continue
        vistos[cod_ef] = n
        if reg["nombre_res"] == "CÓDIGO NO VÁLIDO":
            hallazgos.append(("CODIGO NO VALIDO", cod_ef, f"Fila {n}: código homologado '{cod_res}' no existe en la resolución"))
        if reg["estado"] not in C.ESTADOS:
            hallazgos.append(("ESTADO NO VALIDO", cod_ef, f"Fila {n}: estado '{reg['estado']}' no es ACTIVO/VERIFICAR/NO APLICA/EXCLUIR"))
        if reg["tipo"] not in C.TIPOS:
            hallazgos.append(("TIPO NO VALIDO", cod_ef, f"Fila {n}: tipo '{reg['tipo']}' no reconocido"))
        if not sede:
            hallazgos.append(("SIN SEDE", cod_ef, f"Fila {n}: no tiene sede/IPS"))
        if reg["estado"] == C.ACTIVO and not reg["en_utilizados"] and reg["nombre_res"] != "CÓDIGO NO VÁLIDO":
            hallazgos.append(("ACTIVO FUERA DE UTILIZADOS", cod_ef,
                              f"Fila {n}: {cod_res} no está en la lista 'Utilizados'; agréguelo para que la herramienta de costos lo reconozca"))
        registros.append(reg)

    maestro = pd.DataFrame(registros)
    if maestro.empty:
        raise ErrorReferencia(f"La hoja '{ws.title}' no tiene CECO.")
    asignados = set(maestro.loc[maestro["estado"] == C.ACTIVO, "cod_res"])
    for cod in utilizados["cod_res"]:
        if cod not in asignados:
            hallazgos.append(("UTILIZADO SIN ASIGNAR", cod, f"CECO de resolución 'Utilizado' {cod} sin ningún CECO E.F. ACTIVO asignado"))
    for cod in set_util - set(resolucion["cod_res"]):
        hallazgos.append(("UTILIZADO NO EXISTE EN RESOLUCION", cod, f"{cod} está en 'Utilizados' pero no en la hoja de resolución"))

    ref = Referencia(
        ruta=ruta,
        huella=hashlib.sha256(contenido).hexdigest(),
        maestro=maestro,
        resolucion=resolucion,
        utilizados=utilizados,
        hallazgos=pd.DataFrame(hallazgos, columns=["tipo", "codigo", "detalle"]),
    )
    ref._indice = {r["cod_ef"]: r for r in registros}
    ref._utilizados = set_util
    return ref

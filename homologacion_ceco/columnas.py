"""Detección de la fila de encabezado y de las columnas equivalentes al esquema común.

Cada encabezado se normaliza (MAYÚSCULAS, sin tildes, signos → espacio) y se compara con los sinónimos de
datos/columnas.json: coincidencia exacta (1,00), sin espacios (0,98) o aproximada por similitud de texto
(≥ 0,88). Se elige la fila, entre las primeras 30, que reconoce más campos. Un archivo de mapeo puede fijar
columnas por nombre o por letra (#C) y agregar sinónimos.

Formatos admitidos:
* **largo**: una fila por CECO y cuenta (CECO, CUENTA, VALOR o DÉBITO/CRÉDITO…);
* **matriz**: cuentas en filas y un CECO por columna (encabezados con el código del CECO);
* **por CECO**: sin columna de CECO; el código se toma del nombre del archivo o de la hoja.
"""
import fnmatch
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from . import config as C
from .normalizar import clave, codigo_ceco, es_vacio, texto

MAX_FILAS_ENCABEZADO = 30
UMBRAL_APROXIMADO = 0.88
SUFIJO_AUTO = "AUTO"  # columnas calculadas de la plantilla (p. ej. "ESTADO (AUTO)"): nunca se toman como datos


class ErrorMapeo(Exception):
    pass


@dataclass
class Mapeo:
    fila_encabezado: int                 # índice 0-based dentro de la tabla
    columnas: dict                       # campo → índice de columna
    encabezados: list
    metodo: dict = field(default_factory=dict)   # campo → (método, puntaje, encabezado original)
    formato: str = "LARGO"               # LARGO | MATRIZ | POR_CECO
    columnas_ceco: dict = field(default_factory=dict)  # MATRIZ: índice → código CECO
    ceco_fijo: str = ""                  # POR_CECO
    ceco_fijo_fuente: str = ""

    def faltantes(self):
        falta = [c for c, d in C.campos_columnas().items() if d.get("requerido") and c not in self.columnas]
        if self.formato == "MATRIZ" or self.ceco_fijo:
            falta = [c for c in falta if c != "cod_ceco"]
        if self.formato != "MATRIZ" and not ({"valor", "debito", "credito"} & set(self.columnas)):
            falta.append("valor (o débito/crédito)")
        return falta


def _letra_a_indice(ref):
    m = re.fullmatch(r"#([A-Za-z]{1,3})", ref.strip())
    if not m:
        return None
    n = 0
    for ch in m.group(1).upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


class Detector:
    def __init__(self, mapeo_usuario=None):
        """mapeo_usuario: dict con 'sinonimos' {campo: [encabezados]} y 'archivos' {patrón: {campo: columna}}."""
        self.mapeo_usuario = mapeo_usuario or {}
        self.campos = {}
        extra = self.mapeo_usuario.get("sinonimos", {})
        for campo, d in C.campos_columnas().items():
            sinonimos = [clave(s) for s in extra.get(campo, [])] + [clave(s) for s in d["sinonimos"]]
            self.campos[campo] = {"sinonimos": sinonimos, "excluir": {clave(x) for x in d.get("excluir", [])}}
        desconocidos = set(extra) - set(self.campos)
        if desconocidos:
            raise ErrorMapeo(f"Campos desconocidos en el mapeo: {', '.join(sorted(desconocidos))}")

    def forzados(self, origen, hoja):
        nombre = origen.replace("\\", "/").rsplit("/", 1)[-1]
        for patron, cols in self.mapeo_usuario.get("archivos", {}).items():
            if fnmatch.fnmatch(nombre.upper(), patron.upper()) or fnmatch.fnmatch(f"{nombre}:{hoja}".upper(), patron.upper()):
                return cols
        return {}

    # -------------------------------------------------------------- puntaje de un encabezado
    def puntaje(self, campo, encabezado_k):
        d = self.campos[campo]
        if not encabezado_k or encabezado_k in d["excluir"] or encabezado_k.endswith(SUFIJO_AUTO):
            return 0, None, 0
        compacto = encabezado_k.replace(" ", "")
        mejor = (0, None, 0)
        for i, s in enumerate(d["sinonimos"]):
            if encabezado_k == s:
                p = (1.0, "SINÓNIMO", i)
            elif compacto == s.replace(" ", ""):
                p = (0.98, "SINÓNIMO SIN ESPACIOS", i)
            else:
                r = SequenceMatcher(None, encabezado_k, s).ratio()
                p = (round(r, 3), "APROXIMADO", i) if r >= UMBRAL_APROXIMADO and len(s) > 3 else (0, None, 0)
            if p[0] > mejor[0] or (p[0] == mejor[0] and p[0] and p[2] < mejor[2]):
                mejor = p
        return mejor

    def _asignar(self, encabezados):
        claves = [clave(e) for e in encabezados]
        candidatos = []
        for campo in self.campos:
            for j, k in enumerate(claves):
                p, metodo, prioridad = self.puntaje(campo, k)
                if p:
                    candidatos.append((-p, prioridad, j, campo, metodo))
        candidatos.sort()
        columnas, metodo, usadas = {}, {}, set()
        for neg_p, _prio, j, campo, met in candidatos:
            if campo in columnas or j in usadas:
                continue
            columnas[campo] = j
            usadas.add(j)
            metodo[campo] = (met, -neg_p, texto(encabezados[j]))
        return columnas, metodo

    def detectar(self, tabla, ref=None):
        """Mapeo para una `Tabla`. Lanza ErrorMapeo si no hay una estructura de costos reconocible."""
        df = tabla.datos
        forz = self.forzados(tabla.origen, tabla.hoja)
        limite = min(MAX_FILAS_ENCABEZADO, len(df))
        mejor = None
        for i in range(limite):
            encabezados = [texto(v) for v in df.iloc[i].tolist()]
            if sum(1 for e in encabezados if e) < 2:
                continue
            columnas, metodo = self._asignar(encabezados)
            cecos = self._columnas_ceco(encabezados, ref, set(columnas.values()))
            claves = {clave(e) for e in encabezados}
            forzados_aqui = {c for c, col in forz.items()
                             if (isinstance(col, str) and _letra_a_indice(col) is not None) or clave(col) in claves}
            puntaje = len(set(columnas) | forzados_aqui) + (2 if len(cecos) >= 2 else 0)
            if mejor is None or puntaje > mejor[0]:
                mejor = (puntaje, i, encabezados, columnas, metodo, cecos)
        if mejor is None or mejor[0] < 2:
            raise ErrorMapeo("no tiene encabezados reconocibles (CECO, cuenta, valor)")
        _, i, encabezados, columnas, metodo, cecos = mejor
        mapeo = Mapeo(i, columnas, encabezados, metodo)
        self._aplicar_forzados(mapeo, forz)
        if "cod_ceco" not in mapeo.columnas and len(cecos) >= 2:
            mapeo.formato = "MATRIZ"
            mapeo.columnas_ceco = {j: c for j, c in cecos.items() if j not in mapeo.columnas.values()}
            for campo in ("valor", "debito", "credito"):
                mapeo.columnas.pop(campo, None)
        elif "cod_ceco" not in mapeo.columnas:
            ceco, fuente = self._ceco_de_nombre(tabla, ref)
            if ceco:
                mapeo.formato, mapeo.ceco_fijo, mapeo.ceco_fijo_fuente = "POR_CECO", ceco, fuente
        return mapeo

    def _aplicar_forzados(self, mapeo, forz):
        claves = [clave(e) for e in mapeo.encabezados]
        for campo, ref_col in forz.items():
            if campo not in self.campos:
                raise ErrorMapeo(f"Campo desconocido en el mapeo: {campo}")
            j = _letra_a_indice(ref_col) if isinstance(ref_col, str) else None
            if j is None:
                k = clave(ref_col)
                if k not in claves:
                    raise ErrorMapeo(f"El mapeo indica la columna '{ref_col}' para {campo}, pero no existe en el encabezado")
                j = claves.index(k)
            for otro, col in list(mapeo.columnas.items()):
                if col == j:
                    del mapeo.columnas[otro]
                    mapeo.metodo.pop(otro, None)
            mapeo.columnas[campo] = j
            mapeo.metodo[campo] = ("MAPEO DEL USUARIO", 1.0, texto(mapeo.encabezados[j]) if j < len(mapeo.encabezados) else ref_col)

    @staticmethod
    def _columnas_ceco(encabezados, ref, usadas):
        """Encabezados que son códigos de CECO (formato matriz): 7 dígitos o códigos existentes en el maestro."""
        cecos = {}
        for j, e in enumerate(encabezados):
            if j in usadas or es_vacio(e):
                continue
            try:
                cod = codigo_ceco(e)
            except ValueError:
                continue
            if re.fullmatch(r"\d{7}", cod) or (ref is not None and ref.buscar(cod) is not None):
                cecos[j] = cod
        return cecos

    @staticmethod
    def _ceco_de_nombre(tabla, ref):
        nombre = tabla.origen.replace("\\", "/").rsplit("/", 1)[-1]
        for fuente, txt in (("nombre de la hoja", tabla.hoja), ("nombre del archivo", nombre)):
            for cod in re.findall(r"(?<!\d)(\d{7})(?!\d)", txt or ""):
                if ref is None or ref.buscar(cod) is not None:
                    return cod, fuente
        return "", ""

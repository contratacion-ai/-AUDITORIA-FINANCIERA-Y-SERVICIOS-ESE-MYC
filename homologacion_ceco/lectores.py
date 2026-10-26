"""Lectura de archivos de entrada: Excel (.xlsx, .xlsm, .xls), CSV/TXT y ZIP (incluso ZIP dentro de ZIP).

Cada hoja se entrega como `Tabla` cruda (sin encabezado, todo como objeto) para que la detección de columnas
decida dónde empieza la tabla. Los errores de un archivo (corrupto, cifrado, no soportado, demasiado grande)
quedan registrados en `EventoArchivo` y el proceso continúa con los demás.

Los ZIP se leen en memoria: no se extrae nada a disco (sin riesgo de rutas «../» maliciosas) y se limita
el tamaño descomprimido para evitar bombas de compresión.
"""
import csv
import hashlib
import io
import warnings
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import pandas as pd

EXT_EXCEL = {".xlsx", ".xlsm", ".xls"}
EXT_TEXTO = {".csv", ".txt"}
EXT_ZIP = {".zip"}
SOPORTADAS = EXT_EXCEL | EXT_TEXTO | EXT_ZIP

LIMITES = {
    "max_archivo_mb": 100,        # tamaño máximo de un archivo (descomprimido)
    "max_total_zip_mb": 500,      # tamaño descomprimido total de un ZIP
    "max_ratio_compresion": 200,  # descomprimido / comprimido por entrada
    "max_profundidad_zip": 3,
    "max_entradas_zip": 2000,
}

PROCESADO, ERROR, OMITIDO, DUPLICADO = "PROCESADO", "ERROR", "OMITIDO", "DUPLICADO"


@dataclass
class Tabla:
    origen: str          # ruta mostrable, p. ej. lote.zip!/enero/EF.xlsx
    hoja: str
    datos: pd.DataFrame  # sin encabezado; índice = fila de Excel - 1
    huella: str          # SHA-256 del archivo que contiene la hoja
    meta: dict = field(default_factory=dict)  # datos del formato de origen (ver formatos.py)


@dataclass
class EventoArchivo:
    origen: str
    hoja: str
    estado: str
    motivo: str
    huella: str = ""
    tamano: int = 0
    filas: int = 0


def huella(contenido):
    return hashlib.sha256(contenido).hexdigest()


def _ignorar(nombre):
    p = PurePosixPath(nombre)
    return any(parte.startswith(".") or parte == "__MACOSX" for parte in p.parts) or p.name.startswith("~$") \
        or p.name.lower() in ("thumbs.db", "desktop.ini")


class Lector:
    def __init__(self, limites=None):
        self.limites = {**LIMITES, **(limites or {})}
        self.tablas = []
        self.eventos = []
        self._huellas = {}

    # ---------------------------------------------------------------- entradas
    def leer(self, rutas):
        for ruta in rutas:
            ruta = Path(ruta)
            if ruta.is_dir():
                for archivo in sorted(p for p in ruta.rglob("*") if p.is_file()):
                    if not _ignorar(str(archivo.relative_to(ruta))):
                        self._leer_ruta(archivo)
            elif ruta.is_file():
                self._leer_ruta(ruta)
            else:
                self.eventos.append(EventoArchivo(str(ruta), "", ERROR, "No existe el archivo o la carpeta"))
        return self.tablas, self.eventos

    def leer_bytes(self, nombre, contenido):
        self._despachar(nombre, contenido, profundidad=0)
        return self.tablas, self.eventos

    def _leer_ruta(self, ruta):
        try:
            tam = ruta.stat().st_size
            if tam > self.limites["max_archivo_mb"] * 1024 * 1024 and ruta.suffix.lower() not in EXT_ZIP:
                self.eventos.append(EventoArchivo(str(ruta), "", ERROR, f"Supera {self.limites['max_archivo_mb']} MB", tamano=tam))
                return
            contenido = ruta.read_bytes()
        except OSError as exc:
            self.eventos.append(EventoArchivo(str(ruta), "", ERROR, f"No se pudo leer: {exc}"))
            return
        self._despachar(str(ruta), contenido, profundidad=0)

    # ---------------------------------------------------------------- despacho por tipo
    def _despachar(self, origen, contenido, profundidad):
        ext = PurePosixPath(origen.replace("\\", "/")).suffix.lower()
        h = huella(contenido)
        if ext not in SOPORTADAS:
            self.eventos.append(EventoArchivo(origen, "", OMITIDO,
                                              f"Formato no soportado ({ext or 'sin extensión'}); use .xlsx, .xls, .csv, .txt o .zip",
                                              h, len(contenido)))
            return
        if h in self._huellas:
            self.eventos.append(EventoArchivo(origen, "", DUPLICADO, f"Contenido idéntico a {self._huellas[h]}; no se procesa dos veces",
                                              h, len(contenido)))
            return
        self._huellas[h] = origen
        if ext in EXT_ZIP:
            self._zip(origen, contenido, profundidad)
        elif ext in EXT_EXCEL:
            self._excel(origen, contenido, ext, h)
        else:
            self._texto(origen, contenido, h)

    def _zip(self, origen, contenido, profundidad):
        lim = self.limites
        if profundidad >= lim["max_profundidad_zip"]:
            self.eventos.append(EventoArchivo(origen, "", ERROR, f"ZIP anidado a más de {lim['max_profundidad_zip']} niveles", huella(contenido)))
            return
        try:
            zf = zipfile.ZipFile(io.BytesIO(contenido))
            entradas = [i for i in zf.infolist() if not i.is_dir() and not _ignorar(i.filename)]
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError) as exc:
            self.eventos.append(EventoArchivo(origen, "", ERROR, f"ZIP dañado o ilegible: {exc}", huella(contenido), len(contenido)))
            return
        if len(entradas) > lim["max_entradas_zip"]:
            self.eventos.append(EventoArchivo(origen, "", ERROR, f"El ZIP tiene más de {lim['max_entradas_zip']} archivos", huella(contenido)))
            return
        total = sum(i.file_size for i in entradas)
        if total > lim["max_total_zip_mb"] * 1024 * 1024:
            self.eventos.append(EventoArchivo(origen, "", ERROR,
                                              f"El contenido descomprimido ({total / 1048576:.0f} MB) supera {lim['max_total_zip_mb']} MB",
                                              huella(contenido), len(contenido)))
            return
        self.eventos.append(EventoArchivo(origen, "", PROCESADO, f"ZIP con {len(entradas)} archivo(s)", huella(contenido), len(contenido)))
        for info in sorted(entradas, key=lambda i: i.filename):
            interno = f"{origen}!/{info.filename}"
            if info.flag_bits & 0x1:
                self.eventos.append(EventoArchivo(interno, "", ERROR, "Archivo cifrado con contraseña dentro del ZIP"))
                continue
            if info.file_size > lim["max_archivo_mb"] * 1024 * 1024:
                self.eventos.append(EventoArchivo(interno, "", ERROR, f"Supera {lim['max_archivo_mb']} MB descomprimido", tamano=info.file_size))
                continue
            if info.compress_size and info.file_size / info.compress_size > lim["max_ratio_compresion"] and info.file_size > 10 * 1048576:
                self.eventos.append(EventoArchivo(interno, "", ERROR, "Relación de compresión sospechosa (posible bomba ZIP)"))
                continue
            try:
                with zf.open(info) as f:
                    datos = f.read(info.file_size + 1)
                if len(datos) > info.file_size:
                    raise ValueError("el tamaño real supera el declarado")
            except (zipfile.BadZipFile, RuntimeError, OSError, ValueError, EOFError) as exc:
                self.eventos.append(EventoArchivo(interno, "", ERROR, f"No se pudo extraer: {exc}"))
                continue
            self._despachar(interno, datos, profundidad + 1)

    def _excel(self, origen, contenido, ext, h):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                engine = "xlrd" if ext == ".xls" else "openpyxl"
                hojas = pd.read_excel(io.BytesIO(contenido), sheet_name=None, header=None, dtype=object, engine=engine)
        except Exception as exc:  # noqa: BLE001 - cualquier fallo de lectura se registra y se sigue
            self.eventos.append(EventoArchivo(origen, "", ERROR, f"Excel dañado o ilegible ({exc.__class__.__name__}: {str(exc)[:150]})",
                                              h, len(contenido)))
            return
        if not hojas:
            self.eventos.append(EventoArchivo(origen, "", OMITIDO, "El libro no tiene hojas", h, len(contenido)))
        for nombre, df in hojas.items():
            self.tablas.append(Tabla(origen, str(nombre), df, h))

    def _texto(self, origen, contenido, h):
        texto = None
        for codificacion in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                texto = contenido.decode(codificacion)
                break
            except UnicodeDecodeError:
                continue
        if texto is None or "\x00" in texto[:4096]:
            self.eventos.append(EventoArchivo(origen, "", ERROR, "Archivo de texto ilegible o binario", h, len(contenido)))
            return
        muestra = texto[:8192]
        try:
            delim = csv.Sniffer().sniff(muestra, delimiters=";,\t|").delimiter
        except csv.Error:
            delim = max(";,\t|", key=muestra.count)
        filas = list(csv.reader(io.StringIO(texto), delimiter=delim))
        if not filas:
            self.eventos.append(EventoArchivo(origen, "", OMITIDO, "Archivo vacío", h, len(contenido)))
            return
        ancho = max(len(f) for f in filas)
        df = pd.DataFrame([f + [None] * (ancho - len(f)) for f in filas], dtype=object)
        self.tablas.append(Tabla(origen, "", df, h))


def leer_entradas(rutas, limites=None):
    """Lee archivos, carpetas y ZIP. Devuelve (tablas, eventos)."""
    return Lector(limites).leer(rutas)

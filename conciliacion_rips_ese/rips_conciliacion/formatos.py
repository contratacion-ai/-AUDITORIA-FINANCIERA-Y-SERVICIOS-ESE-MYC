"""Reconocimiento de formatos por CONTENIDO (no por extensión) y lectura de tablas.

Paquetes:  ZIP, RAR, 7Z, GZ, TAR (también .tar.gz/.tgz/.tar.bz2/.tar.xz), anidados.
Tablas:    XLSX/XLSM, XLS (Excel 97-2003), XLSB, ODS (LibreOffice/OpenOffice), CSV/TXT con cualquier
           separador (; , | tabulador), JSON (lista de registros) y XML (registros repetidos).
"""

from __future__ import annotations

import bz2
import csv
import gzip
import io
import json
import lzma
import tarfile
import zipfile
from decimal import Decimal
from pathlib import PurePosixPath
from xml.etree import ElementTree

import pandas as pd

ZIP, RAR, SIETEZ, GZIP, BZIP2, XZ, TAR = "zip", "rar", "7z", "gzip", "bzip2", "xz", "tar"
XLSX, XLSB, XLS, ODS, DOCUMENTO_OFFICE = "xlsx", "xlsb", "xls", "ods", "documento_office"
JSON, XML, TEXTO, BINARIO, VACIO = "json", "xml", "texto", "binario", "vacio"

PAQUETES = {ZIP, RAR, SIETEZ, GZIP, BZIP2, XZ, TAR}
HOJAS_CALCULO = {XLSX, XLSB, XLS, ODS}

_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class FormatoNoLegible(Exception):
    """El archivo no se pudo interpretar; el mensaje explica por qué."""


def _clasificar_zip(contenido: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(contenido)) as zf:
            nombres = set(zf.namelist())
            if "mimetype" in nombres:
                tipo = zf.read("mimetype")[:100]
                if b"opendocument.spreadsheet" in tipo:
                    return ODS
                if b"opendocument" in tipo:
                    return DOCUMENTO_OFFICE
            if "[Content_Types].xml" in nombres:
                if "xl/workbook.xml" in nombres:
                    return XLSX
                if "xl/workbook.bin" in nombres:
                    return XLSB
                return DOCUMENTO_OFFICE
    except zipfile.BadZipFile:
        return ZIP  # el lector de paquetes reportará el daño
    return ZIP


def _es_texto(muestra: bytes) -> bool:
    if not muestra:
        return True
    if muestra.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    if b"\x00" in muestra:
        return False
    controles = sum(1 for b in muestra if b < 9 or (13 < b < 32))
    return controles / len(muestra) < 0.02


def clasificar(nombre: str, contenido: bytes) -> str:
    """Tipo real del archivo según su contenido."""
    if not contenido:
        return VACIO
    if contenido.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return _clasificar_zip(contenido)
    if contenido.startswith(b"Rar!\x1a\x07"):
        return RAR
    if contenido.startswith(b"7z\xbc\xaf\x27\x1c"):
        return SIETEZ
    if contenido.startswith(b"\x1f\x8b"):
        return GZIP
    if contenido.startswith(b"BZh"):
        return BZIP2
    if contenido.startswith(b"\xfd7zXZ\x00"):
        return XZ
    if len(contenido) > 262 and contenido[257:262] == b"ustar":
        return TAR
    if contenido.startswith(_OLE):
        return XLS
    if not _es_texto(contenido[:4096]):
        return BINARIO
    from .deteccion import decodificar

    inicio = decodificar(contenido[:2048]).lstrip()
    if inicio.startswith(("{", "[")):
        return JSON
    if inicio.startswith("<"):
        return XML
    return TEXTO


def expandir_paquete(nombre: str, contenido: bytes, clase: str):
    """Itera (ruta_interna, bytes) de un paquete. Lanza FormatoNoLegible si está dañado o cifrado."""
    if clase == ZIP:
        try:
            zf = zipfile.ZipFile(io.BytesIO(contenido))
        except zipfile.BadZipFile as exc:
            raise FormatoNoLegible(f"ZIP dañado o inválido: {exc}") from exc
        with zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                try:
                    yield info.filename, zf.read(info)
                except RuntimeError as exc:
                    raise FormatoNoLegible(f"'{info.filename}' está protegido con contraseña: {exc}") from exc
                except (zipfile.BadZipFile, OSError, NotImplementedError) as exc:
                    raise FormatoNoLegible(f"No se pudo descomprimir '{info.filename}': {exc}") from exc
    elif clase == SIETEZ:
        try:
            import py7zr
        except ImportError as exc:  # pragma: no cover
            raise FormatoNoLegible("Para leer 7Z instale el paquete 'py7zr'") from exc
        import os
        import tempfile

        with tempfile.TemporaryDirectory(prefix="rips7z_") as carpeta:
            try:
                with py7zr.SevenZipFile(io.BytesIO(contenido), mode="r") as sz:
                    if sz.needs_password():
                        raise FormatoNoLegible("El archivo 7Z está protegido con contraseña")
                    sz.extractall(path=carpeta)
            except FormatoNoLegible:
                raise
            except Exception as exc:  # noqa: BLE001 - py7zr lanza varios tipos
                raise FormatoNoLegible(f"7Z dañado o no soportado: {exc}") from exc
            archivos = []
            for raiz, _dirs, nombres in os.walk(carpeta):
                for n in nombres:
                    completo = os.path.join(raiz, n)
                    archivos.append((os.path.relpath(completo, carpeta).replace(os.sep, "/"), completo))
            for ruta, completo in sorted(archivos):
                with open(completo, "rb") as fh:
                    yield ruta, fh.read()
    elif clase in (GZIP, BZIP2, XZ):
        abrir = {GZIP: gzip.decompress, BZIP2: bz2.decompress, XZ: lzma.decompress}[clase]
        try:
            datos = abrir(contenido)
        except Exception as exc:  # noqa: BLE001
            raise FormatoNoLegible(f"Archivo comprimido dañado: {exc}") from exc
        interno = PurePosixPath(nombre).name
        for sufijo in (".gz", ".gzip", ".bz2", ".xz", ".tgz"):
            if interno.lower().endswith(sufijo):
                interno = interno[: -len(sufijo)] + (".tar" if sufijo == ".tgz" else "")
                break
        yield interno or "contenido", datos
    elif clase == TAR:
        try:
            with tarfile.open(fileobj=io.BytesIO(contenido)) as tf:
                for miembro in tf.getmembers():
                    if miembro.isfile():
                        yield miembro.name, tf.extractfile(miembro).read()
        except tarfile.TarError as exc:
            raise FormatoNoLegible(f"TAR dañado: {exc}") from exc
    else:  # pragma: no cover
        raise FormatoNoLegible(f"Paquete no soportado: {clase}")


# ---------------------------------------------------------------------------
# Tablas
# ---------------------------------------------------------------------------


def _df_desde_registros(registros: list[dict]) -> pd.DataFrame:
    columnas: list[str] = []
    for r in registros:
        for k in r:
            if k not in columnas:
                columnas.append(k)
    filas = [columnas] + [[r.get(c) for c in columnas] for r in registros]
    return pd.DataFrame(filas, dtype=object)


def _aplanar(valor, prefijo="") -> dict:
    salida = {}
    if isinstance(valor, dict):
        for k, v in valor.items():
            if isinstance(v, (dict, list)):
                continue
            salida[f"{prefijo}{k}"] = v
    return salida


def _registros_json(datos) -> list[dict]:
    """Encuentra la lista de registros más grande dentro de un JSON."""
    if isinstance(datos, list) and datos and all(isinstance(x, dict) for x in datos):
        return [_aplanar(x) for x in datos]
    mejor: list[dict] = []
    if isinstance(datos, dict):
        for v in datos.values():
            candidato = _registros_json(v)
            if len(candidato) > len(mejor):
                mejor = candidato
    return mejor


def _tabla_ods(contenido: bytes) -> list[tuple[str, pd.DataFrame]]:
    ns = {
        "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
        "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
        "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    }
    t = "{%s}" % ns["table"]
    o = "{%s}" % ns["office"]
    with zipfile.ZipFile(io.BytesIO(contenido)) as zf:
        raiz = ElementTree.fromstring(zf.read("content.xml"))
    hojas = []
    for tabla in raiz.iter(t + "table"):
        filas = []
        for fila in tabla.iter(t + "table-row"):
            celdas = []
            for celda in fila:
                if celda.tag not in (t + "table-cell", t + "covered-table-cell"):
                    continue
                repetir = min(int(celda.get(t + "number-columns-repeated", "1")), 500)
                tipo = celda.get(o + "value-type")
                if tipo in ("float", "currency", "percentage"):
                    valor = Decimal(celda.get(o + "value"))
                elif tipo == "date":
                    valor = celda.get(o + "date-value")
                else:
                    valor = "\n".join("".join(p.itertext()) for p in celda.findall("{%s}p" % ns["text"])) or None
                celdas.extend([valor] * repetir)
            while celdas and celdas[-1] in (None, ""):
                celdas.pop()
            repetir_fila = min(int(fila.get(t + "number-rows-repeated", "1")), 10000)
            if celdas:
                filas.extend([celdas] * repetir_fila)
        hojas.append((tabla.get(t + "name"), pd.DataFrame(filas, dtype=object)))
    return hojas


def leer_tablas(nombre: str, contenido: bytes, clase: str | None = None) -> list[tuple[str | None, pd.DataFrame]]:
    """Devuelve [(hoja, DataFrame crudo sin encabezado)]. Lanza FormatoNoLegible."""
    clase = clase or clasificar(nombre, contenido)
    try:
        if clase in (XLSX, XLS, XLSB):
            motor = {XLSX: "openpyxl", XLS: "xlrd", XLSB: "pyxlsb"}[clase]
            hojas = pd.read_excel(io.BytesIO(contenido), sheet_name=None, header=None, dtype=object, engine=motor)
            return list(hojas.items())
        if clase == ODS:
            return _tabla_ods(contenido)
    except FormatoNoLegible:
        raise
    except Exception as exc:  # noqa: BLE001 - cualquier fallo del lector de Excel
        raise FormatoNoLegible(f"No se pudo leer la hoja de cálculo ({clase.upper()}): {exc}") from exc

    from .deteccion import decodificar

    texto = decodificar(contenido)
    if clase == JSON:
        try:
            datos = json.loads(texto, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise FormatoNoLegible(f"JSON inválido: línea {exc.lineno}, columna {exc.colno}: {exc.msg}") from exc
        registros = _registros_json(datos)
        if not registros:
            raise FormatoNoLegible("El JSON no contiene una lista de registros")
        return [(None, _df_desde_registros(registros))]
    if clase == XML:
        try:
            raiz = ElementTree.fromstring(texto.encode("utf-8"))
        except ElementTree.ParseError as exc:
            raise FormatoNoLegible(f"XML inválido: {exc}") from exc
        # El grupo de elementos hermanos más numeroso con hijos simples = registros
        mejor: list[dict] = []
        for padre in raiz.iter():
            hijos = list(padre)
            if len(hijos) < 1:
                continue
            registros = []
            for h in hijos:
                campos = {c.tag.split("}")[-1]: (c.text or "").strip() for c in h if len(c) == 0}
                campos.update({k.split("}")[-1]: v for k, v in h.attrib.items()})
                if campos:
                    registros.append(campos)
            if len(registros) > len(mejor):
                mejor = registros
        if not mejor:
            raise FormatoNoLegible("El XML no contiene registros tabulares")
        return [(None, _df_desde_registros(mejor))]
    if clase in (TEXTO, VACIO):
        lineas = texto.splitlines()
        muestra = "\n".join(lineas[:30])
        try:
            separador = csv.Sniffer().sniff(muestra, delimiters=";,|\t").delimiter
        except csv.Error:
            conteos = {s: muestra.count(s) for s in (";", ",", "|", "\t")}
            separador = max(conteos, key=conteos.get)
        try:
            df = pd.read_csv(io.StringIO(texto), sep=separador, header=None, dtype=str, keep_default_na=False,
                             skip_blank_lines=False, engine="python", quotechar='"')
        except Exception as exc:  # noqa: BLE001
            raise FormatoNoLegible(f"No se pudo leer el archivo de texto (separador '{separador}'): {exc}") from exc
        return [(None, df)]
    raise FormatoNoLegible(
        "Formato no reconocido (no es texto, hoja de cálculo, JSON, XML ni archivo comprimido)"
        if clase == BINARIO else f"Formato {clase} no contiene tablas (por ejemplo, un documento de Word o PDF)"
    )


def tabla_a_lineas_csv(df: pd.DataFrame) -> bytes:
    """Convierte una hoja sin encabezado a texto separado por comas (para RIPS 3374 dentro de Excel)."""
    def vacia(v):
        return v is None or (isinstance(v, float) and v != v) or (isinstance(v, str) and not v.strip())

    # Se conservan todas las columnas hasta la última con algún dato (los RIPS tienen campos finales opcionales)
    ultima = -1
    for j in range(df.shape[1]):
        if not all(vacia(v) for v in df.iloc[:, j]):
            ultima = j
    df = df.iloc[:, : ultima + 1]
    salida = []
    for fila in df.itertuples(index=False, name=None):
        celdas = []
        for v in fila:
            if v is None or (isinstance(v, float) and v != v):
                celdas.append("")
            elif isinstance(v, float) and v.is_integer():
                celdas.append(str(int(v)))
            elif hasattr(v, "strftime"):
                celdas.append(v.strftime("%d/%m/%Y"))
            else:
                celdas.append(str(v).strip())
        if any(celdas):
            salida.append(",".join(celdas))
    return "\n".join(salida).encode("utf-8")


# ---------------------------------------------------------------------------
# Clasificación del archivo cargado (RIPS, facturación, catálogo o equivalencias)
# ---------------------------------------------------------------------------

import re as _re

_RE_NOMBRE_RIPS = _re.compile(r"(^|[\\/])(CT|AF|US|AC|AP|AU|AH|AN|AM|AT)[_\-\s]?\d*\.[A-Za-z]+$", _re.I)
_COLUMNAS_RIPS = ("numfactura", "codconsulta", "codprocedimiento", "codtecnologiasalud", "vrservicio",
                  "numero_de_la_factura", "codigo_de_consulta", "codigo_del_procedimiento",
                  "codigo_del_medicamento", "codigo_del_servicio")


def _nombres_en_paquete(contenido: bytes, clase: str) -> list[str]:
    try:
        if clase == ZIP:
            with zipfile.ZipFile(io.BytesIO(contenido)) as zf:
                return zf.namelist()
        if clase == RAR:
            import rarfile

            with rarfile.RarFile(io.BytesIO(contenido)) as rf:
                return rf.namelist()
        if clase == SIETEZ:
            import py7zr

            with py7zr.SevenZipFile(io.BytesIO(contenido), mode="r") as sz:
                return sz.getnames()
        if clase == TAR:
            with tarfile.open(fileobj=io.BytesIO(contenido)) as tf:
                return tf.getnames()
        if clase in (GZIP, BZIP2, XZ):
            return [nombre for nombre, _ in expandir_paquete("x", contenido, clase)]
    except Exception:  # noqa: BLE001 - si no se puede listar, se decide por el nombre
        return []
    return []


def clasificar_para_carga(nombre: str, contenido: bytes) -> str:
    """Decide si un archivo cargado es RIPS, FACTURACION, CATALOGO o EQUIVALENCIAS.

    Orden: palabras del nombre (catálogo/sedes, equivalencias/homologación, rips, factura) y luego el
    contenido (estructura oficial de RIPS, nombres AF/AC/US... o columnas de RIPS). En caso de duda:
    tablas -> facturación; paquetes -> RIPS.
    """
    from .deteccion import detectar
    from .modelos import VERSION_NO_DETERMINADA

    base = PurePosixPath(nombre).stem.lower()
    if _re.search(r"cat[aá]logo|sedes", base):
        return "CATALOGO"
    if _re.search(r"equivalen|homolog", base):
        return "EQUIVALENCIAS"
    clase = clasificar(nombre, contenido)
    if clase in PAQUETES:
        internos = _nombres_en_paquete(contenido, clase)
        if "rips" in base or any(_RE_NOMBRE_RIPS.search(n) or n.lower().endswith(".json") for n in internos):
            return "RIPS"
        if "fact" in base or (internos and all("fact" in n.lower() for n in internos)):
            return "FACTURACION"
        return "RIPS"
    if "rips" in base or _RE_NOMBRE_RIPS.search(PurePosixPath(nombre).name):
        return "RIPS"
    if "fact" in base:
        return "FACTURACION"
    if clase in (TEXTO, JSON, XML) and detectar(nombre, contenido).version != VERSION_NO_DETERMINADA:
        return "RIPS"
    try:
        hojas = leer_tablas(nombre, contenido, clase)
    except FormatoNoLegible:
        return "FACTURACION"
    from .facturacion import normalizar_nombre

    for hoja, crudo in hojas:
        if hoja and _re.fullmatch(r"(CT|AF|US|AC|AP|AU|AH|AN|AM|AT)", str(hoja).strip(), _re.I):
            return "RIPS"
        for i in range(min(30, len(crudo))):
            celdas = {normalizar_nombre(v) for v in crudo.iloc[i].tolist() if v is not None}
            if celdas & set(_COLUMNAS_RIPS):
                return "RIPS"
    return "FACTURACION"

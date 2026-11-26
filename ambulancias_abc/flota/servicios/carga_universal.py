"""Carga de cualquier archivo: reconoce el tipo por su contenido (no solo por la
extensión) y lo envía a la carga que corresponde.

| Contenido | Carga |
|---|---|
| JSON de RIPS (Res. 2275), JSON de intercambio de traslados, planos AF/AT/AP, ZIP con ellos | RIPS (todos juntos, para cruzar AF con AT/AP) |
| Excel con COD. PROCEDIMIENTO y VALOR TOTAL | Facturación del HIS |
| Excel con Placa, Mes y Mantenimiento ($) | Informe mensual de mantenimiento y combustible |
| Excel del formato RYC-FO-015 (v001 o v002) | Referencia y contrarreferencia |
| Excel o CSV con placa, fecha y valor | Consumos |
| Excel con hoja de TRASLADOS (ORIGEN, DESTINO, km) | Manual tarifario (Anexo 3) |
| Excel con VR. PESOS MES o TABLA DE VALOR TH | Costos de las ambulancias (centro de costos) |

Los .xls (Excel 97-2003) se convierten a .xlsx en memoria. Un ZIP puede traer
Excel junto con RIPS: cada archivo se reconoce por separado. Lo que no se
reconoce se informa sin detener los demás.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from openpyxl import Workbook, load_workbook

from ..importacion import normalizar
from ..importacion.referencia import _encontrar_encabezado
from ..importacion.rips import es_intercambio

RIPS, FACTURACION, COSTOS_MENSUALES, REFERENCIA, CONSUMOS, TARIFAS, CECO = (
    "RIPS", "FACTURACION", "COSTOS_MENSUALES", "REFERENCIA", "CONSUMOS", "TARIFAS", "CECO")
NOMBRES = {
    RIPS: "RIPS (traslados y APH)", FACTURACION: "Facturación del HIS", COSTOS_MENSUALES: "Mantenimiento y combustible mensual",
    REFERENCIA: "Referencia y contrarreferencia (RYC-FO-015)", CONSUMOS: "Consumos por ambulancia",
    TARIFAS: "Manual tarifario (Anexo 3)", CECO: "Costos de las ambulancias (centro de costos)",
}
NO_PROCESABLES = {
    ".xml": "Factura electrónica XML: los traslados se toman del RIPS JSON que la acompaña",
    ".pdf": "PDF: no contiene datos tabulares que se puedan cargar",
    ".docx": "Documento de Word: no se puede cargar", ".doc": "Documento de Word: no se puede cargar",
    ".png": "Imagen: no se puede cargar", ".jpg": "Imagen: no se puede cargar", ".jpeg": "Imagen: no se puede cargar",
}
MAX_TOTAL = 1024 * 1024 * 1024


@dataclass
class Resultado:
    archivo: str
    tipo: str | None = None
    lote: object = None
    mensaje: str = ""
    error: bool = False
    incluidos: list[str] = field(default_factory=list)

    @property
    def tipo_nombre(self):
        return NOMBRES.get(self.tipo, "No reconocido")

    @property
    def enlace(self):
        """Pantalla donde se ve lo cargado."""
        from django.urls import reverse

        if self.lote is None:
            return None
        if self.tipo == RIPS:
            return reverse("flota:rips_detalle", args=[self.lote.pk])
        return reverse({FACTURACION: "flota:facturacion_ambito", COSTOS_MENSUALES: "flota:costos_reales", REFERENCIA: "flota:referencia",
                        CONSUMOS: "flota:consumos", TARIFAS: "flota:rentabilidad_rutas", CECO: "flota:parametros"}[self.tipo])


def xls_a_xlsx(contenido: bytes) -> bytes:
    """Convierte un .xls (BIFF) a .xlsx con los valores de las celdas."""
    import xlrd

    libro = xlrd.open_workbook(file_contents=contenido)
    wb = Workbook()
    wb.remove(wb.active)
    for hoja in libro.sheets():
        ws = wb.create_sheet(hoja.name[:31])
        for r in range(hoja.nrows):
            fila = []
            for c in range(hoja.ncols):
                celda = hoja.cell(r, c)
                v = celda.value
                if celda.ctype == xlrd.XL_CELL_DATE:
                    v = xlrd.xldate.xldate_as_datetime(v, libro.datemode)
                elif celda.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    v = None
                elif celda.ctype == xlrd.XL_CELL_ERROR:
                    v = xlrd.error_text_from_code.get(v, "#ERROR")
                fila.append(v)
            ws.append(fila)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _tipo_excel(contenido: bytes) -> tuple[str | None, str]:
    try:
        wb = load_workbook(io.BytesIO(contenido), read_only=False, data_only=True)
    except Exception as e:  # archivo dañado o protegido
        return None, f"No se pudo abrir como Excel: {e}"
    if _encontrar_encabezado_seguro(wb):
        return REFERENCIA, ""
    for ws in wb.worksheets:
        titulo = normalizar(ws.title)
        textos = set()
        for fila in ws.iter_rows(min_row=1, max_row=40, values_only=True):
            textos.update(normalizar(v) for v in fila if isinstance(v, str))
        if "COD. PROCEDIMIENTO" in textos and "VALOR TOTAL" in textos:
            return FACTURACION, ""
        if {"PLACA", "MES", "MANTENIMIENTO ($)"} <= textos:
            return COSTOS_MENSUALES, ""
        if "VR. PESOS MES" in textos or "TABLA DE VALOR TH" in titulo:
            return CECO, ""
        if "TRASLADO" in titulo and any(t.startswith("ORIGEN") for t in textos) and any(t.startswith("DESTINO") for t in textos):
            return TARIFAS, ""
        minus = {t.lower() for t in textos}
        if {"placa", "fecha", "valor"} <= minus:
            return CONSUMOS, ""
    return None, "Excel sin las columnas de ninguna carga conocida (facturación, mantenimiento, referencia, consumos, tarifas, centro de costos)"


def _encontrar_encabezado_seguro(wb) -> bool:
    for ws in wb.worksheets:
        _, col = _encontrar_encabezado(ws)
        if col and ("tipo_traslado" in col or "fecha_reporte_v1" in col or "fecha_reporte" in col):
            return True
    return False


def _tipo_csv(contenido: bytes) -> tuple[str | None, str]:
    texto = contenido.decode("utf-8-sig", errors="replace")
    primera = texto.splitlines()[0] if texto.strip() else ""
    sep = ";" if primera.count(";") >= primera.count(",") else ","
    enc = {normalizar(c).lower() for c in next(csv.reader([primera], delimiter=sep), [])}
    if {"placa", "fecha", "valor"} <= enc:
        return CONSUMOS, ""
    return None, "CSV sin las columnas placa, fecha y valor"


def detectar(nombre: str, contenido: bytes) -> tuple[str | None, str]:
    """(tipo, motivo si no se reconoce). Mira el contenido; la extensión solo orienta."""
    ext = PurePosixPath(nombre).suffix.lower()
    cabeza = contenido[:8]
    if cabeza.startswith(b"PK"):
        if ext in (".xlsx", ".xlsm") or "[Content_Types].xml" in zipfile.ZipFile(io.BytesIO(contenido)).namelist():
            return _tipo_excel(contenido)
        return "ZIP", ""
    if cabeza.startswith(b"\xd0\xcf\x11\xe0"):  # Excel 97-2003
        return "XLS", ""
    if ext in NO_PROCESABLES or cabeza.startswith(b"%PDF"):
        return None, NO_PROCESABLES.get(ext, NO_PROCESABLES[".pdf"])
    texto = contenido.lstrip()[:1]
    if texto in (b"{", b"["):
        try:
            datos = json.loads(contenido.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            return None, f"JSON inválido: {e}"
        if es_intercambio(datos):
            return RIPS, "intercambio"
        primero = datos[0] if isinstance(datos, list) and datos else datos
        if isinstance(primero, dict) and ("numFactura" in primero or "usuarios" in primero):
            return RIPS, ""
        return None, "JSON que no es RIPS (Res. 2275) ni de intercambio de traslados"
    base = PurePosixPath(nombre).name.upper()
    if ext == ".txt" or base[:2] in ("AF", "AT", "AP", "US", "AC", "AH", "AM", "AU", "AN", "CT"):
        if base[:2] in ("AF", "AT", "AP", "US", "AC", "AH", "AM", "AU", "AN", "CT"):
            return RIPS, ""
    if ext == ".csv" or b";" in contenido[:500] or b"," in contenido[:500]:
        return _tipo_csv(contenido)
    return None, "Tipo de archivo no reconocido"


def _expandir(nombre: str, contenido: bytes, salida: list, total: list):
    """Abre los ZIP: lo que es de RIPS queda en el ZIP; los Excel/CSV salen aparte."""
    tipo, motivo = detectar(nombre, contenido)
    if tipo == "XLS":
        try:
            contenido = xls_a_xlsx(contenido)
        except Exception as e:
            salida.append((nombre, None, contenido, f"No se pudo leer el .xls: {e}"))
            return
        nombre = f"{nombre}x" if nombre.lower().endswith(".xls") else f"{nombre}.xlsx"
        tipo, motivo = _tipo_excel(contenido)
    if tipo != "ZIP":
        salida.append((nombre, tipo, contenido, motivo))
        return
    zf = zipfile.ZipFile(io.BytesIO(contenido))
    total[0] += sum(i.file_size for i in zf.infolist())
    if total[0] > MAX_TOTAL:
        salida.append((nombre, None, b"", "ZIP de más de 1 GB descomprimido: divídalo"))
        return
    tiene_rips = False
    for info in zf.infolist():
        if info.is_dir() or "__MACOSX" in info.filename:
            continue
        ext = PurePosixPath(info.filename).suffix.lower()
        if ext in (".xlsx", ".xlsm", ".xls", ".csv"):
            _expandir(f"{nombre}/{info.filename}", zf.read(info), salida, total)
        elif ext in (".json", ".txt", ".zip"):
            tiene_rips = True
    if tiene_rips:
        salida.append((nombre, RIPS, contenido, ""))


def cargar_archivos(archivos: list[tuple[str, bytes]], usuario=None, mes: dt.date | None = None, sede=None,
                    vigencia: int | None = None) -> list[Resultado]:
    from . import importar
    from .referencia import importar_referencia

    piezas: list[tuple[str, str | None, bytes, str]] = []
    total = [0]
    for nombre, contenido in archivos:
        _expandir(nombre, contenido, piezas, total)

    resultados: list[Resultado] = []
    rips = [(n, c) for n, t, c, _ in piezas if t == RIPS]
    if rips:
        nombres = [n for n, _ in rips]
        try:
            lote = importar.importar_rips(rips, usuario, ", ".join(nombres)[:250], mes, sede)
            resultados.append(Resultado(nombres[0] if len(nombres) == 1 else f"{len(nombres)} archivos de RIPS", RIPS, lote,
                                        lote.resumen, incluidos=nombres))
        except Exception as e:
            resultados.append(Resultado(", ".join(nombres)[:200], RIPS, None, f"Error al cargar: {e}", True))
    for nombre, tipo, contenido, motivo in piezas:
        if tipo == RIPS:
            continue
        if tipo is None:
            resultados.append(Resultado(nombre, None, None, motivo, True))
            continue
        try:
            buf = io.BytesIO(contenido)
            if tipo == FACTURACION:
                lote = importar.importar_facturacion(buf, usuario, nombre, mes, sede)
            elif tipo == COSTOS_MENSUALES:
                lote = importar.importar_costos_mensuales(buf, usuario, nombre)
            elif tipo == REFERENCIA:
                lote = importar_referencia(buf, usuario, nombre, sede)
            elif tipo == CONSUMOS:
                if not nombre.lower().endswith((".xlsx", ".xlsm", ".csv")):
                    nombre = f"{nombre}.csv"
                lote = importar.importar_consumos(buf, usuario, nombre)
            elif tipo == TARIFAS:
                lote = importar.importar_tarifas(buf, usuario, vigencia or dt.date.today().year, nombre)
            elif tipo == CECO:
                lote = importar.importar_costos_ceco(buf, usuario, nombre)
            resultados.append(Resultado(nombre, tipo, lote, lote.resumen))
        except Exception as e:
            resultados.append(Resultado(nombre, tipo, None, f"Error al cargar: {e}", True))
    return resultados

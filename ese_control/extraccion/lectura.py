"""Lee cualquier archivo soportado y lo entrega como "rejillas" (listas de filas).

Cada hoja de Excel, archivo CSV o tabla de PDF se convierte en una rejilla de celdas
sin interpretar; los extractores trabajan sobre rejillas, de modo que tolerar un
formato nuevo es solo añadir aquí cómo abrirlo.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from .comunes import ADVERTENCIA, ERROR, INFO, Incidencia

EXTENSIONES = [".xlsx", ".xlsm", ".xls", ".csv", ".txt", ".pdf"]


@dataclass
class Rejilla:
    hoja: str
    filas: list            # lista de listas; índice 0 = fila 1 del archivo
    oculta: bool = False
    texto: str = ""        # texto plano de la página (solo PDF)
    fila_inicial: int = 1  # número de fila real de filas[0]
    extra: dict = field(default_factory=dict)


def leer_archivo(nombre: str, contenido: bytes) -> tuple[list[Rejilla], list[Incidencia]]:
    ext = Path(nombre).suffix.lower()
    try:
        if ext in (".xlsx", ".xlsm"):
            return _leer_xlsx(contenido)
        if ext == ".xls":
            return _leer_xls(contenido)
        if ext in (".csv", ".txt"):
            return _leer_csv(contenido)
        if ext == ".pdf":
            return _leer_pdf(contenido)
    except Exception as e:  # archivo dañado, protegido con clave, etc.
        return [], [Incidencia(ERROR, f"No se pudo abrir el archivo ({type(e).__name__}: {e}). "
                                      "Verifique que no esté dañado ni protegido con contraseña.")]
    return [], [Incidencia(ERROR, f"Tipo de archivo no soportado ({ext or 'sin extensión'}). "
                                  f"Use: {', '.join(EXTENSIONES)}.")]


def _leer_xlsx(contenido: bytes):
    import openpyxl

    inc: list[Incidencia] = []
    libro = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True)
    # Segunda lectura con fórmulas: si una fórmula no trae valor calculado (archivo
    # generado por un programa y nunca abierto en Excel) hay que avisar, no leer vacío.
    formulas = openpyxl.load_workbook(io.BytesIO(contenido), data_only=False)
    rejillas = []
    for ws in libro.worksheets:
        if ws.sheet_state != "visible":
            inc.append(Incidencia(INFO, "Hoja oculta: no se lee.", hoja=ws.title))
            continue
        filas = [list(f) for f in ws.iter_rows(values_only=True)]
        ws_f = formulas[ws.title]
        sin_calcular = 0
        for i, fila in enumerate(ws_f.iter_rows(values_only=True)):
            for j, v in enumerate(fila):
                if isinstance(v, str) and v.startswith("=") and i < len(filas) and filas[i][j] is None:
                    sin_calcular += 1
        if sin_calcular:
            inc.append(Incidencia(ADVERTENCIA, f"{sin_calcular} celdas con fórmulas sin valor calculado: se leen "
                                               "vacías. Abra el archivo en Excel, guárdelo y cárguelo de nuevo.",
                                  hoja=ws.title))
        # Los encabezados combinados se repiten en todas las columnas que abarcan.
        for rango in ws.merged_cells.ranges:
            v = filas[rango.min_row - 1][rango.min_col - 1] if rango.min_row <= len(filas) else None
            if v is None or rango.max_row != rango.min_row:
                continue
            for c in range(rango.min_col, rango.max_col + 1):
                filas[rango.min_row - 1][c - 1] = v
        rejillas.append(Rejilla(ws.title, filas, extra={"combinadas": [str(r) for r in ws.merged_cells.ranges]}))
    return rejillas, inc


def _leer_xls(contenido: bytes):
    import pandas as pd

    hojas = pd.read_excel(io.BytesIO(contenido), sheet_name=None, header=None, engine="xlrd")
    return [Rejilla(n, _df_a_filas(df)) for n, df in hojas.items()], []


def _df_a_filas(df) -> list:
    import pandas as pd

    return [[None if pd.isna(v) else v for v in fila] for fila in df.itertuples(index=False, name=None)]


def _leer_csv(contenido: bytes):
    texto = None
    for cod in ("utf-8-sig", "latin-1"):
        try:
            texto = contenido.decode(cod)
            break
        except UnicodeDecodeError:
            continue
    muestra = texto[:5000]
    try:
        sep = csv.Sniffer().sniff(muestra, delimiters=";,\t|").delimiter
    except csv.Error:
        sep = ";" if muestra.count(";") >= muestra.count(",") else ","
    filas = [[(c.strip() or None) for c in fila] for fila in csv.reader(io.StringIO(texto), delimiter=sep)]
    return [Rejilla("CSV", filas)], []


def _leer_pdf(contenido: bytes):
    import pdfplumber

    rejillas, inc = [], []
    with pdfplumber.open(io.BytesIO(contenido)) as pdf:
        for n, pagina in enumerate(pdf.pages, start=1):
            texto = pagina.extract_text() or ""
            tablas = pagina.extract_tables() or []
            if not texto.strip() and not tablas:
                inc.append(Incidencia(ADVERTENCIA, "Página sin texto: parece una imagen escaneada. Digite los datos "
                                                   "en la plantilla de Excel.", hoja=f"Página {n}"))
                continue
            if not tablas:
                rejillas.append(Rejilla(f"Página {n}", [], texto=texto))
            for k, t in enumerate(tablas, start=1):
                filas = [[(c.replace("\n", " ").strip() or None) if isinstance(c, str) else c for c in fila]
                         for fila in t]
                nombre = f"Página {n}" + (f" tabla {k}" if len(tablas) > 1 else "")
                rejillas.append(Rejilla(nombre, filas, texto=texto if k == 1 else ""))
    return rejillas, inc

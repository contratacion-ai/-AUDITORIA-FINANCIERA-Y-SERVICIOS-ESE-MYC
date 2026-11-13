"""Formatos de entrada que acepta la herramienta.

- Hojas de cálculo: .xlsx / .xlsm (lectura en streaming), .xls (Excel 97-2003), .xlsb (Excel binario), .ods (LibreOffice),
  y los .xls que en realidad son HTML (exportación frecuente de software asistencial).
- Texto: .csv / .txt / .tsv (separador ; , tabulador o | detectado; UTF-8 o Windows-1252).
- Comprimidos: .zip, .7z, .rar, .gz, .tar, .tgz / .tar.gz (también anidados un nivel). Se extraen y se procesa cada hoja de cálculo
  o texto que contengan. El .rar se abre con lo que tenga el equipo: el «tar» de Windows 10/11, 7-Zip o WinRAR.

`abrir_libro(ruta)` devuelve un zipfile.ZipFile para .xlsx (el lector en streaming de loaders.py lo usa directamente) o un `Libro`
con la misma interfaz mínima (hojas() y filas()) para todo lo demás.
"""
import csv, gzip, io, os, re, shutil, subprocess, tarfile, zipfile
from pathlib import Path

EXT_TABLA = {".xlsx", ".xlsm", ".xls", ".xlsb", ".ods", ".csv", ".txt", ".tsv"}
EXT_COMPRIMIDO = {".zip", ".rar", ".7z", ".gz", ".tgz", ".tar", ".bz2"}
EXT_RIPS = {".json", ".xml"}                             # RIPS JSON (Res. 2275/2023) y XML de la FEV; los RIPS TXT usan .txt
ACEPTADOS = sorted(EXT_TABLA | EXT_COMPRIMIDO | EXT_RIPS | {".tar.gz"})

def extension(nombre):
    n = str(nombre).lower()
    return ".tar.gz" if n.endswith(".tar.gz") else Path(n).suffix

def es_xlsx(ruta):
    try:
        with zipfile.ZipFile(ruta) as z: return "xl/workbook.xml" in z.namelist()
    except (zipfile.BadZipFile, OSError): return False

def _tipo_contenido(ruta):
    """xlsx | ods | xls (OLE) | html | texto — por el contenido, no por la extensión (muchos «.xls» son HTML o CSV)."""
    with open(ruta, "rb") as f: cab = f.read(2048)
    if cab.startswith(b"PK"):
        try:
            with zipfile.ZipFile(ruta) as z:
                nombres = z.namelist()
                if "xl/workbook.xml" in nombres or "xl/workbook.bin" in nombres: return "xlsb" if "xl/workbook.bin" in nombres else "xlsx"
                if "content.xml" in nombres: return "ods"
        except zipfile.BadZipFile: pass
    if cab.startswith(b"\xd0\xcf\x11\xe0"): return "xls"
    t = cab.lstrip(b"\xef\xbb\xbf \r\n\t").lower()
    if t.startswith((b"<html", b"<!doctype", b"<table", b"<meta", b"<head", b"<?xml")) or b"<table" in t: return "html"
    return "texto"

# ───────── lectura homogénea: filas como {letra_columna: texto} (igual que loaders.iter_rows) ─────────
def _letra(i):
    s = ""; i += 1
    while i: i, r = divmod(i - 1, 26); s = chr(65 + r) + s
    return s

def _limpia(v):
    if v is None: return ""
    s = str(v).strip()
    if s.lower() in ("nan", "nat", "none"): return ""
    return s[:-2] if re.fullmatch(r"-?\d+\.0", s) else s       # 890201.0 → 890201 (códigos y cantidades leídos como número)

def _abrir_texto(ruta):
    with open(ruta, "rb") as f: muestra = f.read(65536)
    for cod in ("utf-8-sig", "cp1252"):
        try: texto = muestra.decode(cod); break
        except UnicodeDecodeError: continue
    try: sep = csv.Sniffer().sniff(texto, delimiters=";,\t|").delimiter
    except csv.Error:
        linea = texto.splitlines()[0] if texto else ""
        sep = max(";,\t|", key=linea.count)
    return cod, sep

class Libro:
    """Hojas de cálculo que no son .xlsx y archivos de texto, con la interfaz que usa el lector de 360 (hojas / filas)."""
    def __init__(self, ruta):
        self.ruta, self.tipo, self._dfs = ruta, _tipo_contenido(ruta), None
        self.nombre = Path(ruta).stem
    def _tablas(self):
        if self._dfs is None:
            import pandas as pd
            if self.tipo == "html":
                dfs = {}
                with open(self.ruta, "rb") as f: crudo = f.read()
                for cod in ("utf-8-sig", "cp1252"):
                    try: texto = crudo.decode(cod); break
                    except UnicodeDecodeError: continue
                for i, d in enumerate(pd.read_html(io.StringIO(texto))):
                    if not isinstance(d.columns, pd.RangeIndex):          # el encabezado <th> vuelve como primera fila (se detecta como en Excel)
                        cab = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in d.columns]
                        d = pd.concat([pd.DataFrame([cab], columns=d.columns), d], ignore_index=True)
                    d.columns = range(d.shape[1]); dfs[self.nombre if i == 0 else f"{self.nombre} {i + 1}"] = d.astype(object)
                self._dfs = dfs
            else:
                motor = {"xls": "xlrd", "xlsb": "pyxlsb", "ods": "odf", "xlsx": "openpyxl"}[self.tipo]
                self._dfs = pd.read_excel(self.ruta, sheet_name=None, header=None, dtype=str, engine=motor)
        return self._dfs
    def hojas(self):
        return {self.nombre: self.nombre} if self.tipo == "texto" else {n: n for n in self._tablas()}
    def filas(self, hoja, limit=None):
        n = 0
        if self.tipo == "texto":
            cod, sep = _abrir_texto(self.ruta)
            with open(self.ruta, encoding=cod, errors="replace", newline="") as f:
                for r in csv.reader(f, delimiter=sep):
                    yield {_letra(i): v.strip() for i, v in enumerate(r) if v.strip()}; n += 1
                    if limit and n > limit: return
            return
        for r in self._tablas()[hoja].itertuples(index=False, name=None):
            yield {_letra(i): s for i, v in enumerate(r) if (s := _limpia(v))}; n += 1
            if limit and n > limit: return

def abrir_libro(ruta):
    return zipfile.ZipFile(ruta) if es_xlsx(ruta) else Libro(ruta)

# ───────── comprimidos ─────────
def _seguro(destino, nombre):
    d = os.path.realpath(destino); p = os.path.realpath(os.path.join(destino, nombre))
    return p == d or p.startswith(d + os.sep)

def _herramientas_rar():
    """Programas para abrir .rar, en orden: 7-Zip, WinRAR y el tar de Windows 10/11 (o bsdtar/unrar en Linux/Mac)."""
    pf = [os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")]
    cands = [os.path.join(p, "7-Zip", "7z.exe") for p in pf if p] + [shutil.which("7z") or "", shutil.which("7za") or ""]
    out = [("7z", c) for c in cands if c and os.path.exists(c)]
    out += [("unrar", c) for c in [os.path.join(p, "WinRAR", "UnRAR.exe") for p in pf if p] + [shutil.which("unrar") or ""] if c and os.path.exists(c)]
    win_tar = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tar.exe")
    out += [("tar", c) for c in [win_tar, shutil.which("bsdtar") or ""] if c and os.path.exists(c)]
    return out

def _extraer_rar(ruta, destino):
    errores = []
    for tipo, exe in _herramientas_rar():
        cmd = {"7z": [exe, "x", "-y", f"-o{destino}", ruta], "unrar": [exe, "x", "-y", "-o+", ruta, destino + os.sep],
               "tar": [exe, "-xf", ruta, "-C", destino]}[tipo]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=900, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode == 0 and any(os.scandir(destino)): return
            errores.append(f"{tipo}: {r.stderr.decode(errors='replace')[:120]}")
        except (OSError, subprocess.SubprocessError) as e: errores.append(f"{tipo}: {e}")
    raise ValueError("No se pudo abrir el archivo .rar en este equipo. Instale 7-Zip (gratuito) o WinRAR, o descomprímalo y cargue los Excel/CSV. "
                     + (f"Detalle: {' | '.join(errores)}" if errores else "No se encontró 7-Zip, WinRAR ni el «tar» de Windows."))

def extraer(ruta, destino, nivel=0):
    """Extrae un comprimido en `destino` y devuelve las rutas de las hojas de cálculo / textos que contiene (incluye comprimidos anidados)."""
    os.makedirs(destino, exist_ok=True); ext = extension(ruta)
    if ext == ".zip":
        with zipfile.ZipFile(ruta) as z:
            for m in z.infolist():
                if not m.is_dir() and _seguro(destino, m.filename): z.extract(m, destino)
    elif ext in (".tar", ".tgz", ".tar.gz", ".bz2") or (ext == ".gz" and tarfile.is_tarfile(ruta)):
        with tarfile.open(ruta) as t:
            miembros = [m for m in t.getmembers() if m.isfile() and _seguro(destino, m.name)]
            try: t.extractall(destino, members=miembros, filter="data")
            except TypeError: t.extractall(destino, members=miembros)
    elif ext == ".gz":
        with gzip.open(ruta) as g, open(os.path.join(destino, Path(ruta).stem), "wb") as o: shutil.copyfileobj(g, o)
    elif ext == ".7z":
        import py7zr
        with py7zr.SevenZipFile(ruta) as z:
            z.extract(path=destino, targets=[n for n in z.getnames() if _seguro(destino, n)])
    elif ext == ".rar":
        _extraer_rar(ruta, destino)
    else:
        raise ValueError(f"Formato comprimido no soportado: {ext}")
    tablas = []
    for raiz, dirs, archivos in os.walk(destino):
        dirs[:] = [d for d in dirs if not d.startswith((".", "__MACOSX"))]
        for a in sorted(archivos):
            if a.startswith(("~$", ".")): continue
            p = os.path.join(raiz, a); e = extension(a)
            if e in EXT_TABLA or e in EXT_RIPS: tablas.append(p)
            elif e in EXT_COMPRIMIDO | {".tar.gz"} and nivel < 2: tablas += extraer(p, p + "_extraido", nivel + 1)
    return tablas

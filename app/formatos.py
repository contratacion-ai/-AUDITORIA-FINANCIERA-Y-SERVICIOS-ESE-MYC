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

# ───────── extracción con rutas cortas ─────────
# Windows no abre rutas de más de 260 caracteres ni carpetas que terminan en espacio o punto (frecuente en las descargas de Google Drive:
# «Hospital Juan de Dios /09. Septiembre/JSON …/ResultadosMSPS_…»). Por eso cada archivo se escribe en «destino/NNN/nombre», donde NNN
# identifica la carpeta original (los RIPS TXT de un mismo envío quedan juntos) y el nombre se limpia y se acorta. ORIGEN guarda la ruta
# original dentro del comprimido para mostrarla al usuario.
ORIGEN = {}
_INVALIDOS = re.compile(r'[<>:"|?*\x00-\x1f]')
_OMITIR = ("__MACOSX", ".")

def corto(nombre, maximo=60):
    """Nombre de archivo válido en Windows y de longitud acotada (conserva la extensión)."""
    n = _INVALIDOS.sub("_", str(nombre)).replace("/", "_").replace("\\", "_").strip().rstrip(". ") or "archivo"
    if len(n) > maximo:
        st, ex = os.path.splitext(n); ex = ex[:12]; n = st[:maximo - len(ex)].rstrip(". ") + ex
    return n

def _largo(p):
    """Prefijo de ruta extendida de Windows (\\?\\) para herramientas que escriben las rutas originales (7z, rar)."""
    p = os.path.abspath(p)
    return "\\\\?\\" + p if os.name == "nt" and not p.startswith("\\\\?\\") else p

class _Plano:
    def __init__(self, destino): self.destino, self.dirs, self.usados = destino, {}, set()
    def ruta(self, interno):
        partes = [x.strip() for x in re.split(r"[\\/]+", str(interno)) if x.strip() not in ("", ".", "..")]
        if not partes or any(x.startswith(_OMITIR) for x in partes) or partes[-1].startswith("~$"): return None
        d = os.path.join(self.destino, f"{self.dirs.setdefault('/'.join(partes[:-1]), len(self.dirs)):03d}"); os.makedirs(d, exist_ok=True)
        n = corto(partes[-1]); p = os.path.join(d, n); k = 1
        while p in self.usados: st, ex = os.path.splitext(n); p = os.path.join(d, f"{st}_{k}{ex}"); k += 1
        self.usados.add(p); ORIGEN[p] = "/".join(partes); return p

def _copiar(origen, p):
    with open(p, "wb") as o: shutil.copyfileobj(origen, o, 1 << 20)

def _aplanar(tmp, plano):
    """Pasa lo que extrajo una herramienta externa (7z, rar) a la estructura corta y borra la carpeta temporal."""
    base = _largo(tmp)
    for raiz, _, archivos in os.walk(base):
        for a in archivos:
            q = os.path.join(raiz, a); p = plano.ruta(os.path.relpath(q, base)) if util(a) else None
            if p: shutil.move(q, p)
    shutil.rmtree(base, ignore_errors=True)

def nombre_interno(ruta, base):
    """Ruta original (dentro del comprimido) de un archivo extraído, para los mensajes."""
    return ORIGEN.get(ruta) or os.path.relpath(ruta, base)

def util(nombre):
    """Solo se extraen hojas de cálculo, textos, JSON, XML y comprimidos: los PDF e imágenes de soportes se omiten."""
    e = extension(str(nombre).rstrip("/\\ "))
    return e in EXT_TABLA or e in EXT_RIPS or e in EXT_COMPRIMIDO or e == ".tar.gz"

def extraer(ruta, destino, nivel=0, avance=None):
    """Extrae un comprimido en `destino` (con rutas cortas) y devuelve las rutas de las hojas de cálculo, textos, JSON y XML que contiene
    (incluye comprimidos anidados hasta dos niveles). `avance(i, n)` informa el progreso."""
    os.makedirs(destino, exist_ok=True); ext = extension(ruta); plano = _Plano(destino)
    if ext == ".zip":
        with zipfile.ZipFile(ruta) as z:
            miembros = [m for m in z.infolist() if not m.is_dir() and util(m.filename)]
            for i, m in enumerate(miembros, 1):
                p = plano.ruta(m.filename)
                if p:
                    with z.open(m) as s: _copiar(s, p)
                if avance and (i % 200 == 0 or i == len(miembros)): avance(i, len(miembros))
    elif ext in (".tar", ".tgz", ".tar.gz", ".bz2") or (ext == ".gz" and tarfile.is_tarfile(ruta)):
        with tarfile.open(ruta) as t:
            for m in t.getmembers():
                p = plano.ruta(m.name) if m.isfile() and util(m.name) else None
                if p: _copiar(t.extractfile(m), p)
    elif ext == ".gz":
        p = plano.ruta(Path(ruta).stem)
        with gzip.open(ruta) as g: _copiar(g, p)
    elif ext == ".7z":
        import py7zr
        tmp = os.path.join(destino, "_t"); os.makedirs(_largo(tmp), exist_ok=True)
        with py7zr.SevenZipFile(ruta) as z:
            z.extract(path=_largo(tmp), targets=[n for n in z.getnames() if _seguro(tmp, n) and util(n)])
        _aplanar(tmp, plano)
    elif ext == ".rar":
        tmp = os.path.join(destino, "_t"); os.makedirs(_largo(tmp), exist_ok=True)
        _extraer_rar(ruta, _largo(tmp)); _aplanar(tmp, plano)
    else:
        raise ValueError(f"Formato comprimido no soportado: {ext}")
    tablas, n = [], 0
    for p in sorted(plano.usados, key=lambda x: ORIGEN.get(x, x)):
        e = extension(p)
        if e in EXT_TABLA or e in EXT_RIPS: tablas.append(p)
        elif e in EXT_COMPRIMIDO | {".tar.gz"} and nivel < 2:
            n += 1; sub = os.path.join(os.path.dirname(p), f"x{n:03d}_{corto(Path(p).stem, 12)}")
            hijos = extraer(p, sub, nivel + 1)
            for h in hijos: ORIGEN[h] = ORIGEN.get(p, Path(p).name) + " › " + ORIGEN.get(h, Path(h).name)
            tablas += hijos
    return tablas

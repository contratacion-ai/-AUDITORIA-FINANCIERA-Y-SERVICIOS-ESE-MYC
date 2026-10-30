"""Recolección de archivos de entrada: sueltos, carpetas, ZIP (anidados) y RAR.

Cada archivo útil se entrega como ``ArchivoEntrada`` con su *ruta lógica* (ruta relativa
dentro de la carpeta o del comprimido). La ruta lógica es la que se usa para deducir
hospital, municipio y mes; por eso se conserva la estructura de carpetas original.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

EXT_UTILES = {".txt", ".json", ".xlsx", ".xlsm"}
MAX_BYTES_DESCOMPRIMIDOS = 4 * 1024 ** 3   # protección contra "zip bombs"
MAX_NIVEL_ANIDADO = 3


@dataclass
class ArchivoEntrada:
    ruta: str                 # ruta lógica con '/' (p. ej. 'Hospital X/08. Agosto/NUEVA EPS/AF0001.txt')
    datos: bytes
    contenedor: str = ""      # nombre del ZIP/RAR de origen, si aplica

    @property
    def nombre(self) -> str:
        return PurePosixPath(self.ruta).name

    @property
    def extension(self) -> str:
        return PurePosixPath(self.ruta).suffix.lower()


@dataclass
class ArchivoIgnorado:
    ruta: str
    motivo: str
    bytes: int = 0
    severidad: str = "INFO"


def _ruta_segura(nombre: str) -> str | None:
    """Evita 'zip slip': descarta rutas absolutas o con '..'."""
    p = PurePosixPath(nombre.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts:
        return None
    return str(p)


def _desde_zip(datos: bytes, prefijo: str, contenedor: str, nivel: int,
               salida: list, ignorados: list) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(datos))
    except zipfile.BadZipFile:
        ignorados.append(ArchivoIgnorado(prefijo or contenedor, "ZIP_CORRUPTO", len(datos), "ERROR"))
        return
    total = sum(i.file_size for i in zf.infolist())
    if total > MAX_BYTES_DESCOMPRIMIDOS:
        ignorados.append(ArchivoIgnorado(contenedor, "ZIP excede el tamaño máximo descomprimido", total, "ERROR"))
        return
    for info in zf.infolist():
        if info.is_dir():
            continue
        nombre = info.filename
        if info.flag_bits & 0x800 == 0:   # nombres sin bandera UTF-8: suelen venir en cp437 desde Windows
            try:
                nombre = info.filename.encode("cp437").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
        seg = _ruta_segura(nombre)
        if seg is None:
            ignorados.append(ArchivoIgnorado(nombre, "Ruta insegura dentro del ZIP", info.file_size, "ERROR"))
            continue
        ruta = f"{prefijo}/{seg}" if prefijo else seg
        _agregar(ruta, zf.read(info), contenedor, nivel, salida, ignorados)


def _herramienta_rar() -> list[str] | None:
    for exe, args in (("unrar", ["x", "-o+", "-inul"]), ("unar", ["-f", "-q", "-o"]),
                      ("bsdtar", ["-xf"]), ("7z", ["x", "-y"])):
        if shutil.which(exe):
            return [exe, *args]
    return None


def _desde_rar(datos: bytes, prefijo: str, contenedor: str, nivel: int,
               salida: list, ignorados: list) -> None:
    """RAR requiere una herramienta externa (unrar, unar, bsdtar o 7z)."""
    herramienta = _herramienta_rar()
    if herramienta is None:
        ignorados.append(ArchivoIgnorado(prefijo or contenedor,
                                         "RAR_SIN_HERRAMIENTA: instale unrar, unar, bsdtar o 7z",
                                         len(datos), "ERROR"))
        return
    with tempfile.TemporaryDirectory() as tmp:
        arc = Path(tmp) / "entrada.rar"
        arc.write_bytes(datos)
        destino = Path(tmp) / "x"
        destino.mkdir()
        exe = herramienta[0]
        if exe == "unrar":
            cmd = [*herramienta, str(arc), str(destino) + "/"]
        elif exe == "unar":
            cmd = [*herramienta, str(destino), str(arc)]
        elif exe == "bsdtar":
            cmd = [*herramienta, str(arc), "-C", str(destino)]
        else:
            cmd = [*herramienta, str(arc), f"-o{destino}"]
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        if r.returncode != 0:
            ignorados.append(ArchivoIgnorado(prefijo or contenedor, "RAR_CORRUPTO", len(datos), "ERROR"))
            return
        for f in sorted(destino.rglob("*")):
            if f.is_file():
                rel = f.relative_to(destino).as_posix()
                _agregar(f"{prefijo}/{rel}" if prefijo else rel, f.read_bytes(), contenedor, nivel, salida, ignorados)


def _agregar(ruta: str, datos: bytes, contenedor: str, nivel: int,
             salida: list, ignorados: list) -> None:
    ext = PurePosixPath(ruta).suffix.lower()
    if ext in (".zip", ".rar"):
        if nivel >= MAX_NIVEL_ANIDADO:
            ignorados.append(ArchivoIgnorado(ruta, "Comprimido anidado demasiado profundo", len(datos), "ERROR"))
            return
        # El nombre del comprimido no se agrega a la ruta: Google Drive y Windows ya
        # incluyen la carpeta raíz dentro del ZIP.
        prefijo = str(PurePosixPath(ruta).parent) if "/" in ruta else ""
        fn = _desde_zip if ext == ".zip" else _desde_rar
        fn(datos, prefijo, contenedor or PurePosixPath(ruta).name, nivel + 1, salida, ignorados)
    elif ext in EXT_UTILES:
        salida.append(ArchivoEntrada(ruta, datos, contenedor))
    else:
        ignorados.append(ArchivoIgnorado(ruta, f"extensión {ext or '(sin extensión)'} no RIPS", len(datos)))


def recolectar(entradas) -> tuple[list[ArchivoEntrada], list[ArchivoIgnorado]]:
    """Acepta rutas (archivo o carpeta) o tuplas ``(ruta_logica, bytes)`` de una carga web."""
    salida: list[ArchivoEntrada] = []
    ignorados: list[ArchivoIgnorado] = []
    for e in entradas:
        if isinstance(e, tuple):
            ruta, datos = e
            seg = _ruta_segura(ruta) or PurePosixPath(ruta.replace("\\", "/")).name
            _agregar(seg, datos, "", 0, salida, ignorados)
            continue
        p = Path(e)
        if p.is_dir():
            base = p.parent
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    _agregar(f.relative_to(base).as_posix(), f.read_bytes(), "", 0, salida, ignorados)
        elif p.is_file():
            _agregar(p.name, p.read_bytes(), "", 0, salida, ignorados)
        else:
            ignorados.append(ArchivoIgnorado(str(p), "ARCHIVO_ILEGIBLE: no existe", 0, "ERROR"))
    return salida, ignorados

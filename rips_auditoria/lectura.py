"""Descubrimiento de archivos RIPS y agrupación en remisiones (envíos).

Entradas aceptadas (se pueden mezclar en una misma carga):

* un archivo TXT suelto (p. ej. ``AF000123.txt``);
* una carpeta (se recorre recursivamente);
* un archivo ZIP, incluidos ZIP dentro de ZIP (hasta ``PROFUNDIDAD_MAX_ZIP``).

Cada archivo recibe una *ruta lógica* que conserva la jerarquía original
(``lote.zip/Hospital San Antonio/05. Mayo/10260__FIDUPREVISORA/Rips/AF010260.txt``).
De esa ruta se infieren el hospital, el mes y la carpeta de la remisión.

Supuestos documentados:

* Una **remisión** es el conjunto de archivos con el mismo número de remisión
  dentro de la misma carpeta (Anexo, numeral 1: "nombres de los archivos").
* El **hospital** es la carpeta inmediatamente anterior a la carpeta del mes
  (estructura del ZIP de ejemplo: ``Hospital/NN. Mes/<remisión>/Rips/*.txt``).
  Si no hay carpeta de mes, se usa la primera carpeta que no sea un ZIP ni
  una carpeta genérica ("Rips", "TXT"). Si aún así no hay dato, se resuelve
  después del parseo con ``hospitales_por_prestador`` o el código del prestador.
* La **carpeta del mes** se reconoce por el nombre del mes en español
  ("05. Mayo", "Mayo", "MAYO 2026") o por un patrón ``AAAA-MM``/``AAAAMM``.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .especificacion import ESPECIFICACIONES
from .incidencias import RegistroIncidencias

log = logging.getLogger(__name__)

PROFUNDIDAD_MAX_ZIP = 3

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
_RE_MES_NOMBRE = re.compile(r"(?<![a-z])(" + "|".join(MESES) + r")(?![a-z])")
_RE_ANIO_MES = re.compile(r"^(20\d{2})[-_. ]?(0[1-9]|1[0-2])$")
_RE_ANIO = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_TIPOS = "|".join(ESPECIFICACIONES)
_RE_NOMBRE_RIPS = re.compile(
    rf"^(?P<tipo>{_TIPOS})(?P<remision>[0-9A-Za-z]{{1,12}})(?P<sufijo>_[^.]*)?\.txt$", re.IGNORECASE
)
_RE_EXPORT_DRIVE = re.compile(r"-\d{8}T\d{6}Z(-\d+)*$")
_CARPETAS_GENERICAS = {"rips", "txt", "rips txt", "rips_txt", "archivos"}


@dataclass
class ArchivoFuente:
    ruta: str  # ruta lógica con "/" como separador
    contenido: bytes

    @property
    def nombre(self) -> str:
        return PurePosixPath(self.ruta).name

    @property
    def directorio(self) -> str:
        return str(PurePosixPath(self.ruta).parent)


@dataclass
class ArchivoRips:
    ruta: str
    tipo: str
    remision: str
    contenido: bytes
    corregido: bool = False
    nombre_normativo: bool = True


@dataclass
class Envio:
    """Una remisión RIPS: archivos con igual número de remisión en una carpeta."""

    envio_id: str
    directorio: str
    remision: str
    archivos: dict[str, ArchivoRips] = field(default_factory=dict)
    hospital: str | None = None
    mes_carpeta: int | None = None
    anio_carpeta: int | None = None
    carpeta_envio: str = ""
    periodo: str | None = None
    fuente_periodo: str | None = None
    fuente_hospital: str | None = None


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def detectar_mes(componente: str) -> tuple[int | None, int | None]:
    """Devuelve (mes, año) si el nombre de carpeta representa un mes."""
    t = _sin_tildes(componente).lower().strip()
    m = _RE_ANIO_MES.match(t)
    if m:
        return int(m.group(2)), int(m.group(1))
    m = _RE_MES_NOMBRE.search(t)
    if m:
        # Evita falsos positivos en nombres largos de remisión que contengan
        # un mes por casualidad: la carpeta debe ser corta o empezar por número/mes.
        resto = _RE_MES_NOMBRE.sub("", t)
        resto = re.sub(r"[\d\s._\-]", "", resto)
        if len(resto) <= 3:
            anio = _RE_ANIO.search(t)
            return MESES[m.group(1)], int(anio.group(1)) if anio else None
    return None, None


def _limpiar_nombre_zip(nombre: str) -> str:
    base = re.sub(r"\.zip$", "", nombre, flags=re.IGNORECASE)
    base = _RE_EXPORT_DRIVE.sub("", base)  # sufijo de exportación de Google Drive
    return base.replace("_", " ").strip()


def contexto_desde_ruta(ruta: str) -> dict:
    """Infiere hospital, mes/año de carpeta y carpeta de remisión desde la ruta lógica."""
    partes = list(PurePosixPath(ruta).parts[:-1])  # sin el nombre del archivo
    ctx = {"hospital": None, "mes": None, "anio": None, "carpeta_envio": "", "fuente_hospital": None}
    idx_mes = None
    for i, p in enumerate(partes):
        mes, anio = detectar_mes(p)
        if mes:
            idx_mes, ctx["mes"], ctx["anio"] = i, mes, anio
            break
    if idx_mes is not None:
        if ctx["anio"] is None:
            for p in reversed(partes[:idx_mes]):
                if not p.lower().endswith(".zip"):
                    a = _RE_ANIO.search(p)
                    if a:
                        ctx["anio"] = int(a.group(1))
                        break
        if idx_mes > 0:
            previo = partes[idx_mes - 1]
            if _RE_ANIO_MES.match(previo) or re.fullmatch(r"20\d{2}", previo.strip()):
                previo = partes[idx_mes - 2] if idx_mes > 1 else previo
            ctx["hospital"] = _limpiar_nombre_zip(previo) if previo.lower().endswith(".zip") else previo.strip()
            ctx["fuente_hospital"] = "carpeta"
        siguientes = [p for p in partes[idx_mes + 1:] if p.strip().lower() not in _CARPETAS_GENERICAS]
        ctx["carpeta_envio"] = siguientes[0].strip() if siguientes else ""
    else:
        dirs = [p for p in partes if not p.lower().endswith(".zip") and p.strip().lower() not in _CARPETAS_GENERICAS]
        if dirs:
            ctx["hospital"] = dirs[0].strip()
            ctx["fuente_hospital"] = "carpeta"
            ctx["carpeta_envio"] = dirs[-1].strip() if len(dirs) > 1 else ""
        else:
            zips = [p for p in partes if p.lower().endswith(".zip")]
            if zips:
                ctx["hospital"] = _limpiar_nombre_zip(zips[0])
                ctx["fuente_hospital"] = "zip"
    return ctx


def _nombre_en_zip(info: zipfile.ZipInfo) -> str:
    """Corrige nombres de ZIP creados en Windows sin la bandera UTF-8."""
    nombre = info.filename
    if not info.flag_bits & 0x800:
        try:
            nombre = nombre.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return nombre.replace("\\", "/")


def _leer_zip(datos: bytes, prefijo: str, profundidad: int, inc: RegistroIncidencias):
    try:
        zf = zipfile.ZipFile(io.BytesIO(datos))
        infos = zf.infolist()
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        inc.agregar("ZIP_CORRUPTO", f"No se pudo abrir el ZIP: {exc}", archivo=prefijo)
        return
    with zf:
        for info in infos:
            if info.is_dir():
                continue
            nombre = _nombre_en_zip(info)
            ruta = f"{prefijo}/{nombre}"
            try:
                contenido = zf.read(info)
            except (zipfile.BadZipFile, OSError, RuntimeError, ValueError, EOFError) as exc:
                inc.agregar("ARCHIVO_ILEGIBLE", f"No se pudo extraer del ZIP: {exc}", archivo=ruta)
                continue
            if nombre.lower().endswith(".zip"):
                if profundidad >= PROFUNDIDAD_MAX_ZIP:
                    inc.agregar("ZIP_CORRUPTO", "ZIP anidado ignorado: supera la profundidad máxima", archivo=ruta)
                    continue
                yield from _leer_zip(contenido, ruta, profundidad + 1, inc)
            else:
                yield ArchivoFuente(ruta, contenido)


def descubrir_archivos(entradas: list[str | Path], inc: RegistroIncidencias):
    """Genera ``ArchivoFuente`` para cada archivo encontrado en las entradas."""
    for entrada in entradas:
        p = Path(entrada)
        if not p.exists():
            inc.agregar("ARCHIVO_ILEGIBLE", f"La ruta no existe: {p}", archivo=str(p))
            continue
        if p.is_dir():
            p = p.resolve()
            # Si se carga directamente la carpeta del mes ("05. Mayo"), se
            # antepone la carpeta padre para conservar el nombre del hospital.
            raiz = f"{p.parent.name}/{p.name}" if detectar_mes(p.name)[0] and p.parent.name else p.name
            for f in sorted(x for x in p.rglob("*") if x.is_file()):
                rel = f"{raiz}/{f.relative_to(p).as_posix()}"
                yield from _fuente_desde_disco(f, rel, inc)
        else:
            yield from _fuente_desde_disco(p, p.name, inc)


def _fuente_desde_disco(f: Path, ruta: str, inc: RegistroIncidencias):
    try:
        datos = f.read_bytes()
    except OSError as exc:
        inc.agregar("ARCHIVO_ILEGIBLE", f"No se pudo leer: {exc}", archivo=ruta)
        return
    if f.suffix.lower() == ".zip":
        yield from _leer_zip(datos, ruta, 1, inc)
    else:
        yield ArchivoFuente(ruta, datos)


def clasificar(fuente: ArchivoFuente) -> ArchivoRips | None:
    """Reconoce el tipo y la remisión a partir del nombre del archivo."""
    m = _RE_NOMBRE_RIPS.match(fuente.nombre)
    if not m:
        return None
    sufijo = (m.group("sufijo") or "").upper()
    corregido = "CORREGIDO" in sufijo
    if sufijo and (not corregido or "LOG" in sufijo):
        return None
    remision = m.group("remision")
    normativo = not sufijo and 1 <= len(remision) <= 6 and remision.isdigit()
    return ArchivoRips(fuente.ruta, m.group("tipo").upper(), remision, fuente.contenido, corregido, normativo)


def agrupar_envios(
    fuentes, inc: RegistroIncidencias, extensiones_ignoradas: list[str], preferir_corregidos: bool = True
) -> tuple[list[Envio], list[dict]]:
    """Agrupa los archivos RIPS en remisiones y arma el inventario de archivos."""
    envios: dict[tuple[str, str], Envio] = {}
    inventario: list[dict] = []
    ignoradas = {e.lower() for e in extensiones_ignoradas}

    for fuente in fuentes:
        ext = PurePosixPath(fuente.nombre).suffix.lower()
        if ext != ".txt":
            inventario.append({"archivo": fuente.ruta, "tipo_archivo": None, "estado": "ignorado",
                               "motivo": "extensión no RIPS TXT" if ext in ignoradas or ext else "sin extensión",
                               "bytes": len(fuente.contenido)})
            continue
        rips = clasificar(fuente)
        if rips is None:
            inventario.append({"archivo": fuente.ruta, "tipo_archivo": None, "estado": "ignorado",
                               "motivo": "nombre no reconocido", "bytes": len(fuente.contenido)})
            if "LOG" not in fuente.nombre.upper():
                inc.agregar("ARCHIVO_NO_RECONOCIDO", archivo=fuente.ruta)
            continue
        clave = (fuente.directorio, rips.remision.lstrip("0") or "0")
        envio = envios.get(clave)
        if envio is None:
            ctx = contexto_desde_ruta(fuente.ruta)
            envio = Envio(
                envio_id="",
                directorio=fuente.directorio,
                remision=rips.remision,
                hospital=ctx["hospital"],
                mes_carpeta=ctx["mes"],
                anio_carpeta=ctx["anio"],
                carpeta_envio=ctx["carpeta_envio"],
                fuente_hospital=ctx["fuente_hospital"],
            )
            envios[clave] = envio
        previo = envio.archivos.get(rips.tipo)
        if previo is not None:
            if preferir_corregidos and rips.corregido != previo.corregido:
                elegido, descartado = (rips, previo) if rips.corregido else (previo, rips)
                motivo = "reemplazado por versión corregida"
                inc.agregar("ARCHIVO_CORREGIDO_USADO", f"Se usó {elegido.ruta} en lugar de {descartado.ruta}",
                            archivo=elegido.ruta, tipo_archivo=rips.tipo)
            else:
                elegido, descartado = previo, rips
                motivo = "duplicado"
                inc.agregar("ARCHIVO_DUPLICADO", f"Se conserva {previo.ruta}", archivo=rips.ruta, tipo_archivo=rips.tipo)
            envio.archivos[rips.tipo] = elegido
            inventario.append({"archivo": descartado.ruta, "tipo_archivo": descartado.tipo, "estado": "ignorado",
                               "motivo": motivo, "bytes": len(descartado.contenido)})
            continue
        envio.archivos[rips.tipo] = rips

    lista = sorted(envios.values(), key=lambda e: (e.directorio, e.remision))
    for i, e in enumerate(lista, start=1):
        e.envio_id = f"E{i:05d}"
        for a in e.archivos.values():
            if not a.nombre_normativo and not a.corregido:
                inc.agregar("NOMBRE_ARCHIVO_NO_NORMATIVO", archivo=a.ruta, tipo_archivo=a.tipo, envio_id=e.envio_id)
    return lista, inventario

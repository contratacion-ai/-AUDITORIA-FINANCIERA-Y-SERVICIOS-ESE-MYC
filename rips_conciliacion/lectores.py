"""Lectura de RIPS: TXT (Res. 3374/2000), JSON (Res. 2275/2023), ZIP y RAR.

Cada registro leído conserva los nombres de campo originales y se le
agregan metadatos con prefijo ``_`` (archivo, línea, paquete, sede y mes
inferidos de la ruta). Los registros que no pueden interpretarse se
reportan como ``Incidencia`` y se excluyen; el resto del archivo continúa.
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .decimales import ValorInvalido, a_decimal
from .deteccion import Deteccion, detectar
from .esquemas import (
    ESQUEMAS_2275,
    ESQUEMAS_3374,
    LLAVES_RAIZ_2275,
    LLAVES_USUARIO_2275,
)
from .fechas import MESES, _sin_tildes, anio_de_nombre, mes_de_nombre, parse_fecha
from .modelos import ADVERTENCIA, ERROR, VERSION_2275, VERSION_3374, ResultadoCarga

MAGIC_ZIP = b"PK\x03\x04"
MAGIC_ZIP_VACIO = b"PK\x05\x06"
MAGIC_RAR = b"Rar!\x1a\x07"
EXTENSIONES_RIPS = {".txt", ".json", ".csv", ""}
PROFUNDIDAD_MAXIMA = 4
_IGNORAR = re.compile(
    r"(^|/)(__MACOSX|\.DS_Store|Thumbs\.db|desktop\.ini)", re.IGNORECASE
)
_PALABRAS_NO_SEDE = {
    "RIPS",
    "RIP",
    "ARCHIVOS",
    "CONSOLIDADO",
    "CONSOLIDADOS",
    "MES",
    "MESES",
    "TXT",
    "JSON",
    "",
}
# Palabras que se quitan del nombre de un paquete/carpeta para obtener la sede
_PALABRAS_RUIDO = {
    "RIPS",
    "RIP",
    "SEDE",
    "CONSOLIDADO",
    "CONSOLIDADOS",
    "ARCHIVOS",
    "MES",
    "MESES",
    "DE",
    "DEL",
}


@dataclass
class Contexto:
    """De dónde viene un archivo físico dentro de los paquetes."""

    componentes: list[str] = field(
        default_factory=list
    )  # paquetes y carpetas (sin el archivo)
    paquete: str | None = None  # paquete raíz (ZIP/RAR cargado)
    sede_asignada: str | None = None
    hospital_asignado: str | None = None

    def hijo(self, *partes: str) -> "Contexto":
        return Contexto(
            componentes=self.componentes + [p for p in partes if p],
            paquete=self.paquete,
            sede_asignada=self.sede_asignada,
            hospital_asignado=self.hospital_asignado,
        )


def _limpiar_sede(nombre: str) -> str:
    texto = re.sub(r"(?<!\d)20\d{2}[-_ .]?(0[1-9]|1[0-2])?(?!\d)", " ", nombre)
    palabras = re.split(r"[\s_\-.]+", texto)
    utiles = [
        p
        for p in palabras
        if _sin_tildes(p).upper() not in MESES
        and _sin_tildes(p).upper() not in _PALABRAS_RUIDO
        and not p.isdigit()
    ]
    return " ".join(utiles).strip().upper()


def inferir_de_ruta(componentes: list[str]) -> dict:
    """Infiere sede y mes a partir de nombres de paquetes/carpetas.

    * mes: el componente más profundo que contenga un mes; el año se toma de
      ese componente o del más cercano que contenga un año.
    * sede: el componente más profundo que, quitando meses y años, deje un
      nombre útil (``SEDE_TAME_2024`` -> ``SEDE TAME``).
    """
    anio = mes = None
    for parte in componentes:
        a, m = mes_de_nombre(parte)
        if m:
            mes = m
            anio = a or anio
        elif anio_de_nombre(parte) and anio is None:
            anio = anio_de_nombre(parte)
    sede = None
    for parte in reversed(componentes):
        candidato = _limpiar_sede(parte)
        if candidato.upper() not in _PALABRAS_NO_SEDE:
            sede = candidato
            break
    return {
        "_sede_ruta": sede,
        "_anio_ruta": anio,
        "_mes_num_ruta": mes,
        "_mes_ruta": f"{anio:04d}-{mes:02d}" if (anio and mes) else None,
    }


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------


def leer_rips(
    entradas, sede: str | None = None, hospital: str | None = None
) -> ResultadoCarga:
    """Lee una o varias entradas de RIPS.

    ``entradas`` puede ser una ruta (archivo o carpeta), una tupla
    ``(nombre, bytes)`` o una lista de ellas. ``sede``/``hospital`` asignan
    explícitamente la sede/hospital a todo lo leído (tiene prioridad sobre
    lo inferido de la ruta).
    """
    if not isinstance(entradas, (list, tuple)) or (
        isinstance(entradas, tuple)
        and len(entradas) == 2
        and isinstance(entradas[1], (bytes, bytearray))
    ):
        entradas = [entradas]
    total = ResultadoCarga(archivo="RIPS")
    for entrada in entradas:
        if isinstance(entrada, tuple):
            nombre, contenido = entrada
            ctx = Contexto(
                paquete=nombre, sede_asignada=sede, hospital_asignado=hospital
            )
            total.fusionar(_leer_bytes(nombre, bytes(contenido), ctx, 0))
        else:
            ruta = Path(entrada)
            if ruta.is_dir():
                total.fusionar(_leer_carpeta(ruta, sede, hospital))
            else:
                ctx = Contexto(
                    paquete=ruta.name, sede_asignada=sede, hospital_asignado=hospital
                )
                total.fusionar(_leer_bytes(ruta.name, ruta.read_bytes(), ctx, 0))
    _validar_control(total)
    versiones = {a["version"] for a in total.archivos if a.get("tipo") != "IGNORADO"}
    total.version = " + ".join(sorted(versiones)) if versiones else total.version
    return total


def _leer_carpeta(carpeta: Path, sede, hospital) -> ResultadoCarga:
    res = ResultadoCarga(archivo=str(carpeta))
    for raiz, _dirs, archivos in os.walk(carpeta):
        for nombre in sorted(archivos):
            ruta = Path(raiz) / nombre
            rel = ruta.relative_to(carpeta)
            ctx = Contexto(
                componentes=[carpeta.name, *rel.parent.parts],
                paquete=carpeta.name,
                sede_asignada=sede,
                hospital_asignado=hospital,
            )
            res.fusionar(
                _leer_bytes(
                    str(PurePosixPath(carpeta.name, *rel.parts)),
                    ruta.read_bytes(),
                    ctx,
                    0,
                    nombre,
                )
            )
    return res


def _leer_bytes(
    nombre: str,
    contenido: bytes,
    ctx: Contexto,
    profundidad: int,
    nombre_corto: str | None = None,
) -> ResultadoCarga:
    nombre_corto = nombre_corto or PurePosixPath(nombre).name
    if contenido.startswith(MAGIC_ZIP) or contenido.startswith(MAGIC_ZIP_VACIO):
        return _leer_zip(nombre, contenido, ctx, profundidad)
    if contenido.startswith(MAGIC_RAR):
        return _leer_rar(nombre, contenido, ctx, profundidad)

    res = ResultadoCarga(archivo=nombre)
    extension = PurePosixPath(nombre_corto).suffix.lower()
    if extension not in EXTENSIONES_RIPS:
        res.archivos.append(
            {
                "archivo": nombre,
                "version": "-",
                "tipo": "IGNORADO",
                "registros": 0,
                "errores": 0,
                "motivo": f"Extensión {extension} no corresponde a RIPS (se ignora)",
            }
        )
        res.incidencia(
            ADVERTENCIA,
            f"Archivo ignorado: extensión {extension} no es TXT/JSON de RIPS",
        )
        return res

    det = detectar(nombre_corto, contenido)
    meta = {
        "_archivo": nombre,
        "_paquete": ctx.paquete,
        "_version": det.version,
        "_sede_asignada": ctx.sede_asignada,
        "_hospital_asignado": ctx.hospital_asignado,
    }
    meta.update(inferir_de_ruta(ctx.componentes))

    if det.version == VERSION_3374:
        _parsear_3374(nombre, det, meta, res)
    elif det.version == VERSION_2275:
        _parsear_2275(nombre, det, meta, res)
    else:
        res.incidencia(
            ERROR, f"Versión de RIPS NO DETERMINADA; archivo no procesado. {det.motivo}"
        )

    errores = sum(1 for i in res.incidencias if i.severidad == ERROR)
    lineas = (
        len([ln for ln in (det.texto or "").splitlines() if ln.strip()])
        if det.texto is not None
        else None
    )
    res.archivos.append(
        {
            "archivo": nombre,
            "paquete": ctx.paquete,
            "version": det.version,
            "tipo": det.tipo,
            "lineas": lineas,
            "registros": res.total_registros,
            "errores": errores,
            "motivo": det.motivo,
            "sede_ruta": meta["_sede_ruta"],
            "mes_ruta": meta["_mes_ruta"],
        }
    )
    return res


def _leer_zip(
    nombre: str, contenido: bytes, ctx: Contexto, profundidad: int
) -> ResultadoCarga:
    res = ResultadoCarga(archivo=nombre)
    if profundidad >= PROFUNDIDAD_MAXIMA:
        res.incidencia(
            ERROR,
            f"Paquetes anidados con más de {PROFUNDIDAD_MAXIMA} niveles; no se procesa",
        )
        return res
    try:
        zf = zipfile.ZipFile(io.BytesIO(contenido))
    except zipfile.BadZipFile as exc:
        res.incidencia(ERROR, f"ZIP dañado o inválido: {exc}")
        return res
    hijo = ctx.hijo(PurePosixPath(nombre).stem)
    with zf:
        for info in zf.infolist():
            if info.is_dir() or _IGNORAR.search(info.filename):
                continue
            interno = f"{nombre}/{info.filename}"
            try:
                datos = zf.read(info)
            except RuntimeError as exc:  # cifrado
                res.incidencia(
                    ERROR,
                    f"No se pudo leer (¿protegido con contraseña?): {exc}",
                    archivo=interno,
                )
                continue
            except (zipfile.BadZipFile, OSError, NotImplementedError) as exc:
                res.incidencia(
                    ERROR, f"No se pudo descomprimir: {exc}", archivo=interno
                )
                continue
            ruta = PurePosixPath(info.filename)
            sub = hijo.hijo(*ruta.parent.parts)
            res.fusionar(_leer_bytes(interno, datos, sub, profundidad + 1, ruta.name))
    return res


def _leer_rar(
    nombre: str, contenido: bytes, ctx: Contexto, profundidad: int
) -> ResultadoCarga:
    res = ResultadoCarga(archivo=nombre)
    try:
        import rarfile
    except ImportError:  # pragma: no cover
        res.incidencia(
            ERROR, "Para leer RAR instale el paquete 'rarfile' (pip install rarfile)"
        )
        return res
    if profundidad >= PROFUNDIDAD_MAXIMA:
        res.incidencia(
            ERROR,
            f"Paquetes anidados con más de {PROFUNDIDAD_MAXIMA} niveles; no se procesa",
        )
        return res
    try:
        rf = rarfile.RarFile(io.BytesIO(contenido))
    except rarfile.Error as exc:
        res.incidencia(ERROR, f"RAR dañado o inválido: {exc}")
        return res
    hijo = ctx.hijo(PurePosixPath(nombre).stem)
    with rf:
        for info in rf.infolist():
            if info.is_dir() or _IGNORAR.search(info.filename):
                continue
            interno = f"{nombre}/{info.filename}"
            try:
                datos = rf.read(info)
            except rarfile.Error as exc:
                res.incidencia(
                    ERROR,
                    f"No se pudo descomprimir del RAR ({exc.__class__.__name__}: {exc}). Los RAR comprimidos "
                    "requieren la herramienta 'unrar', 'unar', '7z' o 'bsdtar' instalada en el servidor.",
                    archivo=interno,
                )
                continue
            ruta = PurePosixPath(info.filename)
            sub = hijo.hijo(*ruta.parent.parts)
            res.fusionar(_leer_bytes(interno, datos, sub, profundidad + 1, ruta.name))
    return res


# ---------------------------------------------------------------------------
# Resolución 3374 de 2000
# ---------------------------------------------------------------------------


def _parsear_3374(nombre: str, det: Deteccion, meta: dict, res: ResultadoCarga) -> None:
    esquema = ESQUEMAS_3374[det.tipo]
    for num, linea in enumerate(det.texto.splitlines(), start=1):
        if not linea.strip():
            continue
        valores = linea.split(",")
        if len(valores) != esquema.num_campos:
            res.incidencia(
                ERROR,
                f"Línea con {len(valores)} campos; el archivo {esquema.codigo} ({esquema.descripcion}) "
                f"de la Resolución 3374 de 2000 exige {esquema.num_campos}. Registro excluido.",
                linea=num,
                valor=linea[:200],
            )
            continue
        registro = {campo: v.strip() for campo, v in zip(esquema.campos, valores)}
        valido = True
        for campo in esquema.campos_obligatorios:
            if not registro[campo]:
                res.incidencia(
                    ERROR,
                    "Campo obligatorio vacío. Registro excluido.",
                    linea=num,
                    campo=campo,
                )
                valido = False
        for campo in esquema.campos_numericos:
            try:
                a_decimal(registro[campo], separador_decimal=".")
            except ValorInvalido:
                res.incidencia(
                    ERROR,
                    "Valor numérico inválido. Registro excluido.",
                    linea=num,
                    campo=campo,
                    valor=registro[campo],
                )
                valido = False
        for campo in esquema.campos_fecha:
            try:
                parse_fecha(registro[campo])
            except ValueError:
                res.incidencia(
                    ADVERTENCIA,
                    "Fecha inválida (se esperaba dd/mm/aaaa)",
                    linea=num,
                    campo=campo,
                    valor=registro[campo],
                )
        if valido:
            registro.update(meta)
            registro["_tipo"] = esquema.codigo
            registro["_linea"] = num
            registro["_ubicacion"] = f"línea {num}"
            res.agregar(esquema.codigo, registro)


def _validar_control(res: ResultadoCarga) -> None:
    """Compara el 'Total de registros' del archivo CT con lo leído."""
    lineas_por_archivo: dict[tuple, int] = {}
    for a in res.archivos:
        if a.get("version") == VERSION_3374 and a.get("lineas") is not None:
            stem = PurePosixPath(a["archivo"]).stem.upper()
            lineas_por_archivo[
                (a.get("paquete"), str(PurePosixPath(a["archivo"]).parent), stem)
            ] = a["lineas"]
    for ct in res.registros.get("CT", []):
        codigo = ct.get("Código del archivo", "").upper()
        clave = (ct.get("_paquete"), str(PurePosixPath(ct["_archivo"]).parent), codigo)
        try:
            declarado = int(
                a_decimal(ct.get("Total de registros"), separador_decimal=".")
            )
        except (ValorInvalido, TypeError):
            continue
        if clave not in lineas_por_archivo:
            res.incidencia(
                ADVERTENCIA,
                f"El archivo de control referencia {codigo} pero no se encontró en el paquete",
                archivo=ct["_archivo"],
                linea=ct.get("_linea"),
                campo="Código del archivo",
                valor=codigo,
            )
        elif lineas_por_archivo[clave] != declarado:
            res.incidencia(
                ADVERTENCIA,
                f"El control declara {declarado} registros para {codigo} pero el archivo tiene {lineas_por_archivo[clave]}",
                archivo=ct["_archivo"],
                linea=ct.get("_linea"),
                campo="Total de registros",
                valor=str(declarado),
            )


# ---------------------------------------------------------------------------
# Resolución 2275 de 2023
# ---------------------------------------------------------------------------


def _vacio(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _parsear_2275(nombre: str, det: Deteccion, meta: dict, res: ResultadoCarga) -> None:
    documentos = (
        det.contenido_json
        if isinstance(det.contenido_json, list)
        else [det.contenido_json]
    )
    for di, doc in enumerate(documentos):
        prefijo = f"[{di}]" if len(documentos) > 1 else ""
        if _vacio(doc.get("numFactura")):
            res.incidencia(
                ERROR,
                "JSON sin numFactura. Factura excluida.",
                campo=f"{prefijo}numFactura",
            )
            continue
        raiz = {k: doc.get(k) for k in LLAVES_RAIZ_2275 if k != "usuarios"}
        res.agregar(
            "transaccion",
            {
                **raiz,
                **meta,
                "_tipo": "transaccion",
                "_linea": None,
                "_ubicacion": prefijo or "raíz",
            },
        )
        usuarios = doc.get("usuarios")
        if not isinstance(usuarios, list):
            res.incidencia(
                ERROR,
                "'usuarios' debe ser una lista. Factura sin detalle.",
                campo=f"{prefijo}usuarios",
            )
            continue
        for ui, usuario in enumerate(usuarios):
            ubic_u = f"{prefijo}usuarios[{ui}]"
            if not isinstance(usuario, dict):
                res.incidencia(
                    ERROR, "Usuario inválido (no es objeto JSON)", campo=ubic_u
                )
                continue
            datos_usuario = {
                f"usuarios.{k}": usuario.get(k)
                for k in LLAVES_USUARIO_2275
                if k != "servicios"
            }
            res.agregar(
                "usuarios",
                {
                    "numFactura": doc.get("numFactura"),
                    **datos_usuario,
                    **meta,
                    "_tipo": "usuarios",
                    "_linea": None,
                    "_ubicacion": ubic_u,
                },
            )
            servicios = usuario.get("servicios") or {}
            if not isinstance(servicios, dict):
                res.incidencia(
                    ERROR, "'servicios' debe ser un objeto", campo=f"{ubic_u}.servicios"
                )
                continue
            for servicio, items in servicios.items():
                esquema = ESQUEMAS_2275.get(servicio)
                if esquema is None:
                    res.incidencia(
                        ADVERTENCIA,
                        f"Tipo de servicio desconocido '{servicio}' (se ignora)",
                        campo=f"{ubic_u}.servicios.{servicio}",
                    )
                    continue
                if items is None:
                    continue
                if not isinstance(items, list):
                    res.incidencia(
                        ERROR,
                        "Debe ser una lista",
                        campo=f"{ubic_u}.servicios.{servicio}",
                    )
                    continue
                for si, item in enumerate(items):
                    ubic = f"{ubic_u}.servicios.{servicio}[{si}]"
                    if not isinstance(item, dict):
                        res.incidencia(
                            ERROR, "Registro inválido (no es objeto JSON)", campo=ubic
                        )
                        continue
                    valido = True
                    for campo in esquema.campos_obligatorios:
                        if _vacio(item.get(campo)):
                            res.incidencia(
                                ERROR,
                                "Campo obligatorio vacío o ausente. Registro excluido.",
                                campo=f"{ubic}.{campo}",
                            )
                            valido = False
                    for campo in esquema.campos_numericos:
                        try:
                            a_decimal(item.get(campo), separador_decimal=".")
                        except ValorInvalido:
                            res.incidencia(
                                ERROR,
                                "Valor numérico inválido. Registro excluido.",
                                campo=f"{ubic}.{campo}",
                                valor=str(item.get(campo)),
                            )
                            valido = False
                    if esquema.campo_fecha and not _vacio(
                        item.get(esquema.campo_fecha)
                    ):
                        try:
                            parse_fecha(item.get(esquema.campo_fecha))
                        except ValueError:
                            res.incidencia(
                                ADVERTENCIA,
                                "Fecha inválida",
                                campo=f"{ubic}.{esquema.campo_fecha}",
                                valor=str(item.get(esquema.campo_fecha)),
                            )
                    if not valido:
                        continue
                    plano = {
                        k: v for k, v in item.items() if not isinstance(v, (dict, list))
                    }
                    res.agregar(
                        servicio,
                        {
                            "numFactura": doc.get("numFactura"),
                            **datos_usuario,
                            **plano,
                            **meta,
                            "_tipo": servicio,
                            "_linea": None,
                            "_ubicacion": ubic,
                        },
                    )

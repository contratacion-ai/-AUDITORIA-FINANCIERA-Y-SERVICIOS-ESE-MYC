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
from . import progreso
from .fechas import MESES, _sin_tildes, anio_de_nombre, mes_de_nombre, parse_fecha
from .modelos import ADVERTENCIA, ERROR, VERSION_2275, VERSION_3374, VERSION_NO_DETERMINADA, Registro, ResultadoCarga

MAGIC_ZIP = b"PK\x03\x04"
MAGIC_ZIP_VACIO = b"PK\x05\x06"
MAGIC_RAR = b"Rar!\x1a\x07"
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


# Sufijo que agrega Google Drive a las descargas: "Carpeta-20261027T135526Z-1-001"
_RE_SUFIJO_DRIVE = re.compile(r"-\d{8}T\d{6}Z(-\d+)*$", re.IGNORECASE)
_RE_ANIO_O_MES = re.compile(r"(?<!\d)20\d{2}[-_ .]?(0[1-9]|1[0-2])?(?!\d)")


def _parece_documento(nombre: str) -> bool:
    """Carpetas con nombre de factura, documento o código (HSA202213, FE1001, 810010000101): no son sedes."""
    return bool(re.search(r"\d{4,}", _RE_ANIO_O_MES.sub(" ", _RE_SUFIJO_DRIVE.sub("", nombre))))


def _limpiar_sede(nombre: str) -> str:
    nombre = _RE_SUFIJO_DRIVE.sub("", nombre)
    texto = _RE_ANIO_O_MES.sub(" ", nombre)
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
        if _parece_documento(parte):
            continue  # p. ej. una carpeta por factura dentro de la carpeta del hospital
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


def _registrar_archivo(res, nombre, ctx, meta, version, tipo, motivo, lineas=None):
    res.archivos.append({
        "archivo": nombre,
        "paquete": ctx.paquete,
        "version": version,
        "tipo": tipo,
        "lineas": lineas,
        "registros": res.total_registros,
        "errores": sum(1 for i in res.incidencias if i.severidad == ERROR),
        "motivo": motivo,
        "sede_ruta": meta.get("_sede_ruta"),
        "mes_ruta": meta.get("_mes_ruta"),
    })


def _leer_bytes(
    nombre: str,
    contenido: bytes,
    ctx: Contexto,
    profundidad: int,
    nombre_corto: str | None = None,
) -> ResultadoCarga:
    """Lee un archivo físico de RIPS en cualquier formato, reconocido por su contenido."""
    from . import formatos

    nombre_corto = nombre_corto or PurePosixPath(nombre).name
    clase = formatos.clasificar(nombre_corto, contenido)
    if clase in formatos.PAQUETES:
        return _leer_paquete(nombre, contenido, clase, ctx, profundidad)

    res = ResultadoCarga(archivo=nombre)
    meta = {
        "_archivo": nombre,
        "_paquete": ctx.paquete,
        "_sede_asignada": ctx.sede_asignada,
        "_hospital_asignado": ctx.hospital_asignado,
    }
    meta.update(inferir_de_ruta(ctx.componentes))

    if clase in (formatos.BINARIO, formatos.DOCUMENTO_OFFICE, formatos.VACIO):
        motivo = {"vacio": "Archivo vacío"}.get(clase, "No es un archivo de datos (por ejemplo PDF, imagen o Word)")
        res.incidencia(ADVERTENCIA, f"Archivo ignorado: {motivo}")
        _registrar_archivo(res, nombre, ctx, meta, "-", "IGNORADO", motivo)
        return res

    if clase in formatos.HOJAS_CALCULO:
        _parsear_tabla_rips(nombre, nombre_corto, contenido, clase, meta, res)
        return res

    det = detectar(nombre_corto, contenido)
    meta["_version"] = det.version
    if det.version == VERSION_3374:
        _parsear_3374(nombre, det, meta, res)
    elif det.version == VERSION_2275:
        _parsear_2275(nombre, det, meta, res)
    else:
        # No es RIPS oficial: se intenta como tabla con encabezados (CSV/TXT/JSON/XML exportado)
        _parsear_tabla_rips(nombre, nombre_corto, contenido, clase, meta, res, motivo_previo=det.motivo)
        return res
    lineas = len([ln for ln in (det.texto or "").splitlines() if ln.strip()]) if det.texto is not None else None
    _registrar_archivo(res, nombre, ctx, meta, det.version, det.tipo, det.motivo, lineas)
    return res


def _leer_paquete(nombre: str, contenido: bytes, clase: str, ctx: Contexto, profundidad: int) -> ResultadoCarga:
    from .formatos import FormatoNoLegible, expandir_paquete

    res = ResultadoCarga(archivo=nombre)
    if profundidad >= PROFUNDIDAD_MAXIMA:
        res.incidencia(ERROR, f"Paquetes anidados con más de {PROFUNDIDAD_MAXIMA} niveles; no se procesa")
        return res
    hijo = ctx.hijo(PurePosixPath(nombre).stem)
    miembros = miembros_rar(nombre, contenido, res) if clase == "rar" else expandir_paquete(nombre, contenido, clase)
    leidos = 0
    try:
        for ruta_txt, datos in miembros:
            if _IGNORAR.search(ruta_txt):
                continue
            leidos += 1
            progreso.detalle(f"Leyendo RIPS: {PurePosixPath(nombre).name} → {ruta_txt} "
                             f"({leidos:,} archivos leídos del paquete)".replace(",", "."))
            ruta = PurePosixPath(ruta_txt.replace("\\", "/"))
            sub = hijo.hijo(*ruta.parent.parts)
            res.fusionar(_leer_bytes(f"{nombre}/{ruta}", datos, sub, profundidad + 1, ruta.name))
    except FormatoNoLegible as exc:
        res.incidencia(ERROR, str(exc))
    return res


_RAR_CONFIGURADO = False


def _configurar_herramientas_rar(rarfile) -> None:
    """En Windows usa las herramientas que ya tenga el equipo (sin instalar nada):
    UnRAR de WinRAR, 7-Zip o el ``tar.exe`` (libarchive) incluido en Windows 10/11."""
    global _RAR_CONFIGURADO
    if _RAR_CONFIGURADO or os.name != "nt":
        return
    _RAR_CONFIGURADO = True
    carpetas = [os.environ.get(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    for base in filter(None, carpetas):
        unrar = Path(base) / "WinRAR" / "UnRAR.exe"
        if unrar.exists():
            rarfile.UNRAR_TOOL = str(unrar)
        siete = Path(base) / "7-Zip" / "7z.exe"
        if siete.exists():
            rarfile.SEVENZIP_TOOL = str(siete)
    rarfile.BSDTAR_TOOL = "tar"


def miembros_rar(nombre: str, contenido: bytes, res: ResultadoCarga):
    """Itera (ruta, bytes) de un RAR; los archivos que no se pueden extraer se reportan y se omiten."""
    try:
        import rarfile

        _configurar_herramientas_rar(rarfile)
    except ImportError:  # pragma: no cover
        res.incidencia(ERROR, "Para leer RAR instale el paquete 'rarfile' (pip install rarfile)")
        return
    try:
        rf = rarfile.RarFile(io.BytesIO(contenido))
    except rarfile.Error as exc:
        res.incidencia(ERROR, f"RAR dañado o inválido: {exc}")
        return
    with rf:
        for info in rf.infolist():
            if info.is_dir():
                continue
            try:
                datos = rf.read(info)
            except rarfile.Error as exc:
                res.incidencia(
                    ERROR,
                    f"No se pudo descomprimir del RAR ({exc.__class__.__name__}: {exc}). Los RAR comprimidos "
                    "requieren 'unrar', 'unar', '7z' o 'bsdtar' en el equipo (en Windows: WinRAR, 7-Zip o el "
                    "tar.exe de Windows 10/11). También puede descomprimirlo y cargar los archivos o un ZIP.",
                    archivo=f"{nombre}/{info.filename}",
                )
                continue
            yield info.filename, datos


# ---------------------------------------------------------------------------
# RIPS en tabla (Excel, ODS, CSV, JSON o XML exportados con encabezados)
# ---------------------------------------------------------------------------

_COLUMNAS_2275 = {"numfactura", "codconsulta", "codprocedimiento", "codtecnologiasalud", "vrservicio", "codprestador"}
_COLUMNAS_3374 = {"numero_de_la_factura", "codigo_de_consulta", "codigo_del_procedimiento", "codigo_del_medicamento",
                  "codigo_del_servicio", "valor_de_la_consulta", "valor_del_procedimiento"}
_CATEGORIA_POR_COLUMNA = [
    ({"codconsulta", "codigo_de_consulta", "valor_de_la_consulta"}, "CONSULTA"),
    ({"codprocedimiento", "codigo_del_procedimiento", "valor_del_procedimiento"}, "PROCEDIMIENTO"),
    ({"cantidadmedicamento", "codigo_del_medicamento", "valor_total_de_medicamento", "vrunitmedicamento", "cums",
      "codigo_cums"}, "MEDICAMENTO"),
    ({"cantidados", "codigo_del_servicio", "valor_total_del_material_e_insumo", "vrunitos"}, "OTRO_SERVICIO"),
]
_CATEGORIA_POR_NOMBRE = [
    (re.compile(r"(^|[^A-Z])(AC|CONSULTA)", re.I), "CONSULTA"),
    (re.compile(r"(^|[^A-Z])(AP|PROCEDIMIENTO)", re.I), "PROCEDIMIENTO"),
    (re.compile(r"(^|[^A-Z])(AM|MEDICAMENTO|FARMACIA)", re.I), "MEDICAMENTO"),
    (re.compile(r"(^|[^A-Z])(AT|OTROS?.?SERVICIO|INSUMO)", re.I), "OTRO_SERVICIO"),
]


def _categoria_tabla(columnas_norm: set[str], *nombres: str) -> str | None:
    for claves, categoria in _CATEGORIA_POR_COLUMNA:
        if columnas_norm & claves:
            return categoria
    for nombre in nombres:
        if not nombre:
            continue
        for patron, categoria in _CATEGORIA_POR_NOMBRE:
            if patron.search(PurePosixPath(str(nombre)).stem):
                return categoria
    return None


def _parsear_tabla_rips(nombre, nombre_corto, contenido, clase, meta, res, motivo_previo=None) -> None:
    """RIPS exportados como tabla. Si una hoja no tiene encabezado pero sus filas tienen la estructura
    de la Resolución 3374 (AF, AC, AM...), se procesan como esos archivos."""
    from .facturacion import _leer_un_archivo, normalizar_nombre
    from .formatos import FormatoNoLegible, leer_tablas, tabla_a_lineas_csv

    try:
        hojas = leer_tablas(nombre_corto, contenido, clase)
    except FormatoNoLegible as exc:
        mensaje = f"Versión de RIPS NO DETERMINADA; archivo no procesado. {motivo_previo or ''} {exc}".strip()
        res.incidencia(ERROR, mensaje)
        _registrar_archivo(res, nombre, _ctx_de(meta), meta, VERSION_NO_DETERMINADA, None, mensaje)
        return

    procesadas = 0
    for hoja, crudo in hojas:
        etiqueta = f"{nombre}" + (f" [{hoja}]" if hoja and len(hojas) > 1 else "")
        # 1) ¿Hoja con filas de estructura 3374 sin encabezado?
        texto = tabla_a_lineas_csv(crudo)
        det = detectar(str(hoja or nombre_corto), texto) if texto else None
        if det and det.version == VERSION_3374 and not _tiene_encabezado(crudo):
            sub = ResultadoCarga(archivo=etiqueta)
            _parsear_3374(etiqueta, det, {**meta, "_archivo": etiqueta, "_version": det.version}, sub)
            res.fusionar(sub)
            _registrar_archivo(res, etiqueta, _ctx_de(meta), meta, det.version, det.tipo,
                               f"Hoja de cálculo con estructura {det.tipo}: {det.motivo}")
            procesadas += 1
            continue
        # 2) Tabla con encabezados
        sub = ResultadoCarga(archivo=etiqueta)
        buffer = _hoja_como_csv(crudo)
        leido = _leer_un_archivo(etiqueta, buffer, None, "auto", None, None, None)
        registros = leido.registros.get("facturacion", [])
        if not registros:
            mensaje = ("Versión de RIPS NO DETERMINADA; no se procesó. "
                       + (f"{motivo_previo} " if motivo_previo and len(hojas) == 1 else "")
                       + "Tampoco es una tabla con columnas de factura, código y valor.")
            sub.incidencia(ERROR if len(hojas) == 1 else ADVERTENCIA, mensaje)
            res.fusionar(sub)
            _registrar_archivo(res, etiqueta, _ctx_de(meta), meta, VERSION_NO_DETERMINADA, None, mensaje)
            continue
        columnas_norm = {normalizar_nombre(c) for c in registros[0] if not str(c).startswith("_")}
        if columnas_norm & _COLUMNAS_2275:
            version = VERSION_2275.replace("(JSON)", "(en tabla)")
        elif columnas_norm & _COLUMNAS_3374:
            version = VERSION_3374.replace("(TXT)", "(en tabla)")
        else:
            version = VERSION_NO_DETERMINADA + " (tabla sin estructura oficial)"
            sub.incidencia(ADVERTENCIA, "La tabla no tiene la estructura oficial de RIPS (3374 ni 2275): se procesa por "
                                        "las columnas reconocidas de factura, código, cantidad y valor.")
        categoria = _categoria_tabla(columnas_norm, hoja, nombre_corto)
        ctx_hoja = {**meta, "_archivo": etiqueta, "_version": version, "_tipo": "tabla", "_categoria": categoria}
        for r in registros:
            # datos comunes de la hoja compartidos (los propios de la fila tienen prioridad)
            for llave in ("_archivo", "_hoja"):
                r.pop(llave, None)
            r["_ctx"] = ctx_hoja
            sub.agregar("tabla", r)
        for inc in leido.incidencias:
            inc.archivo = etiqueta
        sub.incidencias.extend(leido.incidencias)
        res.fusionar(sub)
        _registrar_archivo(res, etiqueta, _ctx_de(meta), meta, version, "TABLA" + (f" {categoria}" if categoria else ""),
                           "Tabla con encabezados")
        procesadas += 1


def _es_fila_titulos(valores) -> bool:
    """Una línea sin dígitos en el número de factura ni en ningún campo numérico es de títulos."""
    from .facturacion import _es_encabezado

    return _es_encabezado(valores) or not any(any(ch.isdigit() for ch in v) for v in valores)


def _ctx_de(meta: dict) -> Contexto:
    return Contexto(paquete=meta.get("_paquete"))


def _tiene_encabezado(crudo) -> bool:
    from .facturacion import _es_encabezado

    return any(_es_encabezado(crudo.iloc[i].tolist()) for i in range(min(30, len(crudo))))


def _hoja_como_csv(crudo) -> bytes:
    """Serializa la hoja a CSV con ';' conservando valores exactos para reutilizar el lector tabular."""
    import csv as _csv

    from .formatos import tabla_a_lineas_csv  # noqa: F401 (misma convención de celdas)

    salida = io.StringIO()
    escritor = _csv.writer(salida, delimiter=";", quotechar='"', quoting=_csv.QUOTE_MINIMAL, lineterminator="\n")
    for fila in crudo.itertuples(index=False, name=None):
        celdas = []
        for v in fila:
            if v is None or (isinstance(v, float) and v != v):
                celdas.append("")
            elif isinstance(v, float):
                celdas.append(str(int(v)) if v.is_integer() else repr(v))
            elif hasattr(v, "strftime"):
                celdas.append(v.strftime("%Y-%m-%d"))
            else:
                celdas.append(str(v))
        escritor.writerow(celdas)
    return salida.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# Resolución 3374 de 2000
# ---------------------------------------------------------------------------


def _parsear_3374(nombre: str, det: Deteccion, meta: dict, res: ResultadoCarga) -> None:
    esquema = ESQUEMAS_3374[det.tipo]
    for num, linea in enumerate(det.texto.splitlines(), start=1):
        if not linea.strip():
            continue
        if num % 20000 == 0:
            progreso.detalle(f"Leyendo RIPS: {PurePosixPath(nombre).name}: {num:,} líneas".replace(",", "."))
        valores = linea.split(",")
        if num == 1 and _es_fila_titulos(valores):
            res.incidencia(ADVERTENCIA, "Primera línea con títulos de columna: se omite", linea=num)
            continue
        if len(valores) != esquema.num_campos:
            res.incidencia(
                ERROR,
                f"Línea con {len(valores)} campos; el archivo {esquema.codigo} ({esquema.descripcion}) "
                f"de la Resolución 3374 de 2000 exige {esquema.num_campos}. Registro excluido.",
                linea=num,
                valor=linea[:200],
            )
            continue
        registro = Registro(zip(esquema.campos, map(str.strip, valores)))
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
            registro["_ctx"] = meta  # datos comunes del archivo (compartidos, no copiados)
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


_CAMPOS_2275: dict[str, tuple[str, ...]] = {}


def _campos_2275(esquema) -> tuple[str, ...]:
    campos = {esquema.campo_codigo, esquema.campo_valor, esquema.campo_cantidad, esquema.campo_valor_unitario,
              esquema.campo_fecha, "codPrestador", "nomTecnologiaSalud", "grupoServicios",
              "modalidadGrupoServicioTecSal", "codServicio", "numAutorizacion", "tipoDocumentoIdentificacion",
              "numDocumentoIdentificacion", "consecutivo", "valorPagoModerador",
              *esquema.campos_codigos_adicionales, *esquema.campos_obligatorios}
    _CAMPOS_2275[esquema.servicio] = tuple(sorted(c for c in campos if c))
    return _CAMPOS_2275[esquema.servicio]


def _parsear_2275(nombre: str, det: Deteccion, meta: dict, res: ResultadoCarga) -> None:
    meta_json = {**meta, "_linea": None}
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
        res.agregar("transaccion", Registro(raiz, _ctx=meta_json, _tipo="transaccion", _ubicacion=prefijo or "raíz"))
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
            # Datos del usuario compartidos por todos sus servicios (no se copian en cada uno)
            datos_usuario = {f"usuarios.{k}": usuario.get(k) for k in LLAVES_USUARIO_2275 if k != "servicios"}
            datos_usuario["numFactura"] = doc.get("numFactura")
            res.agregar("usuarios", Registro(_usuario=datos_usuario, _ctx=meta_json, _tipo="usuarios",
                                             _ubicacion=ubic_u))
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
                    # Solo los campos que usa la conciliación (con su nombre original): menos memoria
                    conservar = _CAMPOS_2275.get(servicio) or _campos_2275(esquema)
                    registro = Registro((k, item[k]) for k in conservar if k in item)
                    registro["_usuario"] = datos_usuario
                    registro["_ctx"] = meta_json
                    registro["_tipo"] = servicio
                    registro["_ubicacion"] = ubic
                    res.agregar(servicio, registro)

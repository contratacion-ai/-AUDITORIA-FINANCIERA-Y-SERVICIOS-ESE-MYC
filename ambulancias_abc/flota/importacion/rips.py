"""Lector de RIPS: extrae las líneas de traslados asistenciales y de atención
prehospitalaria (APH).

Formatos aceptados (se pueden mezclar dentro de un ZIP):

* JSON de la Resolución 2275 de 2023: una factura (objeto) o varias (lista).
  Se revisan ``usuarios[].servicios.otrosServicios[]`` y ``procedimientos[]``.
* Archivos planos de la Resolución 3374 de 2000, separados por coma:
  ``AF`` (facturas: prestador, número y fechas), ``AT`` (otros servicios,
  donde tipo de servicio 2 = traslados) y ``AP`` (procedimientos, con ámbito
  1 ambulatorio, 2 hospitalario, 3 urgencias). ``US`` no se lee: los datos
  personales no son necesarios.

Clasificación de cada línea (la primera regla que aplica):

1. El código está en el catálogo de códigos de traslado de la aplicación
   (Administración › Códigos de traslado), con su categoría TRASLADO o APH.
2. El nombre del servicio contiene "PREHOSPITALARI" o empieza por "APH",
   o la autorización es "APH"                                        -> APH
3. El nombre contiene "TRASLADO" o "AMBULANCIA"                     -> TRASLADO
4. Archivo AT con tipo de servicio 2 (traslados)                     -> TRASLADO

Las líneas que no cumplen ninguna regla no se extraen.

Cargues de varios meses u hospitales (consolidados):

* Un ZIP puede traer carpetas por mes ("01. Enero", "08. Agosto"...) y ZIP
  dentro de ZIP. El mes de cada línea es el de su fecha de servicio; el de la
  carpeta se conserva como referencia (mes del envío).
* Cada carpeta es un envío: sus AT/AP se cruzan con el AF de la misma carpeta.
* Si una misma línea (factura, usuario, código, fecha, valor) aparece en
  varios archivos (p. ej. la factura repetida en las carpetas de agosto y
  septiembre), se toma una sola vez y se informa.
* ``cobertura``: facturas por prefijo, prestador y mes del periodo que
  declaran (AF: mes de la fecha inicial y de la final; JSON: mes de servicio
  de sus líneas). Con ella la auditoría sabe qué hospital y mes tienen RIPS cargados.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath

from . import hallazgo, normalizar, normalizar_cups
from .facturacion_his import hash_documento

FUENTE = "RIPS"
TRASLADO, APH = "TRASLADO", "APH"
AMBITO_AP = {"1": "Ambulatorio", "2": "Hospitalario", "3": "Urgencias"}
MAX_ZIP_BYTES = 1024 * 1024 * 1024
EXT_IGNORADAS = {".xml", ".pdf", ".xlsx", ".xls", ".csv", ".png", ".jpg"}
MESES = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6, "JULIO": 7, "AGOSTO": 8,
    "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12,
}


def prefijo_factura(numero) -> str:
    """Letras iniciales del número de factura (FHJD15037 -> FHJD)."""
    m = re.match(r"\s*([A-Za-z]+)", str(numero or ""))
    return m.group(1).upper() if m else ""


def mes_de_carpeta(ruta: str) -> int | None:
    """Mes indicado en el nombre de la carpeta ("08. Agosto", "SEPTIEMBRE 2026"); None si no hay."""
    for parte in reversed(PurePosixPath(ruta).parts[:-1]):
        nom = normalizar(parte)
        for nombre, num in MESES.items():
            if nombre in nom:
                return num
    return None


def _carpeta(nombre: str) -> str:
    return str(PurePosixPath(nombre).parent)


def clasificar(codigo, nombre="", autorizacion="", tipo_servicio="", catalogo: dict | None = None):
    """Devuelve (categoría, regla) o (None, '')."""
    catalogo = catalogo or {}
    cod = normalizar_cups(codigo)
    if cod in catalogo:
        return catalogo[cod], "catálogo de códigos"
    nom = normalizar(nombre)
    if "PREHOSPITALARI" in nom or nom.startswith("APH") or normalizar(autorizacion) == "APH":
        return APH, "texto 'prehospitalaria/APH'"
    if "TRASLADO" in nom or "AMBULANCIA" in nom:
        return TRASLADO, "texto 'traslado/ambulancia'"
    if str(tipo_servicio).strip() == "2":
        return TRASLADO, "AT tipo de servicio 2"
    return None, ""


def _fecha(v):
    if not v:
        return None
    s = str(v).strip()
    for fmt, largo in (("%Y-%m-%d", 10), ("%d/%m/%Y", 10)):
        try:
            return dt.datetime.strptime(s[:largo], fmt).date()
        except ValueError:
            continue
    return None


def _dec(v, defecto=None):
    if v in (None, ""):
        return defecto
    try:
        return Decimal(str(v).replace(",", ".").strip())
    except InvalidOperation:
        return defecto


def _texto(contenido: bytes) -> str:
    for cod in ("utf-8-sig", "latin-1"):
        try:
            return contenido.decode(cod)
        except UnicodeDecodeError:
            continue
    return contenido.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# JSON (Res. 2275 de 2023)
# ---------------------------------------------------------------------------


def _leer_json(nombre: str, contenido: bytes, sal: str, catalogo: dict, salida: dict):
    try:
        datos = json.loads(_texto(contenido))
    except json.JSONDecodeError as e:
        salida["hallazgos"].append(hallazgo(nombre, "archivo", "FALTANTE", f"JSON inválido: {e}", "ALTA"))
        return
    facturas = datos if isinstance(datos, list) else [datos]
    for factura in facturas:
        if not isinstance(factura, dict):
            continue
        num_factura = str(factura.get("numFactura") or "").strip()
        if not num_factura:
            salida["hallazgos"].append(hallazgo(nombre, "factura", "FALTANTE", "Factura RIPS sin numFactura; se omite", "ALTA"))
            continue
        salida["facturas"].add(num_factura)
        for usuario in factura.get("usuarios") or []:
            tipo_doc = usuario.get("tipoDocumentoIdentificacion", "")
            doc_hash = hash_documento(tipo_doc, usuario.get("numDocumentoIdentificacion"), sal)
            servicios = usuario.get("servicios") or {}
            lineas = []
            for os_ in servicios.get("otrosServicios") or []:
                lineas.append({
                    "codigo": os_.get("codTecnologiaSalud"), "nombre": os_.get("nomTecnologiaSalud") or "",
                    "cantidad": os_.get("cantidadOS") or 1, "valor_unitario": os_.get("vrUnitOS"),
                    "valor": os_.get("vrServicio"), "fecha": os_.get("fechaSuministroTecnologia"),
                    "consecutivo": os_.get("consecutivo"), "autorizacion": os_.get("numAutorizacion") or "",
                    "cod_prestador": os_.get("codPrestador") or "", "tipo": "otrosServicios", "tipo_servicio": "",
                    "ambito": "",
                })
            for pr in servicios.get("procedimientos") or []:
                lineas.append({
                    "codigo": pr.get("codProcedimiento"), "nombre": "", "cantidad": None,
                    "valor_unitario": pr.get("vrServicio"), "valor": pr.get("vrServicio"),
                    "fecha": pr.get("fechaInicioAtencion"), "consecutivo": pr.get("consecutivo"),
                    "autorizacion": pr.get("numAutorizacion") or "", "cod_prestador": pr.get("codPrestador") or "",
                    "tipo": "procedimientos", "tipo_servicio": "", "ambito": "",
                })
            for ln in lineas:
                salida["lineas_revisadas"] += 1
                _cobertura(salida, num_factura, ln["cod_prestador"], _fecha(ln["fecha"]))
                _agregar(nombre, num_factura, tipo_doc, doc_hash, ln, catalogo, salida)


# ---------------------------------------------------------------------------
# Archivos planos (Res. 3374 de 2000)
# ---------------------------------------------------------------------------


def _filas_txt(contenido: bytes):
    lector = csv.reader(io.StringIO(_texto(contenido)), delimiter=",")
    return [[c.strip() for c in fila] for fila in lector if fila and any(c.strip() for c in fila)]


def _cobertura(salida, num_factura, cod_prestador, fecha):
    if fecha:
        salida["cobertura"][(prefijo_factura(num_factura), str(cod_prestador or ""), f"{fecha:%Y-%m}")] += 1


def _cobertura_periodo(salida, num_factura, cod_prestador, inicio, fin):
    """Mes inicial y mes final del periodo de la factura (AF). No se cuentan los
    meses intermedios: una fecha de inicio mal digitada (p. ej. 2022 en una
    factura de 2026) haría parecer cubiertos años enteros."""
    for fecha in {f"{d:%Y-%m}" for d in (inicio, fin) if d}:
        salida["cobertura"][(prefijo_factura(num_factura), str(cod_prestador or ""), fecha)] += 1


def _incompleta(salida, nombre, tipo, n, campos, esperados):
    """Agrupa las líneas mal formadas por archivo (un hallazgo por archivo, no por línea)."""
    salida["_incompletas"][(nombre, tipo, esperados)].append((n, campos))


def _leer_txt(archivos: dict[str, bytes], sal: str, catalogo: dict, salida: dict):
    """archivos: nombre -> contenido. Cada carpeta es un envío (AF + AT + AP ...)."""
    por_carpeta: dict[str, dict] = defaultdict(dict)  # carpeta -> factura -> (prestador, exp., inicio, final)
    global_af: dict[str, tuple] = {}
    carpetas_factura: dict[str, set] = defaultdict(set)
    for nombre, contenido in archivos.items():
        if PurePosixPath(nombre).name.upper().startswith("AF"):
            for n, f in enumerate(_filas_txt(contenido), start=1):
                if len(f) < 8:
                    _incompleta(salida, nombre, "AF", n, len(f), 17)
                    continue
                datos = (f[0], _fecha(f[5]), _fecha(f[6]), _fecha(f[7]))
                por_carpeta[_carpeta(nombre)][f[4]] = datos
                global_af.setdefault(f[4], datos)
                if f[4] not in salida["facturas"]:
                    _cobertura_periodo(salida, f[4], f[0], datos[2] or datos[1], datos[3] or datos[2] or datos[1])
                carpetas_factura[f[4]].add(_carpeta(nombre))
                salida["facturas"].add(f[4])
    raras = sorted(
        k for k, (_, exp, ini, fin) in global_af.items()
        if ini and ((fin and ini > fin) or (exp and (exp - ini).days > 366))
    )
    if raras:
        salida["hallazgos"].append(hallazgo(
            "RIPS", "AF fecha inicio/final", "INCONSISTENCIA",
            f"{len(raras)} facturas con periodo de atención inconsistente (inicio más de un año antes de la expedición "
            f"o posterior a la fecha final). Ejemplos: {', '.join(raras[:10])}", "MEDIA"))
    repetidas = {k: v for k, v in carpetas_factura.items() if len(v) > 1}
    if repetidas:
        ejemplos = ", ".join(sorted(repetidas)[:10])
        carpetas = sorted({c for v in repetidas.values() for c in v})
        salida["hallazgos"].append(hallazgo(
            "RIPS", "AF", "DUPLICADO",
            f"{len(repetidas)} facturas aparecen en más de un envío/carpeta ({'; '.join(carpetas)[:300]}). "
            f"Ejemplos: {ejemplos}. Sus líneas de traslado se cargan una sola vez.", "MEDIA"))
        salida["facturas_repetidas"] = len(repetidas)

    def af_de(nombre, factura):
        return por_carpeta.get(_carpeta(nombre), {}).get(factura) or global_af.get(factura)

    for nombre, contenido in archivos.items():
        base = PurePosixPath(nombre).name.upper()
        if base.startswith("AT"):
            for n, f in enumerate(_filas_txt(contenido), start=1):
                if len(f) < 11:
                    _incompleta(salida, nombre, "AT", n, len(f), 11)
                    continue
                salida["lineas_revisadas"] += 1
                af = af_de(nombre, f[0])
                fecha = (af[2] or af[1]) if af else None  # AT no trae fecha: inicio del periodo de la factura
                ln = {
                    "codigo": f[6], "nombre": f[7], "cantidad": f[8] or 1, "valor_unitario": f[9], "valor": f[10],
                    "fecha": fecha.isoformat() if fecha else None, "consecutivo": f"AT{n}", "autorizacion": f[4],
                    "cod_prestador": f[1], "tipo": "AT", "tipo_servicio": f[5], "ambito": "",
                    "fecha_aproximada": True,
                }
                _agregar(nombre, f[0], f[2], hash_documento(f[2], f[3], sal), ln, catalogo, salida)
        elif base.startswith("AP"):
            for n, f in enumerate(_filas_txt(contenido), start=1):
                if len(f) < 15:
                    _incompleta(salida, nombre, "AP", n, len(f), 15)
                    continue
                salida["lineas_revisadas"] += 1
                ln = {
                    "codigo": f[6], "nombre": "", "cantidad": None, "valor_unitario": f[14], "valor": f[14],
                    "fecha": f[4], "consecutivo": f"AP{n}", "autorizacion": f[5], "cod_prestador": f[1],
                    "tipo": "AP", "tipo_servicio": "", "ambito": AMBITO_AP.get(f[7], ""),
                }
                _agregar(nombre, f[0], f[2], hash_documento(f[2], f[3], sal), ln, catalogo, salida)


def _agregar(nombre, num_factura, tipo_doc, doc_hash, ln, catalogo, salida):
    categoria, regla = clasificar(ln["codigo"], ln["nombre"], ln["autorizacion"], ln["tipo_servicio"], catalogo)
    if categoria is None:
        return
    cod = normalizar_cups(ln["codigo"])
    fecha = _fecha(ln["fecha"])
    valor = _dec(ln["valor"])
    ubic = f"factura {num_factura} / {ln['tipo']} {ln['consecutivo']}"
    if fecha is None or valor is None:
        salida["hallazgos"].append(hallazgo(nombre, ubic, "FALTANTE", "Línea de traslado/APH sin fecha o valor; se omite", "ALTA"))
        return
    obs = []
    if ln.get("fecha_aproximada"):
        obs.append("fecha tomada del periodo de la factura (AF): el archivo AT no trae fecha")
    cantidad = _dec(ln["cantidad"], Decimal(1))
    salida["registros"].append({
        # Llave por contenido (no por número de línea): la misma línea en dos
        # archivos o carpetas es la misma. ``_deduplicar`` agrega la ocurrencia.
        "clave_contenido": (str(num_factura), doc_hash[:16], cod, fecha.isoformat(), f"{valor:.2f}", f"{cantidad:.2f}"),
        "carpeta": _carpeta(nombre),
        "mes_carpeta": mes_de_carpeta(nombre),
        "numero_factura": num_factura,
        "rips_consecutivo": str(ln["consecutivo"] or "")[:20],
        "codigo_cups": cod,
        "codigo_cups_original": str(ln["codigo"] or "")[:30],
        "descripcion": str(ln["nombre"] or "")[:250],
        "cantidad": cantidad,
        # AP y procedimientos no traen cantidad: no se compara con la facturación
        "cantidad_reportada": None if ln["cantidad"] is None else cantidad,
        "valor_unitario": _dec(ln["valor_unitario"]),
        "valor_facturado": valor,
        "fecha_servicio": fecha,
        "tipo_documento": str(tipo_doc or "")[:5],
        "documento_hash": doc_hash,
        "autorizacion": str(ln["autorizacion"] or "")[:60],
        "cod_prestador": str(ln["cod_prestador"] or ""),
        "ambito": ln["ambito"],
        "categoria": categoria,
        "regla": regla,
        "archivo": nombre,
        "observaciones": obs,
    })


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------


def _deduplicar(salida):
    """Una línea repetida en varios archivos se toma una vez (la mayor cantidad de
    repeticiones en un mismo archivo, por si una factura trae dos traslados iguales)."""
    grupos = defaultdict(list)
    for r in salida["registros"]:
        grupos[r["clave_contenido"]].append(r)
    finales, repetidas = [], 0
    for clave, regs in grupos.items():
        por_archivo = Counter(r["archivo"] for r in regs)
        archivo, veces = max(por_archivo.items(), key=lambda kv: (kv[1], -len(kv[0])))
        elegidos = [r for r in regs if r["archivo"] == archivo][:veces]
        repetidas += len(regs) - len(elegidos)
        factura, doc16, cod, fecha, valor, _ = clave
        for k, r in enumerate(elegidos, start=1):
            r["llave_externa"] = f"RIPS2|{factura}|{doc16}|{cod}|{fecha.replace('-', '')}|{valor}|{k}"[:200]
            finales.append(r)
    salida["registros"] = finales
    salida["lineas_repetidas"] = repetidas
    if repetidas:
        salida["hallazgos"].append(hallazgo(
            "RIPS", "traslados/APH", "DUPLICADO",
            f"{repetidas} líneas de traslado/APH repetidas en más de un archivo o carpeta: se toman una sola vez.", "MEDIA"))


def leer_rips_archivos(archivos: list[tuple[str, bytes]], sal: str, catalogo: dict) -> dict:
    """archivos: [(nombre, contenido)] de tipo .json, .txt o .zip (con carpetas y ZIP anidados)."""
    salida = {
        "registros": [], "hallazgos": [], "facturas": set(), "lineas_revisadas": 0, "cobertura": Counter(),
        "_incompletas": defaultdict(list), "ignorados": 0, "facturas_repetidas": 0, "lineas_repetidas": 0,
    }
    planos: dict[str, bytes] = {}
    total_zip = [0]

    def procesar(nombre: str, contenido: bytes):
        ext = PurePosixPath(nombre).suffix.lower()
        if ext == ".zip":
            try:
                zf = zipfile.ZipFile(io.BytesIO(contenido))
            except zipfile.BadZipFile:
                salida["hallazgos"].append(hallazgo(nombre, "archivo", "FALTANTE", "ZIP dañado", "ALTA"))
                return
            total_zip[0] += sum(i.file_size for i in zf.infolist())
            if total_zip[0] > MAX_ZIP_BYTES:
                salida["hallazgos"].append(hallazgo(nombre, "archivo", "FALTANTE", "ZIP de más de 1 GB descomprimido: divídalo", "ALTA"))
                return
            for info in zf.infolist():
                if info.is_dir() or PurePosixPath(info.filename).name.startswith((".", "__MACOSX")) or "__MACOSX" in info.filename:
                    continue
                if PurePosixPath(info.filename).suffix.lower() in (".json", ".txt", ".zip"):
                    procesar(f"{nombre}/{info.filename}", zf.read(info))
                else:
                    salida["ignorados"] += 1
        elif ext == ".json":
            _leer_json(nombre, contenido, sal, catalogo, salida)
        elif ext == ".txt":
            planos[nombre] = contenido
        elif ext in EXT_IGNORADAS:
            salida["ignorados"] += 1
        else:
            salida["hallazgos"].append(hallazgo(nombre, "archivo", "FALTANTE", "Extensión no soportada (use .json, .txt o .zip)", "MEDIA"))

    for nombre, contenido in archivos:
        procesar(nombre, contenido)
    if planos:
        _leer_txt(planos, sal, catalogo, salida)
    for (nombre, tipo, esperados), lineas in salida.pop("_incompletas").items():
        muestra = ", ".join(str(n) for n, _ in lineas[:8])
        campos = sorted({c for _, c in lineas})
        salida["hallazgos"].append(hallazgo(
            nombre, f"líneas {muestra}{'…' if len(lineas) > 8 else ''}", "FALTANTE",
            f"{len(lineas)} líneas {tipo} con {', '.join(map(str, campos))} campos (se esperan {esperados}); no se leen. "
            "Suelen ser registros con el número de factura incompleto.", "MEDIA"))
    _deduplicar(salida)
    salida["facturas"] = len(salida["facturas"])
    return salida


def leer_rips(contenido, sal: str, codigos_traslado, nombre_fuente: str = FUENTE) -> dict:
    """Compatibilidad: un solo JSON y un conjunto (o dict) de códigos."""
    catalogo = codigos_traslado if isinstance(codigos_traslado, dict) else {c: TRASLADO for c in codigos_traslado}
    if hasattr(contenido, "read"):
        contenido = contenido.read()
    if isinstance(contenido, str):
        contenido = contenido.encode("utf-8")
    nombre = nombre_fuente if nombre_fuente.lower().endswith((".json", ".zip", ".txt")) else f"{nombre_fuente}.json"
    return leer_rips_archivos([(nombre, contenido)], sal, catalogo)

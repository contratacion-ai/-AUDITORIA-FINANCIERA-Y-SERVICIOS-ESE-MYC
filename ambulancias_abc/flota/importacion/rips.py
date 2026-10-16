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
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath

from . import hallazgo, normalizar, normalizar_cups
from .facturacion_his import hash_documento

FUENTE = "RIPS"
TRASLADO, APH = "TRASLADO", "APH"
AMBITO_AP = {"1": "Ambulatorio", "2": "Hospitalario", "3": "Urgencias"}
MAX_ZIP_BYTES = 300 * 1024 * 1024


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
                    "codigo": pr.get("codProcedimiento"), "nombre": "", "cantidad": 1,
                    "valor_unitario": pr.get("vrServicio"), "valor": pr.get("vrServicio"),
                    "fecha": pr.get("fechaInicioAtencion"), "consecutivo": pr.get("consecutivo"),
                    "autorizacion": pr.get("numAutorizacion") or "", "cod_prestador": pr.get("codPrestador") or "",
                    "tipo": "procedimientos", "tipo_servicio": "", "ambito": "",
                })
            for ln in lineas:
                salida["lineas_revisadas"] += 1
                _agregar(nombre, num_factura, tipo_doc, doc_hash, ln, catalogo, salida)


# ---------------------------------------------------------------------------
# Archivos planos (Res. 3374 de 2000)
# ---------------------------------------------------------------------------


def _filas_txt(contenido: bytes):
    lector = csv.reader(io.StringIO(_texto(contenido)), delimiter=",")
    return [[c.strip() for c in fila] for fila in lector if fila and any(c.strip() for c in fila)]


def _leer_txt(archivos: dict[str, bytes], sal: str, catalogo: dict, salida: dict):
    """archivos: nombre -> contenido, de un mismo envío (AF + AT + AP ...)."""
    facturas = {}  # número -> (prestador, fecha expedición, fecha inicio, fecha final)
    for nombre, contenido in archivos.items():
        if PurePosixPath(nombre).name.upper().startswith("AF"):
            for n, f in enumerate(_filas_txt(contenido), start=1):
                if len(f) < 8:
                    salida["hallazgos"].append(hallazgo(nombre, f"línea {n}", "FALTANTE", f"AF con {len(f)} campos (se esperan 17)", "MEDIA"))
                    continue
                facturas[f[4]] = (f[0], _fecha(f[5]), _fecha(f[6]), _fecha(f[7]))
                salida["facturas"].add(f[4])

    for nombre, contenido in archivos.items():
        base = PurePosixPath(nombre).name.upper()
        if base.startswith("AT"):
            for n, f in enumerate(_filas_txt(contenido), start=1):
                if len(f) < 11:
                    salida["hallazgos"].append(hallazgo(nombre, f"línea {n}", "FALTANTE", f"AT con {len(f)} campos (se esperan 11)", "MEDIA"))
                    continue
                salida["lineas_revisadas"] += 1
                af = facturas.get(f[0])
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
                    salida["hallazgos"].append(hallazgo(nombre, f"línea {n}", "FALTANTE", f"AP con {len(f)} campos (se esperan 15)", "MEDIA"))
                    continue
                salida["lineas_revisadas"] += 1
                ln = {
                    "codigo": f[6], "nombre": "", "cantidad": 1, "valor_unitario": f[14], "valor": f[14],
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
    salida["registros"].append({
        "llave_externa": f"RIPS|{num_factura}|{doc_hash[:16]}|{cod}|{ln['tipo']}|{ln['consecutivo']}",
        "numero_factura": num_factura,
        "rips_consecutivo": str(ln["consecutivo"] or "")[:20],
        "codigo_cups": cod,
        "codigo_cups_original": str(ln["codigo"] or "")[:30],
        "descripcion": str(ln["nombre"] or "")[:250],
        "cantidad": _dec(ln["cantidad"], Decimal(1)),
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


def leer_rips_archivos(archivos: list[tuple[str, bytes]], sal: str, catalogo: dict) -> dict:
    """archivos: [(nombre, contenido)] de tipo .json, .txt o .zip."""
    salida = {"registros": [], "hallazgos": [], "facturas": set(), "lineas_revisadas": 0}
    planos: dict[str, bytes] = {}

    def procesar(nombre: str, contenido: bytes):
        ext = PurePosixPath(nombre).suffix.lower()
        if ext == ".zip":
            try:
                zf = zipfile.ZipFile(io.BytesIO(contenido))
            except zipfile.BadZipFile:
                salida["hallazgos"].append(hallazgo(nombre, "archivo", "FALTANTE", "ZIP dañado", "ALTA"))
                return
            if sum(i.file_size for i in zf.infolist()) > MAX_ZIP_BYTES:
                salida["hallazgos"].append(hallazgo(nombre, "archivo", "FALTANTE", "ZIP demasiado grande descomprimido", "ALTA"))
                return
            for info in zf.infolist():
                if info.is_dir() or PurePosixPath(info.filename).suffix.lower() not in (".json", ".txt"):
                    continue
                procesar(f"{nombre}/{info.filename}", zf.read(info))
        elif ext == ".json":
            _leer_json(nombre, contenido, sal, catalogo, salida)
        elif ext == ".txt":
            planos[nombre] = contenido
        else:
            salida["hallazgos"].append(hallazgo(nombre, "archivo", "FALTANTE", "Extensión no soportada (use .json, .txt o .zip)", "MEDIA"))

    for nombre, contenido in archivos:
        procesar(nombre, contenido)
    if planos:
        _leer_txt(planos, sal, catalogo, salida)
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

"""Lector de RIPS en JSON (estructura de la Resolución 2275 de 2023).

Toma de cada factura las líneas de ``otrosServicios`` y ``procedimientos``
cuyo código esté en la lista de códigos de traslado configurada. Se acepta
un objeto (una factura) o una lista de facturas.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from . import hallazgo, normalizar_cups
from .facturacion_his import hash_documento

FUENTE = "RIPS JSON"


def _fecha(v):
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(s[: len(dt.datetime.now().strftime(fmt))], fmt).date()
        except ValueError:
            continue
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def leer_rips(contenido, sal: str, codigos_traslado: set[str], nombre_fuente: str = FUENTE) -> dict:
    if isinstance(contenido, (bytes, str)):
        datos = json.loads(contenido)
    else:
        datos = json.load(contenido)
    facturas = datos if isinstance(datos, list) else [datos]
    registros, hallazgos = [], []

    for factura in facturas:
        num_factura = str(factura.get("numFactura") or "").strip()
        if not num_factura:
            hallazgos.append(hallazgo(nombre_fuente, "factura", "FALTANTE", "Factura RIPS sin numFactura; se omite", "ALTA"))
            continue
        for usuario in factura.get("usuarios") or []:
            tipo_doc = usuario.get("tipoDocumentoIdentificacion", "")
            doc_hash = hash_documento(tipo_doc, usuario.get("numDocumentoIdentificacion"), sal)
            servicios = usuario.get("servicios") or {}
            lineas = []
            for os_ in servicios.get("otrosServicios") or []:
                lineas.append(
                    {
                        "codigo": os_.get("codTecnologiaSalud"),
                        "descripcion": os_.get("nomTecnologiaSalud") or "",
                        "cantidad": os_.get("cantidadOS") or 1,
                        "valor_unitario": os_.get("vrUnitOS"),
                        "valor": os_.get("vrServicio"),
                        "fecha": os_.get("fechaSuministroTecnologia"),
                        "consecutivo": os_.get("consecutivo"),
                        "autorizacion": os_.get("numAutorizacion") or "",
                        "cod_prestador": os_.get("codPrestador") or "",
                        "tipo": "otrosServicios",
                    }
                )
            for pr in servicios.get("procedimientos") or []:
                lineas.append(
                    {
                        "codigo": pr.get("codProcedimiento"),
                        "descripcion": "",
                        "cantidad": 1,
                        "valor_unitario": pr.get("vrServicio"),
                        "valor": pr.get("vrServicio"),
                        "fecha": pr.get("fechaInicioAtencion"),
                        "consecutivo": pr.get("consecutivo"),
                        "autorizacion": pr.get("numAutorizacion") or "",
                        "cod_prestador": pr.get("codPrestador") or "",
                        "tipo": "procedimientos",
                    }
                )
            for ln in lineas:
                cod = normalizar_cups(ln["codigo"])
                if cod not in codigos_traslado:
                    continue
                fecha = _fecha(ln["fecha"])
                ubic = f"factura {num_factura} / {ln['tipo']} consecutivo {ln['consecutivo']}"
                if fecha is None or ln["valor"] is None:
                    hallazgos.append(hallazgo(nombre_fuente, ubic, "FALTANTE", "Línea de traslado sin fecha o valor; se omite", "ALTA"))
                    continue
                registros.append(
                    {
                        "llave_externa": f"RIPS|{num_factura}|{doc_hash[:16]}|{cod}|{ln['tipo']}|{ln['consecutivo']}",
                        "numero_factura": num_factura,
                        "rips_consecutivo": str(ln["consecutivo"] or ""),
                        "codigo_cups": cod,
                        "codigo_cups_original": str(ln["codigo"]),
                        "descripcion": str(ln["descripcion"])[:250],
                        "cantidad": Decimal(str(ln["cantidad"])),
                        "valor_unitario": Decimal(str(ln["valor_unitario"])) if ln["valor_unitario"] is not None else None,
                        "valor_facturado": Decimal(str(ln["valor"])),
                        "fecha_servicio": fecha,
                        "tipo_documento": tipo_doc,
                        "documento_hash": doc_hash,
                        "autorizacion": str(ln["autorizacion"])[:60],
                        "cod_prestador": str(ln["cod_prestador"]),
                        "observaciones": [],
                    }
                )
    return {"registros": registros, "hallazgos": hallazgos}

"""Resultado de la validación del Ministerio de Salud (MSPS) de cada factura: el CUV.

Al validar el RIPS JSON en el Mecanismo Único de Validación, el Ministerio
devuelve un archivo ``ResultadosMSPS_<factura>_ID<proceso>_A_CUV.txt`` (o ``_R_``
si fue rechazado), que la ESE guarda junto al JSON y al XML de la factura. Es
un JSON con:

* ``ResultState``: true = validado (con CUV), false = rechazado.
* ``NumFactura``, ``ProcesoId``, ``CodigoUnicoValidacion`` (CUV),
  ``FechaRadicacion``, ``ModalidadPago`` y ``PeriodoAtencion``.
* ``ResultadosValidacion``: lista de hallazgos con ``Clase`` (RECHAZADO o
  NOTIFICACION), ``Codigo``, ``Descripcion``, ``Observaciones`` y ``PathFuente``.

Con él se llena la columna VALIDACION de la plantilla del aplicativo de
auditoría (estado, CUV, fecha de radicación y número de hallazgos) y el
informe ``VALIDACION_MSPS.xlsx`` por factura.
"""

from __future__ import annotations

import json
import re
from collections import Counter

import pandas as pd

from .incidencias import RegistroIncidencias
from .lectura import contexto_desde_ruta, descubrir_archivos

_RE_NOMBRE = re.compile(r"(?i)^ResultadosMSPS_.*\.(txt|json)$")
COLUMNAS = ["SEDE", "MES_CARPETA", "FACTURA", "ESTADO", "CUV", "FECHA_RADICACION", "PROCESO_ID", "MODALIDAD_PAGO",
            "PERIODO_INICIO", "PERIODO_FIN", "RECHAZOS", "NOTIFICACIONES", "CODIGOS_RECHAZO", "CODIGOS_NOTIFICACION",
            "ARCHIVO"]


def es_resultado_msps(nombre: str) -> bool:
    return bool(_RE_NOMBRE.match(nombre.rsplit("/", 1)[-1]))


def leer_cuv(contenido: bytes) -> dict | None:
    """Datos del resultado de validación; None si el contenido no es uno."""
    for cod in ("utf-8-sig", "latin-1"):
        try:
            doc = json.loads(contenido.decode(cod))
            break
        except (UnicodeDecodeError, json.JSONDecodeError):
            doc = None
    if not isinstance(doc, dict) or "NumFactura" not in doc:
        return None
    hallazgos = [h for h in doc.get("ResultadosValidacion") or [] if isinstance(h, dict)]
    clase = Counter(str(h.get("Clase", "")).upper() for h in hallazgos)
    cod = {c: Counter(str(h.get("Codigo", "")) for h in hallazgos if str(h.get("Clase", "")).upper() == c)
           for c in ("RECHAZADO", "NOTIFICACION")}
    periodo = doc.get("PeriodoAtencion") or {}
    cuv = str(doc.get("CodigoUnicoValidacion") or "").strip()
    estado = "VALIDADA" if doc.get("ResultState") is True and cuv else "RECHAZADA"
    return {
        "factura": str(doc.get("NumFactura") or "").strip().upper(), "estado": estado, "cuv": cuv,
        "fecha_radicacion": str(doc.get("FechaRadicacion") or "")[:19].replace("T", " "),
        "proceso_id": str(doc.get("ProcesoId") or ""), "modalidad_pago": str(doc.get("ModalidadPago") or "").strip(" ."),
        "periodo_inicio": str(periodo.get("FechaInicio") or "")[:10], "periodo_fin": str(periodo.get("FechaFin") or "")[:10],
        "rechazos": clase.get("RECHAZADO", 0), "notificaciones": clase.get("NOTIFICACION", 0),
        "codigos_rechazo": ", ".join(f"{c} ({n})" for c, n in cod["RECHAZADO"].most_common(8)),
        "codigos_notificacion": ", ".join(f"{c} ({n})" for c, n in cod["NOTIFICACION"].most_common(8)),
    }


def texto_validacion(d: dict | None) -> str:
    """Valor de la columna VALIDACION del aplicativo para una factura."""
    if not d:
        return ""
    if d["estado"] == "VALIDADA":
        return (f"CUV MSPS VALIDADA {d['fecha_radicacion'][:10]} · CUV {d['cuv'][:12]}… · "
                f"{d['notificaciones']} notificaciones")
    return f"CUV MSPS RECHAZADA · {d['rechazos']} rechazos: {d['codigos_rechazo']}"


def indice_cuv(entradas) -> dict[str, dict]:
    """numFactura -> resultado de validación, para los ResultadosMSPS de ``entradas``.
    Si una factura tiene varios (re-envíos), prima el validado y luego el más reciente."""
    indice: dict[str, dict] = {}
    for fuente in descubrir_archivos(list(entradas), RegistroIncidencias()):
        if not es_resultado_msps(fuente.nombre):
            continue
        d = leer_cuv(fuente.contenido)
        if not d or not d["factura"]:
            continue
        ctx = contexto_desde_ruta(fuente.ruta)
        d = {**d, "archivo": fuente.ruta, "sede": ctx.get("hospital") or "", "mes_carpeta": ctx.get("periodo") or ""}
        previo = indice.get(d["factura"])
        if previo is None or _prioridad(d) > _prioridad(previo):
            indice[d["factura"]] = d
    return indice


def _prioridad(d: dict) -> tuple:
    return (d["estado"] == "VALIDADA", d["fecha_radicacion"])


def reporte(indice: dict[str, dict]) -> pd.DataFrame:
    filas = [{"SEDE": d["sede"], "MES_CARPETA": d["mes_carpeta"], "FACTURA": f, "ESTADO": d["estado"], "CUV": d["cuv"],
              "FECHA_RADICACION": d["fecha_radicacion"], "PROCESO_ID": d["proceso_id"],
              "MODALIDAD_PAGO": d["modalidad_pago"], "PERIODO_INICIO": d["periodo_inicio"],
              "PERIODO_FIN": d["periodo_fin"], "RECHAZOS": d["rechazos"], "NOTIFICACIONES": d["notificaciones"],
              "CODIGOS_RECHAZO": d["codigos_rechazo"], "CODIGOS_NOTIFICACION": d["codigos_notificacion"],
              "ARCHIVO": d["archivo"]} for f, d in sorted(indice.items())]
    return pd.DataFrame(filas, columns=COLUMNAS)


__all__ = ["es_resultado_msps", "indice_cuv", "leer_cuv", "reporte", "texto_validacion"]

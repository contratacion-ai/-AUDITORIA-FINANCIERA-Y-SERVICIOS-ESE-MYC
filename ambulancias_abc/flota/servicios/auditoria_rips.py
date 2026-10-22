"""Auditoría RIPS vs facturación HIS, mes a mes, por hospital y recorrido.

Cada traslado/APH del periodo queda en una de estas situaciones:

* ``ambos``: facturado en el HIS y reportado en RIPS con el mismo valor.
* ``diferencia``: facturado y reportado, pero con otro valor o cantidad en RIPS.
* ``solo_his``: facturado en el HIS y NO reportado en los RIPS de ese hospital y
  mes, aunque esos RIPS están cargados (falta en RIPS).
* ``solo_rips``: reportado en RIPS sin línea en la facturación HIS de ese mes,
  aunque la facturación del mes está cargada (falta en la facturación).
* ``sin_factura``: facturado en el HIS sin número de factura (prefactura aún
  no facturada): todavía no puede estar en RIPS; no es diferencia.
* ``rips_no_cargado`` / ``his_no_cargado``: no se puede auditar porque falta
  el otro archivo; no se cuentan como diferencia.

Cobertura de RIPS: hospital y mes que declaran las facturas de los RIPS
cargados (``LoteImportacion.detalle["cobertura"]``); para cargues anteriores sin
ese dato, el hospital y mes del cargue. Cobertura del HIS: meses con al menos
una línea de facturación HIS.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from ..models import LoteImportacion, Sede, Traslado

SITUACIONES = {
    "ambos": "Facturado y en RIPS",
    "diferencia": "Valor distinto en RIPS",
    "solo_his": "Facturado, falta en RIPS",
    "solo_rips": "En RIPS, falta en facturación",
    "sin_factura": "Prefactura sin número de factura",
    "rips_no_cargado": "RIPS no cargado",
    "his_no_cargado": "Facturación no cargada",
}
DISCREPANCIAS = ("diferencia", "solo_his", "solo_rips")


def cobertura_rips() -> set[tuple[str, str]]:
    cubiertos = set()
    for lote in LoteImportacion.objects.filter(tipo="RIPS").select_related("sede"):
        cob = (lote.detalle or {}).get("cobertura")
        if cob is not None:
            cubiertos.update((c, m) for c, m, *_ in cob)
        elif lote.sede_id and lote.mes:
            cubiertos.add((lote.sede.codigo, f"{lote.mes:%Y-%m}"))
    return cubiertos


def meses_his() -> set[str]:
    return {f"{m:%Y-%m}" for m in Traslado.objects.filter(origen_registro=Traslado.HIS).values_list("mes", flat=True).distinct()}


def situacion(t: Traslado, cubiertos: set, his: set) -> str:
    mes = f"{t.mes:%Y-%m}"
    if t.origen_registro == Traslado.RIPS:
        return "solo_rips" if mes in his else "his_no_cargado"
    if t.conciliado_rips:
        if t.valor_rips is not None and (t.valor_rips != t.valor_facturado or (t.cantidad_rips is not None and t.cantidad_rips != t.cantidad)):
            return "diferencia"
        return "ambos"
    if not t.numero_factura:
        return "sin_factura"
    return "solo_his" if (t.sede.codigo, mes) in cubiertos else "rips_no_cargado"


def _vacio():
    d = {"his_lineas": 0, "his_valor": 0.0, "rips_lineas": 0, "rips_valor": 0.0, "dif_valor": 0.0}
    for k in SITUACIONES:
        d[k] = 0
        d[f"{k}_valor"] = 0.0
    return d


def _sumar(d, t, sit):
    valor = float(t.valor_facturado)
    valor_rips = float(t.valor_rips) if t.valor_rips is not None else valor
    if t.origen_registro != Traslado.RIPS:
        d["his_lineas"] += 1
        d["his_valor"] += valor
    if sit in ("ambos", "diferencia", "solo_rips", "his_no_cargado"):
        d["rips_lineas"] += 1
        d["rips_valor"] += valor_rips
    d[sit] += 1
    d[f"{sit}_valor"] += valor if sit in ("solo_his", "rips_no_cargado", "sin_factura") else valor_rips
    if sit == "diferencia":
        d["dif_valor"] += valor_rips - valor


def _estado(d, cubierto: bool, his_cargado: bool) -> str:
    if not cubierto and not d["rips_lineas"]:
        return "Sin RIPS cargado"
    if not his_cargado:
        return "Sin facturación HIS cargada"
    if any(d[k] for k in DISCREPANCIAS):
        return "Con diferencias"
    return "Cuadra"


def auditoria_rips(desde: dt.date, hasta: dt.date, sede_codigo=None, categoria=None, limite_detalle=2000) -> dict:
    cubiertos = cobertura_rips()
    his = meses_his()
    qs = Traslado.objects.filter(mes__gte=desde, mes__lte=hasta).select_related("sede", "ruta", "ambulancia")
    if sede_codigo:
        qs = qs.filter(sede__codigo=sede_codigo)
    if categoria:
        qs = qs.filter(categoria=categoria)

    por_mes = defaultdict(_vacio)  # (sede_codigo, "AAAA-MM")
    por_ruta = defaultdict(_vacio)  # (sede_codigo, ruta_id | None)
    total = _vacio()
    detalle, n_detalle = [], 0
    sedes = {}
    rutas = {}
    for t in qs.order_by("sede__nombre", "mes", "fecha_servicio", "id").iterator():
        sit = situacion(t, cubiertos, his)
        mes = f"{t.mes:%Y-%m}"
        sedes[t.sede.codigo] = t.sede
        if t.ruta_id:
            rutas[t.ruta_id] = t.ruta
        for d in (por_mes[(t.sede.codigo, mes)], por_ruta[(t.sede.codigo, t.ruta_id)], total):
            _sumar(d, t, sit)
        if sit in DISCREPANCIAS:
            n_detalle += 1
            if len(detalle) < limite_detalle:
                detalle.append({"t": t, "situacion": sit, "texto": SITUACIONES[sit]})

    # Meses con RIPS cargados pero sin ninguna línea de traslado ni facturación: también se muestran.
    for codigo, mes in cubiertos:
        if (not sede_codigo or codigo == sede_codigo) and f"{desde:%Y-%m}" <= mes <= f"{hasta:%Y-%m}":
            por_mes[(codigo, mes)]
            if codigo not in sedes:
                sedes[codigo] = Sede.objects.filter(codigo=codigo).first()

    filas_mes = []
    for (codigo, mes), d in sorted(por_mes.items(), key=lambda kv: (sedes[kv[0][0]].nombre if sedes.get(kv[0][0]) else kv[0][0], kv[0][1])):
        d = dict(d)
        d.update(sede=sedes.get(codigo), mes=dt.date.fromisoformat(f"{mes}-01"), cubierto=(codigo, mes) in cubiertos,
                 his_cargado=mes in his)
        d["estado"] = _estado(d, d["cubierto"], d["his_cargado"])
        d["n_discrepancias"] = sum(d[k] for k in DISCREPANCIAS)
        filas_mes.append(d)
    filas_ruta = []
    for (codigo, ruta_id), d in sorted(
        por_ruta.items(), key=lambda kv: (sedes[kv[0][0]].nombre, str(rutas.get(kv[0][1], "~")))
    ):
        d = dict(d)
        d.update(sede=sedes[codigo], ruta=rutas.get(ruta_id))
        d["n_discrepancias"] = sum(d[k] for k in DISCREPANCIAS)
        filas_ruta.append(d)
    total["n_discrepancias"] = sum(total[k] for k in DISCREPANCIAS)
    return {
        "por_mes": filas_mes, "por_ruta": filas_ruta, "total": total, "detalle": detalle, "n_detalle": n_detalle,
        "situaciones": SITUACIONES,
    }

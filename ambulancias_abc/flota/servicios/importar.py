"""Persistencia de las importaciones (idempotentes: reimportar no duplica)."""

from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils.text import slugify

from ..importacion import hallazgo, normalizar
from ..importacion.anexo_tarifas import leer_anexo_traslados
from ..importacion.costos_ceco import leer_costos_ambulancias
from ..importacion.facturacion_his import leer_facturacion
from ..importacion.rips_json import leer_rips
from ..models import (
    CodigoTraslado,
    Consumo,
    LoteImportacion,
    Parametro,
    PartidaCostoIndirecto,
    TarifaRuta,
    Traslado,
)
from .comunes import ResolutorCatalogos, guardar_hallazgos, parametro, parametros


def _cerrar_lote(lote, nuevos, actualizados, omitidos, hallazgos, resumen=""):
    lote.nuevos, lote.actualizados, lote.omitidos = nuevos, actualizados, omitidos
    lote.hallazgos = guardar_hallazgos(hallazgos, lote)
    lote.resumen = resumen
    lote.save()
    return lote


def codigo_ruta(origen, destino) -> str:
    return slugify(f"{normalizar(origen)}-{normalizar(destino)}")[:160]


# ---------------------------------------------------------------------------
# Tarifas (Anexo 3)
# ---------------------------------------------------------------------------


def guardar_tarifas(datos: dict, usuario=None, archivo="ANEXOS DE TARIFAS 2026.xlsx") -> LoteImportacion:
    lote = LoteImportacion.objects.create(tipo="TARIFAS", archivo=archivo, usuario=usuario)
    nuevos = actualizados = 0
    with transaction.atomic():
        for r in datos["rutas"]:
            _, creado = TarifaRuta.objects.update_or_create(
                vigencia=r["vigencia"],
                codigo=codigo_ruta(r["origen"], r["destino"]),
                defaults={
                    "origen": normalizar(r["origen"]),
                    "destino": normalizar(r["destino"]),
                    "km": r["km"],
                    "valor_km": r["valor_km"],
                    "tarifa": r["tarifa"],
                    "ambito_geografico": r["ambito_geografico"],
                    "fila_fuente": r["fila"],
                    "observacion": r.get("observacion", "")[:300],
                },
            )
            nuevos += int(creado)
            actualizados += int(not creado)
        fuente = f"{archivo}, hoja {datos.get('hoja', 'Anexo 3')}"
        if datos.get("valor_km"):
            _actualizar_parametro("valor_km_tarifa", datos["valor_km"], fuente)
        if datos.get("tarifa_base") and isinstance(datos["tarifa_base"].get("valor"), (int, float)):
            _actualizar_parametro("tarifa_traslado_basico", datos["tarifa_base"]["valor"], fuente)
    return _cerrar_lote(lote, nuevos, actualizados, 0, datos["hallazgos"], f"{len(datos['rutas'])} rutas vigencia {datos['vigencia']}")


def importar_tarifas(archivo, usuario=None, vigencia=2026, nombre="") -> LoteImportacion:
    datos = leer_anexo_traslados(archivo, vigencia, nombre or "ANEXOS DE TARIFAS")
    return guardar_tarifas(datos, usuario, nombre or getattr(archivo, "name", str(archivo)))


def _actualizar_parametro(clave, valor, fuente):
    p, creado = Parametro.objects.get_or_create(
        clave=clave, defaults={"valor": valor, "descripcion": clave, "fuente": fuente}
    )
    if not creado and p.valor != Decimal(str(valor)):
        p.valor = valor
        p.fuente = fuente
        p.version += 1
        p.save()


# ---------------------------------------------------------------------------
# Costos del centro de costos (referencia de indirectos)
# ---------------------------------------------------------------------------


def guardar_costos_ceco(datos: dict, usuario=None, archivo="COSTOS AMBULANCIAS.xlsx") -> LoteImportacion:
    lote = LoteImportacion.objects.create(tipo="COSTOS_CECO", archivo=archivo, usuario=usuario)
    nuevos = actualizados = 0
    with transaction.atomic():
        for p in datos["partidas"]:
            _, creado = PartidaCostoIndirecto.objects.update_or_create(
                categoria=p["categoria"],
                concepto=p["concepto"],
                defaults={"valor_mensual": Decimal(str(p["valor_mensual"])), "fuente": f"{archivo} {p['ubicacion']}"},
            )
            nuevos += int(creado)
            actualizados += int(not creado)
        if datos.get("tripulacion"):
            dia = sum(t["valor_dia"] or 0 for t in datos["tripulacion"])
            _actualizar_parametro("tripulacion_dia_tabla_th", round(dia, 2), f"{archivo}, TABLA DE VALOR TH MYC")
    return _cerrar_lote(lote, nuevos, actualizados, 0, datos["hallazgos"], f"{len(datos['partidas'])} partidas mensuales")


def importar_costos_ceco(archivo, usuario=None, nombre="") -> LoteImportacion:
    datos = leer_costos_ambulancias(archivo, float(parametro("tripulacion_dia")), nombre or "COSTOS AMBULANCIAS.xlsx")
    return guardar_costos_ceco(datos, usuario, nombre or getattr(archivo, "name", str(archivo)))


# ---------------------------------------------------------------------------
# Traslados (facturación HIS y RIPS)
# ---------------------------------------------------------------------------


def es_por_km(cantidad: Decimal, valor_unitario, codigo: CodigoTraslado | None, pars) -> bool:
    """La línea se factura por kilómetro (y su cantidad son km) o con tarifa básica.

    Se decide por el valor unitario frente al manual tarifario; si no coincide
    con ninguno, por la modalidad del código; y en último caso por la cantidad.
    """
    if valor_unitario is not None:
        if pars["valor_km_tarifa"] and valor_unitario == pars["valor_km_tarifa"]:
            return True
        if pars["tarifa_traslado_basico"] and valor_unitario == pars["tarifa_traslado_basico"]:
            return False
    if codigo is not None:
        return codigo.modalidad == CodigoTraslado.POR_KM
    return cantidad > 1


def calcular_km(cantidad: Decimal, por_km: bool, pars) -> Decimal:
    """Km conducidos (driver ABC) de una línea facturada.

    * Por km: km facturados × factor ida y vuelta.
    * Tarifa básica: km_traslado_basico por cada unidad facturada.
    """
    if por_km:
        return (cantidad * pars["factor_ida_vuelta"]).quantize(Decimal("0.01"))
    return (pars["km_traslado_basico"] * max(cantidad, Decimal(1))).quantize(Decimal("0.01"))


def _aplicar_reglas_ingreso(traslados: list[Traslado]):
    """En un ingreso con tarifa básica y cobro por km, la línea básica no suma km
    (el recorrido ya está en la línea por km)."""
    por_ingreso: dict[str, list[Traslado]] = {}
    for t in traslados:
        if t.nro_ingreso:
            por_ingreso.setdefault(f"{t.sede_id}|{t.nro_ingreso}", []).append(t)
    for grupo in por_ingreso.values():
        if any(getattr(t, "_por_km", False) for t in grupo):
            for t in grupo:
                if not getattr(t, "_por_km", False) and t.km_recorridos:
                    t.km_recorridos = Decimal(0)
                    nota = "línea básica sin km: el recorrido se costea en la línea por km del mismo ingreso"
                    if nota not in t.observaciones_calidad:
                        t.observaciones_calidad = "; ".join(filter(None, [t.observaciones_calidad, nota]))


def importar_facturacion(archivo, usuario=None, nombre="") -> LoteImportacion:
    nombre = nombre or getattr(archivo, "name", str(archivo))
    codigos = {c.codigo: c for c in CodigoTraslado.objects.all()}
    base = {c for c, o in codigos.items() if o.modalidad == CodigoTraslado.BASE} or {"601T01"}
    datos = leer_facturacion(archivo, settings.SAL_DOCUMENTO, base, nombre)
    lote = LoteImportacion.objects.create(tipo="FACTURACION_HIS", archivo=nombre, usuario=usuario)
    res = ResolutorCatalogos()
    pars = parametros()
    nuevos = actualizados = omitidos = 0
    hallazgos = list(datos["hallazgos"])
    sin_ruta: dict[str, int] = {}
    sedes_desconocidas: dict[str, int] = {}
    tocados: list[Traslado] = []

    with transaction.atomic():
        existentes = {t.llave_externa: t for t in Traslado.objects.filter(llave_externa__in=[r["llave_externa"] for r in datos["registros"]])}
        for r in datos["registros"]:
            sede = res.sede(r["sede"])
            if sede is None:
                sedes_desconocidas[r["sede"]] = sedes_desconocidas.get(r["sede"], 0) + 1
                omitidos += 1
                continue
            ruta, motivo = (None, "")
            por_km = es_por_km(r["cantidad"], r["valor_unitario"], codigos.get(r["codigo_cups"]), pars)
            if por_km:
                ruta, motivo = res.ruta_por_km(sede, r["cantidad"], pars["tolerancia_km_ruta"])
                if ruta is None:
                    sin_ruta[f"{sede.origen_tarifa} {r['cantidad']} km"] = sin_ruta.get(f"{sede.origen_tarifa} {r['cantidad']} km", 0) + 1
                    r["observaciones"].append(motivo)
            if r["codigo_cups"] not in codigos:
                r["observaciones"].append(f"código {r['codigo_cups']} no está en el catálogo de traslados")
            t = existentes.get(r["llave_externa"])
            creado = t is None
            if creado:
                t = Traslado(llave_externa=r["llave_externa"], origen_registro=Traslado.HIS, creado_por=usuario)
            elif t.origen_registro == Traslado.MANUAL:
                omitidos += 1
                continue
            # Campos que el usuario corrige a mano (ambulancia, ruta) no se pisan.
            if creado or t.ruta_id is None:
                t.ruta = ruta
                t.ruta_inferida = ruta is not None
            for campo in (
                "fecha_servicio", "codigo_cups", "codigo_cups_original", "descripcion", "cantidad", "valor_unitario",
                "valor_facturado", "ambito", "unidad_funcional", "contrato", "eps_nombre", "eps_codigo", "regimen",
                "numero_factura", "prefactura", "nro_ingreso", "autorizacion", "tipo_documento", "documento_hash",
            ):
                setattr(t, campo, r[campo])
            t.sede = sede
            t.lote = lote
            t.km_recorridos = calcular_km(r["cantidad"], por_km, pars)
            t._por_km = por_km
            t.observaciones_calidad = "; ".join(r["observaciones"])[:2000]
            t.actualizado_por = usuario
            if not creado:
                t.version += 1
            tocados.append(t)
            nuevos += int(creado)
            actualizados += int(not creado)
        _aplicar_reglas_ingreso(tocados)
        for t in tocados:
            t.save()

    for s, n in sedes_desconocidas.items():
        hallazgos.append(hallazgo(nombre, f"SEDE '{s}'", "FALTANTE", f"{n} líneas con sede no registrada; se omitieron. Agregue el alias en Sedes.", "ALTA"))
    for clave, n in sin_ruta.items():
        hallazgos.append(
            hallazgo(nombre, clave, "INCONSISTENCIA", f"{n} líneas facturan {clave} y no hay una ruta única con esos km en el Anexo 3", "MEDIA")
        )
    r = datos["resumen"]
    return _cerrar_lote(
        lote, nuevos, actualizados, omitidos, hallazgos,
        f"{r['lineas']} líneas, ${r['valor_total']:,.0f}, mes principal {r['mes_principal']}",
    )


def importar_rips(contenido, usuario=None, nombre="RIPS.json") -> LoteImportacion:
    """Concilia RIPS con los traslados existentes; crea solo lo que no existe.

    Correspondencia: número de factura + código CUPS + valor (+ hash del
    documento cuando ambos lo tienen). Así una línea ya importada desde el HIS
    no se duplica: se marca conciliado_rips y se guarda el consecutivo RIPS.
    """
    codigos = set(CodigoTraslado.objects.filter(activo=True).values_list("codigo", flat=True))
    datos = leer_rips(contenido, settings.SAL_DOCUMENTO, codigos, nombre)
    lote = LoteImportacion.objects.create(tipo="RIPS", archivo=nombre, usuario=usuario)
    res = ResolutorCatalogos()
    pars = parametros()
    sedes_por_prestador = {s.codigo_prestador: s for s in res.sedes.values() if s.codigo_prestador}
    nuevos = conciliados = omitidos = 0
    hallazgos = list(datos["hallazgos"])
    with transaction.atomic():
        for r in datos["registros"]:
            if Traslado.objects.filter(llave_externa=r["llave_externa"]).exists():
                omitidos += 1
                continue
            candidatos = Traslado.objects.filter(
                numero_factura=r["numero_factura"], codigo_cups=r["codigo_cups"], valor_facturado=r["valor_facturado"], conciliado_rips=False
            )
            if r["documento_hash"]:
                con_doc = candidatos.filter(documento_hash=r["documento_hash"])
                candidatos = con_doc if con_doc.exists() else candidatos
            existente = candidatos.order_by("fecha_servicio", "id").first()
            if existente:
                existente.conciliado_rips = True
                existente.rips_consecutivo = r["rips_consecutivo"]
                existente.version += 1
                existente.save(update_fields=["conciliado_rips", "rips_consecutivo", "version", "actualizado"])
                conciliados += 1
                continue
            sede = sedes_por_prestador.get(r["cod_prestador"])
            if sede is None:
                hallazgos.append(
                    hallazgo(nombre, f"factura {r['numero_factura']}", "FALTANTE",
                             f"Traslado RIPS sin equivalente en la facturación HIS y prestador '{r['cod_prestador']}' sin sede asociada; no se crea.",
                             "ALTA")
                )
                omitidos += 1
                continue
            por_km = es_por_km(r["cantidad"], r["valor_unitario"], None, pars)
            ruta, _ = res.ruta_por_km(sede, r["cantidad"], pars["tolerancia_km_ruta"]) if por_km else (None, "")
            t = Traslado(
                llave_externa=r["llave_externa"],
                origen_registro=Traslado.RIPS,
                lote=lote,
                sede=sede,
                ruta=ruta,
                ruta_inferida=ruta is not None,
                conciliado_rips=True,
                creado_por=usuario,
                ambito="SIN ÁMBITO",
                **{k: r[k] for k in ("fecha_servicio", "codigo_cups", "codigo_cups_original", "descripcion", "cantidad",
                                     "valor_unitario", "valor_facturado", "numero_factura", "autorizacion",
                                     "tipo_documento", "documento_hash", "rips_consecutivo")},
            )
            t.km_recorridos = calcular_km(t.cantidad, por_km, pars)
            t.observaciones_calidad = "creado desde RIPS: no estaba en la facturación HIS"
            t.save()
            nuevos += 1
    return _cerrar_lote(lote, nuevos, conciliados, omitidos, hallazgos, f"{conciliados} conciliados con HIS, {nuevos} nuevos")


# ---------------------------------------------------------------------------
# Consumos (CSV)
# ---------------------------------------------------------------------------

COLUMNAS_CONSUMO = ["placa", "fecha", "tipo", "descripcion", "cantidad", "unidad", "valor", "odometro", "documento_soporte", "proveedor"]


def importar_consumos_csv(archivo, usuario=None, nombre="consumos.csv") -> LoteImportacion:
    """CSV separado por ';' o ',' con las columnas de COLUMNAS_CONSUMO.

    Llave: placa + fecha + tipo + documento_soporte (si el documento ya existe
    se actualiza en lugar de duplicar).
    """
    texto = archivo.read()
    if isinstance(texto, bytes):
        texto = texto.decode("utf-8-sig", errors="replace")
    dialecto = csv.Sniffer().sniff(texto.splitlines()[0], delimiters=";,")
    lector = csv.DictReader(io.StringIO(texto), dialect=dialecto)
    lote = LoteImportacion.objects.create(tipo="CONSUMOS", archivo=nombre, usuario=usuario)
    res = ResolutorCatalogos()
    tipos = dict(Consumo.TIPOS)
    nuevos = actualizados = omitidos = 0
    hallazgos = []
    with transaction.atomic():
        for n, fila in enumerate(lector, start=2):
            fila = {normalizar(k).lower(): (v or "").strip() for k, v in fila.items() if k}
            amb = res.ambulancia(fila.get("placa"))
            tipo = normalizar(fila.get("tipo"))
            try:
                fecha = dt.date.fromisoformat(fila.get("fecha", ""))
                valor = Decimal(fila.get("valor", "").replace(",", "."))
                cantidad = Decimal((fila.get("cantidad") or "0").replace(",", "."))
            except (ValueError, InvalidOperation):
                hallazgos.append(hallazgo(nombre, f"fila {n}", "FALTANTE", "Fecha (AAAA-MM-DD), valor o cantidad no válidos; se omite", "MEDIA"))
                omitidos += 1
                continue
            if amb is None or tipo not in tipos:
                hallazgos.append(hallazgo(nombre, f"fila {n}", "FALTANTE", f"Placa '{fila.get('placa')}' o tipo '{fila.get('tipo')}' no válidos; se omite", "MEDIA"))
                omitidos += 1
                continue
            doc = fila.get("documento_soporte", "")
            existente = (
                Consumo.objects.filter(ambulancia=amb, fecha=fecha, tipo=tipo, documento_soporte=doc).first() if doc else None
            )
            c = existente or Consumo(ambulancia=amb, fecha=fecha, tipo=tipo, documento_soporte=doc, creado_por=usuario)
            c.descripcion = fila.get("descripcion", "")[:200]
            c.cantidad = cantidad
            c.unidad = fila.get("unidad", "")[:20]
            c.valor = valor
            c.odometro = int(fila["odometro"]) if fila.get("odometro", "").isdigit() else None
            c.proveedor = fila.get("proveedor", "")[:120]
            c.lote = lote
            c.actualizado_por = usuario
            if existente:
                c.version += 1
            c.save()
            nuevos += int(existente is None)
            actualizados += int(existente is not None)
    return _cerrar_lote(lote, nuevos, actualizados, omitidos, hallazgos)

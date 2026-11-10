"""Convierte registros de RIPS y de facturación en "líneas conciliables".

Una línea conciliable tiene siempre las mismas columnas, sin importar la
fuente, y conserva el valor original del código y el nombre del campo de
donde salió (``campo_codigo``), para que el reporte muestre exactamente lo
que trae cada archivo.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pandas as pd

from .decimales import CERO, ValorInvalido, a_decimal
from .esquemas import ESQUEMAS_2275, ESQUEMAS_3374, TIPOS_CON_VALOR_3374
from .fechas import parse_fecha
from .modelos import ADVERTENCIA, Incidencia, ResultadoCarga

COLUMNAS_LINEA = [
    "origen",
    "archivo",
    "ubicacion",
    "version",
    "tipo_archivo",
    "categoria",
    "tipo_codigo",
    "factura",
    "factura_clave",
    "codigo",
    "codigo_clave",
    "campo_codigo",
    "descripcion",
    "cantidad",
    "valor",
    "valor_unitario",
    "fecha",
    "mes",
    "mes_origen",
    "sede",
    "hospital",
    "ambito",
    "servicio",
    "farmacia",
    "prestador",
    "documento",
]

SIN_SEDE = "SIN SEDE"
SIN_HOSPITAL = "SIN HOSPITAL"
SIN_MES = "SIN MES"

AMBITO_3374 = {"1": "AMBULATORIO", "2": "HOSPITALARIO", "3": "URGENCIAS"}

_RE_CUMS = re.compile(r"^\d{3,}-\d{1,3}$")
_RE_CUPS = re.compile(r"^[0-9A-Z]{6}(-\d{2})?$")


def clave_factura(valor, ignorar_ceros: bool = False) -> str:
    """Llave de emparejamiento de factura: mayúsculas, solo letras y dígitos.

    ``FE-000123``, ``fe 000123`` y ``FE000123`` producen ``FE000123``.
    Con ``ignorar_ceros`` también ``FE123``.
    """
    texto = re.sub(r"[^0-9A-Z]", "", str(valor or "").upper())
    if ignorar_ceros:
        texto = re.sub(r"(?<=[A-Z])0+(?=\d)|^0+(?=\d)", "", texto)
    return texto


def clave_codigo(valor) -> str:
    """Llave de código: mayúsculas y sin espacios (no altera guiones ni ceros)."""
    return re.sub(r"\s+", "", str(valor or "").upper())


def tipo_codigo(codigo: str, categoria: str | None) -> str:
    if categoria == "MEDICAMENTO" or _RE_CUMS.match(codigo or ""):
        return "CUMS"
    if categoria in ("CONSULTA", "PROCEDIMIENTO") or _RE_CUPS.match(codigo or ""):
        return "CUPS"
    return "OTRO"


def _resolver_catalogo(prestador, catalogo: dict) -> dict:
    if not prestador or not catalogo:
        return {}
    prestador = str(prestador).strip()
    if prestador in catalogo:
        return catalogo[prestador]
    if len(prestador) >= 10 and prestador[:10] in catalogo:
        return catalogo[prestador[:10]]
    return {}


def etiqueta(valor) -> str | None:
    """Sede/hospital comparables entre fuentes: mayúsculas, sin tildes ni espacios repetidos."""
    if valor is None or (isinstance(valor, float) and valor != valor):
        return None
    import unicodedata

    texto = "".join(
        c
        for c in unicodedata.normalize("NFD", str(valor))
        if unicodedata.category(c) != "Mn"
    )
    texto = re.sub(r"\s+", " ", texto).strip().upper()
    return texto or None


def _mes(fecha) -> str | None:
    return f"{fecha.year:04d}-{fecha.month:02d}" if fecha else None


def _fecha_segura(valor):
    try:
        return parse_fecha(valor)
    except ValueError:
        return None


def lineas_rips(
    carga: ResultadoCarga, catalogo: dict | None = None, ignorar_ceros: bool = False
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list]:
    """Devuelve (líneas conciliables, facturas RIPS, otros códigos, incidencias)."""
    catalogo = catalogo or {}
    incidencias: list[Incidencia] = []
    filas: list[dict] = []
    otros: list[dict] = []

    # Encabezados de factura: AF (3374) y raíz del JSON (2275)
    facturas: dict[str, dict] = {}
    for af in carga.registros.get("AF", []):
        clave = clave_factura(af["Número de la factura"], ignorar_ceros)
        facturas.setdefault(
            clave,
            {
                "factura_clave": clave,
                "factura": af["Número de la factura"],
                "archivo_rips": af["_archivo"],
                "version": af["_version"],
                "fecha_factura_rips": _fecha_segura(
                    af.get("Fecha de expedición de la factura")
                ),
                "prestador": af.get("Código del prestador de servicios de salud"),
                "entidad_administradora": af.get("Código entidad administradora"),
                "AF_valor_neto": a_decimal(
                    af.get("Valor neto a pagar por la entidad contratante"), ".", True
                ),
                "AF_copago": a_decimal(
                    af.get("Valor total del pago compartido (copago)"), ".", True
                ),
                "AF_comision": a_decimal(af.get("Valor de la comisión"), ".", True),
                "AF_descuentos": a_decimal(
                    af.get("Valor total de descuentos"), ".", True
                ),
                "_meta": af,
            },
        )
    for tr in carga.registros.get("transaccion", []):
        clave = clave_factura(tr["numFactura"], ignorar_ceros)
        facturas.setdefault(
            clave,
            {
                "factura_clave": clave,
                "factura": tr["numFactura"],
                "archivo_rips": tr["_archivo"],
                "version": tr["_version"],
                "fecha_factura_rips": None,
                "prestador": tr.get("numDocumentoIdObligado"),
                "entidad_administradora": None,
                "AF_valor_neto": None,
                "AF_copago": None,
                "AF_comision": None,
                "AF_descuentos": None,
                "tipoNota": tr.get("tipoNota"),
                "numNota": tr.get("numNota"),
                "_meta": tr,
            },
        )

    def contexto(reg: dict, prestador, fecha, factura_clave: str) -> dict:
        cat = _resolver_catalogo(prestador, catalogo)
        sede = (
            etiqueta(
                reg.get("_sede_asignada") or cat.get("sede") or reg.get("_sede_ruta")
            )
            or SIN_SEDE
        )
        hospital = (
            etiqueta(reg.get("_hospital_asignado") or cat.get("hospital"))
            or SIN_HOSPITAL
        )
        fecha_factura = facturas.get(factura_clave, {}).get("fecha_factura_rips")
        if reg.get("_mes_ruta"):
            mes, origen = reg["_mes_ruta"], "CARPETA"
        elif reg.get("_mes_num_ruta") and (fecha_factura or fecha):
            anio = (fecha_factura or fecha).year
            mes, origen = (
                f"{anio:04d}-{reg['_mes_num_ruta']:02d}",
                "CARPETA+AÑO_DE_FECHA",
            )
        elif fecha_factura:
            mes, origen = _mes(fecha_factura), "FECHA_FACTURA"
        elif fecha:
            mes, origen = _mes(fecha), "FECHA_ATENCION"
        else:
            mes, origen = SIN_MES, "SIN_DATO"
        return {"sede": sede, "hospital": hospital, "mes": mes, "mes_origen": origen}

    # --- Resolución 3374 de 2000
    for tipo in TIPOS_CON_VALOR_3374:
        esquema = ESQUEMAS_3374[tipo]
        for reg in carga.registros.get(tipo, []):
            factura = reg[esquema.campo_factura]
            fclave = clave_factura(factura, ignorar_ceros)
            fecha = (
                _fecha_segura(reg.get(esquema.campo_fecha))
                if esquema.campo_fecha
                else None
            )
            prestador = reg.get(esquema.campo_prestador)
            try:
                cantidad = (
                    a_decimal(reg.get(esquema.campo_cantidad), ".")
                    if esquema.campo_cantidad
                    else Decimal(1)
                )
                valor = a_decimal(reg.get(esquema.campo_valor), ".", vacio_es_cero=True)
                unitario = (
                    a_decimal(reg.get(esquema.campo_valor_unitario), ".")
                    if esquema.campo_valor_unitario
                    else None
                )
            except ValorInvalido:  # pragma: no cover - validado en el lector
                continue
            if cantidad is None:
                cantidad = Decimal(1)
                incidencias.append(
                    Incidencia(
                        reg["_archivo"],
                        ADVERTENCIA,
                        "Cantidad vacía: se asume 1",
                        reg["_linea"],
                        esquema.campo_cantidad,
                    )
                )
            ctx = contexto(
                reg,
                prestador,
                fecha or facturas.get(fclave, {}).get("fecha_factura_rips"),
                fclave,
            )
            codigo = reg[esquema.campo_codigo]
            filas.append(
                {
                    "origen": "RIPS",
                    "archivo": reg["_archivo"],
                    "ubicacion": reg["_ubicacion"],
                    "version": reg["_version"],
                    "tipo_archivo": tipo,
                    "categoria": esquema.categoria,
                    "tipo_codigo": tipo_codigo(codigo, esquema.categoria),
                    "factura": factura,
                    "factura_clave": fclave,
                    "codigo": codigo,
                    "codigo_clave": clave_codigo(codigo),
                    "campo_codigo": esquema.campo_codigo,
                    "descripcion": reg.get("Nombre genérico del medicamento")
                    or reg.get("Nombre del servicio"),
                    "cantidad": cantidad,
                    "valor": valor,
                    "valor_unitario": unitario,
                    "fecha": fecha,
                    **ctx,
                    "ambito": AMBITO_3374.get(
                        reg.get(esquema.campo_ambito or "", ""),
                        reg.get(esquema.campo_ambito or ""),
                    )
                    or None,
                    "servicio": esquema.categoria,
                    "farmacia": (
                        ctx["sede"] if esquema.categoria == "MEDICAMENTO" else None
                    ),
                    "prestador": prestador,
                    "documento": reg.get(
                        "Número de identificación del usuario en el sistema"
                    ),
                }
            )
            for campo in esquema.campos_codigos_adicionales:
                if reg.get(campo):
                    otros.append(
                        {
                            "campo": campo,
                            "codigo": reg[campo],
                            "tipo_archivo": tipo,
                            "factura": factura,
                            "sede": ctx["sede"],
                            "hospital": ctx["hospital"],
                            "mes": ctx["mes"],
                        }
                    )

    # --- Resolución 2275 de 2023
    for servicio, esquema in ESQUEMAS_2275.items():
        for reg in carga.registros.get(servicio, []):
            factura = reg["numFactura"]
            fclave = clave_factura(factura, ignorar_ceros)
            fecha = (
                _fecha_segura(reg.get(esquema.campo_fecha))
                if esquema.campo_fecha
                else None
            )
            prestador = reg.get("codPrestador")
            ctx = contexto(reg, prestador, fecha, fclave)
            for campo in esquema.campos_codigos_adicionales:
                if reg.get(campo) not in (None, ""):
                    otros.append(
                        {
                            "campo": campo,
                            "codigo": str(reg[campo]),
                            "tipo_archivo": servicio,
                            "factura": factura,
                            "sede": ctx["sede"],
                            "hospital": ctx["hospital"],
                            "mes": ctx["mes"],
                        }
                    )
            if not esquema.campo_codigo:
                continue
            codigo = str(reg[esquema.campo_codigo])
            cantidad = (
                a_decimal(reg.get(esquema.campo_cantidad), ".")
                if esquema.campo_cantidad
                else Decimal(1)
            )
            filas.append(
                {
                    "origen": "RIPS",
                    "archivo": reg["_archivo"],
                    "ubicacion": reg["_ubicacion"],
                    "version": reg["_version"],
                    "tipo_archivo": servicio,
                    "categoria": esquema.categoria,
                    "tipo_codigo": tipo_codigo(codigo, esquema.categoria),
                    "factura": factura,
                    "factura_clave": fclave,
                    "codigo": codigo,
                    "codigo_clave": clave_codigo(codigo),
                    "campo_codigo": esquema.campo_codigo,
                    "descripcion": reg.get("nomTecnologiaSalud"),
                    "cantidad": cantidad if cantidad is not None else Decimal(1),
                    "valor": a_decimal(
                        reg.get(esquema.campo_valor), ".", vacio_es_cero=True
                    ),
                    "valor_unitario": (
                        a_decimal(reg.get(esquema.campo_valor_unitario), ".")
                        if esquema.campo_valor_unitario
                        else None
                    ),
                    "fecha": fecha,
                    **ctx,
                    "ambito": reg.get("grupoServicios")
                    or reg.get("modalidadGrupoServicioTecSal"),
                    "servicio": esquema.categoria,
                    "farmacia": (
                        ctx["sede"] if esquema.categoria == "MEDICAMENTO" else None
                    ),
                    "prestador": prestador,
                    "documento": reg.get("usuarios.numDocumentoIdentificacion"),
                }
            )

    # --- RIPS exportados como tabla (Excel, ODS, CSV, JSON o XML con encabezados)
    for reg in carga.registros.get("tabla", []):
        factura = reg["_factura"]
        fclave = clave_factura(factura, ignorar_ceros)
        prestador = reg.get("_prestador")
        ctx = contexto(reg, prestador, reg.get("_fecha"), fclave)
        if reg.get("_sede") and not reg.get("_sede_asignada"):
            ctx["sede"] = etiqueta(reg["_sede"]) or ctx["sede"]
        if reg.get("_hospital") and not reg.get("_hospital_asignado"):
            ctx["hospital"] = etiqueta(reg["_hospital"]) or ctx["hospital"]
        if not reg.get("_mes_ruta") and reg.get("_mes"):
            ctx["mes"], ctx["mes_origen"] = reg["_mes"], reg.get("_mes_origen") or "TABLA"
        codigo = reg["_codigo"]
        categoria = reg.get("_categoria") or ("MEDICAMENTO" if _RE_CUMS.match(clave_codigo(codigo)) else "SIN CLASIFICAR")
        filas.append({
            "origen": "RIPS", "archivo": reg["_archivo"], "ubicacion": reg["_ubicacion"],
            "version": reg["_version"], "tipo_archivo": "TABLA" + (f" {reg['_hoja']}" if reg.get("_hoja") else ""),
            "categoria": categoria, "tipo_codigo": tipo_codigo(codigo, categoria),
            "factura": factura, "factura_clave": fclave, "codigo": codigo, "codigo_clave": clave_codigo(codigo),
            "campo_codigo": reg["_campo_codigo"], "descripcion": reg.get("_descripcion"),
            "cantidad": reg["_cantidad"], "valor": reg["_valor"], "valor_unitario": reg.get("_valor_unitario"),
            "fecha": reg.get("_fecha"), **ctx,
            "ambito": reg.get("_ambito"), "servicio": categoria,
            "farmacia": etiqueta(reg.get("_farmacia")) or (ctx["sede"] if categoria == "MEDICAMENTO" else None),
            "prestador": prestador, "documento": reg.get("_documento"),
        })

    lineas = pd.DataFrame(filas, columns=COLUMNAS_LINEA)

    # Atributos de factura para las facturas RIPS
    tabla_facturas = []
    for clave, f in facturas.items():
        meta = f.pop("_meta")
        cat = _resolver_catalogo(f.get("prestador"), catalogo)
        f["sede_rips"] = (
            etiqueta(
                meta.get("_sede_asignada") or cat.get("sede") or meta.get("_sede_ruta")
            )
            or SIN_SEDE
        )
        f["hospital_rips"] = (
            etiqueta(meta.get("_hospital_asignado") or cat.get("hospital"))
            or SIN_HOSPITAL
        )
        tabla_facturas.append(f)
    facturas_df = pd.DataFrame(tabla_facturas)
    otros_df = pd.DataFrame(
        otros,
        columns=[
            "campo",
            "codigo",
            "tipo_archivo",
            "factura",
            "sede",
            "hospital",
            "mes",
        ],
    )
    return lineas, facturas_df, otros_df, incidencias


def lineas_facturacion(
    carga: ResultadoCarga,
    catalogo: dict | None = None,
    equivalencias: dict | None = None,
    ignorar_ceros: bool = False,
) -> pd.DataFrame:
    catalogo = catalogo or {}
    equivalencias = {
        clave_codigo(k): clave_codigo(v) for k, v in (equivalencias or {}).items()
    }
    filas = []
    for reg in carga.registros.get("facturacion", []):
        cat = _resolver_catalogo(reg.get("_prestador"), catalogo)
        codigo = reg["_codigo"]
        cclave = clave_codigo(codigo)
        filas.append(
            {
                "origen": "FACTURACION",
                "archivo": reg["_archivo"],
                "ubicacion": reg["_ubicacion"],
                "version": "Facturación",
                "tipo_archivo": reg.get("_hoja") or "FACTURACION",
                "categoria": None,
                "tipo_codigo": None,
                "factura": reg["_factura"],
                "factura_clave": clave_factura(reg["_factura"], ignorar_ceros),
                "codigo": codigo,
                "codigo_clave": equivalencias.get(cclave, cclave),
                "campo_codigo": reg["_campo_codigo"],
                "descripcion": reg.get("_descripcion"),
                "cantidad": reg["_cantidad"],
                "valor": reg["_valor"],
                "valor_unitario": reg.get("_valor_unitario"),
                "fecha": reg.get("_fecha"),
                "mes": reg.get("_mes") or SIN_MES,
                "mes_origen": reg.get("_mes_origen") or "SIN_DATO",
                "sede": etiqueta(reg.get("_sede") or cat.get("sede")) or SIN_SEDE,
                "hospital": etiqueta(reg.get("_hospital") or cat.get("hospital"))
                or SIN_HOSPITAL,
                "ambito": reg.get("_ambito"),
                "servicio": reg.get("_servicio"),
                "farmacia": etiqueta(reg.get("_farmacia")),
                "prestador": reg.get("_prestador"),
                "documento": reg.get("_documento"),
            }
        )
    return pd.DataFrame(filas, columns=COLUMNAS_LINEA)


def clasificar_facturacion(fact: pd.DataFrame, rips: pd.DataFrame) -> pd.DataFrame:
    """Asigna categoría/tipo de código a la facturación usando lo que RIPS dice del mismo código.

    Si el código no está en RIPS se usa el texto de servicio/ámbito
    (``MEDICAMENTO`` si menciona medicamento o farmacia) o ``SIN CLASIFICAR``.
    """
    if fact.empty:
        return fact
    categorias = (
        rips.drop_duplicates("codigo_clave")
        .set_index("codigo_clave")["categoria"]
        .to_dict()
        if not rips.empty
        else {}
    )
    fact = fact.copy()

    def categoria(codigo, servicio, ambito, tipo_archivo, farmacia):
        if codigo in categorias:
            return categorias[codigo]
        texto = f"{servicio or ''} {ambito or ''} {tipo_archivo or ''}".upper()
        if "MEDICAMENT" in texto or "FARMAC" in texto or (farmacia and farmacia == farmacia):
            return "MEDICAMENTO"
        if _RE_CUMS.match(codigo):
            return "MEDICAMENTO"
        return "SIN CLASIFICAR"

    # zip sobre columnas en lugar de DataFrame.apply(axis=1): mucho más rápido con cientos de miles de filas
    fact["categoria"] = [
        categoria(*fila)
        for fila in zip(fact["codigo_clave"], fact["servicio"], fact["ambito"], fact["tipo_archivo"], fact["farmacia"])
    ]
    fact["tipo_codigo"] = [
        tipo_codigo(c, k) for c, k in zip(fact["codigo_clave"], fact["categoria"])
    ]
    es_med = fact["categoria"] == "MEDICAMENTO"
    fact.loc[es_med & fact["farmacia"].isna(), "farmacia"] = fact.loc[
        es_med & fact["farmacia"].isna(), "sede"
    ]
    fact["servicio"] = fact["servicio"].fillna(fact["categoria"])
    return fact


def asegurar_decimales(
    df: pd.DataFrame, columnas=("cantidad", "valor")
) -> pd.DataFrame:
    for c in columnas:
        if c in df.columns:
            df[c] = [v if isinstance(v, Decimal) else CERO for v in df[c]]
    return df


def completar_hospital_desde_facturacion(
    rips: pd.DataFrame, facturas_rips: pd.DataFrame, fact: pd.DataFrame
) -> list[str]:
    """Si RIPS no trae hospital (sin catálogo ni asignación) se toma de la facturación
    cuando la sede pertenece a un único hospital. Devuelve las sedes completadas."""
    if fact.empty or rips.empty:
        return []
    pares = fact[fact["hospital"] != SIN_HOSPITAL].groupby("sede")["hospital"].unique()
    mapa = {
        sede: hosp[0]
        for sede, hosp in pares.items()
        if len(hosp) == 1 and sede != SIN_SEDE
    }
    faltan = rips["hospital"] == SIN_HOSPITAL
    sedes = sorted(set(rips.loc[faltan, "sede"]) & set(mapa))
    if sedes:
        rips.loc[faltan, "hospital"] = (
            rips.loc[faltan, "sede"].map(mapa).fillna(SIN_HOSPITAL)
        )
        if not facturas_rips.empty:
            f = facturas_rips["hospital_rips"] == SIN_HOSPITAL
            facturas_rips.loc[f, "hospital_rips"] = (
                facturas_rips.loc[f, "sede_rips"].map(mapa).fillna(SIN_HOSPITAL)
            )
    return sedes

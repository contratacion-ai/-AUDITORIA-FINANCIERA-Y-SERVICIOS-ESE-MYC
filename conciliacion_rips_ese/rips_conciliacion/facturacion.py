"""Lectura de la facturación (incluida la facturación por ámbito).

La facturación de la ESE no tiene un formato normativo: se acepta CSV,
TXT delimitado, XLSX o XLS con encabezado. Las columnas se reconocen por
alias (sin importar mayúsculas ni tildes) y pueden fijarse con un mapeo
explícito. Las columnas originales se conservan con su nombre; los valores
interpretados se agregan en llaves con prefijo ``_``.

Facturación por ámbito: si el libro de Excel trae una hoja por ámbito
(Ambulatorio, Urgencias, Hospitalización...) y no existe una columna de
ámbito, se toma el nombre de la hoja; si se cargan varios archivos (uno por
ámbito) puede asignarse el ámbito al cargarlos.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from decimal import Decimal
from pathlib import Path, PurePosixPath

import pandas as pd

from .decimales import ValorInvalido, a_decimal, inferir_separador
from .fechas import mes_de_fecha, mes_de_nombre, parse_fecha
from .modelos import ADVERTENCIA, ERROR, ResultadoCarga

# Campo canónico -> alias aceptados (normalizados: minúsculas, sin tildes, '_' en lugar de símbolos).
# Incluye los nombres oficiales de RIPS (anexo de la Res. 3374 y llaves JSON de la Res. 2275) para leer
# RIPS exportados como tabla.
ALIAS: dict[str, tuple[str, ...]] = {
    "factura": (
        "numero_factura",
        "num_factura",
        "nro_factura",
        "no_factura",
        "n_factura",
        "numfactura",
        "factura",
        "numero_de_factura",
        "factura_numero",
        "nfactura",
        "documento_factura",
        "numero_de_la_factura",
        "no_de_factura",
        "num_fact",
        "factura_no",
    ),
    "prefijo": ("prefijo", "prefijo_factura"),
    "consecutivo": ("consecutivo", "consecutivo_factura", "numero"),
    "codigo": (
        "codigo",
        "cod_servicio",
        "codigo_servicio",
        "codigo_cups",
        "cups",
        "cod_cups",
        "cums",
        "codigo_cums",
        "cod_cums",
        "cod_producto",
        "codigo_producto",
        "cod_tecnologia",
        "codtecnologiasalud",
        "cod_procedimiento",
        "codigo_procedimiento",
        "codprocedimiento",
        "codconsulta",
        "cod_medicamento",
        "codigo_medicamento",
        "codigo_articulo",
        "cod_articulo",
        "codigo_de_consulta",
        "codigo_del_procedimiento",
        "codigo_del_medicamento",
        "codigo_del_servicio",
        "cod_cups_cums",
        "codigo_cups_cums",
    ),
    "valor": (
        "valor_total",
        "vr_total",
        "vlr_total",
        "total",
        "valor",
        "valor_servicio",
        "vrservicio",
        "valor_facturado",
        "total_facturado",
        "subtotal",
        "valor_bruto",
        "valor_de_la_consulta",
        "valor_del_procedimiento",
        "valor_total_de_medicamento",
        "valor_total_del_material_e_insumo",
        "vr_servicio",
    ),
    "valor_unitario": (
        "valor_unitario",
        "vr_unitario",
        "vlr_unitario",
        "precio_unitario",
        "precio",
        "vrunitario",
        "valor_unidad",
        "valor_unitario_de_medicamento",
        "valor_unitario_del_material_e_insumo",
        "vrunitmedicamento",
        "vrunitos",
    ),
    "cantidad": (
        "cantidad",
        "cant",
        "unidades",
        "cantidad_facturada",
        "numero_unidades",
        "cantidad_total",
        "numero_de_unidades",
        "cantidadmedicamento",
        "cantidados",
    ),
    "fecha": (
        "fecha_factura",
        "fecha_expedicion",
        "fecha_emision",
        "fecha_facturacion",
        "fecha_de_factura",
        "fecha",
        "fecha_servicio",
        "fecha_atencion",
        "fecha_de_expedicion_de_la_factura",
        "fecha_de_la_consulta",
        "fecha_del_procedimiento",
        "fechainicioatencion",
        "fechadispensadmon",
        "fechasuministrotecnologia",
    ),
    "mes": ("mes", "periodo", "mes_facturacion", "mes_factura", "periodo_facturacion"),
    "sede": (
        "sede",
        "centro_de_atencion",
        "centro_atencion",
        "punto_de_atencion",
        "punto_atencion",
        "centro",
        "nombre_sede",
    ),
    "hospital": (
        "hospital",
        "unidad_funcional",
        "institucion",
        "ips",
        "nombre_hospital",
        "unidad",
    ),
    "ambito": ("ambito", "ambito_atencion", "tipo_atencion", "ambito_de_atencion"),
    "servicio": (
        "servicio",
        "tipo_servicio",
        "grupo_servicio",
        "grupo",
        "concepto",
        "area",
        "centro_costo",
        "centro_de_costo",
    ),
    "descripcion": (
        "descripcion",
        "nombre_servicio",
        "nombre_producto",
        "nombre",
        "detalle",
        "producto",
        "nombre_medicamento",
        "descripcion_servicio",
        "nomtecnologiasalud",
        "nombre_generico_del_medicamento",
        "nombre_del_servicio",
    ),
    "farmacia": ("farmacia", "bodega", "punto_dispensacion", "punto_de_dispensacion"),
    "prestador": (
        "cod_prestador",
        "codigo_prestador",
        "codigo_habilitacion",
        "codprestador",
        "cod_habilitacion",
        "codigo_del_prestador_de_servicios_de_salud",
        "codigo_del_prestador",
    ),
    "documento": (
        "documento_paciente",
        "identificacion",
        "num_documento",
        "numero_documento",
        "documento",
        "numdocumentoidentificacion",
        "cedula",
        "numero_de_identificacion_del_usuario_en_el_sistema",
        "numero_de_identificacion_del_usuario",
    ),
    "eps": (
        "eps",
        "entidad",
        "administradora",
        "pagador",
        "erp",
        "aseguradora",
        "entidad_responsable",
    ),
}

OBLIGATORIOS = ("factura", "codigo")


def normalizar_nombre(nombre) -> str:
    texto = "".join(
        c
        for c in unicodedata.normalize("NFD", str(nombre))
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", "_", texto.lower()).strip("_")


def detectar_columnas(columnas, mapeo: dict | None = None) -> dict[str, list[str]]:
    """Devuelve campo canónico -> lista de columnas originales (en orden de prioridad).

    Para ``codigo`` puede haber varias columnas (p. ej. ``CUPS`` y ``CUMS``):
    se usa la primera que tenga valor en cada fila.
    """
    mapeo = mapeo or {}
    normalizadas = {normalizar_nombre(c): c for c in columnas}
    resultado: dict[str, list[str]] = {}
    usadas: set[str] = set()
    for campo, alias in ALIAS.items():
        if campo in mapeo and mapeo[campo]:
            explicitas = (
                mapeo[campo]
                if isinstance(mapeo[campo], (list, tuple))
                else [mapeo[campo]]
            )
            encontradas = [c for c in explicitas if c in columnas]
            if encontradas:
                resultado[campo] = encontradas
                usadas.update(encontradas)
            continue
        candidatas = [
            normalizadas[a]
            for a in alias
            if a in normalizadas and normalizadas[a] not in usadas
        ]
        if campo != "codigo":
            candidatas = candidatas[:1]
        if candidatas:
            resultado[campo] = candidatas
            usadas.update(candidatas)
    return resultado


def _es_encabezado(fila) -> bool:
    nombres = {normalizar_nombre(v) for v in fila if v is not None and str(v).strip()}
    tiene_factura = any(a in nombres for a in ALIAS["factura"]) or (
        any(a in nombres for a in ALIAS["prefijo"])
        and any(a in nombres for a in ALIAS["consecutivo"])
    )
    return tiene_factura and any(a in nombres for a in ALIAS["codigo"])


def _tablas_de_archivo(
    nombre: str, contenido: bytes, res: ResultadoCarga, sin_encabezado: list | None = None
) -> list[tuple[str | None, pd.DataFrame, int]]:
    """Devuelve [(hoja, DataFrame de texto, fila_excel_del_encabezado)].

    Acepta cualquier formato tabular (ver ``formatos.leer_tablas``). Si se pasa ``sin_encabezado``,
    las hojas sin encabezado reconocible se agregan ahí en lugar de reportarse como error.
    """
    from .formatos import FormatoNoLegible, leer_tablas

    try:
        tablas = leer_tablas(nombre, contenido)
    except FormatoNoLegible as exc:
        res.incidencia(ERROR, str(exc))
        return []

    salida = []
    for hoja, crudo in tablas:
        encabezado = None
        for i in range(min(30, len(crudo))):
            if _es_encabezado(crudo.iloc[i].tolist()):
                encabezado = i
                break
        if encabezado is None and sin_encabezado is not None:
            sin_encabezado.append((hoja, crudo))
            continue
        if encabezado is None:
            res.incidencia(
                ADVERTENCIA if len(tablas) > 1 else ERROR,
                "No se encontró la fila de encabezado (se requiere una columna de factura y una de código "
                "en las primeras 30 filas)" + (f" en la hoja '{hoja}'" if hoja else ""),
                campo=hoja,
            )
            continue
        columnas = []
        for j, v in enumerate(crudo.iloc[encabezado].tolist()):
            nombre_col = (
                str(v).strip()
                if v is not None and str(v).strip() and str(v) != "nan"
                else f"columna_{j + 1}"
            )
            while nombre_col in columnas:
                nombre_col += "_"
            columnas.append(nombre_col)
        df = crudo.iloc[encabezado + 1 :].copy()
        df.columns = columnas
        salida.append((hoja, df, encabezado + 1))
    return salida


def _texto(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if v != v:
            return ""
        if v.is_integer():
            return str(int(v))
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    return str(v).strip()


def _mes_desde_texto(valor) -> str | None:
    if valor is None or _texto(valor) == "":
        return None
    if hasattr(valor, "year") and hasattr(valor, "month"):
        return f"{valor.year:04d}-{valor.month:02d}"
    texto = _texto(valor)
    m = mes_de_fecha(texto)
    if m:
        return m
    anio, mes = mes_de_nombre(texto)
    if anio and mes:
        return f"{anio:04d}-{mes:02d}"
    return None


def leer_facturacion(
    entradas,
    mapeo: dict | None = None,
    separador_decimal: str = "auto",
    sede: str | None = None,
    hospital: str | None = None,
    ambito: str | None = None,
) -> ResultadoCarga:
    """Lee uno o varios archivos de facturación.

    ``entradas``: ruta, ``(nombre, bytes)`` o lista de ellos.
    ``sede``/``hospital``/``ambito``: valores asignados cuando el archivo no
    trae esas columnas.
    """
    if not isinstance(entradas, list):
        entradas = [entradas]
    total = ResultadoCarga(archivo="FACTURACION")
    for entrada in entradas:
        if isinstance(entrada, tuple):
            nombre, contenido = entrada
        else:
            nombre, contenido = Path(entrada).name, Path(entrada).read_bytes()
        total.fusionar(
            _leer_un_archivo(
                nombre,
                bytes(contenido),
                mapeo,
                separador_decimal,
                sede,
                hospital,
                ambito,
            )
        )
    total.version = "Facturación"
    return total


def _leer_un_archivo(
    nombre, contenido, mapeo, separador_decimal, sede, hospital, ambito
) -> ResultadoCarga:
    res = ResultadoCarga(archivo=nombre, version="Facturación")
    from .formatos import PAQUETES, FormatoNoLegible, clasificar, expandir_paquete

    clase = clasificar(nombre, contenido)
    if clase in PAQUETES:
        # Facturación comprimida (ZIP, RAR, 7Z, GZ, TAR): se lee cada archivo interno
        if clase == "rar":
            from .lectores import miembros_rar

            miembros = miembros_rar(nombre, contenido, res)
        else:
            miembros = expandir_paquete(nombre, contenido, clase)
        try:
            for ruta, datos in miembros:
                if re.search(r"(^|/)(__MACOSX|\.DS_Store|Thumbs\.db|desktop\.ini)", ruta, re.IGNORECASE):
                    continue
                res.fusionar(_leer_un_archivo(f"{nombre}/{ruta}", datos, mapeo, separador_decimal,
                                              sede, hospital, ambito))
        except FormatoNoLegible as exc:
            res.incidencia(ERROR, str(exc))
        return res

    tablas = _tablas_de_archivo(nombre, contenido, res)
    anio_archivo, mes_archivo = mes_de_nombre(PurePosixPath(nombre).stem)
    mes_por_nombre = (
        f"{anio_archivo:04d}-{mes_archivo:02d}"
        if anio_archivo and mes_archivo
        else None
    )

    for hoja, df, fila_encabezado in tablas:
        columnas = detectar_columnas(list(df.columns), mapeo)
        faltan = [
            c
            for c in OBLIGATORIOS
            if c not in columnas
            and not (
                c == "factura" and "prefijo" in columnas and "consecutivo" in columnas
            )
        ]
        if "valor" not in columnas and "valor_unitario" not in columnas:
            faltan.append("valor (o valor_unitario)")
        if faltan:
            res.incidencia(
                ERROR,
                f"Faltan columnas obligatorias: {', '.join(faltan)}. Columnas encontradas: "
                f"{list(df.columns)}",
                campo=hoja,
            )
            continue
        if "cantidad" not in columnas:
            res.incidencia(
                ADVERTENCIA,
                "No hay columna de cantidad: se asume 1 por línea",
                campo=hoja,
            )
        ambito_hoja = ambito or (hoja if hoja and len(tablas) > 1 else None)
        separador = separador_decimal
        if separador_decimal == "auto":
            textos = [
                v
                for campo in ("valor", "valor_unitario", "cantidad")
                for col in columnas.get(campo, [])
                for v in df[col].tolist()
                if isinstance(v, str)
            ]
            separador, aviso = inferir_separador(textos)
            if aviso:
                res.incidencia(ADVERTENCIA, aviso, campo=hoja)

        leidos = 0
        nombres_columnas = list(df.columns)
        # itertuples es mucho más rápido que iterrows en archivos de cientos de miles de filas
        for posicion, tupla in enumerate(df.itertuples(index=False, name=None)):
            numero_fila = fila_encabezado + posicion + 1  # fila real en Excel / línea en CSV
            valores = dict(zip(nombres_columnas, tupla))
            if all(_texto(v) == "" for v in valores.values()):
                continue

            def tomar(campo):
                for col in columnas.get(campo, []):
                    texto = _texto(valores.get(col))
                    if texto != "":
                        return texto, col
                return "", (columnas.get(campo) or [None])[0]

            factura, col_factura = tomar("factura")
            if not factura and "prefijo" in columnas and "consecutivo" in columnas:
                factura = tomar("prefijo")[0] + tomar("consecutivo")[0]
                col_factura = f"{columnas['prefijo'][0]}+{columnas['consecutivo'][0]}"
            codigo, col_codigo = tomar("codigo")
            if not factura or not codigo:
                faltante = "factura" if not factura else "código"
                # Filas de totales/subtotales al final de los reportes
                if any(
                    re.search(r"total", _texto(v), re.IGNORECASE)
                    for v in valores.values()
                ):
                    res.incidencia(
                        ADVERTENCIA,
                        "Fila de total/subtotal ignorada",
                        linea=numero_fila,
                        campo=hoja,
                    )
                else:
                    res.incidencia(
                        ERROR,
                        f"Fila sin {faltante}. Registro excluido.",
                        linea=numero_fila,
                        campo=col_factura if not factura else col_codigo,
                    )
                continue

            valido = True
            numeros = {}
            for campo in ("cantidad", "valor", "valor_unitario"):
                texto, col = tomar(campo)
                try:
                    numeros[campo] = (
                        a_decimal(valores.get(col) if col else None, separador)
                        if texto
                        else None
                    )
                except ValorInvalido:
                    res.incidencia(
                        ERROR,
                        "Valor numérico inválido. Registro excluido.",
                        linea=numero_fila,
                        campo=col,
                        valor=texto,
                    )
                    valido = False
            if not valido:
                continue
            cantidad = (
                numeros["cantidad"] if numeros["cantidad"] is not None else Decimal(1)
            )
            if numeros["valor"] is not None:
                valor = numeros["valor"]
                if (
                    numeros["valor_unitario"] is not None
                    and numeros["valor_unitario"] * cantidad != valor
                ):
                    res.incidencia(
                        ADVERTENCIA,
                        f"Valor total ({valor}) distinto de cantidad × valor unitario "
                        f"({cantidad} × {numeros['valor_unitario']} = {cantidad * numeros['valor_unitario']}); "
                        "se usa el valor total",
                        linea=numero_fila,
                        campo=columnas["valor"][0],
                    )
            elif numeros["valor_unitario"] is not None:
                valor = numeros["valor_unitario"] * cantidad
            else:
                res.incidencia(
                    ERROR,
                    "Fila sin valor. Registro excluido.",
                    linea=numero_fila,
                    campo=(columnas.get("valor") or columnas.get("valor_unitario"))[0],
                )
                continue

            fecha_txt, col_fecha = tomar("fecha")
            fecha = None
            if fecha_txt:
                try:
                    fecha = parse_fecha(
                        valores.get(col_fecha)
                        if not isinstance(valores.get(col_fecha), str)
                        else fecha_txt
                    )
                except ValueError:
                    res.incidencia(
                        ADVERTENCIA,
                        "Fecha inválida",
                        linea=numero_fila,
                        campo=col_fecha,
                        valor=fecha_txt,
                    )
            mes_txt, col_mes = tomar("mes")
            mes = _mes_desde_texto(valores.get(col_mes)) if mes_txt else None
            if mes_txt and mes is None:
                # Mes sin año (p. ej. "ENERO"): se completa con el año de la fecha
                _a, _m = mes_de_nombre(mes_txt)
                if _m and fecha:
                    mes = f"{fecha.year:04d}-{_m:02d}"
                elif _m is None:
                    res.incidencia(
                        ADVERTENCIA,
                        "Mes no reconocible",
                        linea=numero_fila,
                        campo=col_mes,
                        valor=mes_txt,
                    )
            mes_origen = "COLUMNA_MES" if mes else None
            if mes is None and fecha:
                mes, mes_origen = f"{fecha.year:04d}-{fecha.month:02d}", "FECHA_FACTURA"
            if mes is None and mes_por_nombre:
                mes, mes_origen = mes_por_nombre, "NOMBRE_ARCHIVO"

            registro = {
                k: (v if not (isinstance(v, float) and v != v) else None)
                for k, v in valores.items()
            }
            registro.update(
                {
                    "_archivo": nombre,
                    "_hoja": hoja,
                    "_linea": numero_fila,
                    "_ubicacion": (f"hoja '{hoja}' " if hoja else "")
                    + f"fila {numero_fila}",
                    "_factura": factura,
                    "_campo_factura": col_factura,
                    "_codigo": codigo,
                    "_campo_codigo": col_codigo,
                    "_cantidad": cantidad,
                    "_valor": valor,
                    "_valor_unitario": numeros["valor_unitario"],
                    "_fecha": fecha,
                    "_mes": mes,
                    "_mes_origen": mes_origen,
                    "_sede": tomar("sede")[0] or sede,
                    "_hospital": tomar("hospital")[0] or hospital,
                    "_ambito": tomar("ambito")[0] or ambito_hoja,
                    "_servicio": tomar("servicio")[0] or None,
                    "_descripcion": tomar("descripcion")[0] or None,
                    "_farmacia": tomar("farmacia")[0] or None,
                    "_prestador": tomar("prestador")[0] or None,
                    "_documento": tomar("documento")[0] or None,
                    "_eps": tomar("eps")[0] or None,
                }
            )
            res.agregar("facturacion", registro)
            leidos += 1

        res.archivos.append(
            {
                "archivo": nombre + (f" [{hoja}]" if hoja else ""),
                "version": "Facturación",
                "tipo": "FACTURACION" + (f" ({ambito_hoja})" if ambito_hoja else ""),
                "registros": leidos,
                "errores": sum(1 for i in res.incidencias if i.severidad == ERROR),
                "motivo": "Columnas reconocidas: "
                + "; ".join(f"{k}={'|'.join(v)}" for k, v in columnas.items()),
            }
        )
    return res


def leer_catalogo_sedes(entrada) -> tuple[dict[str, dict], list]:
    """Catálogo opcional: código de prestador/habilitación -> sede y hospital.

    Columnas esperadas (alias admitidos): ``codigo_prestador`` (o
    ``codigo_habilitacion``), ``sede``, ``hospital``.
    """
    from .modelos import Incidencia

    nombre, contenido = (
        entrada
        if isinstance(entrada, tuple)
        else (Path(entrada).name, Path(entrada).read_bytes())
    )
    df = _tabla_simple(nombre, contenido)
    cols = {normalizar_nombre(c): c for c in df.columns}
    col_cod = next((cols[a] for a in ALIAS["prestador"] if a in cols), None)
    col_sede = next((cols[a] for a in ALIAS["sede"] if a in cols), None)
    col_hosp = next((cols[a] for a in ALIAS["hospital"] if a in cols), None)
    incidencias = []
    if not col_cod:
        incidencias.append(
            Incidencia(
                nombre,
                ERROR,
                "El catálogo de sedes no tiene columna de código de prestador",
            )
        )
        return {}, incidencias
    catalogo = {}
    for i, fila in df.iterrows():
        codigo = str(fila[col_cod]).strip()
        if not codigo:
            continue
        catalogo[codigo] = {
            "sede": str(fila[col_sede]).strip() if col_sede else None,
            "hospital": str(fila[col_hosp]).strip() if col_hosp else None,
        }
    return catalogo, incidencias


def leer_equivalencias(entrada) -> dict[str, str]:
    """Tabla opcional de homologación: código de facturación -> código RIPS.

    Columnas: ``codigo_facturacion`` y ``codigo_rips``.
    """
    nombre, contenido = (
        entrada
        if isinstance(entrada, tuple)
        else (Path(entrada).name, Path(entrada).read_bytes())
    )
    df = _tabla_simple(nombre, contenido)
    cols = {normalizar_nombre(c): c for c in df.columns}
    origen = (
        cols.get("codigo_facturacion") or cols.get("codigo_origen") or df.columns[0]
    )
    destino = cols.get("codigo_rips") or cols.get("codigo_destino") or df.columns[1]
    return {
        str(a).strip(): str(b).strip()
        for a, b in zip(df[origen], df[destino])
        if str(a).strip()
    }


def _tabla_simple(nombre: str, contenido: bytes) -> pd.DataFrame:
    """Primera hoja con datos, con la primera fila como encabezado (cualquier formato tabular)."""
    from .formatos import leer_tablas

    for _hoja, crudo in leer_tablas(nombre, contenido):
        crudo = crudo.dropna(how="all")
        if len(crudo) >= 1:
            columnas = [str(c).strip() for c in crudo.iloc[0].tolist()]
            df = crudo.iloc[1:].copy()
            df.columns = columnas
            return df.map(lambda v: "" if v is None or (isinstance(v, float) and v != v) else
                          (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)))
    return pd.DataFrame()

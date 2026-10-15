"""Validaciones conforme a la Resolución 3374 de 2000.

Dos niveles, ambos vectorizados con pandas (los archivos reales superan los
cientos de miles de registros):

1. **Campo** (``validar_campos``): obligatoriedad, longitud máxima, formato
   de fecha/hora/valor, catálogo de valores permitidos y formato CIE-10.
2. **Remisión** (``validar_envios``): presencia de archivos obligatorios,
   coherencia del CT con los archivos, referencia cruzada de facturas (AF) y
   usuarios (US), duplicados, consistencia de valores y de fechas
   (Art. 9 num. 2: "verificar la consistencia ... y la referencia cruzada").

Ninguna validación detiene el procesamiento: todo se registra como
incidencia y el registro permanece en los datos (salvo los rechazados por
cantidad de campos en el parser), para que el auditor decida.
"""

from __future__ import annotations

import pandas as pd

from .especificacion import (
    ARCHIVOS_OBLIGATORIOS,
    ARCHIVOS_SERVICIO,
    CLAVE_USUARIO,
    ESPECIFICACIONES,
    VALOR_SERVICIO,
    Campo,
    especificacion,
)
from .incidencias import REGLAS, RegistroIncidencias

_RE_FECHA = r"\d{2}/\d{2}/\d{4}"
_RE_HORA = r"([01]\d|2[0-3]):[0-5]\d"
_RE_ENTERO = r"\d+"
_RE_VALOR = r"\d+(\.\d+)?"
_RE_CIE10 = r"[A-Z]\d{2}[0-9A-Z]"
_UBICACION = ["envio_id", "archivo", "linea"]


# ---------------------------------------------------------------------------
# Utilidades de conversión (también usadas por exportación y homologación)
# ---------------------------------------------------------------------------

def normalizar_codigo(s: pd.Series) -> pd.Series:
    """Mayúsculas y sin ceros a la izquierda en códigos numéricos ("01" == "1")."""
    s = s.astype("string").str.strip().str.upper()
    es_num = s.str.fullmatch(r"\d+").fillna(False).astype(bool)
    out = s.copy()
    out[es_num] = s[es_num].str.lstrip("0").replace("", "0")
    return out


def _norm_claves(cat: dict[str, str] | None) -> set[str]:
    if not cat:
        return set()
    return {(k.lstrip("0") or "0") if k.isdigit() else k.upper() for k in cat}


def a_fecha(s: pd.Series) -> pd.Series:
    s = s.astype("string")
    ok = s.str.fullmatch(_RE_FECHA).fillna(False).astype(bool)
    out = pd.to_datetime(s.where(ok), format="%d/%m/%Y", errors="coerce")
    return out


def a_numero(s: pd.Series) -> pd.Series:
    s = s.astype("string").str.strip()
    ok = s.str.fullmatch(_RE_VALOR).fillna(False).astype(bool)
    return pd.to_numeric(s.where(ok), errors="coerce")


def a_fecha_hora(fecha: pd.Series, hora: pd.Series) -> pd.Series:
    f = a_fecha(fecha)
    h = hora.astype("string").where(hora.astype("string").str.fullmatch(_RE_HORA).fillna(False).astype(bool), "00:00")
    return f + pd.to_timedelta(h.fillna("00:00") + ":00", errors="coerce").fillna(pd.Timedelta(0))


# ---------------------------------------------------------------------------
# Validación por campo
# ---------------------------------------------------------------------------

def _bloque(df: pd.DataFrame, mask: pd.Series, tipo: str, campo: str, mensaje: str) -> pd.DataFrame:
    sub = df.loc[mask, _UBICACION].copy()
    sub["tipo_archivo"] = tipo
    sub["campo"] = campo
    sub["valor"] = df.loc[mask, campo].astype("string").str.slice(0, 250)
    sub["mensaje"] = mensaje
    return sub


def _mask(serie) -> pd.Series:
    return serie.fillna(False).astype(bool)


def validar_campos(tipo: str, df: pd.DataFrame, inc: RegistroIncidencias) -> None:
    spec = especificacion(tipo)
    if df.empty:
        return
    for c in spec.campos:
        s = df[c.nombre].fillna("").astype("string")
        vacio = s == ""
        _validar_obligatorio(tipo, df, c, vacio, inc)
        # En códigos de catálogo "05" equivale a "5": la longitud se mide sobre
        # el código normalizado para no reportar dos veces el mismo valor.
        medida = normalizar_codigo(s) if c.tipo == "C" else s
        larga = _mask(medida.str.len() > c.longitud)
        if larga.any():
            inc.agregar_bloque(
                _bloque(df, larga, tipo, c.nombre, f"{c.etiqueta}: longitud máxima {c.longitud}"),
                "LONGITUD_EXCEDIDA")
        con_valor = ~vacio
        if c.tipo == "F":
            mal = con_valor & a_fecha(s).isna()
            regla, msg = "FORMATO_FECHA", f"{c.etiqueta}: se espera dd/mm/aaaa"
        elif c.tipo == "H":
            mal = con_valor & ~_mask(s.str.fullmatch(_RE_HORA))
            regla, msg = "FORMATO_HORA", f"{c.etiqueta}: se espera hh:mm"
        elif c.tipo == "E":
            mal = con_valor & ~_mask(s.str.fullmatch(_RE_ENTERO))
            regla, msg = "FORMATO_NUMERICO", f"{c.etiqueta}: se espera un número entero"
        elif c.tipo == "V":
            mal = con_valor & ~_mask(s.str.fullmatch(_RE_VALOR))
            regla, msg = "FORMATO_NUMERICO", f"{c.etiqueta}: se espera número con punto decimal, sin separador de miles"
        elif c.tipo == "X":
            mal = con_valor & ~_mask(s.str.upper().str.fullmatch(_RE_CIE10))
            regla, msg = "FORMATO_CIE10", f"{c.etiqueta}: formato CIE-10 esperado (p. ej. J069)"
        elif c.tipo == "C" and c.valores:
            _validar_catalogo(tipo, df, c, s, con_valor, inc)
            continue
        else:
            continue
        if mal.any():
            inc.agregar_bloque(_bloque(df, mal, tipo, c.nombre, msg), regla)


def _validar_obligatorio(tipo, df, c: Campo, vacio, inc) -> None:
    if c.obligatorio:
        falta = vacio
        msg = f"{c.etiqueta} es obligatorio"
    elif c.obligatorio_si:
        campo_cond, valores = c.obligatorio_si
        cond = normalizar_codigo(df[campo_cond].fillna("")).isin(_norm_claves(dict.fromkeys(valores)))
        falta = vacio & cond
        msg = f"{c.etiqueta} es obligatorio cuando {campo_cond} = {'/'.join(valores)}"
    else:
        return
    if falta.any():
        inc.agregar_bloque(_bloque(df, falta, tipo, c.nombre, msg), "CAMPO_OBLIGATORIO")


def _validar_catalogo(tipo, df, c: Campo, s, con_valor, inc) -> None:
    norm = normalizar_codigo(s)
    base = _norm_claves(c.valores)
    ext = _norm_claves(c.valores_ext)
    fuera = con_valor & ~norm.isin(base)
    if not fuera.any():
        return
    en_ext = fuera & norm.isin(ext)
    invalido = fuera & ~en_ext
    permitidos = ", ".join(c.valores)
    if en_ext.any():
        inc.agregar_bloque(_bloque(df, en_ext, tipo, c.nombre,
                                   f"{c.etiqueta}: valor de catálogo posterior a la Res. 3374 (permitidos 3374: {permitidos})"),
                           "VALOR_CATALOGO_EXTENDIDO")
    if invalido.any():
        inc.agregar_bloque(_bloque(df, invalido, tipo, c.nombre, f"{c.etiqueta}: permitidos {permitidos}"),
                           "VALOR_NO_PERMITIDO")


# ---------------------------------------------------------------------------
# Validación por remisión (referencia cruzada)
# ---------------------------------------------------------------------------

def validar_envios(datos: dict[str, pd.DataFrame], envios, inventario: dict[str, dict],
                   inc: RegistroIncidencias, tolerancia: float = 1.0) -> None:
    """``inventario``: envio_id -> {tipo: {"archivo": ruta, "lineas": n}}"""
    _validar_archivos_y_ct(datos.get("CT"), envios, inventario, inc)
    _validar_duplicados(datos, inc)
    _validar_referencias(datos, inventario, inc)
    _validar_valores(datos, inc, tolerancia)
    _validar_fechas(datos, inc)
    at = datos.get("AT")
    if at is not None and not at.empty:
        falta = (at["cod_servicio"].fillna("") == "") & (at["nombre_servicio"].fillna("") == "")
        if falta.any():
            inc.agregar_bloque(_bloque(at, falta, "AT", "nombre_servicio", REGLAS["NOMBRE_O_CODIGO_SERVICIO"][1]),
                               "NOMBRE_O_CODIGO_SERVICIO")


def _validar_archivos_y_ct(ct, envios, inventario, inc) -> None:
    declarados: dict[str, set[str]] = {}
    if ct is not None and not ct.empty:
        for fila in ct.itertuples(index=False):
            codigo = str(fila.codigo_archivo or "").strip().upper()
            tipo_decl, rem_decl = codigo[:2], codigo[2:]
            declarados.setdefault(fila.envio_id, set()).add(tipo_decl)
            archivos = inventario.get(fila.envio_id, {})
            ubic = {"envio_id": fila.envio_id, "archivo": fila.archivo, "linea": fila.linea,
                    "tipo_archivo": "CT", "campo": "codigo_archivo", "valor": codigo}
            info = archivos.get(tipo_decl)
            rem_real = (info or {}).get("remision", "")
            if info is None or (rem_decl.lstrip("0") != rem_real.lstrip("0")):
                inc.agregar("CT_ARCHIVO_NO_ENCONTRADO", f"El CT declara {codigo} y no está en la remisión", **ubic)
                continue
            total = str(fila.total_registros or "").strip()
            if total.isdigit() and int(total) != info["lineas"]:
                inc.agregar("CT_TOTAL_REGISTROS",
                            f"{codigo}: CT declara {int(total)} registros y el archivo tiene {info['lineas']}", **ubic)
    for e in envios:
        presentes = set(e.archivos)
        for t in ARCHIVOS_OBLIGATORIOS:
            if t not in presentes:
                inc.agregar("ARCHIVO_OBLIGATORIO_FALTANTE", f"Falta el archivo {t}{e.remision}",
                            envio_id=e.envio_id, archivo=e.directorio, tipo_archivo=t)
        if not presentes & set(ARCHIVOS_SERVICIO + ["AD"]):
            inc.agregar("ENVIO_SIN_SERVICIOS", envio_id=e.envio_id, archivo=e.directorio)
        if "CT" in presentes:
            for t in sorted(presentes - {"CT"} - declarados.get(e.envio_id, set())):
                inc.agregar("CT_ARCHIVO_NO_DECLARADO", f"{t}{e.remision} no figura en el CT",
                            envio_id=e.envio_id, archivo=e.archivos[t].ruta, tipo_archivo=t)


def _validar_duplicados(datos, inc) -> None:
    af = datos.get("AF")
    if af is not None and not af.empty:
        dup = af.duplicated(["envio_id", "num_factura"], keep="first")
        if dup.any():
            inc.agregar_bloque(_bloque(af, dup, "AF", "num_factura", REGLAS["FACTURA_DUPLICADA"][1]), "FACTURA_DUPLICADA")
    us = datos.get("US")
    if us is not None and not us.empty:
        dup = us.duplicated(["envio_id", "tipo_id_usuario", "num_id_usuario"], keep="first")
        if dup.any():
            inc.agregar_bloque(_bloque(us, dup, "US", "num_id_usuario", REGLAS["USUARIO_DUPLICADO_US"][1]),
                               "USUARIO_DUPLICADO_US")
    for tipo, df in datos.items():
        if df is None or df.empty or tipo in ("CT",):
            continue
        campos = especificacion(tipo).columnas
        dup = df.duplicated(["archivo"] + campos, keep="first")
        if dup.any():
            inc.agregar_bloque(_bloque(df, dup, tipo, campos[0], REGLAS["REGISTRO_DUPLICADO"][1]), "REGISTRO_DUPLICADO")


def _validar_referencias(datos, inventario, inc) -> None:
    af = datos.get("AF")
    us = datos.get("US")
    con_af = {e for e, arch in inventario.items() if "AF" in arch}
    con_us = {e for e, arch in inventario.items() if "US" in arch}
    facturas = (af[["envio_id", "num_factura"]].drop_duplicates() if af is not None and not af.empty
                else pd.DataFrame(columns=["envio_id", "num_factura"]))
    usuarios = (us[["envio_id", "tipo_id_usuario", "num_id_usuario"]].drop_duplicates() if us is not None and not us.empty
                else pd.DataFrame(columns=["envio_id", "tipo_id_usuario", "num_id_usuario"]))
    usados: list[pd.DataFrame] = []
    for tipo in ARCHIVOS_SERVICIO + ["AD"]:
        df = datos.get(tipo)
        if df is None or df.empty:
            continue
        usados.append(df[["envio_id", "num_factura"]])
        m = df[["envio_id", "num_factura"]].merge(facturas.assign(_ok=True), how="left", on=["envio_id", "num_factura"])
        falta = (m["_ok"].isna() & df["envio_id"].isin(con_af)).to_numpy()
        if falta.any():
            inc.agregar_bloque(_bloque(df, pd.Series(falta, index=df.index), tipo, "num_factura",
                                       REGLAS["FACTURA_NO_EXISTE_EN_AF"][1]), "FACTURA_NO_EXISTE_EN_AF")
        if tipo in CLAVE_USUARIO:
            ct, cn = CLAVE_USUARIO[tipo]
            claves = df[["envio_id", ct, cn]].rename(columns={ct: "tipo_id_usuario", cn: "num_id_usuario"})
            m = claves.merge(usuarios.assign(_ok=True), how="left", on=["envio_id", "tipo_id_usuario", "num_id_usuario"])
            falta = (m["_ok"].isna() & df["envio_id"].isin(con_us)).to_numpy()
            if falta.any():
                bloque = _bloque(df, pd.Series(falta, index=df.index), tipo, cn, REGLAS["USUARIO_NO_EXISTE_EN_US"][1])
                bloque["valor"] = (df.loc[falta, ct].astype("string") + " " + df.loc[falta, cn].astype("string")).to_numpy()
                inc.agregar_bloque(bloque, "USUARIO_NO_EXISTE_EN_US")
    if af is not None and not af.empty:
        if usados:
            u = pd.concat(usados).drop_duplicates().assign(_ok=True)
            m = af[["envio_id", "num_factura"]].merge(u, how="left", on=["envio_id", "num_factura"])
            sin = pd.Series(m["_ok"].isna().to_numpy(), index=af.index)
        else:
            sin = pd.Series(True, index=af.index)
        if sin.any():
            inc.agregar_bloque(_bloque(af, sin, "AF", "num_factura", REGLAS["FACTURA_SIN_SERVICIOS"][1]),
                               "FACTURA_SIN_SERVICIOS")


def _validar_valores(datos, inc, tol: float) -> None:
    def revisar(tipo, esperado, reportado, campo, desc):
        df = datos[tipo]
        dif = (esperado - reportado).abs()
        mal = _mask(dif > tol)
        if mal.any():
            b = _bloque(df, mal, tipo, campo, desc)
            b["valor"] = (reportado[mal].astype(str) + " (esperado " + esperado[mal].round(2).astype(str) + ")").to_numpy()
            inc.agregar_bloque(b, "INCONSISTENCIA_VALOR")

    if _hay(datos, "AC"):
        ac = datos["AC"]
        cuota = a_numero(ac["valor_cuota_moderadora"]).fillna(0)
        revisar("AC", a_numero(ac["valor_consulta"]) - cuota, a_numero(ac["valor_neto"]), "valor_neto",
                "Valor neto debe ser valor de la consulta - cuota moderadora")
    if _hay(datos, "AM"):
        am = datos["AM"]
        revisar("AM", a_numero(am["num_unidades"]) * a_numero(am["valor_unitario"]), a_numero(am["valor_total"]),
                "valor_total", "Valor total debe ser número de unidades x valor unitario")
    for tipo, cant in (("AT", "cantidad"), ("AD", "cantidad")):
        if _hay(datos, tipo):
            df = datos[tipo]
            unit = a_numero(df["valor_unitario"])
            esperado = (a_numero(df[cant]) * unit).where(unit > 0)
            revisar(tipo, esperado, a_numero(df["valor_total"]), "valor_total",
                    "Valor total debe ser cantidad x valor unitario")


def _validar_fechas(datos, inc) -> None:
    def revisar(tipo, inicio, fin, campo, desc):
        df = datos[tipo]
        mal = _mask(fin < inicio)
        if mal.any():
            inc.agregar_bloque(_bloque(df, mal, tipo, campo, desc), "INCONSISTENCIA_FECHAS")

    if _hay(datos, "AF"):
        af = datos["AF"]
        revisar("AF", a_fecha(af["fecha_inicio"]), a_fecha(af["fecha_final"]), "fecha_final",
                "Fecha final del período anterior a la fecha de inicio")
    if _hay(datos, "AU"):
        au = datos["AU"]
        revisar("AU", a_fecha_hora(au["fecha_ingreso"], au["hora_ingreso"]),
                a_fecha_hora(au["fecha_salida"], au["hora_salida"]), "fecha_salida",
                "Salida de observación anterior al ingreso")
    if _hay(datos, "AH"):
        ah = datos["AH"]
        revisar("AH", a_fecha_hora(ah["fecha_ingreso"], ah["hora_ingreso"]),
                a_fecha_hora(ah["fecha_egreso"], ah["hora_egreso"]), "fecha_egreso",
                "Egreso anterior al ingreso")


def validar_periodo_facturas(af: pd.DataFrame, inc: RegistroIncidencias) -> None:
    """Advierte facturas cuya fecha de expedición no cae en el período asignado."""
    if af is None or af.empty or "periodo" not in af:
        return
    mes = a_fecha(af["fecha_expedicion"]).dt.strftime("%Y-%m")
    fuera = _mask(mes.notna() & (af["periodo"] != "SIN_PERIODO") & (mes != af["periodo"]))
    if fuera.any():
        b = _bloque(af, fuera, "AF", "fecha_expedicion", REGLAS["FACTURA_FUERA_DE_PERIODO"][1])
        b["mensaje"] = ("Expedida en " + mes[fuera] + "; período asignado " + af.loc[fuera, "periodo"]).to_numpy()
        inc.agregar_bloque(b, "FACTURA_FUERA_DE_PERIODO", mensaje=None)


def _hay(datos, tipo) -> bool:
    return tipo in datos and datos[tipo] is not None and not datos[tipo].empty


# ---------------------------------------------------------------------------
# Conciliación AF vs. servicios
# ---------------------------------------------------------------------------

COLUMNAS_CONCILIACION = [
    "envio_id", "num_factura", "fecha_expedicion", "cod_entidad", "nombre_entidad", "num_contrato",
    "valor_neto_af", "valor_copago", "valor_comision", "valor_descuentos",
    "valor_ac", "valor_ap", "valor_am", "valor_at", "valor_ad", "total_servicios",
    "valor_neto_esperado", "diferencia", "estado", "registros_servicio",
]


def conciliar_facturas(datos: dict[str, pd.DataFrame], tolerancia: float = 1.0) -> pd.DataFrame:
    """Concilia el valor neto de cada factura (AF) con sus servicios RIPS.

    Supuesto documentado (el Anexo no define la fórmula): el valor neto a
    pagar por la entidad = suma de servicios - copago - descuentos, donde la
    suma de servicios toma AC.valor_neto (ya descuenta la cuota moderadora),
    AP.valor, AM.valor_total y AT.valor_total. Si la factura sólo trae
    descripción agrupada (AD), se usa AD.valor_total. La comisión (Art. 4)
    no se suma porque la reconoce la EPS por fuera del valor de los servicios.
    """
    af = datos.get("AF")
    if af is None or af.empty:
        return pd.DataFrame(columns=COLUMNAS_CONCILIACION)
    base = af.drop_duplicates(["envio_id", "num_factura"])[
        ["envio_id", "num_factura", "fecha_expedicion", "cod_entidad", "nombre_entidad", "num_contrato"]].copy()
    vals = af.drop_duplicates(["envio_id", "num_factura"])
    base["valor_neto_af"] = a_numero(vals["valor_neto"]).to_numpy()
    for c in ("valor_copago", "valor_comision", "valor_descuentos"):
        base[c] = a_numero(vals[c]).fillna(0).to_numpy()
    conteo = pd.Series(0, index=base.index)
    for tipo in list(VALOR_SERVICIO) + ["AD"]:
        col = f"valor_{tipo.lower()}"
        df = datos.get(tipo)
        if df is None or df.empty:
            base[col] = 0.0
            continue
        campo = VALOR_SERVICIO.get(tipo, "valor_total")
        g = (df.assign(_v=a_numero(df[campo]).fillna(0))
               .groupby(["envio_id", "num_factura"])["_v"].agg(["sum", "size"]).reset_index())
        m = base[["envio_id", "num_factura"]].merge(g, how="left", on=["envio_id", "num_factura"])
        base[col] = m["sum"].fillna(0).to_numpy()
        conteo = conteo + m["size"].fillna(0).to_numpy()
    detalle = base[["valor_ac", "valor_ap", "valor_am", "valor_at"]].sum(axis=1)
    base["total_servicios"] = detalle.where(detalle > 0, base["valor_ad"])
    base["valor_neto_esperado"] = base["total_servicios"] - base["valor_copago"] - base["valor_descuentos"]
    base["diferencia"] = (base["valor_neto_af"] - base["valor_neto_esperado"]).round(2)
    base["estado"] = "CONCILIADA"
    base.loc[base["diferencia"].abs() > tolerancia, "estado"] = "DIFERENCIA"
    base.loc[base["valor_neto_af"].isna(), "estado"] = "SIN VALOR AF"
    base.loc[conteo.to_numpy() == 0, "estado"] = "SIN SERVICIOS"
    base["registros_servicio"] = conteo.astype(int).to_numpy()
    return base[COLUMNAS_CONCILIACION].reset_index(drop=True)


def incidencias_conciliacion(conc: pd.DataFrame, af: pd.DataFrame, inc: RegistroIncidencias) -> None:
    if conc.empty:
        return
    dif = conc[conc["estado"] == "DIFERENCIA"]
    if dif.empty:
        return
    ubic = af.drop_duplicates(["envio_id", "num_factura"])[["envio_id", "num_factura", "archivo", "linea"]]
    b = dif.merge(ubic, on=["envio_id", "num_factura"], how="left")
    b["tipo_archivo"] = "AF"
    b["campo"] = "valor_neto"
    b["valor"] = b["valor_neto_af"].astype(str)
    b["mensaje"] = ("Factura " + b["num_factura"].astype(str) + ": neto AF " + b["valor_neto_af"].astype(str)
                    + " vs. servicios - copago - descuentos " + b["valor_neto_esperado"].round(2).astype(str)
                    + " (diferencia " + b["diferencia"].astype(str) + ")")
    inc.agregar_bloque(b, "DIFERENCIA_VALOR_FACTURA", mensaje=None)


__all__ = [
    "validar_campos", "validar_envios", "validar_periodo_facturas", "conciliar_facturas",
    "incidencias_conciliacion", "a_fecha", "a_numero", "a_fecha_hora", "normalizar_codigo", "ESPECIFICACIONES",
]

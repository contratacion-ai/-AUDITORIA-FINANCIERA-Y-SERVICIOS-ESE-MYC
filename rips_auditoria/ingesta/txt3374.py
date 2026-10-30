"""Lector de archivos RIPS planos (TXT separados por coma, Res. 3374/2000 Anexo Técnico No. 1)."""
from __future__ import annotations

import re
from datetime import datetime

from ..config import CampoRips, diccionario_rips
from .incidencias import Incidencias

RE_NOMBRE = re.compile(r"^(CT|AF|US|AD|AC|AP|AU|AH|AN|AM|AT)([A-Za-z0-9_\-]*)\.txt$", re.I)
RE_NORMATIVO = re.compile(r"^(CT|AF|US|AD|AC|AP|AU|AH|AN|AM|AT)\d{1,6}\.txt$", re.I)
RE_FECHA = re.compile(r"^\d{2}/\d{2}/\d{4}$")
RE_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
RE_CIE10 = re.compile(r"^[A-Z]\d{2}[0-9X]$")
# Campo de texto libre que absorbe comas sobrantes (descripciones con coma).
CAMPO_TEXTO = {"AF": "nombre_entidad", "AM": "nombre_generico", "AT": "nombre_servicio"}


def tipo_y_remision(nombre: str) -> tuple[str | None, str | None, bool]:
    m = RE_NOMBRE.match(nombre)
    if not m:
        return None, None, False
    rem = re.sub(r"(?i)_?CORREGIDO$", "", m.group(2))
    return m.group(1).upper(), rem, bool(RE_NORMATIVO.match(nombre))


def decodificar(datos: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return datos.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return datos.decode("latin-1", errors="replace"), "latin-1"


def reparar(texto: str) -> tuple[str, bool]:
    """Repara doble codificación UTF-8 leída como cp1252 (p. ej. 'Ã“' -> 'Ó'), línea a línea."""
    if "Ã" not in texto and "Â" not in texto:
        return texto, False
    cambio = False
    salida = []
    for linea in texto.split("\n"):
        if "Ã" in linea or "Â" in linea:
            try:
                linea2 = linea.encode("cp1252").decode("utf-8")
                cambio = cambio or linea2 != linea
                linea = linea2
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
        salida.append(linea)
    return "\n".join(salida), cambio


def _num(v: str):
    v = v.strip()
    if v == "":
        return None
    try:
        f = float(v)
    except ValueError:
        return v
    return int(f) if f.is_integer() and "." not in v else f


def validar_campo(c: CampoRips, valor: str, registro: dict, inc: Incidencias, base: dict) -> None:
    """Reglas de campo del Anexo: obligatorio, longitud, formato y catálogo."""
    def add(regla, sev, msg):
        inc.add(**base, campo=c.nombre, valor=valor, regla=regla, severidad=sev, mensaje=msg)

    if valor == "":
        cond = c.obligatorio
        req = cond == "SI"
        if cond.startswith("SI cuando"):
            m = re.match(r"SI cuando (\w+)=(\w+)", cond)
            req = bool(m) and str(registro.get(m.group(1), "")).strip() == m.group(2)
        if req:
            add("CAMPO_OBLIGATORIO", "ERROR", f"{c.dato}: obligatorio sin diligenciar")
        return
    if c.longitud and len(valor) > c.longitud:
        add("LONGITUD_EXCEDIDA", "ERROR", f"{c.dato}: longitud máxima {c.longitud}")
    t = c.tipo
    if t.startswith("Fecha"):
        ok = bool(RE_FECHA.match(valor))
        if ok:
            try:
                datetime.strptime(valor, "%d/%m/%Y")
            except ValueError:
                ok = False
        if not ok:
            add("FORMATO_FECHA", "ERROR", f"{c.dato}: fecha inválida, se espera dd/mm/aaaa")
    elif t.startswith("Hora") and not RE_HORA.match(valor):
        add("FORMATO_HORA", "ERROR", f"{c.dato}: hora inválida, se espera hh:mm")
    elif t.startswith("Valor") or t == "Entero":
        try:
            float(valor)
        except ValueError:
            add("FORMATO_NUMERICO", "ERROR", f"{c.dato}: valor numérico inválido")
    elif t.startswith("Código CIE") and not RE_CIE10.match(valor.upper()):
        add("FORMATO_CIE10", "ADVERTENCIA", f"{c.dato}: no tiene formato CIE-10")
    elif t == "Código de catálogo" and c.permitidos:
        v = valor.zfill(2) if all(len(k) == 2 and k.isdigit() for k in c.permitidos) and valor.isdigit() else valor
        if v not in c.permitidos:
            if valor in c.extendidos or v in c.extendidos:
                add("VALOR_CATALOGO_EXTENDIDO", "ADVERTENCIA",
                    f"{c.dato}: '{valor}' no previsto en Res. 3374, válido en Res. 2275/2023")
            else:
                add("VALOR_NO_PERMITIDO", "ERROR", f"{c.dato}: '{valor}' fuera de la tabla del Anexo")


def leer_txt(tipo: str, datos: bytes, archivo: str, inc: Incidencias, meta: dict) -> list[dict]:
    """Devuelve registros con nombres técnicos; las incidencias no detienen la lectura."""
    campos = diccionario_rips()[tipo]
    n = len(campos)
    texto, enc = decodificar(datos)
    base = {"archivo": archivo, "tipo_archivo": tipo, "hospital": meta.get("hospital"),
            "periodo": meta.get("periodo"), "envio_id": meta.get("envio_id")}
    if enc != "utf-8-sig":
        inc.add(**base, regla="CODIFICACION_NO_UTF8", severidad="INFO", mensaje=f"Leído como {enc}")
    texto, rep = reparar(texto)
    if rep:
        inc.add(**base, regla="TEXTO_REPARADO", severidad="INFO", mensaje="Caracteres con doble codificación reparados")
    lineas = texto.splitlines()
    regs: list[dict] = []
    vistos: dict[str, int] = {}
    for i, linea in enumerate(lineas, start=1):
        b = {**base, "linea": i}
        if not linea.strip():
            if i < len(lineas):
                inc.add(**b, regla="LINEA_VACIA", severidad="ADVERTENCIA", mensaje="Línea en blanco")
            continue
        vals = linea.split(",")
        if len(vals) == n + 1 and vals[-1].strip() == "":
            vals = vals[:-1]
            inc.add(**b, regla="SEPARADOR_FINAL", severidad="ADVERTENCIA", mensaje="Registro termina en coma")
        if len(vals) > n and tipo in CAMPO_TEXTO:
            k = [c.nombre for c in campos].index(CAMPO_TEXTO[tipo])
            extra = len(vals) - n
            vals = vals[:k] + [",".join(vals[k:k + extra + 1])] + vals[k + extra + 1:]
            inc.add(**b, regla="CANTIDAD_CAMPOS", severidad="ADVERTENCIA",
                    mensaje=f"Coma dentro de '{CAMPO_TEXTO[tipo]}'; se unieron {extra + 1} fragmentos")
        if len(vals) != n:
            inc.add(**b, regla="CANTIDAD_CAMPOS", severidad="ERROR",
                    mensaje=f"Tiene {len(vals)} campos, el Anexo define {n}; se excluye del análisis")
            continue
        if any(v.strip().startswith('"') for v in vals):
            vals = [v.strip().strip('"') for v in vals]
            inc.add(**b, regla="COMILLAS", severidad="ADVERTENCIA", mensaje="Campos entre comillas; se retiraron")
        vals = [v.strip() for v in vals]
        crudo = dict(zip([c.nombre for c in campos], vals))
        for c, v in zip(campos, vals):
            validar_campo(c, v, crudo, inc, b)
        firma = linea.strip()
        if firma in vistos:
            inc.add(**b, regla="REGISTRO_DUPLICADO", severidad="ADVERTENCIA",
                    mensaje=f"Idéntico a la línea {vistos[firma]} del mismo archivo")
        else:
            vistos[firma] = i
        reg = {}
        for c, v in zip(campos, vals):
            reg[c.nombre] = _num(v) if (c.tipo.startswith("Valor") or c.tipo == "Entero") else (v or None)
        reg["linea"] = i
        regs.append(reg)
    if not regs:
        inc.add(**base, regla="ARCHIVO_VACIO", severidad="ERROR", mensaje="El archivo no contiene registros")
    return regs

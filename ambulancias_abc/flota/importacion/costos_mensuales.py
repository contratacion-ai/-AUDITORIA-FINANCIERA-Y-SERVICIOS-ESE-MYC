"""Lector del informe «Costos por ambulancia y mes» del área de mantenimiento
del parque automotor (libro con una hoja por sede y una hoja Resumen).

Hoja de sede: encabezado con las columnas Placa, Vehículo, Combustible,
Rend. esperado (km/gal), Mes, Mantenimiento ($), Viajes, Km estimados,
Galones esperados, Combustible asignado ($) y Observación; una fila por
ambulancia y mes, hasta la fila «TOTAL SEDE». Las columnas se ubican por su
nombre, no por su posición.

Hoja Resumen: tabla «CÓMO SE REPARTE EL COMBUSTIBLE REAL» (combustible real
de toda la flota, consumo esperado, factor y lo asignado a las ambulancias)
y «RESUMEN POR MES» (combustible real de la flota y periodo del reporte).

Textos como «sin registro», «—» o «Pendiente» se leen como dato faltante,
nunca como cero; el mantenimiento en 0 sí es un dato (mes sin mantenimiento).
No se recalcula ninguna cifra del libro: se leen los valores y se validan
contra los totales que trae (fórmulas guardadas por Excel).
"""

from __future__ import annotations

import re
from collections import defaultdict

from openpyxl import load_workbook

from . import hallazgo, normalizar

FUENTE = "Costos por ambulancia y mes"
MESES = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6, "JULIO": 7, "AGOSTO": 8,
    "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12,
}
# nombre normalizado de la columna -> clave
COLUMNAS = {
    "PLACA": "placa", "VEHICULO": "vehiculo", "COMBUSTIBLE": "tipo_combustible", "REND. ESPERADO (KM/GAL)": "rendimiento",
    "MES": "mes", "MANTENIMIENTO ($)": "mantenimiento", "VIAJES": "viajes", "KM ESTIMADOS": "km",
    "GALONES ESPERADOS": "galones_esperados", "COMBUSTIBLE ASIGNADO ($)": "combustible", "OBSERVACION": "observacion",
}
OBLIGATORIAS = ("placa", "mes", "mantenimiento")


def _numero(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace("$", "").replace(" ", "")
        if re.fullmatch(r"-?[\d.]+(,\d+)?", s):
            s = s.replace(".", "").replace(",", ".") if "," in s else s
            try:
                return float(s)
            except ValueError:
                return None
    return None


def _anio(wb, nombre_archivo: str) -> int | None:
    for ws in wb.worksheets[:2]:
        for fila in ws.iter_rows(min_row=1, max_row=8, values_only=True):
            for v in fila:
                if isinstance(v, str):
                    m = re.search(r"\b(20\d\d)\b", v)
                    if m and ("COSTOS" in normalizar(v) or "ENERO" in normalizar(v)):
                        return int(m.group(1))
    m = re.search(r"(20\d\d)", nombre_archivo or "")
    return int(m.group(1)) if m else None


def _titulo_sede(ws) -> str:
    for fila in ws.iter_rows(min_row=1, max_row=6, values_only=True):
        for v in fila:
            if isinstance(v, str) and "SEDE" in normalizar(v):
                return v.split("SEDE", 1)[-1].strip(" –-") if "SEDE" in v else v
    return ws.title


def _leer_resumen(ws, anio, salida):
    """Tablas de combustible real de la flota."""
    filas = list(ws.iter_rows(values_only=True))
    for i, fila in enumerate(filas):
        textos = [normalizar(v) if isinstance(v, str) else "" for v in fila]
        if textos and textos[0] == "MES" and any("COMBUSTIBLE REAL REPORTADO" in t for t in textos):
            col = {t: j for j, t in enumerate(textos) if t}
            def c(nombre):
                return next((j for t, j in col.items() if t.startswith(nombre)), None)
            j_real, j_esp, j_fac, j_amb, j_otr, j_obs = (
                c("COMBUSTIBLE REAL REPORTADO"), c("CONSUMO ESPERADO"), c("FACTOR"), c("ASIGNADO A LAS AMBULANCIAS"),
                c("ASIGNADO A LOS DEMAS"), c("OBSERVACION"),
            )
            for f in filas[i + 1:]:
                mes = MESES.get(normalizar(f[0]) if isinstance(f[0], str) else "")
                if not mes:
                    break
                d = salida["flota"].setdefault(mes, {})
                d.update({
                    "real_flota": _numero(f[j_real]) if j_real is not None else None,
                    "esperado_flota": _numero(f[j_esp]) if j_esp is not None else None,
                    "factor": _numero(f[j_fac]) if j_fac is not None else None,
                    "asignado_ambulancias": _numero(f[j_amb]) if j_amb is not None else None,
                    "asignado_otros": _numero(f[j_otr]) if j_otr is not None else None,
                    "observacion": str(f[j_obs] or "")[:500] if j_obs is not None else "",
                })
        if textos and textos[0] == "MES" and any(t.startswith("COMBUSTIBLE REAL") and "TODA LA FLOTA" in t for t in textos) \
                and any("PERIODO" in t for t in textos):
            j_real = next(j for j, t in enumerate(textos) if t.startswith("COMBUSTIBLE REAL"))
            j_per = next(j for j, t in enumerate(textos) if "PERIODO" in t)
            j_man = next((j for j, t in enumerate(textos) if t.startswith("MANTENIMIENTO AMBULANCIAS")), None)
            for f in filas[i + 1:]:
                mes = MESES.get(normalizar(f[0]) if isinstance(f[0], str) else "")
                if not mes:
                    break
                d = salida["flota"].setdefault(mes, {})
                if d.get("real_flota") is None:
                    d["real_flota"] = _numero(f[j_real])
                d["periodo_reporte"] = str(f[j_per] or "")[:200]
                if j_man is not None and _numero(f[j_man]) is not None:
                    salida["control_mantenimiento"][mes] = _numero(f[j_man])


def leer_costos_mensuales(archivo, nombre_archivo: str = "") -> dict:
    """Devuelve {"anio", "filas": [...], "flota": {mes: {...}}, "hallazgos": [...]}."""
    wb = load_workbook(archivo, data_only=True, read_only=False)
    anio = _anio(wb, nombre_archivo)
    salida = {"anio": anio, "filas": [], "flota": {}, "hallazgos": [], "control_mantenimiento": {}, "hojas": []}
    fuente = nombre_archivo or FUENTE
    if anio is None:
        salida["hallazgos"].append(hallazgo(fuente, "título", "FALTANTE", "No se encontró el año del informe (p. ej. «ENERO A OCTUBRE DE 2026»)", "ALTA"))
        return salida
    for ws in wb.worksheets:
        if normalizar(ws.title) == "RESUMEN":
            _leer_resumen(ws, anio, salida)
            continue
        encabezado = None
        sede_texto = _titulo_sede(ws)
        for r, fila in enumerate(ws.iter_rows(values_only=True), start=1):
            textos = [normalizar(v) if isinstance(v, str) else "" for v in fila]
            if encabezado is None:
                if "PLACA" in textos and "MES" in textos:
                    encabezado = {COLUMNAS[t]: j for j, t in enumerate(textos) if t in COLUMNAS}
                    faltan = [k for k in OBLIGATORIAS if k not in encabezado]
                    if faltan:
                        salida["hallazgos"].append(hallazgo(fuente, f"{ws.title}!fila {r}", "ENCABEZADO",
                                                            f"Faltan columnas: {', '.join(faltan)}", "ALTA"))
                        break
                    salida["hojas"].append(ws.title)
                continue
            if any(t.startswith("TOTAL") for t in textos):
                break
            placa = fila[encabezado["placa"]]
            mes_txt = fila[encabezado["mes"]]
            if not placa or not mes_txt:
                continue
            placa = re.sub(r"[^A-Z0-9]", "", str(placa).upper())
            mes = MESES.get(normalizar(mes_txt))
            ubic = f"{ws.title}!fila {r}"
            if not mes:
                salida["hallazgos"].append(hallazgo(fuente, ubic, "INCONSISTENCIA", f"Mes no reconocido: {mes_txt}", "MEDIA"))
                continue

            def val(clave):
                j = encabezado.get(clave)
                return fila[j] if j is not None and j < len(fila) else None

            mant_raw = val("mantenimiento")
            mant = _numero(mant_raw)
            if mant is None:
                salida["hallazgos"].append(hallazgo(fuente, ubic, "FALTANTE", f"{placa} {mes_txt}: mantenimiento no numérico ({mant_raw!r})", "MEDIA"))
            comb_raw = val("combustible")
            comb = _numero(comb_raw)
            salida["filas"].append({
                "hoja": ws.title, "fila": r, "sede_texto": sede_texto, "placa": placa,
                "vehiculo": str(val("vehiculo") or "")[:120], "tipo_combustible": normalizar(val("tipo_combustible") or ""),
                "rendimiento": _numero(val("rendimiento")), "anio": anio, "mes": mes,
                "mantenimiento": mant, "viajes": _numero(val("viajes")), "km": _numero(val("km")),
                "galones_esperados": _numero(val("galones_esperados")), "combustible": comb,
                "combustible_estado": "" if comb is not None else str(comb_raw or "").strip(),
                "observacion": str(val("observacion") or "")[:500],
            })
        if encabezado is None and normalizar(ws.title) != "RESUMEN":
            salida["hallazgos"].append(hallazgo(fuente, ws.title, "ENCABEZADO", "Hoja sin encabezado Placa/Mes: no se leyó", "BAJA"))

    # Validaciones contra los totales del propio libro
    por_mes = defaultdict(lambda: {"mantenimiento": 0.0, "combustible": 0.0})
    vistos = defaultdict(list)
    for f in salida["filas"]:
        por_mes[f["mes"]]["mantenimiento"] += f["mantenimiento"] or 0
        por_mes[f["mes"]]["combustible"] += f["combustible"] or 0
        vistos[(f["placa"], f["mes"])].append(f"{f['hoja']}!{f['fila']}")
    for (placa, mes), ubic in vistos.items():
        if len(ubic) > 1:
            salida["hallazgos"].append(hallazgo(fuente, "; ".join(ubic), "DUPLICADO", f"{placa} aparece {len(ubic)} veces en el mes {mes}", "ALTA"))
    for mes, d in salida["flota"].items():
        asignado = d.get("asignado_ambulancias")
        if asignado is not None and abs(asignado - por_mes[mes]["combustible"]) > 1:
            salida["hallazgos"].append(hallazgo(
                fuente, f"Resumen mes {mes}", "INCONSISTENCIA",
                f"Combustible asignado a las ambulancias {asignado:,.0f} en el Resumen, pero las hojas de sede suman "
                f"{por_mes[mes]['combustible']:,.0f}", "ALTA"))
        if d.get("factor") and d["factor"] > 2:
            salida["hallazgos"].append(hallazgo(
                fuente, f"Resumen mes {mes}", "SUPUESTO",
                f"Factor real ÷ esperado = {d['factor']:g}: el consumo esperado cubre solo parte del mes; el combustible "
                "real se repartió en proporción al consumo esperado de cada vehículo.", "MEDIA"))
    for mes, control in salida["control_mantenimiento"].items():
        if abs(control - por_mes[mes]["mantenimiento"]) > 1:
            salida["hallazgos"].append(hallazgo(
                fuente, f"Resumen mes {mes}", "INCONSISTENCIA",
                f"Mantenimiento del Resumen {control:,.0f} distinto de la suma de las hojas {por_mes[mes]['mantenimiento']:,.0f}", "ALTA"))
    salida["totales_mes"] = {m: dict(v) for m, v in sorted(por_mes.items())}
    return salida

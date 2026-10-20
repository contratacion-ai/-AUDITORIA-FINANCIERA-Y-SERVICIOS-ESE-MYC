"""Lector de la hoja 'TRASLADOS Anexo 3' de ANEXOS DE TARIFAS 2026.xlsx.

Tolera celdas combinadas en ORIGEN, encabezados desalineados y fórmulas con
#REF!. Solo usa la columna de la vigencia pedida (2026 por defecto); las
columnas históricas se leen únicamente para reportar inconsistencias.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from . import es_error_excel, hallazgo, normalizar

FUENTE = "ANEXOS DE TARIFAS 2026.xlsx"


def _buscar_hoja(wb):
    for ws in wb.worksheets:
        if "TRASLADO" in normalizar(ws.title):
            return ws
    raise ValueError("No se encontró una hoja de traslados (p. ej. 'TRASLADOS Anexo 3')")


def leer_anexo_traslados(ruta_o_archivo, vigencia: int = 2026, nombre_fuente: str = FUENTE) -> dict:
    wb_f = load_workbook(ruta_o_archivo, data_only=False)
    if hasattr(ruta_o_archivo, "seek"):
        ruta_o_archivo.seek(0)
    wb_v = load_workbook(ruta_o_archivo, data_only=True)
    ws_f = _buscar_hoja(wb_f)
    ws_v = wb_v[ws_f.title]
    hoja = ws_f.title
    hallazgos: list[dict] = []

    def ref(fila, col):
        return f"{hoja}!{get_column_letter(col)}{fila}"

    # 1. Fila de encabezado (ORIGEN / DESTINO / Km) y fila de vigencias encima.
    fila_enc = None
    for r in range(1, min(ws_f.max_row, 40) + 1):
        if normalizar(ws_f.cell(r, 1).value) == "ORIGEN":
            fila_enc = r
            break
    if fila_enc is None:
        raise ValueError("No se encontró la fila de encabezado 'ORIGEN'")
    col_km = next(
        (c for c in range(1, ws_f.max_column + 1) if normalizar(ws_f.cell(fila_enc, c).value) == "KM"), 3
    )

    fila_vig = fila_enc - 1
    col_tarifa = None
    vigencias = {}
    for c in range(1, ws_f.max_column + 1):
        v = ws_f.cell(fila_vig, c).value
        if isinstance(v, (int, float)) and 2000 <= v <= 2100:
            vigencias[c] = int(v)
            if int(v) == vigencia:
                col_tarifa = c
    if col_tarifa is None:
        raise ValueError(f"No se encontró la columna de vigencia {vigencia}")

    # Encabezados desalineados: texto en una columna de vigencia de la fila de encabezado.
    for c, anio in vigencias.items():
        enc = ws_f.cell(fila_enc, c).value
        if isinstance(enc, str):
            hallazgos.append(
                hallazgo(
                    nombre_fuente,
                    ref(fila_enc, c),
                    "ENCABEZADO",
                    f"Encabezado desalineado: la columna de vigencia {anio} tiene el texto '{enc.strip()}' "
                    f"en lugar del año; el valor por km vigente se toma de la columna {vigencia}.",
                    "MEDIA",
                    enc,
                )
            )
    # Años faltantes en la serie de vigencias.
    anios = sorted(vigencias.values())
    faltantes = [a for a in range(anios[0], anios[-1]) if a not in anios] if anios else []
    if faltantes:
        hallazgos.append(
            hallazgo(
                nombre_fuente,
                f"{hoja}!fila {fila_vig}",
                "FALTANTE",
                f"La serie de vigencias no tiene columna para los años {', '.join(map(str, faltantes))}; "
                f"la tarifa {vigencia} no se puede trazar contra la vigencia anterior.",
                "BAJA",
            )
        )

    valor_km = ws_v.cell(fila_enc, col_tarifa).value
    if not isinstance(valor_km, (int, float)):
        hallazgos.append(
            hallazgo(nombre_fuente, ref(fila_enc, col_tarifa), "FALTANTE", "No hay valor por kilómetro numérico", "ALTA", valor_km)
        )
        valor_km = None

    # 2. Filas de datos.
    rutas = []
    tarifa_base = None
    origen_actual = None
    ambito_geo = "DEPARTAMENTAL"
    errores_por_col: dict[int, list[int]] = defaultdict(list)
    vistos = Counter()

    for r in range(fila_enc + 1, ws_f.max_row + 1):
        a_txt = ws_f.cell(r, 1).value
        b_txt = ws_f.cell(r, 2).value
        km = ws_v.cell(r, col_km).value
        a_norm = normalizar(a_txt)

        if a_norm == "ORIGEN":
            continue  # encabezado repetido (versión depurada del anexo)
        if a_norm.startswith("NOTA"):
            if "KILOMETRO" in a_norm or "KM" in a_norm:
                hallazgos.append(
                    hallazgo(
                        nombre_fuente,
                        ref(r, 1),
                        "INCONSISTENCIA",
                        "La nota de actualización anuncia un ajuste por la ruta alterna hacia Saravena "
                        "(+30 km aprox. por el cierre del puente El Tigre), pero las tarifas a SARAVENA "
                        "siguen calculadas con los km originales. Verificar si el ajuste debe aplicarse.",
                        "ALTA",
                    )
                )
            continue
        if a_norm.startswith("TRASLADO BASICO"):
            valor = ws_v.cell(r, col_tarifa).value
            tarifa_base = {"descripcion": str(a_txt).strip(), "valor": valor, "fila": r}
            continue
        if a_norm == "NACIONALES":
            ambito_geo = "NACIONAL"
            continue
        if a_txt and not b_txt and km is None:
            continue  # títulos, firmas
        if a_txt:
            origen_actual = str(a_txt).strip()
        if not b_txt:
            continue

        destino = str(b_txt).strip()
        # Celdas con error en cualquier columna de la fila.
        for c in range(1, ws_f.max_column + 1):
            vf, vv = ws_f.cell(r, c).value, ws_v.cell(r, c).value
            if es_error_excel(vf) or es_error_excel(vv):
                errores_por_col[c].append(r)
        # Contenido fuera de la tabla (p. ej. cálculos sueltos).
        for c in range(col_tarifa + 1, ws_f.max_column + 1):
            vf = ws_f.cell(r, c).value
            if vf not in (None, "") and not (isinstance(vf, str) and not vf.strip()):
                hallazgos.append(
                    hallazgo(
                        nombre_fuente,
                        ref(r, c),
                        "INCONSISTENCIA",
                        f"Celda fuera de la tabla con contenido '{vf}' (resultado {ws_v.cell(r, c).value}); se ignora.",
                        "BAJA",
                        vf,
                    )
                )

        tarifa = ws_v.cell(r, col_tarifa).value
        formula = ws_f.cell(r, col_tarifa).value
        obs = []
        if not isinstance(km, (int, float)):
            hallazgos.append(hallazgo(nombre_fuente, ref(r, col_km), "FALTANTE", f"Ruta {origen_actual} - {destino} sin km", "ALTA", km))
            km = None
        if not isinstance(tarifa, (int, float)):
            hallazgos.append(
                hallazgo(nombre_fuente, ref(r, col_tarifa), "FALTANTE", f"Ruta {origen_actual} - {destino} sin tarifa {vigencia} numérica", "ALTA", tarifa)
            )
            tarifa = None
        elif km is not None and valor_km and abs(tarifa - km * valor_km) > 1:
            obs.append(f"tarifa {tarifa} ≠ km×valor ({km * valor_km})")
            hallazgos.append(
                hallazgo(
                    nombre_fuente,
                    ref(r, col_tarifa),
                    "INCONSISTENCIA",
                    f"Ruta {origen_actual} - {destino}: tarifa {tarifa:,.0f} distinta de km × valor km ({km * valor_km:,.0f})",
                    "ALTA",
                )
            )
        if isinstance(formula, str) and formula.startswith("=") and "$" not in formula:
            hallazgos.append(
                hallazgo(
                    nombre_fuente,
                    ref(r, col_tarifa),
                    "FORMULA",
                    f"Fórmula con referencia relativa al valor por km ('{formula}'); funciona, pero se rompe si se copia.",
                    "BAJA",
                    formula,
                )
            )
        # Saltos atípicos en la serie histórica (solo informativo).
        hist = [
            (vigencias[c], ws_v.cell(r, c).value)
            for c in sorted(vigencias)
            if c != col_tarifa and isinstance(ws_v.cell(r, c).value, (int, float))
        ]
        for (a1, v1), (a2, v2) in zip(hist, hist[1:]):
            if v1 and (v2 / v1 > 2 or v2 / v1 < 0.5):
                hallazgos.append(
                    hallazgo(
                        nombre_fuente,
                        f"{hoja}!fila {r}",
                        "INCONSISTENCIA",
                        f"Ruta {origen_actual} - {destino}: valor histórico {a1}={v1:,.0f} y {a2}={v2:,.0f} "
                        "(salto atípico, posible error de digitación). No afecta la tarifa vigente.",
                        "BAJA",
                    )
                )
        if km and hist:
            ult_anio, ult = hist[-1]
            if ult / km < 1000:
                hallazgos.append(
                    hallazgo(
                        nombre_fuente,
                        f"{hoja}!fila {r}",
                        "INCONSISTENCIA",
                        f"Ruta {origen_actual} - {destino}: valor {ult_anio} de {ult:,.0f} para {km} km "
                        f"({ult / km:,.0f} $/km) es atípicamente bajo frente a rutas similares.",
                        "BAJA",
                    )
                )

        clave = (normalizar(origen_actual), normalizar(destino))
        vistos[clave] += 1
        rutas.append(
            {
                "origen": origen_actual,
                "destino": destino,
                "km": km,
                "valor_km": valor_km,
                "tarifa": tarifa,
                "vigencia": vigencia,
                "ambito_geografico": ambito_geo,
                "fila": r,
                "observacion": "; ".join(obs),
            }
        )

    for c, filas in sorted(errores_por_col.items()):
        anio = vigencias.get(c)
        hallazgos.append(
            hallazgo(
                nombre_fuente,
                f"{hoja}!{get_column_letter(c)}{filas[0]}:{get_column_letter(c)}{filas[-1]}",
                "ERROR_FORMULA",
                f"{len(filas)} celdas con #REF! en la columna {get_column_letter(c)}"
                + (f" (vigencia {anio})" if anio else "")
                + ": la fórmula apunta a una celda eliminada. No se usa ni se reemplaza.",
                "MEDIA" if c != col_tarifa else "ALTA",
            )
        )
    for (o, d), n in vistos.items():
        if n > 1:
            hallazgos.append(
                hallazgo(nombre_fuente, hoja, "DUPLICADO", f"La ruta {o} - {d} aparece {n} veces", "MEDIA")
            )
    if tarifa_base is None:
        hallazgos.append(hallazgo(nombre_fuente, hoja, "FALTANTE", "No se encontró la tarifa de traslado básico", "MEDIA"))

    return {
        "vigencia": vigencia,
        "valor_km": valor_km,
        "tarifa_base": tarifa_base,
        "rutas": rutas,
        "hallazgos": hallazgos,
        "hoja": hoja,
    }

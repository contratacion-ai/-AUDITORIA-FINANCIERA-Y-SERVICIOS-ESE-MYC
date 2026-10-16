"""Lector de COSTOS AMBULANCIAS.xlsx.

* Hoja 'AMBULANCIAS': partidas mensuales del centro de costos (columna
  'VR. PESOS MES'). Se cargan como referencia de costos indirectos; por
  defecto NO entran al ABC (el administrador decide cuáles incluir).
* Hoja 'TABLA DE VALOR TH MYC': tabla salarial de la tripulación 2026.

Además se reportan celdas con error (#DIV/0!, [object Object], ...), la
duración de procedimiento en texto y las fórmulas que dependen de celdas
vacías.
"""

from __future__ import annotations

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from . import es_error_excel, hallazgo, normalizar

FUENTE = "COSTOS AMBULANCIAS.xlsx"


def leer_costos_ambulancias(archivo, tripulacion_dia_estudio: float | None = None, nombre_fuente: str = FUENTE) -> dict:
    wb_f = load_workbook(archivo, data_only=False)
    if hasattr(archivo, "seek"):
        archivo.seek(0)
    wb_v = load_workbook(archivo, data_only=True)
    hallazgos, partidas, tripulacion = [], [], []
    externos: dict[str, list[str]] = {}

    # Errores de Excel en todo el libro.
    for ws in wb_f.worksheets:
        wv = wb_v[ws.title]
        for fila in ws.iter_rows():
            for celda in fila:
                vf = celda.value
                vv = wv[celda.coordinate].value
                if es_error_excel(vf) or es_error_excel(vv):
                    hallazgos.append(
                        hallazgo(
                            nombre_fuente,
                            f"{ws.title}!{celda.coordinate}",
                            "ERROR_FORMULA",
                            f"Celda con error '{vv if es_error_excel(vv) else vf}' (fórmula: {vf}); no se usa.",
                            "MEDIA",
                            vf,
                        )
                    )
                elif isinstance(vf, str) and vf.startswith("=") and "[" in vf and "]" in vf:
                    externos.setdefault(ws.title, []).append(celda.coordinate)
        if ws.title in externos:
            celdas = externos[ws.title]
            hallazgos.append(
                hallazgo(
                    nombre_fuente,
                    f"{ws.title}!{celdas[0]}:{celdas[-1]}",
                    "FORMULA",
                    f"{len(celdas)} celdas vinculadas a un libro externo ('[1]TALENTO HUMANO') que no se entregó; "
                    "los valores mostrados son los últimos guardados y no se pueden recalcular.",
                    "BAJA",
                )
            )

    hoja = next((ws for ws in wb_f.worksheets if normalizar(ws.title) == "AMBULANCIAS"), None)
    if hoja is not None:
        hv = wb_v[hoja.title]
        # Fila de encabezado 'RECURSOS' / 'VR. PESOS MES'.
        fila_enc, col_mes, col_hora, col_proc = None, None, None, None
        for r in range(1, 30):
            for c in range(1, hoja.max_column + 1):
                t = normalizar(hoja.cell(r, c).value)
                if t == "VR. PESOS MES":
                    fila_enc, col_mes = r, c
                elif t == "VR. PESOS HORA":
                    col_hora = c
                elif t == "VALOR PROCEDIMIENTO":
                    col_proc = c
            if fila_enc:
                break
        if fila_enc:
            categoria = ""
            for r in range(fila_enc + 1, hoja.max_row + 1):
                cat = hoja.cell(r, col_mes - 2).value
                concepto = hoja.cell(r, col_mes - 1).value
                if normalizar(cat).startswith("TOTAL"):
                    total = hv.cell(r, col_mes).value
                    suma = sum(p["valor_mensual"] for p in partidas)
                    if isinstance(total, (int, float)) and abs(total - suma) > 1:
                        hallazgos.append(
                            hallazgo(nombre_fuente, f"{hoja.title}!{get_column_letter(col_mes)}{r}", "INCONSISTENCIA",
                                     f"Total {total:,.0f} ≠ suma de partidas {suma:,.0f}", "MEDIA")
                        )
                    break
                if cat:
                    categoria = str(cat).strip()
                if not concepto:
                    continue
                valor = hv.cell(r, col_mes).value
                if isinstance(valor, (int, float)) and valor != 0:
                    partidas.append(
                        {
                            "categoria": categoria,
                            "concepto": str(concepto).strip(),
                            "valor_mensual": round(float(valor), 2),
                            "ubicacion": f"{hoja.title}!{get_column_letter(col_mes)}{r}",
                        }
                    )
            # Columna 'VR. PESOS HORA' vacía -> valor procedimiento en cero.
            if col_hora and all(hv.cell(r, col_hora).value in (None, 0) for r in range(fila_enc + 1, fila_enc + 30)):
                hallazgos.append(
                    hallazgo(nombre_fuente, f"{hoja.title}!{get_column_letter(col_hora)}", "FALTANTE",
                             "La columna 'VR. PESOS HORA' está vacía: todos los 'VALOR PROCEDIMIENTO' dan 0 y el "
                             "costo calculado del procedimiento no es utilizable.", "ALTA")
                )
        # Duración del procedimiento.
        for r in range(1, 20):
            if "DURACION" in normalizar(hoja.cell(r, 3).value):
                d_val = hoja.cell(r, 4).value
                textos = [hoja.cell(r, c).value for c in range(4, 8) if isinstance(hoja.cell(r, c).value, str)]
                if not isinstance(d_val, (int, float)):
                    hallazgos.append(
                        hallazgo(nombre_fuente, f"{hoja.title}!D{r}", "FALTANTE",
                                 f"La duración del procedimiento no es numérica (D{r} vacía; aparece como texto {textos}). "
                                 f"Las fórmulas que multiplican por D{r} dan 0. Además, 20 minutos no representa un "
                                 "traslado: el informe técnico mide 3,6 h/día en misión en promedio.", "ALTA")
                    )
                break
        # Valor EPS sin CUPS asociado.
        cups = None
        for r in range(1, 15):
            if "CODIGO CUPS" in normalizar(hoja.cell(r, 3).value):
                cups = hoja.cell(r, 4).value
        for r in range(1, hoja.max_row + 1):
            if normalizar(hoja.cell(r, 3).value) == "VR. ACTUAL" and col_proc:
                v = hv.cell(r, col_proc).value
                hallazgos.append(
                    hallazgo(nombre_fuente, f"{hoja.title}!{get_column_letter(col_proc)}{r}", "SUPUESTO",
                             f"'VR. ACTUAL' (valor EPS) de {v:,.0f} sin código CUPS asociado (CUPS: {cups or 'vacío'}); "
                             "no corresponde a las tarifas por km del Anexo 3. Requiere validación.", "ALTA", v)
                )

    hoja_th = next((ws for ws in wb_f.worksheets if "TABLA DE VALOR TH" in normalizar(ws.title)), None)
    if hoja_th is not None:
        hv = wb_v[hoja_th.title]
        bloque_2026 = False
        for r in range(1, hoja_th.max_row + 1):
            titulo = normalizar(hoja_th.cell(r, 2).value)
            if "AMBULANCIAS" in titulo and "2026" in titulo:
                bloque_2026 = True
                continue
            if not bloque_2026 or titulo in ("", "CARGO"):
                continue
            total = hv.cell(r, 4).value
            horas_mes = hv.cell(r, 5).value
            horas_dia = hv.cell(r, 7).value
            valor_dia = hv.cell(r, 8).value
            if isinstance(total, (int, float)):
                tripulacion.append(
                    {"cargo": titulo, "salario_mes": total, "horas_mes": horas_mes, "horas_dia": horas_dia, "valor_dia": valor_dia}
                )
        if tripulacion:
            dia_th = sum(t["valor_dia"] or 0 for t in tripulacion)
            mes = sum(t["salario_mes"] for t in tripulacion)
            if tripulacion_dia_estudio and abs(dia_th - tripulacion_dia_estudio) > 1:
                hallazgos.append(
                    hallazgo(nombre_fuente, f"{hoja_th.title}!H20", "INCONSISTENCIA",
                             f"Tripulación por día según la tabla TH (7 h × valor hora con 191 h/mes) = {dia_th:,.0f}; "
                             f"el informe técnico usa {tripulacion_dia_estudio:,.0f} (= {mes:,.0f} ÷ 30). "
                             "Se usa la cifra del informe (fuente principal); la de la tabla TH queda como parámetro de contraste.",
                             "MEDIA")
                )
            if any((t["horas_dia"] or 0) < 24 for t in tripulacion):
                hallazgos.append(
                    hallazgo(nombre_fuente, f"{hoja_th.title}!G17:G19", "SUPUESTO",
                             "Cada cargo trabaja 7 h/día, pero la ambulancia se valora disponible 24 h con una sola "
                             "tripulación. Si se requieren turnos para cubrir 24 h, el costo de tripulación estaría subestimado.",
                             "MEDIA")
                )
    # Número de ambulancias declarado (hoja 'Hoja1': CANTIDAD).
    n_ambulancias = None
    for ws in wb_v.worksheets:
        for fila in ws.iter_rows():
            for celda in fila:
                if normalizar(celda.value) == "CANTIDAD":
                    vecino = ws.cell(celda.row, celda.column + 1).value
                    if isinstance(vecino, (int, float)):
                        n_ambulancias = int(vecino)
    personal = sum(p["valor_mensual"] for p in partidas if normalizar(p["categoria"]) == "COSTOS PERSONAL")
    if tripulacion and n_ambulancias and personal:
        mes_tripulacion = sum(t["salario_mes"] for t in tripulacion) * n_ambulancias
        hallazgos.append(
            hallazgo(nombre_fuente, "AMBULANCIAS!E12:E14", "INCONSISTENCIA",
                     f"El costo de personal del centro de costos es {personal:,.0f}/mes, mientras que "
                     f"{n_ambulancias} tripulaciones completas cuestan {mes_tripulacion:,.0f}/mes. El personal de planta "
                     "(E12) está en 0, de modo que el centro de costos no recoge toda la tripulación. Las partidas de "
                     "personal no se suman al ABC para no duplicar la tripulación del informe.", "ALTA")
        )
    return {"partidas": partidas, "tripulacion": tripulacion, "hallazgos": hallazgos, "n_ambulancias": n_ambulancias}

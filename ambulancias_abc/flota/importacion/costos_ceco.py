"""Lector de COSTOS AMBULANCIAS.xlsx.

Reconoce dos versiones del libro:

* Modelo ABC corregido por la ESE (hoja "MODELO COSTOS ABC PARAMETRO"):
  partidas mensuales del centro de costos que entran al ABC repartidas por
  ambulancia ("TOTAL AMBULANCIAS"), tasa de gastos administrativos, tabla
  por placa (km/día, h/día, combustible y mantenimiento) y tabla salarial de
  la tripulación con horas por día de cada cargo.
* Versión anterior (hoja "AMBULANCIAS"): las mismas partidas, solo como
  referencia (no entran al ABC por defecto).

Las columnas se ubican por su encabezado, no por su posición. Se reportan
celdas con error, vínculos a libros externos y cifras que no cuadran; nada
se corrige.
"""

from __future__ import annotations

import re

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from . import es_error_excel, hallazgo, normalizar

FUENTE = "COSTOS AMBULANCIAS.xlsx"
CATEGORIAS_PERSONAL = {"COSTOS PERSONAL"}


def _numero(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _buscar(ws, texto, max_fila=None):
    """Primera celda cuyo texto normalizado es ``texto`` -> (fila, columna)."""
    for fila in ws.iter_rows(max_row=max_fila or ws.max_row):
        for celda in fila:
            if normalizar(celda.value) == texto:
                return celda.row, celda.column
    return None


def _a_la_derecha(ws, fila, col, max_saltos=4):
    for c in range(col + 1, col + 1 + max_saltos):
        v = _numero(ws.cell(fila, c).value)
        if v is not None:
            return v, c
    return None, None


def leer_costos_ambulancias(archivo, tripulacion_dia_estudio: float | None = None, nombre_fuente: str = FUENTE) -> dict:
    wb_f = load_workbook(archivo, data_only=False)
    if hasattr(archivo, "seek"):
        archivo.seek(0)
    wb_v = load_workbook(archivo, data_only=True)
    hallazgos, partidas, tripulacion, perfiles = [], [], [], []
    parametros: dict[str, float] = {}
    externos: dict[str, list[str]] = {}

    # 1. Errores de Excel y vínculos externos en todo el libro.
    for ws in wb_f.worksheets:
        wv = wb_v[ws.title]
        for fila in ws.iter_rows():
            for celda in fila:
                vf = celda.value
                vv = wv[celda.coordinate].value
                if es_error_excel(vf) or es_error_excel(vv):
                    hallazgos.append(
                        hallazgo(nombre_fuente, f"{ws.title}!{celda.coordinate}", "ERROR_FORMULA",
                                 f"Celda con error '{vv if es_error_excel(vv) else vf}' (fórmula: {vf}); no se usa.", "MEDIA", vf)
                    )
                elif isinstance(vf, str) and vf.startswith("=") and "[" in vf and "]" in vf:
                    externos.setdefault(ws.title, []).append(celda.coordinate)
        if ws.title in externos:
            celdas = externos[ws.title]
            hallazgos.append(
                hallazgo(nombre_fuente, f"{ws.title}!{celdas[0]}:{celdas[-1]}", "FORMULA",
                         f"{len(celdas)} celdas vinculadas a un libro externo que no se entregó; los valores mostrados "
                         "son los últimos guardados y no se pueden recalcular.", "BAJA")
            )

    # 2. Hoja de partidas: la que tiene el encabezado "VR. PESOS MES".
    hoja = hv = None
    pos = None
    for ws in wb_f.worksheets:
        pos = _buscar(ws, "VR. PESOS MES", 40)
        if pos:
            hoja, hv = ws, wb_v[ws.title]
            break
    es_modelo = bool(hoja is not None and "MODELO" in normalizar(hoja.title))

    if hoja is not None:
        fila_enc, col_mes = pos
        categoria = ""
        for r in range(fila_enc + 1, hoja.max_row + 1):
            cat = hoja.cell(r, col_mes - 2).value
            concepto = hoja.cell(r, col_mes - 1).value
            if normalizar(cat).startswith("TOTAL"):
                total = _numero(hv.cell(r, col_mes).value)
                suma = sum(p["valor_mensual"] for p in partidas)
                if total is not None and abs(total - suma) > 1:
                    hallazgos.append(
                        hallazgo(nombre_fuente, f"{hoja.title}!{get_column_letter(col_mes)}{r}", "INCONSISTENCIA",
                                 f"Total {total:,.0f} ≠ suma de partidas {suma:,.0f}", "MEDIA")
                    )
                break
            if cat:
                categoria = str(cat).strip()
            if not concepto:
                continue
            valor = _numero(hv.cell(r, col_mes).value)
            if valor:
                es_personal = normalizar(categoria) in CATEGORIAS_PERSONAL
                partidas.append({
                    "categoria": categoria,
                    "concepto": str(concepto).strip(),
                    "valor_mensual": round(valor, 2),
                    "ubicacion": f"{hoja.title}!{get_column_letter(col_mes)}{r}",
                    # En el modelo corregido las partidas (sin personal) entran al ABC.
                    "incluir_en_abc": es_modelo and not es_personal,
                })

        # Número de ambulancias del reparto y tasa de gastos administrativos.
        p_amb = _buscar(hoja, "TOTAL AMBULANCIAS")
        if p_amb:
            n, c = _a_la_derecha(hv, *p_amb)
            if n:
                parametros["ambulancias_reparto_indirectos"] = n
                if n != 21:
                    hallazgos.append(
                        hallazgo(nombre_fuente, f"{hoja.title}!{get_column_letter(c)}{p_amb[0]}", "SUPUESTO",
                                 f"El modelo reparte los costos indirectos entre {n:g} ambulancias; el informe técnico costea 21. "
                                 f"La parte de la unidad adicional (1/{n:g}) no se asigna a la flota costeada. "
                                 "Ajustable en el parámetro ambulancias_reparto_indirectos.", "MEDIA", n)
                    )
        p_adm = _buscar(hoja, "GASTOS ADMINISTRATIVOS")
        if p_adm and es_modelo:
            r_adm = p_adm[0]
            tasa, c = _a_la_derecha(hv, *p_adm, max_saltos=2)
            if tasa is not None and 0 < tasa < 1:
                parametros["tasa_gastos_admin"] = tasa
                for col in range(c + 1, c + 3):
                    f = hoja.cell(r_adm, col).value
                    if isinstance(f, str) and f.startswith("=") and re.search(r"\*\s*E%d\s*\+" % r_adm, f.replace("$", "")):
                        hallazgos.append(
                            hallazgo(nombre_fuente, f"{hoja.title}!{get_column_letter(col)}{r_adm}", "FORMULA",
                                     f"La fórmula '{f}' suma el costo base al recargo; la celda vecina calcula solo el "
                                     f"{tasa:.1%}. La aplicación aplica el {tasa:.1%} sobre el total de costos.", "BAJA", f)
                        )

        # Tabla por placa (Tabla 5 del informe, con las correcciones del modelo).
        p_placa = _buscar(hoja, "PLACA")
        if p_placa and es_modelo:
            r0, c0 = p_placa
            enc = {normalizar(hoja.cell(r0, c).value): c for c in range(c0, c0 + 12)}
            col = {k: enc.get(v) for k, v in {
                "km_dia": "KM/DIA", "horas_dia": "H/DIA", "combustible_dia": "COMBUSTIBLE/DIA",
                "mantenimiento_dia": "MANTENIMIENTO /DIA",
            }.items()}
            if all(col.values()):
                for r in range(r0 + 1, r0 + 40):
                    placa = re.sub(r"[^A-Z0-9]", "", normalizar(hv.cell(r, c0).value))
                    if not re.fullmatch(r"[A-Z]{3}\d{3}", placa or ""):
                        if perfiles:
                            break
                        continue
                    fila = {k: _numero(hv.cell(r, c).value) for k, c in col.items()}
                    if all(v is not None for v in fila.values()):
                        perfiles.append({"placa": placa, **fila, "ubicacion": f"{hoja.title}!fila {r}"})

    # 3. Tabla salarial de la tripulación (ubicada por encabezados).
    hoja_th = next((ws for ws in wb_v.worksheets if "TABLA DE VALOR TH" in normalizar(ws.title)), None)
    if hoja_th is not None:
        inicio = 1
        for r in range(1, hoja_th.max_row + 1):
            t = " ".join(normalizar(hoja_th.cell(r, c).value) for c in range(1, 4))
            if "AMBULANCIAS" in t and "2026" in t:
                inicio = r
        enc_r, cols = None, {}
        for r in range(inicio, hoja_th.max_row + 1):
            fila = {normalizar(hoja_th.cell(r, c).value): c for c in range(1, hoja_th.max_column + 1)}
            if "CARGO" in fila and "HORAS DIA" in fila:
                enc_r, cols = r, fila
                break
        if enc_r:
            c_cargo = cols["CARGO"]
            c_sal = cols.get("TOTAL MES") or cols.get("TOTAL")
            for r in range(enc_r + 1, hoja_th.max_row + 1):
                cargo = normalizar(hoja_th.cell(r, c_cargo).value)
                if not cargo or cargo.startswith("TOTAL"):
                    break
                salario = _numero(hoja_th.cell(r, c_sal).value) if c_sal else None
                horas_mes = _numero(hoja_th.cell(r, cols.get("HORAS MES", 0)).value) if cols.get("HORAS MES") else None
                horas_dia = _numero(hoja_th.cell(r, cols["HORAS DIA"]).value)
                valor_dia = _numero(hoja_th.cell(r, cols["VALOR TRIPULACION"]).value) if cols.get("VALOR TRIPULACION") else None
                if valor_dia is None and salario and horas_mes and horas_dia:
                    valor_dia = salario / horas_mes * horas_dia
                if salario:
                    tripulacion.append({"cargo": cargo, "salario_mes": salario, "horas_mes": horas_mes,
                                        "horas_dia": horas_dia, "valor_dia": valor_dia})
        if tripulacion:
            dia_th = sum(t["valor_dia"] or 0 for t in tripulacion)
            if es_modelo:
                parametros["tripulacion_dia"] = round(dia_th, 4)
            if tripulacion_dia_estudio and abs(dia_th - tripulacion_dia_estudio) > 1:
                if es_modelo:
                    texto = (f"Tripulación por día del modelo corregido (valor hora × horas/día de cada cargo) = {dia_th:,.2f}; "
                             f"el informe técnico usaba {tripulacion_dia_estudio:,.0f}. Se aplica la del modelo.")
                    sev = "BAJA"
                else:
                    texto = (f"Tripulación por día según la tabla TH = {dia_th:,.0f}; el informe técnico usa "
                             f"{tripulacion_dia_estudio:,.0f}. Se usa la cifra del informe (fuente principal).")
                    sev = "MEDIA"
                hallazgos.append(hallazgo(nombre_fuente, f"{hoja_th.title}", "INCONSISTENCIA", texto, sev))
            if any((t["horas_dia"] or 0) < 24 for t in tripulacion):
                hallazgos.append(
                    hallazgo(nombre_fuente, f"{hoja_th.title}!HORAS DIA", "SUPUESTO",
                             "Cada cargo se costea por las horas de su jornada diaria, pero la ambulancia se valora disponible "
                             "24 h con una sola tripulación. Si se requieren turnos para cubrir 24 h, el costo de tripulación "
                             "estaría subestimado.", "MEDIA")
                )

    # 4. Controles de la versión anterior del libro (si aplican).
    if hoja is not None and not es_modelo:
        personal = sum(p["valor_mensual"] for p in partidas if normalizar(p["categoria"]) in CATEGORIAS_PERSONAL)
        n = parametros.get("ambulancias_reparto_indirectos") or _cantidad_hoja1(wb_v)
        if tripulacion and n and personal:
            mes_trip = sum(t["salario_mes"] for t in tripulacion) * n
            hallazgos.append(
                hallazgo(nombre_fuente, f"{hoja.title}!personal", "INCONSISTENCIA",
                         f"El costo de personal del centro de costos es {personal:,.0f}/mes, mientras que {n:g} tripulaciones "
                         f"completas cuestan {mes_trip:,.0f}/mes. Las partidas de personal no se suman al ABC para no "
                         "duplicar la tripulación.", "ALTA")
            )
        for r in range(1, 20):
            if "DURACION" in normalizar(hoja.cell(r, 3).value) and _numero(hoja.cell(r, 4).value) is None:
                hallazgos.append(
                    hallazgo(nombre_fuente, f"{hoja.title}!D{r}", "FALTANTE",
                             f"La duración del procedimiento no es numérica (D{r}); las fórmulas que multiplican por D{r} "
                             "dan 0. Además, 20 minutos no representa un traslado (3,6 h/día en misión según el informe).", "ALTA")
                )

    return {
        "partidas": partidas,
        "tripulacion": tripulacion,
        "parametros": parametros,
        "perfiles": perfiles,
        "es_modelo": es_modelo,
        "hallazgos": hallazgos,
        "n_ambulancias": parametros.get("ambulancias_reparto_indirectos") or _cantidad_hoja1(wb_v),
    }


def _cantidad_hoja1(wb_v):
    for ws in wb_v.worksheets:
        pos = _buscar(ws, "CANTIDAD")
        if pos:
            n, _ = _a_la_derecha(ws, *pos, max_saltos=2)
            if n:
                return int(n)
    return None

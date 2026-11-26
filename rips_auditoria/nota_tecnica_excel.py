"""Salidas en Excel de la nota técnica (ver ``nota_tecnica``).

1. **Nota técnica** (``NOTA_TECNICA_<EPS>_<periodo>.xlsx`` o la plantilla en
   blanco): objetivo, fuente de datos, criterios de inclusión y exclusión,
   metodología de conteo por mes, tabla resumen de pacientes por CUPS y mes,
   observaciones de calidad de datos y conclusiones. Los datos del contrato
   quedan entre corchetes ``[ ]`` para completar.
2. **Hoja de cálculo** (``HOJA_PACIENTES_CUPS_<EPS>_<periodo>.xlsx``):

   * ``Datos``: una fila por paciente único, código y mes (tabla de Excel
     ``tblPacientes``). Es la fuente de las fórmulas y de la tabla dinámica.
   * ``Matriz``: todos los códigos de la nota (también los que están en 0)
     por mes, con ``CONTAR.SI.CONJUNTO`` sobre ``Datos`` (se recalcula si se
     agregan filas), totales por código y por mes con ``SUMA``, conteo de
     meses sin información y columna de observaciones.
   * ``Tabla dinámica``: tabla dinámica nativa (CUPS × mes, cuenta de
     pacientes) que Excel actualiza al abrir.
   * ``Validación``: meses sin información en todo el período y por código.
   * ``Instrucciones``: las fórmulas sugeridas.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import xlsxwriter
from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.pivot.cache import CacheDefinition, CacheField, CacheSource, SharedItems, WorksheetSource
from openpyxl.pivot.fields import Index, Text
from openpyxl.pivot.record import Record, RecordList
from openpyxl.pivot.table import (
    DataField,
    FieldItem,
    Location,
    PivotField,
    PivotTableStyle,
    RowColField,
    RowColItem,
    TableDefinition,
)
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from .nota_tecnica import CURSOS, MESES_ES, MOTIVOS, SIN_CURSO, nombre_mes

VERDE, VINO, GRIS = "18A020", "A01038", "5F6B63"
INSTITUCION = "ESE Departamental de Primer Nivel Moreno y Clavijo"


# ---------------------------------------------------------------------------
# 1. Nota técnica (plantilla y versión con datos)
# ---------------------------------------------------------------------------

def _secciones(r: dict | None) -> list[tuple[str, list[str]]]:
    """Texto de cada sección; con ``r`` (resumen) se completan las cifras calculadas."""
    def v(clave, por_defecto):
        return str(r[clave]) if r and clave in r else por_defecto

    def w(clave, por_defecto):  # dato de la nota técnica (puede venir vacío)
        return str(r[clave]) if r and r.get(clave) else por_defecto

    periodo = (f"{nombre_mes(r['desde'])} a {nombre_mes(r['hasta'])}" if r else "[MES INICIAL] a [MES FINAL] de [AÑO]")
    fuentes = ", ".join(r["fuentes"]) if r else "[RIPS TXT Res. 3374 / RIPS JSON Res. 2275]"
    sedes = ", ".join(r["sedes"]) if r and r["sedes"] else "[SEDES / HOSPITALES]"
    return [
        ("1. IDENTIFICACIÓN DEL CONTRATO", [
            f"Prestador: {INSTITUCION} · NIT 900034131",
            f"EPS: {v('eps', '[NOMBRE DE LA EPS]')} · Régimen: {w('regimen', '[SUBSIDIADO / CONTRIBUTIVO]')}",
            f"Contrato N.º: {w('contrato', '[NÚMERO DEL CONTRATO]')} · Modalidad: CAPITACIÓN · Valor per cápita: [VALOR]",
            f"Población asignada: {w('poblacion', '[NÚMERO DE AFILIADOS]')} · Municipio(s): {w('ambito', '[MUNICIPIOS]')}"
            + (f" · Sedes: {', '.join(r['sedes_ambito'])}" if r and r.get("sedes_ambito") else ""),
            f"Período analizado: {periodo} · Fecha de corte: [FECHA DE CORTE]",
            "Elaboró: [NOMBRE Y CARGO] · Revisó: [NOMBRE Y CARGO] · Fecha: [FECHA]",
        ]),
        ("2. OBJETIVO", [
            "Establecer el número de pacientes únicos atendidos por cada código CUPS de la nota técnica del contrato "
            f"de capitación con {v('eps', '[EPS]')}, por mes, a partir de los RIPS reportados, como soporte para el "
            "seguimiento de la ejecución del contrato, la evaluación de metas y la negociación de la nota técnica.",
            "[OBJETIVOS ESPECÍFICOS ADICIONALES]",
        ]),
        ("3. FUENTE DE DATOS", [
            f"Archivos procesados: {fuentes} · {v('archivos', '[N.º]')} archivo(s) · sedes: {sedes}.",
            "RIPS Res. 3374 de 2000 (TXT separados por comas): AC (cod_consulta, fecha_consulta), AP (cod_procedimiento, "
            "fecha_procedimiento), AT (cod_servicio) y AM (cod_medicamento); paciente = tipo + número de identificación; "
            "EPS por el AF de cada factura; edad por el US.",
            "RIPS JSON Res. 2275 de 2023: usuarios[].tipoDocumentoIdentificacion y numDocumentoIdentificacion; "
            "codConsulta, codProcedimiento, codTecnologiaSalud; fechaInicioAtencion, fechaDispensAdmon, "
            "fechaSuministroTecnologia; fechaNacimiento.",
            f"Nota técnica de referencia: {v('codigos_nota', '[N.º]')} códigos (CUPS y medicamentos MD con su CUM homólogo). "
            "[NOMBRE DEL ARCHIVO DE LA NOTA TÉCNICA Y FECHA]",
            f"Registros leídos: {v('registros_leidos', '[N.º]')} · de códigos de la nota: {v('registros_nota', '[N.º]')} · "
            f"de la EPS: {v('registros_eps', '[N.º]')} · incluidos en el conteo: {v('registros_incluidos', '[N.º]')}.",
        ]),
        ("4. CRITERIOS DE INCLUSIÓN Y EXCLUSIÓN", [
            "Inclusión: atenciones de la EPS del contrato (AF: código o nombre de la entidad) con un código de la nota "
            "técnica, paciente identificado y fecha de prestación dentro del período.",
            "Medicamentos MD: se reconocen por su CUM homólogo (sin ceros a la izquierda) o por el código MD.",
            f"Exclusión (reportada por código): {', '.join(t.lower() for t in MOTIVOS.values())}; atenciones de otra EPS "
            f"({v('registros_otra_eps', '[N.º]')} registros) y códigos fuera de la nota ({v('registros_fuera_nota', '[N.º]')}).",
            "Se incluyen y se señalan: registros con errores de diligenciamiento (validación Res. 3374), duplicados "
            "(contados una vez), AT/AM sin fecha de prestación (mes de la factura) y JSON sin EPS (se asume la del contrato).",
            "[CRITERIOS ADICIONALES DEL CONTRATO]",
        ]),
        ("5. METODOLOGÍA DE CONTEO POR MES", [
            "Unidad de conteo: paciente único = tipo + número de identificación.",
            "Por cada código CUPS y mes se cuentan los pacientes distintos atendidos: varias atenciones del mismo paciente "
            "con el mismo código en el mismo mes cuentan 1. Un paciente atendido en dos meses cuenta en cada mes.",
            "Cada atención se asigna al mes de su fecha de prestación; no se distribuye entre meses.",
            "El total del período por código es la suma de los meses (pacientes-mes); los pacientes únicos del período "
            "se informan aparte.",
            "Los códigos de la nota sin registros se reportan con 0 y la observación «SIN REGISTROS».",
            "Curso de vida (Res. 3280 de 2018) por la edad del paciente al momento de la atención.",
            "Procesamiento: rips_auditoria.nota_tecnica (Python + pandas), reproducible con los mismos archivos.",
        ]),
        ("6. TABLA RESUMEN", [
            "Ver la hoja «Tabla resumen» (pacientes únicos por código CUPS y mes, con totales) y la hoja de cálculo "
            "adjunta con fórmulas y tabla dinámica." if r else
            "[PEGAR O VINCULAR LA TABLA DE PACIENTES ÚNICOS POR CÓDIGO CUPS Y MES — ver hoja «Tabla resumen»]",
        ]),
        ("7. OBSERVACIONES DE CALIDAD DE DATOS", ([
            f"Registros duplicados: {r['duplicados']} (contados una vez).",
            f"Registros anulados (nota crédito): {r['excl_anulado']} (excluidos).",
            f"Registros con errores de diligenciamiento: {r['con_errores']} (incluidos y señalados por código).",
            f"Registros sin identificación: {r['excl_sin_id']} · sin fecha válida: {r['excl_sin_fecha']} · "
            f"fuera del período: {r['excl_fuera_periodo']} (excluidos).",
            f"AT/AM con mes tomado de la factura: {r['fecha_de_factura']} · JSON con EPS asumida: {r['json_eps_asumida']}.",
            f"Códigos de la nota sin registros: {r['codigos_sin_registros']} de {r['codigos_nota']} (en 0, ver hoja «Calidad»).",
            "Meses sin información en todo el período: " + (", ".join(nombre_mes(m) for m in r["meses_sin_informacion"])
                                                             or "ninguno") + ".",
            *([f"Sedes sin municipio reconocido (sus atenciones no entran en ninguna nota por municipio): "
               f"{', '.join(r['sedes_sin_ambito'])}. Agregue la sede en nota_tecnica.sedes_municipio de la configuración."]
              if r.get("sedes_sin_ambito") else []),
            *([f"ATENCIÓN: ninguna atención de los códigos de la nota coincidió con la EPS {r['eps']}. Entidades "
               f"encontradas en los AF: {r.get('entidades_encontradas') or 'ninguna'}. Verifique el nombre o código de "
               "la EPS (opción «EPS del contrato» o nota_tecnica.eps_codigos)."]
              if not r["registros_eps"] and r.get("registros_otra_eps") else []),
            "[OTRAS OBSERVACIONES DEL AUDITOR]",
        ] if r else [
            "Registros duplicados: [N.º] · anulados: [N.º] · con errores de diligenciamiento: [N.º] · fuera de período: [N.º]",
            "Códigos sin registros: [N.º] · meses sin información: [MESES]",
            "[DETALLE POR CÓDIGO — ver hoja «Calidad»]",
        ])),
        ("8. CONCLUSIONES", ([
            f"Se atendieron {r['pacientes_unicos_periodo']:,} pacientes únicos en {r['codigos_con_pacientes']} de los "
            f"{r['codigos_nota']} códigos de la nota técnica.".replace(",", "."),
            *([f"Cumplimiento de metas (atenciones): {r['cumplimiento_meta']:.1%} — {r['atenciones_con_meta']:,} "
               f"atenciones frente a una meta de {r['meta_periodo']:,.0f} en {r['codigos_con_meta']} códigos con meta; "
               f"{r['codigos_bajo_80']} código(s) por debajo del 80 % (ver «Cumplimiento de metas»)."
               .replace(",", "X").replace(".", ",").replace("X", ".")]
              if r.get("cumplimiento_meta") is not None else []),
            "[CONCLUSIONES SOBRE EL CUMPLIMIENTO DE METAS Y LA EJECUCIÓN DEL CONTRATO]",
            "[RECOMENDACIONES PARA LA EPS Y PARA LA ESE]",
        ] if r else ["[CONCLUSIONES]", "[RECOMENDACIONES]"])),
        ("9. FIRMAS", ["[NOMBRE] — [CARGO] · ESE", "[NOMBRE] — [CARGO] · EPS"]),
    ]


def exportar_nota(ruta: str | Path, calc: dict | None = None, catalogo: pd.DataFrame | None = None,
                  anio: str | None = None) -> Path:
    """Nota técnica en Excel. Sin ``calc`` genera la plantilla con corchetes; con ``catalogo``
    (``nota_tecnica.cargar_catalogo``) la «Tabla resumen» trae todos los servicios de la
    nota técnica (código, descripción, estado y tipo) con los meses para diligenciar."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    r = calc["resumen"] if calc else None
    wb = xlsxwriter.Workbook(str(ruta), {"strings_to_numbers": False, "strings_to_formulas": False})
    tit = wb.add_format({"bold": True, "font_size": 16, "font_color": f"#{VERDE}"})
    sub = wb.add_format({"italic": True, "font_color": f"#{GRIS}"})
    sec = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": f"#{VERDE}", "border": 1})
    txt = wb.add_format({"text_wrap": True, "valign": "top", "border": 1, "border_color": "#DCE3DD"})
    cor = wb.add_format({"text_wrap": True, "valign": "top", "border": 1, "border_color": "#DCE3DD",
                         "bg_color": "#FFF8DC", "font_color": f"#{VINO}"})
    ws = wb.add_worksheet("Nota técnica")
    ws.set_column(0, 0, 140)
    ws.write(0, 0, "NOTA TÉCNICA · NÚMERO DE PACIENTES POR CÓDIGO CUPS Y MES · CONTRATO DE CAPITACIÓN", tit)
    ws.write(1, 0, f"{INSTITUCION} · {'generada ' + dt.datetime.now().strftime('%Y-%m-%d %H:%M') if r else 'PLANTILLA'}"
                   " · los campos entre [corchetes] se completan con los datos del período y del contrato", sub)
    fila = 3
    for titulo, lineas in _secciones(r):
        ws.write(fila, 0, titulo, sec)
        fila += 1
        for linea in lineas:
            ws.write(fila, 0, linea, cor if "[" in linea else txt)
            fila += 1
        fila += 1
    ws.set_column(0, 0, 120)
    if calc:
        _hoja_tabla_resumen(wb, calc)
        _hoja_cumplimiento(wb, calc)
        _hoja_calidad(wb, calc)
        _hoja_curso_vida(wb, calc)
    else:
        _tabla_plantilla(wb, catalogo, anio, cor)
    for hoja in wb.worksheets():  # impresión: carta horizontal, ajustada al ancho de la página
        hoja.set_landscape()
        hoja.set_paper(1)
        hoja.fit_to_pages(1, 0)
        hoja.set_margins(0.4, 0.4, 0.5, 0.5)
        hoja.set_footer("&L" + INSTITUCION + "&RPágina &P de &N")
    wb.close()
    return ruta


def _tabla_plantilla(wb, catalogo: pd.DataFrame | None, anio: str | None, cor) -> None:
    """«Tabla resumen» de la plantilla: todos los servicios de la nota técnica × 12 meses,
    con celdas para diligenciar, totales por código y por mes con fórmulas y observaciones."""
    from xlsxwriter.utility import xl_col_to_name, xl_rowcol_to_cell

    w = wb.add_worksheet("Tabla resumen")
    enc = _enc(wb)
    txt = wb.add_format({"border": 1, "border_color": "#DCE3DD", "text_wrap": True, "valign": "top"})
    celda = wb.add_format({"border": 1, "border_color": "#DCE3DD", "bg_color": "#FFFBEA", "num_format": "#,##0",
                           "locked": False})
    tot = wb.add_format({"bold": True, "num_format": "#,##0", "border": 1, "bg_color": "#E8F6E9"})
    obs = wb.add_format({"border": 1, "border_color": "#DCE3DD", "bg_color": "#FFFBEA", "text_wrap": True, "locked": False})
    meses = [f"{m} {anio or '[AÑO]'}" for m in MESES_ES]
    cols = ["CÓDIGO CUPS", "DESCRIPCIÓN", "ESTADO", "TIPO", *meses, "TOTAL PACIENTES-MES", "OBSERVACIONES"]
    w.write_row(0, 0, cols, enc)
    filas = (catalogo[["codigo", "descripcion", "estado", "tipo"]].itertuples(index=False, name=None)
             if catalogo is not None and len(catalogo) else [("[CÓDIGO]", "[DESCRIPCIÓN]", "[ESTADO]", "[TIPO]")])
    n = 0
    for i, (cod, desc, est, tipo) in enumerate(filas, start=1):
        for j, v in enumerate((cod, desc, est, tipo)):
            w.write_string(i, j, str(v), cor if str(v).startswith("[") else txt)
        for j in range(4, 16):
            w.write_blank(i, j, None, celda)
        w.write_formula(i, 16, f"=SUM({xl_rowcol_to_cell(i, 4)}:{xl_rowcol_to_cell(i, 15)})", tot, 0)
        w.write_blank(i, 17, None, obs)
        n = i
    w.write(n + 1, 1, "TOTAL POR MES", tot)
    for j in range(4, 17):
        col = xl_col_to_name(j)
        w.write_formula(n + 1, j, f"=SUM({col}2:{col}{n + 1})", tot, 0)
    w.write(n + 2, 1, "VALIDACIÓN DEL MES", tot)
    for j in range(4, 16):
        col = xl_col_to_name(j)
        w.write_formula(n + 2, j, f'=IF({col}{n + 2}=0,"SIN INFORMACION","OK")', tot, "SIN INFORMACION")
    alerta = wb.add_format({"bg_color": "#FBEAEF", "font_color": f"#{VINO}", "bold": True})
    w.conditional_format(n + 2, 4, n + 2, 15, {"type": "cell", "criteria": "==", "value": '"SIN INFORMACION"', "format": alerta})
    w.freeze_panes(1, 4)
    w.autofilter(0, 0, n, len(cols) - 1)
    w.set_column(0, 0, 13)
    w.set_column(1, 1, 55)
    w.set_column(2, 3, 12)
    w.set_column(4, 16, 11)
    w.set_column(17, 17, 50)
    w.write(n + 4, 1, f"{n} servicio(s) de la nota técnica. Diligencie el número de pacientes únicos por mes "
                      "(o genere la nota desde la ventana de cargue con los RIPS para llenarla automáticamente).")


def _enc(wb):
    return wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": f"#{VERDE}", "border": 1, "text_wrap": True,
                          "valign": "vcenter"})


def _hoja_tabla_resumen(wb, calc):
    m, cat, cal = calc["matriz"], calc["catalogo"].set_index("codigo"), calc["calidad"]
    w = wb.add_worksheet("Tabla resumen")
    enc, num = _enc(wb), wb.add_format({"num_format": "#,##0", "border": 1, "border_color": "#DCE3DD"})
    cero = wb.add_format({"num_format": "#,##0", "border": 1, "border_color": "#DCE3DD", "font_color": f"#{VINO}",
                          "bg_color": "#FBEAEF"})
    tot = wb.add_format({"bold": True, "num_format": "#,##0", "border": 1, "bg_color": "#E8F6E9"})
    txt = wb.add_format({"border": 1, "border_color": "#DCE3DD", "text_wrap": True, "valign": "top"})
    con_meta = "meta_mes" in cat and cat["meta_mes"].notna().any()
    cols = ["CÓDIGO CUPS", "DESCRIPCIÓN", "ESTADO"] + [nombre_mes(x) for x in m.columns] + [
        "TOTAL PACIENTES-MES", "PACIENTES ÚNICOS DEL PERÍODO", "OBSERVACIONES"] + (
        ["ATENCIONES DEL PERÍODO", "META MES (nota técnica)", "META DEL PERÍODO", "% CUMPLIMIENTO"] if con_meta else [])
    pct = wb.add_format({"num_format": "0.0%", "border": 1, "border_color": "#DCE3DD"})
    for j, c in enumerate(cols):
        w.write(0, j, c, enc)
    n_m = len(m.columns)
    for i, (cod, fila) in enumerate(m.iterrows(), start=1):
        w.write_string(i, 0, cod, txt)
        w.write_string(i, 1, cat.loc[cod, "descripcion"], txt)
        w.write_string(i, 2, cat.loc[cod, "estado"], txt)
        for j, valor in enumerate(fila, start=3):
            w.write_number(i, j, int(valor), cero if valor == 0 else num)
        w.write_formula(i, 3 + n_m, f"=SUM({xlsxwriter.utility.xl_rowcol_to_cell(i, 3)}:"
                                     f"{xlsxwriter.utility.xl_rowcol_to_cell(i, 2 + n_m)})", tot, int(fila.sum()))
        w.write_number(i, 4 + n_m, int(cal.loc[cod, "pacientes_periodo"]), num)
        w.write_string(i, 5 + n_m, cal.loc[cod, "observaciones"], txt)
        meta = cat.loc[cod, "meta_mes"] if con_meta else None
        if con_meta:
            aten = int(cal.loc[cod, "atenciones_periodo"])
            w.write_number(i, 6 + n_m, aten, num)
        if con_meta and pd.notna(meta):
            c_at, c_meta, c_per = (xlsxwriter.utility.xl_rowcol_to_cell(i, j) for j in (6 + n_m, 7 + n_m, 8 + n_m))
            w.write_number(i, 7 + n_m, round(float(meta), 2), num)
            w.write_formula(i, 8 + n_m, f"={c_meta}*{n_m}", num, round(float(meta) * n_m, 2))
            w.write_formula(i, 9 + n_m, f'=IF({c_per}>0,{c_at}/{c_per},"")', pct,
                            (aten / (float(meta) * n_m)) if meta else "")
    ult = len(m)
    w.write(ult + 1, 1, "TOTAL POR MES", tot)
    for j in range(3, 4 + n_m):
        col = xlsxwriter.utility.xl_col_to_name(j)
        w.write_formula(ult + 1, j, f"=SUM({col}2:{col}{ult + 1})", tot)
    w.freeze_panes(1, 3)
    w.autofilter(0, 0, ult, len(cols) - 1)
    w.set_column(0, 0, 13)
    w.set_column(1, 1, 55)
    w.set_column(2, 2, 10)
    w.set_column(3, 4 + n_m, 11)
    w.set_column(5 + n_m, 5 + n_m, 60)
    if con_meta:
        w.set_column(6 + n_m, 9 + n_m, 14)
        _semaforo(wb, w, 1, 9 + n_m, ult)
        w.write(ult + 3, 1, "% cumplimiento = atenciones del período (sin duplicados) / (meta mensual de la nota × meses). "
                            "Semáforo: verde ≥ 100 %, amarillo 80 % a 99 %, rojo < 80 %. Detalle por mes en «Cumplimiento de metas».")


def _semaforo(wb, w, fila_ini, col, fila_fin):
    for criterio, valor, color, letra in ((">=", 1, "#E8F6E9", "#0F6B15"), ("between", (0.8, 0.9999), "#FFF4D6", "#8A5A00"),
                                          ("<", 0.8, "#FBEAEF", f"#{VINO}")):
        fmt = wb.add_format({"bg_color": color, "font_color": letra, "num_format": "0.0%", "bold": True})
        regla = ({"type": "cell", "criteria": "between", "minimum": valor[0], "maximum": valor[1], "format": fmt}
                 if criterio == "between" else {"type": "cell", "criteria": criterio, "value": valor, "format": fmt})
        w.conditional_format(fila_ini, col, fila_fin, col, regla)


def _hoja_cumplimiento(wb, calc):
    """Atenciones por mes contra la meta mensual de la nota técnica (sólo códigos con meta)."""
    cat = calc["catalogo"].set_index("codigo")
    if "meta_mes" not in cat or not cat["meta_mes"].notna().any():
        return
    at = calc["atenciones"]
    codigos = [c for c in at.index if pd.notna(cat.loc[c, "meta_mes"]) and cat.loc[c, "meta_mes"] > 0]
    w = wb.add_worksheet("Cumplimiento de metas")
    enc, num = _enc(wb), wb.add_format({"num_format": "#,##0", "border": 1, "border_color": "#DCE3DD"})
    dec = wb.add_format({"num_format": "#,##0.0", "border": 1, "border_color": "#DCE3DD"})
    pct = wb.add_format({"num_format": "0.0%", "border": 1, "border_color": "#DCE3DD"})
    tot = wb.add_format({"bold": True, "num_format": "#,##0", "border": 1, "bg_color": "#E8F6E9"})
    totp = wb.add_format({"bold": True, "num_format": "0.0%", "border": 1, "bg_color": "#E8F6E9"})
    meses = list(at.columns)
    n = len(meses)
    w.write_row(0, 0, ["CÓDIGO", "DESCRIPCIÓN", "AGRUPADOR", *[nombre_mes(m) for m in meses], "TOTAL ATENCIONES",
                       "META MES", "META DEL PERÍODO", "% CUMPLIMIENTO", "BRECHA (atenciones)"], enc)
    cel = xlsxwriter.utility.xl_rowcol_to_cell
    for i, cod in enumerate(codigos, start=1):
        meta = float(cat.loc[cod, "meta_mes"])
        w.write_string(i, 0, cod)
        w.write_string(i, 1, str(cat.loc[cod, "descripcion"]))
        w.write_string(i, 2, str(cat.loc[cod, "agrupador"] if "agrupador" in cat else ""))
        for j, m in enumerate(meses, start=3):
            w.write_number(i, j, int(at.loc[cod, m]), num)
        total = int(at.loc[cod].sum())
        w.write_formula(i, 3 + n, f"=SUM({cel(i, 3)}:{cel(i, 2 + n)})", num, total)
        w.write_number(i, 4 + n, round(meta, 2), dec)
        w.write_formula(i, 5 + n, f"={cel(i, 4 + n)}*{n}", dec, round(meta * n, 2))
        w.write_formula(i, 6 + n, f'=IF({cel(i, 5 + n)}>0,{cel(i, 3 + n)}/{cel(i, 5 + n)},"")', pct, total / (meta * n))
        w.write_formula(i, 7 + n, f"={cel(i, 3 + n)}-{cel(i, 5 + n)}", dec, round(total - meta * n, 2))
    ult = len(codigos)
    w.write(ult + 1, 1, "TOTAL", tot)
    for j in (*range(3, 4 + n), 5 + n, 7 + n):
        col = xlsxwriter.utility.xl_col_to_name(j)
        w.write_formula(ult + 1, j, f"=SUM({col}2:{col}{ult + 1})", tot)
    w.write_formula(ult + 1, 6 + n, f'=IF({cel(ult + 1, 5 + n)}>0,{cel(ult + 1, 3 + n)}/{cel(ult + 1, 5 + n)},"")', totp)
    _semaforo(wb, w, 1, 6 + n, ult + 1)
    w.freeze_panes(1, 3)
    w.autofilter(0, 0, ult, 7 + n)
    w.set_column(0, 0, 11)
    w.set_column(1, 1, 50)
    w.set_column(2, 2, 26)
    w.set_column(3, 7 + n, 12)
    w.write(ult + 3, 1, "Atenciones = registros incluidos de la EPS sin duplicados (mismo paciente, código, fecha y factura). "
                        "Meta mes = «Actividades mes» de la nota técnica (suma si el código está en varias actividades).")


def _hoja_calidad(wb, calc):
    cal, cat = calc["calidad"], calc["catalogo"].set_index("codigo")
    w = wb.add_worksheet("Calidad")
    enc, txt = _enc(wb), wb.add_format({"text_wrap": True, "valign": "top"})
    etiquetas = {"registros_eps": "Registros de la EPS", "registros_incluidos": "Incluidos", "pacientes_periodo": "Pacientes únicos",
                 **{f"excl_{k}": f"Excluidos: {v}" for k, v in MOTIVOS.items()},
                 "duplicados": "Duplicados (contados 1 vez)", "con_errores_validacion": "Con errores de diligenciamiento",
                 "fecha_de_factura": "Mes por factura (AT/AM)", "json_eps_asumida": "JSON con EPS asumida",
                 "sin_edad": "Sin edad", "registros_otra_eps": "De otra EPS", "meses_sin_informacion": "Meses sin pacientes",
                 "observaciones": "Observaciones"}
    cols = ["codigo", *etiquetas]
    w.write_row(0, 0, ["Código", "Descripción", *etiquetas.values()], enc)
    for i, (cod, r) in enumerate(cal.iterrows(), start=1):
        w.write_string(i, 0, cod)
        w.write_string(i, 1, cat.loc[cod, "descripcion"])
        for j, c in enumerate(cols[1:], start=2):
            (w.write_string(i, j, r[c], txt) if c == "observaciones" else w.write_number(i, j, int(r[c])))
    w.set_column(0, 0, 12)
    w.set_column(1, 1, 45)
    w.set_column(2, len(cols) - 1, 12)
    w.set_column(len(cols), len(cols), 90)
    w.freeze_panes(1, 2)
    w.autofilter(0, 0, len(cal), len(cols))
    f = calc["fuera_nota"]
    w2 = wb.add_worksheet("Códigos fuera de la nota")
    w2.write_row(0, 0, ["Código en RIPS/JSON", "Registros", "Pacientes (aprox.)"], enc)
    for i, (cod, r) in enumerate(f.iterrows(), start=1):
        w2.write_row(i, 0, [str(cod), int(r["registros"]), int(r["pacientes"])])
    w2.set_column(0, 2, 20)


def _hoja_curso_vida(wb, calc):
    cv, cat = calc["curso_vida"], calc["catalogo"].set_index("codigo")
    w = wb.add_worksheet("Curso de vida")
    enc = _enc(wb)
    w.write_row(0, 0, ["Código", "Descripción", *cv.columns, "Total"], enc)
    for i, (cod, fila) in enumerate(cv.iterrows(), start=1):
        w.write_string(i, 0, cod)
        w.write_string(i, 1, cat.loc[cod, "descripcion"])
        for j, valor in enumerate(fila, start=2):
            w.write_number(i, j, int(valor))
        w.write_number(i, 2 + len(cv.columns), int(fila.sum()))
    w.set_column(0, 0, 12)
    w.set_column(1, 1, 50)
    w.set_column(2, 3 + len(cv.columns), 16)
    w.write(len(cv) + 2, 0, "Pacientes únicos por código y curso de vida en todo el período (edad al momento de la atención).")


# ---------------------------------------------------------------------------
# 2. Hoja de cálculo: datos, matriz con fórmulas, tabla dinámica y validación
# ---------------------------------------------------------------------------

_ENC = dict(font=Font(bold=True, color="FFFFFF"), fill=PatternFill("solid", fgColor=VERDE),
            alignment=Alignment(wrap_text=True, vertical="center"))
_BORDE = Border(*(Side(style="thin", color="DCE3DD"),) * 4)


def _encabezado(ws, fila, valores):
    for j, v in enumerate(valores, start=1):
        c = ws.cell(row=fila, column=j, value=v)
        c.font, c.fill, c.alignment = _ENC["font"], _ENC["fill"], _ENC["alignment"]


def exportar_hoja(ruta: str | Path, calc: dict) -> Path:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    m, cat, cal, pac = calc["matriz"], calc["catalogo"].set_index("codigo"), calc["calidad"], calc["pacientes"]
    meses = list(m.columns)
    wb = Workbook()

    # Datos: CUPS, MES y PACIENTE primero (fuente de la tabla dinámica)
    wd = wb.active
    wd.title = "Datos"
    cols = ["CUPS", "MES", "PACIENTE", "DESCRIPCION", "NOMBRE_MES", "TIPO_ID", "NUM_ID", "CURSO_VIDA", "SEDE",
            "ATENCIONES", "FUENTE"]
    _encabezado(wd, 1, cols)
    pac = pac.sort_values(["codigo", "mes", "paciente"])
    for f in pac.itertuples(index=False):
        wd.append([f.codigo, f.mes, f.paciente, cat.loc[f.codigo, "descripcion"], nombre_mes(f.mes), f.tipo_id, f.num_id,
                   f.curso_vida, f.sede, int(f.atenciones), f.fuentes])
    n = len(pac)
    if n:
        t = Table(displayName="tblPacientes", ref=f"A1:{get_column_letter(len(cols))}{n + 1}")
        t.tableStyleInfo = TableStyleInfo(name="TableStyleMedium7", showRowStripes=True)
        wd.add_table(t)
    for j, w in enumerate([12, 10, 18, 45, 14, 8, 16, 26, 26, 11, 18], start=1):
        wd.column_dimensions[get_column_letter(j)].width = w
    wd.freeze_panes = "A2"

    # Matriz con fórmulas
    wm = wb.create_sheet("Matriz", 0)
    wm["A1"] = f"Número de pacientes únicos por código CUPS y mes · {calc['resumen']['eps']} · " \
               f"{nombre_mes(meses[0])} a {nombre_mes(meses[-1])}"
    wm["A1"].font = Font(bold=True, size=14, color=VERDE)
    wm["A2"] = ("Cada celda = CONTAR.SI.CONJUNTO(Datos!A:A; código; Datos!B:B; mes): pacientes únicos (la hoja Datos "
                "tiene una fila por paciente, código y mes). Celdas en 0 resaltadas; «Meses sin información» cuenta los "
                "meses en 0 de cada código.")
    wm["A2"].font = Font(italic=True, color=GRIS)
    _encabezado(wm, 3, ["CÓDIGO CUPS", "DESCRIPCIÓN", "ESTADO", *meses, "TOTAL POR CÓDIGO", "MESES SIN INFORMACIÓN",
                        "VALIDACIÓN", "OBSERVACIONES"])
    for j, mes in enumerate(meses, start=4):
        wm.cell(row=4, column=j, value=nombre_mes(mes)).font = Font(italic=True, color=GRIS)
    wm.cell(row=4, column=1, value="(mes en clave AAAA-MM en la fila 3)").font = Font(italic=True, color=GRIS)
    c_ini, c_fin = get_column_letter(4), get_column_letter(3 + len(meses))
    c_tot, c_sin, c_val, c_obs = (get_column_letter(4 + len(meses) + k) for k in range(4))
    fila0 = 5
    for i, cod in enumerate(m.index):
        r = fila0 + i
        wm.cell(row=r, column=1, value=cod)
        wm.cell(row=r, column=2, value=cat.loc[cod, "descripcion"])
        wm.cell(row=r, column=3, value=cat.loc[cod, "estado"])
        for j in range(len(meses)):
            col = get_column_letter(4 + j)
            wm.cell(row=r, column=4 + j, value=f'=COUNTIFS(Datos!$A:$A,$A{r},Datos!$B:$B,{col}$3)')
        wm[f"{c_tot}{r}"] = f"=SUM({c_ini}{r}:{c_fin}{r})"
        wm[f"{c_sin}{r}"] = f'=COUNTIF({c_ini}{r}:{c_fin}{r},0)'
        wm[f"{c_val}{r}"] = (f'=IF({c_tot}{r}=0,"SIN REGISTROS EN EL PERIODO",IF({c_sin}{r}>0,'
                             f'"REVISAR: "&{c_sin}{r}&" MES(ES) SIN INFORMACION","OK"))')
        wm[f"{c_obs}{r}"] = cal.loc[cod, "observaciones"]
    ult = fila0 + len(m) - 1
    rt = ult + 1
    wm.cell(row=rt, column=2, value="TOTAL POR MES").font = Font(bold=True)
    for j in range(len(meses) + 1):
        col = get_column_letter(4 + j)
        wm[f"{col}{rt}"] = f"=SUM({col}{fila0}:{col}{ult})"
        wm[f"{col}{rt}"].font = Font(bold=True)
    wm.cell(row=rt + 1, column=2, value="VALIDACIÓN DEL MES").font = Font(bold=True)
    for j in range(len(meses)):
        col = get_column_letter(4 + j)
        wm[f"{col}{rt + 1}"] = f'=IF({col}{rt}=0,"SIN INFORMACION","OK")'
    rojo = PatternFill("solid", fgColor="FBEAEF")
    wm.conditional_formatting.add(f"{c_ini}{fila0}:{c_fin}{ult}", CellIsRule(operator="equal", formula=["0"], fill=rojo,
                                                                              font=Font(color=VINO)))
    wm.conditional_formatting.add(f"{c_ini}{rt + 1}:{c_fin}{rt + 1}",
                                  FormulaRule(formula=[f'{c_ini}{rt + 1}="SIN INFORMACION"'], fill=rojo, font=Font(bold=True, color=VINO)))
    wm.conditional_formatting.add(f"{c_val}{fila0}:{c_val}{ult}",
                                  FormulaRule(formula=[f'{c_val}{fila0}<>"OK"'], fill=rojo, font=Font(color=VINO)))
    for j, w in enumerate([13, 50, 10] + [11] * len(meses) + [12, 12, 30, 90], start=1):
        wm.column_dimensions[get_column_letter(j)].width = w
    wm.freeze_panes = f"D{fila0}"
    wm.auto_filter.ref = f"A3:{c_obs}{ult}"

    # Tabla dinámica nativa
    if n:
        _tabla_dinamica(wb, pac, n)

    # Validación por mes y meses sin información
    wv = wb.create_sheet("Validación")
    _encabezado(wv, 1, ["MES", "NOMBRE", "PACIENTES-MES (Matriz)", "CÓDIGOS CON PACIENTES", "ESTADO"])
    for i, mes in enumerate(meses, start=2):
        col = get_column_letter(4 + i - 2)
        wv.cell(row=i, column=1, value=mes)
        wv.cell(row=i, column=2, value=nombre_mes(mes))
        wv.cell(row=i, column=3, value=f"=Matriz!{col}{rt}")
        wv.cell(row=i, column=4, value=f'=COUNTIF(Matriz!{col}{fila0}:{col}{ult},">0")')
        wv.cell(row=i, column=5, value=f'=IF(C{i}=0,"SIN INFORMACION EN EL MES","OK")')
    wv.conditional_formatting.add(f"E2:E{len(meses) + 1}", FormulaRule(formula=['E2<>"OK"'], fill=rojo, font=Font(bold=True, color=VINO)))
    for j, w in enumerate([10, 16, 22, 22, 28], start=1):
        wv.column_dimensions[get_column_letter(j)].width = w

    # Instrucciones
    wi = wb.create_sheet("Instrucciones")
    lineas = [
        "HOJA DE CÁLCULO · PACIENTES ÚNICOS POR CÓDIGO CUPS Y MES",
        "",
        "Datos: una fila por paciente único, código y mes (tabla tblPacientes). Agregue filas al final y las fórmulas "
        "de Matriz y Validación se recalculan; actualice la tabla dinámica con Datos > Actualizar todo.",
        "Fórmulas sugeridas (en inglés; en Excel en español: CONTAR.SI.CONJUNTO, SUMA, CONTAR.SI, SI):",
        f"  Pacientes de un código en un mes:  =COUNTIFS(Datos!$A:$A,$A{fila0},Datos!$B:$B,D$3)",
        f"  Total por código:                  =SUM({c_ini}{fila0}:{c_fin}{fila0})",
        f"  Total por mes:                     =SUM(D{fila0}:D{ult})",
        f"  Meses sin información (código):    =COUNTIF({c_ini}{fila0}:{c_fin}{fila0},0)",
        f'  Validación (código):               =IF({c_tot}{fila0}=0,"SIN REGISTROS EN EL PERIODO",IF({c_sin}{fila0}>0,"REVISAR","OK"))',
        f'  Validación (mes):                  =IF(D{rt}=0,"SIN INFORMACION","OK")',
        "  Pacientes únicos de un código en el período (Excel 365): =COUNTA(UNIQUE(FILTER(Datos!C:C,Datos!A:A=A5)))",
        "",
        "Tabla dinámica: filas = CUPS, columnas = MES, valores = Cuenta de PACIENTE (= pacientes únicos, porque cada fila "
        "de Datos ya es un paciente único por código y mes). Se actualiza al abrir el libro.",
        "Los códigos de la nota sin registros aparecen en Matriz con 0 (la tabla dinámica sólo muestra códigos con datos).",
        "Observaciones: calidad de datos por código (duplicados, anulados, errores de diligenciamiento, fuera de período).",
    ]
    for i, t in enumerate(lineas, start=1):
        wi.cell(row=i, column=1, value=t)
    wi["A1"].font = Font(bold=True, size=13, color=VERDE)
    wi.column_dimensions["A"].width = 150
    wb.save(ruta)
    return ruta


def _tabla_dinamica(wb, pac: pd.DataFrame, n: int) -> None:
    """Tabla dinámica nativa de Excel (CUPS × MES, cuenta de PACIENTE) sobre Datos!A:C."""
    cups = sorted(pac["codigo"].unique())
    meses = sorted(pac["mes"].unique())
    pacs = sorted(pac["paciente"].unique())
    i_c, i_m, i_p = ({v: k for k, v in enumerate(x)} for x in (cups, meses, pacs))
    cache = CacheDefinition(
        refreshOnLoad=True, recordCount=n,
        cacheSource=CacheSource(type="worksheet", worksheetSource=WorksheetSource(ref=f"A1:C{n + 1}", sheet="Datos")),
        cacheFields=[CacheField(name=nombre, sharedItems=SharedItems(_fields=[Text(v=v) for v in valores], count=len(valores)))
                     for nombre, valores in (("CUPS", cups), ("MES", meses), ("PACIENTE", pacs))],
    )
    cache.records = RecordList(r=[Record(_fields=[Index(v=i_c[c]), Index(v=i_m[m]), Index(v=i_p[p])])
                                  for c, m, p in zip(pac["codigo"], pac["mes"], pac["paciente"], strict=True)])

    def campo(k, eje=None):
        if eje is None:
            return PivotField(dataField=True, showAll=False)
        return PivotField(axis=eje, showAll=False, items=[FieldItem(x=i) for i in range(k)] + [FieldItem(t="default")])

    ws = wb.create_sheet("Tabla dinámica", 1)
    ws["A1"] = "Tabla dinámica: pacientes únicos por código CUPS (filas) y mes (columnas). Datos > Actualizar todo."
    ws["A1"].font = Font(bold=True, color=VERDE)
    top, filas, cols = 3, len(cups), len(meses)
    ref = f"A{top}:{get_column_letter(cols + 2)}{top + filas + 2}"
    pt = TableDefinition(
        name="TD_Pacientes_CUPS", cacheId=1, dataCaption="Valores", updatedVersion=6, minRefreshableVersion=3,
        createdVersion=6, applyNumberFormats=False, applyBorderFormats=False, applyFontFormats=False,
        applyPatternFormats=False, applyAlignmentFormats=False, applyWidthHeightFormats=True, useAutoFormatting=True,
        itemPrintTitles=True, indent=0, outline=True, outlineData=True,
        location=Location(ref=ref, firstHeaderRow=1, firstDataRow=2, firstDataCol=1),
        pivotFields=[campo(len(cups), "axisRow"), campo(len(meses), "axisCol"), campo(len(pacs))],
        rowFields=[RowColField(x=0)], colFields=[RowColField(x=1)],
        dataFields=[DataField(name="Pacientes", fld=2, subtotal="count", baseField=0, baseItem=0)],
        pivotTableStyleInfo=PivotTableStyle(name="PivotStyleMedium7", showRowHeaders=True, showColHeaders=True,
                                            showRowStripes=False, showColStripes=False, showLastColumn=True),
    )
    pt.rowItems = [RowColItem(x=[Index(v=i)]) for i in range(filas)] + [RowColItem(t="grand", x=[Index()])]
    pt.colItems = [RowColItem(x=[Index(v=i)]) for i in range(cols)] + [RowColItem(t="grand", x=[Index()])]
    pt.cache = cache
    ws._pivots.append(pt)
    # Valores ya calculados en el área de la tabla (se ven aunque no se actualice)
    cruce = pac.groupby(["codigo", "mes"])["paciente"].nunique().unstack("mes").reindex(index=cups, columns=meses).fillna(0)
    ws.cell(row=top, column=1, value="Pacientes")
    ws.cell(row=top, column=2, value="MES")
    ws.cell(row=top + 1, column=1, value="CUPS")
    for j, mes in enumerate(meses, start=2):
        ws.cell(row=top + 1, column=j, value=mes)
    ws.cell(row=top + 1, column=cols + 2, value="Total general")
    for i, cod in enumerate(cups, start=top + 2):
        ws.cell(row=i, column=1, value=cod)
        for j, mes in enumerate(meses, start=2):
            v = int(cruce.loc[cod, mes])
            ws.cell(row=i, column=j, value=v or None)
        ws.cell(row=i, column=cols + 2, value=int(cruce.loc[cod].sum()))
    ws.cell(row=top + filas + 2, column=1, value="Total general")
    for j, mes in enumerate(meses, start=2):
        ws.cell(row=top + filas + 2, column=j, value=int(cruce[mes].sum()))
    ws.cell(row=top + filas + 2, column=cols + 2, value=int(cruce.values.sum()))


def generar(calc: dict, carpeta: str | Path) -> list[Path]:
    """Nota técnica + hoja de cálculo del resultado de ``nota_tecnica.calcular``."""
    carpeta = Path(carpeta)
    r = calc["resumen"]
    eps = "".join(ch if ch.isalnum() else "_" for ch in f"{r['eps']} {r.get('ambito') or ''}".strip().upper()).strip("_")
    periodo = f"{r['desde']}_{r['hasta']}"
    return [exportar_nota(carpeta / f"NOTA_TECNICA_{eps}_{periodo}.xlsx", calc),
            exportar_hoja(carpeta / f"HOJA_PACIENTES_CUPS_{eps}_{periodo}.xlsx", calc)]


__all__ = ["exportar_hoja", "exportar_nota", "generar", "CURSOS", "SIN_CURSO"]

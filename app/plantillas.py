"""Plantillas homologadas (columnas comunes entre las 4 fuentes) y conversión de archivos nativos a esas plantillas."""
import io, os, tempfile
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation
from .db import AZUL, AZUL_CLARO, AZUL_MED, FUENTE, GRIS, GRIS_CLARO, IDENTIDAD, INSTITUCION, NIT, VERSION
from .loaders import leer_rips, leer_tabla

# (columna plantilla, obligatoria, llave común con Software 360, descripción, ejemplo)
SPEC = {
 "rips": [
  ("MES", "Sí", "MES de la hoja de facturación", "Periodo AAAA-MM (también acepta 01-ENERO + año del FEV)", "2026-01"),
  ("EPS", "Sí", "NOMBRE EPS", "Nombre de la EPS exactamente como sale en Software 360", "NUEVA EMPRESA PROMOTORA DE SALUD"),
  ("NIT_EPS", "No", "—", "NIT de la EPS", "900156264"),
  ("CONTRATO", "Sí", "CONTRATO", "Nombre del contrato EXACTO de Software 360 / PARAM CONTRATO (en el RIPS nativo: 'Contrato / Descripción FEV')", "NUEVA EPS SUBSIDIADO PYM GLOBAL"),
  ("CUCON", "No", "—", "Número de contrato / CUCON", "0227-2016"),
  ("MODALIDAD", "Sí", "PARAM CONTRATO.MODALIDAD", "CAPITA / EVENTO / PGP", "CAPITA"),
  ("SEDE", "Sí", "SEDE", "Sede prestadora tal como sale en Software 360", "HOSPITAL SAN LORENZO"),
  ("FACTURA_FEV", "Sí", "FACTURA NRO", "Número de la factura electrónica (FEV). Es la llave de radicación", "FHSL25142"),
  ("FACTURA_RIPS", "No", "—", "Número de factura del JSON de RIPS (si difiere de la FEV)", "FHSL24460"),
  ("TIPO_SERVICIO", "No", "SERVICIO (por CUPS)", "Tipo de servicio del RIPS; se homologa al SERVICIO de PARAM SERVICIO mediante el CUPS", "Consultas"),
  ("CODIGO_CUPS_CUMS", "Sí", "COD. PROCEDIMIENTO / COD. MEDICAMENTO", "Código CUPS o CUMS", "890201"),
  ("DESCRIPCION_SERVICIO", "No", "ACTIVIDAD", "Descripción del servicio. Regla de actividad: si el código viene vacío, el CUPS se toma de la ACTIVIDAD de la facturación", "CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL"),
  ("CANTIDAD", "No", "CANTIDAD", "Cantidad (1 por línea si no se informa)", 1),
  ("VALOR_SERVICIO", "No", "VALOR TOTAL", "Valor del servicio (en cápita suele ser 0)", 0),
  ("VALOR_FACTURA_FEV", "Sí", "Σ VALOR TOTAL por FACTURA NRO", "Valor total de la factura (se repite en cada línea de la misma factura)", 586159560),
  ("VALIDACION", "No", "—", "Resultado de validación XML/JSON", "DIFERENCIA PERMITIDA - CÁPITA")],
 "costos": [
  ("MES", "Sí", "MES", "Periodo AAAA-MM", "2026-01"),
  ("SEDE", "Sí", "SEDE", "Sede tal como sale en Software 360", "HOSPITAL SAN LORENZO"),
  ("SERVICIO", "Sí", "SERVICIO", "Debe ser uno de PARAM SERVICIO (p. ej. LABORATORIO CLINICO, CONSULTAS)", "LABORATORIO CLINICO"),
  ("CUPS", "No", "COD. PROCEDIMIENTO", "Para costo unitario por CUPS (ABC): habilita margen por procedimiento", "903817"),
  ("CONCEPTO", "No", "—", "Rubro: personal, insumos, depreciación, indirectos…", "Personal"),
  ("TIPO_COSTO", "No", "—", "DIRECTO / INDIRECTO / FIJO / VARIABLE (clasificación del costo)", "DIRECTO"),
  ("CANTIDAD", "No", "CANTIDAD", "Producción del periodo", 1200),
  ("COSTO_UNITARIO", "No", "—", "Costo unitario (si no informa el total se calcula = unitario × cantidad)", 4500),
  ("VALOR_COSTO_TOTAL", "Sí*", "VALOR TOTAL (comparable)", "Costo total del periodo (*obligatorio si no hay costo unitario)", 5400000)],
 "codigos": [
  ("CODIGO_RIPS", "Sí", "CUMS/CUPS del RIPS", "Código tal como viene en el RIPS (p. ej. CUMS 19942561-6)", "19942561-6"),
  ("CODIGO_SOFTWARE360", "Sí", "COD. MEDICAMENTO / COD. PROCEDIMIENTO", "Código interno que usa Software 360 para el mismo ítem (p. ej. G-0052)", "G-0052"),
  ("DESCRIPCION", "No", "—", "Descripción del ítem", "LACTATO DE RINGER SOLUCION INYECTABLE")],
 "notas": [
  ("EPS", "Sí", "NOMBRE EPS", "EPS tal como sale en Software 360 (alias editables en /api/homologacion/eps)", "NUEVA EMPRESA PROMOTORA DE SALUD"),
  ("MODALIDAD", "Sí", "PARAM CONTRATO.MODALIDAD", "CAPITA / EVENTO / PGP", "EVENTO"),
  ("CONTRATO", "No", "CONTRATO", "Contrato específico de Software 360 (opcional)", "NUEVA EPS EVENTO SUBSIDIADO"),
  ("CUPS", "Sí", "COD. PROCEDIMIENTO", "Código CUPS", "890201"),
  ("DESCRIPCION", "No", "—", "Descripción del servicio", "CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL"),
  ("CATEGORIA", "No", "SERVICIO", "Agrupador / grupo de la nota técnica", "CONSULTAS"),
  ("TARIFA", "Sí (evento)", "VALOR ACTIVIDAD", "Tarifa pactada por evento", 90100),
  ("EVENTOS_MES", "Sí (cápita)", "CANTIDAD", "Eventos esperados por mes según la nota técnica de cápita", 42.4),
  ("COSTO_UNITARIO", "No", "COSTO UNITARIO", "Costo unitario por CUPS de la nota técnica (hoja «NT NUEVA EPS»: COSTOS)", 7027.76),
  ("TOPE_VALOR", "No", "VALOR TOTAL", "Techo mensual de valor (si aplica)", 0),
  ("VIGENCIA", "No", "—", "Vigencia de la tarifa", "2026-12-31")],
}
NOMBRE_COL = {"rips": {"fecha_egreso": None}}

TITULOS = {"rips": "Consolidado RIPS", "costos": "Costos mensuales", "codigos": "Homologación de códigos", "notas": "Notas técnicas EPS"}
LISTAS = {"MODALIDAD": '"CAPITA,EVENTO,PGP"', "TIPO_COSTO": '"DIRECTO,INDIRECTO,FIJO,VARIABLE"'}
NUMERICAS_ = {"CANTIDAD", "VALOR_SERVICIO", "VALOR_FACTURA_FEV", "COSTO_UNITARIO", "VALOR_COSTO_TOTAL", "TARIFA", "EVENTOS_MES", "TOPE_VALOR"}
_lado = Side(style="thin", color="BFBFBF")

def _banda(ws, texto, ncols, fila, size, fill, color="FFFFFF", bold=True, alto=22):
    ws.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=ncols)
    c = ws.cell(fila, 1, texto); c.font = Font(name=FUENTE, size=size, bold=bold, color=color); c.fill = PatternFill("solid", fgColor=fill)
    c.alignment = Alignment(vertical="center", indent=1, wrap_text=True); ws.row_dimensions[fila].height = alto

def _wb(tipo, rows):
    """Plantilla con identidad: LEEME, PLANTILLA (encabezado en la fila 4; obligatorias en azul, opcionales en gris) e INSTRUCCIONES."""
    wb = Workbook(); wl = wb.active; wl.title = "LEEME"; wl.sheet_view.showGridLines = False
    wl.column_dimensions["A"].width = 26; wl.column_dimensions["B"].width = 100
    _banda(wl, INSTITUCION + f" · NIT {NIT}", 2, 1, 14, AZUL, alto=26); _banda(wl, f"Plantilla de {TITULOS[tipo]}", 2, 2, 12, AZUL_MED)
    _banda(wl, f"{IDENTIDAD} · Versión {VERSION}", 2, 3, 9, GRIS_CLARO, color=GRIS, bold=False, alto=16)
    pasos = [("Cómo usarla", "Diligencie la hoja PLANTILLA desde la fila 5 (la fila 5 trae un ejemplo en cursiva: reemplácelo). No cambie los encabezados de la fila 4."),
             ("Colores", "Encabezado azul oscuro = columna OBLIGATORIA · encabezado gris = opcional. Pase el cursor sobre el encabezado para ver la descripción."),
             ("Llaves comunes", "EPS, CONTRATO, MODALIDAD, SEDE y CUPS deben escribirse como en Software 360 (ver hoja INSTRUCCIONES, columna «llave común»)."),
             ("Validaciones", "MODALIDAD y TIPO_COSTO tienen lista desplegable; las columnas numéricas solo aceptan valores ≥ 0. Al cargar, la herramienta registra errores y advertencias por fila."),
             ("Dónde cargarla", "Pestaña «Cargas» del dashboard, tipo «" + tipo + "»."),
             ("Supuesto a validar", "La regla de actividad identifica el CUPS por la descripción de la ACTIVIDAD cuando el código llega vacío (ver README).")]
    for i, (k, v) in enumerate(pasos, start=5):
        a = wl.cell(i, 1, k); a.font = Font(name=FUENTE, bold=True, color=AZUL); b = wl.cell(i, 2, v); b.font = Font(name=FUENTE); b.alignment = Alignment(wrap_text=True, vertical="top")
        a.alignment = Alignment(vertical="top"); wl.row_dimensions[i].height = 34
    ws = wb.create_sheet("PLANTILLA"); ws.sheet_view.showGridLines = False; spec = SPEC[tipo]; n = len(spec)
    _banda(ws, f"{TITULOS[tipo]} · {INSTITUCION}", n, 1, 12, AZUL); _banda(ws, f"{IDENTIDAD}", n, 2, 9, GRIS_CLARO, color=GRIS, bold=False, alto=16)
    for j, (col, obl, llave, desc, ej) in enumerate(spec, start=1):
        c = ws.cell(4, j, col); req = obl.startswith("Sí")
        c.font = Font(name=FUENTE, bold=True, color="FFFFFF" if req else "000000"); c.fill = PatternFill("solid", fgColor=AZUL if req else "D9D9D9")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True); c.border = Border(left=_lado, right=_lado, top=_lado, bottom=_lado)
        c.comment = Comment(f"{desc}\nLlave común: {llave}\nObligatoria: {obl}", IDENTIDAD)
        ws.column_dimensions[c.column_letter].width = max(16, min(len(col) + 6, 30))
        ejemplo = ws.cell(5, j, ej); ejemplo.font = Font(name=FUENTE, italic=True, color=GRIS)
        letra = c.column_letter
        if col in LISTAS:
            dv = DataValidation(type="list", formula1=LISTAS[col], allow_blank=True, showErrorMessage=True, errorTitle="Valor no válido", error="Elija un valor de la lista"); ws.add_data_validation(dv); dv.add(f"{letra}5:{letra}20000")
        if col in NUMERICAS_:
            dv = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1="0", allow_blank=True, showErrorMessage=True, errorTitle="Valor no válido", error="Solo números ≥ 0"); ws.add_data_validation(dv); dv.add(f"{letra}5:{letra}20000")
    ws.row_dimensions[4].height = 32; ws.freeze_panes = "A5"
    for r in rows: ws.append(r)
    wi = wb.create_sheet("INSTRUCCIONES"); wi.sheet_view.showGridLines = False
    _banda(wi, f"Instrucciones de columnas · {TITULOS[tipo]}", 5, 1, 12, AZUL)
    for j, h in enumerate(["COLUMNA", "OBLIGATORIA", "LLAVE COMÚN CON SOFTWARE 360 / PARÁMETROS", "DESCRIPCIÓN", "EJEMPLO"], start=1):
        c = wi.cell(2, j, h); c.font = Font(name=FUENTE, bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor=AZUL_MED); c.alignment = Alignment(horizontal="center")
    for i, r in enumerate(spec, start=3):
        for j, v in enumerate(r, start=1):
            c = wi.cell(i, j, v); c.font = Font(name=FUENTE, bold=(j == 1)); c.alignment = Alignment(wrap_text=True, vertical="top")
            if i % 2 == 0: c.fill = PatternFill("solid", fgColor=GRIS_CLARO)
    for col, w in zip("ABCDE", (24, 13, 38, 80, 36)): wi.column_dimensions[col].width = w
    for w_ in (ws, wi): w_.oddFooter.left.text = IDENTIDAD; w_.oddFooter.right.text = "Página &P de &N"
    return wb

def plantilla(tipo):
    """Libro vacío (con 1 fila de ejemplo) para RIPS, costos, notas o parámetros."""
    if tipo == "parametros":
        wb = Workbook(); ws = wb.active; ws.title = "PARAM CONTRATO"; ws.append([]); ws.append([]); ws.append([None, "MES", "CONTRATO", "MODALIDAD", "SEDE", "SERVICIO"])
        ws.append([None, None, "NUEVA EPS SUBSIDIADO PYM GLOBAL", "CAPITA"]); ws.append([None, None, "NUEVA EPS EVENTO SUBSIDIADO", "EVENTO"])
        w2 = wb.create_sheet("PARAM SERVICIO"); w2.append([]); w2.append([]); w2.append([None, "SERVICIO"])
        for s in ("CONSULTAS", "LABORATORIO CLINICO", "MEDICAMENTOS", "ODONTOLOGIA"): w2.append([None, s])
        return wb
    return _wb(tipo, [])

def homologar(tipo, path, mes_param=None, eps_alias=()):
    """Convierte un archivo nativo (consolidado RIPS, comparativo de notas técnicas, costos) al formato homologado. Devuelve ruta xlsx."""
    out = os.path.join(tempfile.mkdtemp(), f"HOMOLOGADO_{tipo.upper()}.xlsx")
    if tipo == "rips":
        srv, fac = leer_rips(path, mes_param, eps_alias)
        v = fac.set_index("factura")
        srv["factura_rips"] = srv.factura.map(v.factura_rips); srv["valor_fev"] = srv.factura.map(v.valor_fev).fillna(0)
        srv["validacion"] = srv.factura.map(v.validacion)
        d = srv.rename(columns={"nit_eps": "NIT_EPS", "factura": "FACTURA_FEV", "factura_rips": "FACTURA_RIPS", "servicio": "TIPO_SERVICIO", "descripcion": "DESCRIPCION_SERVICIO",
                                "cups": "CODIGO_CUPS_CUMS", "valor": "VALOR_SERVICIO", "valor_fev": "VALOR_FACTURA_FEV"})
        d.columns = [c.upper() for c in d.columns]
        d = d[[c[0] for c in SPEC["rips"]]]
    elif tipo == "notas":
        t = leer_tabla(path, "notas", None, eps_alias)
        d = t.rename(columns=str.upper)[[c[0] for c in SPEC["notas"]]]
    elif tipo == "costos":
        t = leer_tabla(path, "costos", mes_param, eps_alias)
        d = t.rename(columns={"costo_unitario": "COSTO_UNITARIO", "valor": "VALOR_COSTO_TOTAL"}); d.columns = [c.upper() for c in d.columns]
        d = d[[c[0] for c in SPEC["costos"]]]
    else: raise ValueError(tipo)
    wb = _wb(tipo, []); ws = wb["PLANTILLA"]
    for c in ws[5]: c.value = None
    ws.delete_rows(5)
    for r in d.itertuples(index=False): ws.append([None if x != x else x for x in r])
    wb.save(out); return out, len(d)

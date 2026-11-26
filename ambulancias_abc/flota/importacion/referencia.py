"""Formato RYC-FO-015 «Plantilla de referencia y contrarreferencia».

* ``generar_formato``: versión 002 del formato, lista para diligenciar mes a
  mes (listas desplegables, fechas y horas validadas, cálculo de tiempos,
  indicador de oportunidad y medición por EPS con fórmulas).
* ``leer_referencia``: lee la versión 002 y también la 001 (la que usa hoy el
  proceso), ubicando las columnas por su nombre.

Protección de datos (Ley 1581 de 2012): el nombre del paciente nunca se lee y
el número de documento solo se usa para calcular su hash con sal, igual que en
la facturación y los RIPS; así se cruzan las remisiones con lo facturado sin
guardar datos personales.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from . import hallazgo, normalizar
from .facturacion_his import hash_documento

CODIGO, VERSION, NIT = "RYC-FO-015", "002", "900.304.131 - 8"
TITULO = "PLANTILLA DE REFERENCIA Y CONTRARREFERENCIA DE LA ESE MORENO Y CLAVIJO"
LOGO = Path(__file__).resolve().parent.parent / "static" / "flota" / "logo_ese.png"
FILAS = 600  # filas preparadas por archivo (un mes de un hospital, o el consolidado)
FILA_ENC = 7

TIPOS_REGISTRO = ["REFERENCIA", "CONTRARREFERENCIA"]
TIPOS_DOCUMENTO = ["CC", "TI", "RC", "CE", "PA", "PE", "PT", "CN", "MS", "AS", "SC", "CD", "DE"]
UNIDADES_EDAD = ["AÑOS", "MESES", "DÍAS"]
SEXOS = ["F", "M", "I"]
REGIMENES = ["CONTRIBUTIVO", "SUBSIDIADO", "ESPECIAL O EXCEPCIÓN", "POBLACIÓN NO AFILIADA", "PARTICULAR", "SOAT / ADRES"]
TIPOS_TRASLADO = ["TAB", "TAM", "AÉREO", "FLUVIAL", "OTRO"]
SI_NO = ["SI", "NO"]
ESTADOS = ["TRASLADADO", "PENDIENTE DE ACEPTACIÓN", "RECHAZADO POR IPS DESTINO", "CANCELADO", "FALLECIDO ANTES DEL TRASLADO",
           "TRASLADO POR OTRO MEDIO"]
ESPECIALIDADES = [
    "MEDICINA INTERNA", "PEDIATRÍA", "GINECOOBSTETRICIA", "CIRUGÍA GENERAL", "ORTOPEDIA", "NEUROLOGÍA", "NEUROCIRUGÍA",
    "CARDIOLOGÍA", "PSIQUIATRÍA", "UROLOGÍA", "OFTALMOLOGÍA", "OTORRINOLARINGOLOGÍA", "UCI ADULTOS", "UCI PEDIÁTRICA",
    "UCI NEONATAL", "UNIDAD DE QUEMADOS", "IMAGENOLOGÍA / APOYO DIAGNÓSTICO", "OTRA",
]
EPS_FRECUENTES = ["NUEVA EPS", "SANITAS", "COOSALUD", "FAMISANAR", "SALUD TOTAL", "CAJACOPI", "ASMET SALUD", "MALLAMAS",
                  "FUERZAS MILITARES", "POLICÍA NACIONAL", "MAGISTERIO (FOMAG)", "SOAT / ADRES", "PARTICULAR"]

# (clave, encabezado v002, ancho, tipo, obligatorio, ayuda)
COLUMNAS = [
    ("tipo_registro", "TIPO DE REGISTRO", 17, "lista", True, "REFERENCIA (remisión que sale) o CONTRARREFERENCIA (paciente que regresa)."),
    ("sede", "HOSPITAL QUE REMITE (IPS QUE REPORTA)", 30, "lista", True, "Hospital o centro de salud de la ESE desde donde sale el paciente."),
    ("fecha_reporte", "FECHA Y HORA DE REPORTE DE LA REMISIÓN", 20, "fechahora", True, "dd/mm/aaaa hh:mm. Momento en que el médico solicita la remisión."),
    ("eps", "EPS", 18, "lista_libre", True, "EPS o pagador del paciente. Si no está en la lista, escríbala."),
    ("regimen", "RÉGIMEN", 16, "lista", False, "Régimen de afiliación."),
    ("nombre", "NOMBRE DEL PACIENTE", 28, "texto", False, "Solo para el registro interno de referencia; la herramienta de costeo no lo lee."),
    ("tipo_documento", "TIPO DE DOCUMENTO", 11, "lista", True, "CC, TI, RC, CE, PA, PE, PT, CN, MS, AS..."),
    ("documento", "NÚMERO DE DOCUMENTO", 16, "texto", True, "Sin puntos ni espacios. Con él se cruza la remisión con la facturación (la herramienta lo guarda cifrado)."),
    ("edad", "EDAD", 7, "entero", True, "Número entero."),
    ("unidad_edad", "UNIDAD DE MEDIDA DE LA EDAD", 12, "lista", True, "AÑOS, MESES o DÍAS."),
    ("sexo", "SEXO", 7, "lista", True, "F, M o I (indeterminado)."),
    ("especialidad", "ESPECIALIDAD REQUERIDA", 22, "lista_libre", True, "Especialidad o servicio que se solicita."),
    ("cie10", "DIAGNÓSTICO CIE-10 (CÓDIGO)", 12, "cie10", True, "Código CIE-10 de 4 caracteres, p. ej. O800, S069, J189."),
    ("diagnostico", "DIAGNÓSTICO (DESCRIPCIÓN)", 28, "texto", False, "Descripción del diagnóstico."),
    ("tipo_traslado", "TIPO DE TRASLADO", 11, "lista", True, "TAB básico, TAM medicalizado, AÉREO, FLUVIAL u OTRO."),
    ("acompanamiento", "ACOMPAÑAMIENTO MÉDICO", 13, "lista", True, "SI o NO."),
    ("fecha_llamada", "FECHA Y HORA DE LLAMADA AL EQUIPO DE REFERENCIA", 20, "fechahora", False, "dd/mm/aaaa hh:mm."),
    ("fecha_llegada_equipo", "FECHA Y HORA DE LLEGADA DEL EQUIPO DE REFERENCIA", 20, "fechahora", False, "dd/mm/aaaa hh:mm."),
    ("placa", "PLACA DE LA AMBULANCIA", 12, "lista", True, "Placa de la ambulancia de la ESE (lista). NO APLICA si el traslado fue aéreo, de otra IPS o por otro medio."),
    ("ips_destino", "IPS DESTINO", 26, "texto", True, "Nombre de la IPS que recibe."),
    ("municipio_destino", "MUNICIPIO DESTINO", 18, "lista_libre", True, "Municipio de la IPS destino (lista del manual tarifario). Con él se identifica el recorrido."),
    ("fecha_aceptacion", "FECHA Y HORA DE ACEPTACIÓN", 20, "fechahora", True, "dd/mm/aaaa hh:mm. Cuando la IPS destino acepta al paciente."),
    ("fecha_salida", "FECHA Y HORA DE SALIDA DE LA REMISIÓN", 20, "fechahora", True, "dd/mm/aaaa hh:mm. Salida de la ambulancia con el paciente."),
    ("fecha_llegada", "FECHA Y HORA DE LLEGADA AL HOSPITAL DESTINO", 20, "fechahora", True, "dd/mm/aaaa hh:mm."),
    ("km", "KM RECORRIDOS (IDA Y VUELTA)", 11, "decimal", False, "Odómetro final − inicial, si se registra."),
    ("autorizacion", "NÚMERO DE AUTORIZACIÓN", 16, "texto", False, "Autorización de la EPS para el traslado."),
    ("factura", "No. DE FACTURA O INGRESO", 16, "texto", False, "Factura (p. ej. FHSA12345) o número de ingreso del HIS, si se conoce."),
    ("estado", "ESTADO DE LA REMISIÓN", 22, "lista", True, "TRASLADADO, PENDIENTE, RECHAZADO, CANCELADO..."),
    ("observaciones", "OBSERVACIONES", 30, "texto", False, "Novedades (no escriba datos personales adicionales)."),
    ("responsable", "RESPONSABLE DEL TURNO", 22, "texto", True, "Nombre de quien registra."),
    ("espera_h", "ESPERA HASTA ACEPTACIÓN (HORAS)", 12, "calculo", False, "Se calcula sola: aceptación − reporte."),
    ("respuesta_min", "RESPUESTA DEL EQUIPO (MINUTOS)", 12, "calculo", False, "Se calcula sola: llegada del equipo − llamada."),
    ("duracion_h", "DURACIÓN DEL TRASLADO (HORAS)", 12, "calculo", False, "Se calcula sola: llegada al destino − salida."),
    ("mes_clave", "MES (AAAAMM)", 9, "calculo", False, "Se calcula solo; lo usan las hojas INDICADOR y MEDICIÓN POR EPS."),
]
COL = {c[0]: i + 1 for i, c in enumerate(COLUMNAS)}

# Encabezados que se aceptan al leer (v002 y v001, normalizados) -> clave
SINONIMOS = {normalizar(c[1]): c[0] for c in COLUMNAS}
SINONIMOS.update({
    "FECHA DE REPORTE DE REMISION": "fecha_reporte_v1",
    "HORA DE REPORTE DE REMISION (D/M/A)": "hora_reporte_v1",
    "HORA DE REPORTE DE REMISION": "hora_reporte_v1",
    "IPS QUE REPORTA": "sede",
    "NUMERO": "documento",
    "UNIDAD MEDIA EDAD": "unidad_edad",
    "UNIDAD MEDIDA EDAD": "unidad_edad",
    "DIAGNOSTICO": "diagnostico_v1",
    "TIPO TRASLADO": "tipo_traslado",
    "HORA DE LLAMADA AL EQUIPO DE REFERENCIA": "hora_llamada_v1",
    "HORA DE LLEGADA DEL EQUIPO DE REFERENCIA": "hora_llegada_equipo_v1",
    "NO DE PLACA DE LA AMBULANCIA": "placa",
    "DESTINO": "destino_v1",
    "HORA DE ACEPTACION": "hora_aceptacion_v1",
    "HORA DE SALIDA DE REMISION": "hora_salida_v1",
    "HORA DE LLEGADA HOSPITAL DESTINO": "hora_llegada_v1",
})

GRIS = PatternFill("solid", fgColor="EDEDED")
VERDE = PatternFill("solid", fgColor="1E7B34")
VERDE_CLARO = PatternFill("solid", fgColor="E8F3EA")
AMARILLO = PatternFill("solid", fgColor="FFF4CC")
ROJO_CLARO = PatternFill("solid", fgColor="F8D7DA")
FINO = Side(style="thin", color="9E9E9E")
BORDE = Border(left=FINO, right=FINO, top=FINO, bottom=FINO)
CENTRO = Alignment(horizontal="center", vertical="center", wrap_text=True)


# ---------------------------------------------------------------------------
# Generación del formato v002
# ---------------------------------------------------------------------------


def _encabezado(ws, titulo: str, ultima_col: int, logo: bool):
    """Bloque de logo y NIT, título y bloque de código/versión. En hojas anchas
    (REGISTRO) el título y el código quedan junto al logo, visibles en pantalla."""
    n_logo = 3 if ultima_col >= 8 else 1  # columnas del bloque del logo
    if ultima_col > 12:
        fin_titulo, col_cod, fin_cod = n_logo + 7, n_logo + 8, n_logo + 9
    else:
        fin_titulo, col_cod, fin_cod = ultima_col - 1, ultima_col, ultima_col
    ini = get_column_letter(n_logo + 1)
    fin = get_column_letter(fin_titulo)
    ws.merge_cells(f"A1:{get_column_letter(n_logo)}4")
    ws["A1"] = f"NIT: {NIT}"
    ws["A1"].alignment = Alignment(horizontal="left", vertical="bottom")
    ws["A1"].font = Font(size=8, bold=True)
    ws.merge_cells(f"{ini}1:{fin}2")
    ws[f"{ini}1"] = "SISTEMA INTEGRADO DE GESTIÓN"
    ws.merge_cells(f"{ini}3:{fin}4")
    ws[f"{ini}3"] = titulo
    for celda, es_titulo in ((f"{ini}1", False), (f"{ini}3", True)):
        ws[celda].alignment = CENTRO
        ws[celda].font = Font(bold=True, size=12 if es_titulo else 11, color="1E7B34" if es_titulo else "000000")
    for fila, texto in ((1, f"CÓDIGO: {CODIGO}"), (2, f"VERSIÓN: {VERSION}"), (3, "FECHA: noviembre de 2026"), (4, "PÁGINA: 1 DE 1")):
        if fin_cod > col_cod:
            ws.merge_cells(start_row=fila, start_column=col_cod, end_row=fila, end_column=fin_cod)
        c = ws.cell(fila, col_cod, texto)
        c.font = Font(size=9, bold=True)
        c.alignment = Alignment(horizontal="left", vertical="center")
        for j in range(col_cod, fin_cod + 1):
            ws.cell(fila, j).border = BORDE
    for fila in range(1, 5):
        ws.row_dimensions[fila].height = 18
    if logo and LOGO.exists():
        from openpyxl.drawing.image import Image

        img = Image(str(LOGO))
        img.width, img.height = 210, 57
        ws.add_image(img, "A1")
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_setup.orientation = "landscape"


def _titulo_tabla(ws, fila, texto, ultima_col):
    ws.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=ultima_col)
    c = ws.cell(fila, 1, texto)
    c.font = Font(bold=True, color="FFFFFF", size=11)
    c.fill = VERDE
    c.alignment = Alignment(horizontal="left" if ultima_col > 12 else "center", vertical="center", wrap_text=False)


def _encabezados_tabla(ws, fila, textos):
    for j, t in enumerate(textos, start=1):
        c = ws.cell(fila, j, t)
        c.font = Font(bold=True, size=9)
        c.fill = VERDE_CLARO
        c.alignment = CENTRO
        c.border = BORDE
    ws.row_dimensions[fila].height = 45


def generar_formato(sedes: list[str], placas: list[str], destinos: list[str], eps: list[str] | None = None,
                    logo: bool = True) -> bytes:
    """Libro .xlsx del formato RYC-FO-015 versión 002."""
    wb = Workbook()
    reg = wb.active
    reg.title = "REGISTRO"
    instr = wb.create_sheet("INSTRUCCIONES")
    ind = wb.create_sheet("INDICADOR")
    med = wb.create_sheet("MEDICION POR EPS")
    ctrl = wb.create_sheet("CONTROL DE CAMBIOS")
    lis = wb.create_sheet("LISTAS")

    # --- Listas (hoja oculta) ---
    listas = {
        "tipo_registro": TIPOS_REGISTRO, "sede": sedes, "eps": eps or EPS_FRECUENTES, "regimen": REGIMENES,
        "tipo_documento": TIPOS_DOCUMENTO, "unidad_edad": UNIDADES_EDAD, "sexo": SEXOS, "especialidad": ESPECIALIDADES,
        "tipo_traslado": TIPOS_TRASLADO, "acompanamiento": SI_NO, "placa": list(placas) + ["NO APLICA"],
        "municipio_destino": destinos, "estado": ESTADOS,
    }
    rangos = {}
    for j, (clave, valores) in enumerate(listas.items(), start=1):
        lis.cell(1, j, clave).font = Font(bold=True)
        for i, v in enumerate(valores, start=2):
            lis.cell(i, j, v)
        letra = get_column_letter(j)
        rangos[clave] = f"LISTAS!${letra}$2:${letra}${max(2, len(valores) + 1)}"
    lis.sheet_state = "hidden"

    # --- REGISTRO ---
    n = len(COLUMNAS)
    _encabezado(reg, TITULO, n, logo)
    _titulo_tabla(reg, 6, "PACIENTES REMITIDOS Y CONTRARREFERIDOS – un registro por paciente y traslado", n)
    _encabezados_tabla(reg, FILA_ENC, [c[1] for c in COLUMNAS])
    reg.row_dimensions[FILA_ENC].height = 62
    ultima = FILA_ENC + FILAS
    for clave, enc, ancho, tipo, oblig, ayuda in COLUMNAS:
        j = COL[clave]
        letra = get_column_letter(j)
        reg.column_dimensions[letra].width = ancho
        enc_c = reg.cell(FILA_ENC, j)
        if oblig:
            enc_c.value = f"{enc} *"
        if tipo == "calculo":
            enc_c.fill = GRIS
        rango = f"{letra}{FILA_ENC + 1}:{letra}{ultima}"
        dv = None
        if tipo in ("lista", "lista_libre"):
            dv = DataValidation(type="list", formula1=f"={rangos[clave]}", allow_blank=True,
                                errorStyle="stop" if tipo == "lista" else "information",
                                error="Elija un valor de la lista." if tipo == "lista" else "El valor no está en la lista; verifíquelo.",
                                errorTitle="Valor no válido")
        elif tipo == "fechahora":
            dv = DataValidation(type="date", operator="between", formula1="DATE(2020,1,1)", formula2="DATE(2035,12,31)",
                                allow_blank=True, error="Escriba fecha y hora: dd/mm/aaaa hh:mm", errorTitle="Fecha no válida")
        elif tipo == "entero":
            dv = DataValidation(type="whole", operator="between", formula1="0", formula2="120", allow_blank=True,
                                error="Edad entre 0 y 120", errorTitle="Edad no válida")
        elif tipo == "decimal":
            dv = DataValidation(type="decimal", operator="between", formula1="0", formula2="3000", allow_blank=True,
                                error="Km entre 0 y 3.000", errorTitle="Km no válido")
        elif tipo == "cie10":
            dv = DataValidation(type="textLength", operator="between", formula1="3", formula2="4", allow_blank=True,
                                error="Código CIE-10 de 3 o 4 caracteres (p. ej. O800)", errorTitle="CIE-10 no válido")
        if dv is not None:
            dv.promptTitle = enc[:32]
            dv.prompt = ayuda[:250]
            dv.showInputMessage = True
            reg.add_data_validation(dv)
            dv.add(rango)
        for i in range(FILA_ENC + 1, ultima + 1):
            c = reg.cell(i, j)
            c.border = BORDE
            c.alignment = Alignment(vertical="center", wrap_text=tipo == "texto")
            if tipo == "fechahora":
                c.number_format = "dd/mm/yyyy hh:mm"
            elif tipo in ("documento", "texto"):
                c.number_format = "@"
            if tipo == "calculo":
                c.fill = GRIS
    # documento como texto (conserva ceros a la izquierda)
    for i in range(FILA_ENC + 1, ultima + 1):
        reg.cell(i, COL["documento"]).number_format = "@"
        L = {k: get_column_letter(v) for k, v in COL.items()}
        reg.cell(i, COL["espera_h"]).value = (
            f'=IF(AND(ISNUMBER({L["fecha_reporte"]}{i}),ISNUMBER({L["fecha_aceptacion"]}{i})),'
            f'ROUND(({L["fecha_aceptacion"]}{i}-{L["fecha_reporte"]}{i})*24,1),"")')
        reg.cell(i, COL["respuesta_min"]).value = (
            f'=IF(AND(ISNUMBER({L["fecha_llamada"]}{i}),ISNUMBER({L["fecha_llegada_equipo"]}{i})),'
            f'ROUND(({L["fecha_llegada_equipo"]}{i}-{L["fecha_llamada"]}{i})*1440,0),"")')
        reg.cell(i, COL["duracion_h"]).value = (
            f'=IF(AND(ISNUMBER({L["fecha_salida"]}{i}),ISNUMBER({L["fecha_llegada"]}{i})),'
            f'ROUND(({L["fecha_llegada"]}{i}-{L["fecha_salida"]}{i})*24,1),"")')
        reg.cell(i, COL["mes_clave"]).value = (
            f'=IF(ISNUMBER({L["fecha_reporte"]}{i}),YEAR({L["fecha_reporte"]}{i})*100+MONTH({L["fecha_reporte"]}{i}),"")')
    # Alertas visuales: tiempos negativos u horas fuera de orden
    L = {k: get_column_letter(v) for k, v in COL.items()}
    for clave in ("espera_h", "respuesta_min", "duracion_h"):
        letra = L[clave]
        reg.conditional_formatting.add(f"{letra}{FILA_ENC + 1}:{letra}{ultima}",
                                       FormulaRule(formula=[f'AND(ISNUMBER({letra}{FILA_ENC + 1}),{letra}{FILA_ENC + 1}<0)'], fill=ROJO_CLARO))
    reg.conditional_formatting.add(
        f"{L['espera_h']}{FILA_ENC + 1}:{L['espera_h']}{ultima}",
        FormulaRule(formula=[f'AND(ISNUMBER({L["espera_h"]}{FILA_ENC + 1}),{L["espera_h"]}{FILA_ENC + 1}>24)'], fill=AMARILLO))
    reg.freeze_panes = reg.cell(FILA_ENC + 1, COL["tipo_documento"])
    reg.auto_filter.ref = f"A{FILA_ENC}:{get_column_letter(n)}{ultima}"
    reg.sheet_view.zoomScale = 90
    reg.print_title_rows = f"{FILA_ENC}:{FILA_ENC}"
    reg.print_area = f"A1:{get_column_letter(n)}{FILA_ENC + 60}"

    # --- INSTRUCCIONES ---
    _encabezado(instr, "INSTRUCCIONES DE DILIGENCIAMIENTO – FORMATO DE REFERENCIA Y CONTRARREFERENCIA", 5, logo)
    instr.column_dimensions["A"].width = 6
    instr.column_dimensions["B"].width = 42
    instr.column_dimensions["C"].width = 14
    instr.column_dimensions["D"].width = 80
    instr.column_dimensions["E"].width = 24
    pasos = [
        "Diligencie la hoja REGISTRO: una fila por paciente y traslado. Las columnas con * son obligatorias.",
        "Use las listas desplegables. Las fechas se escriben con hora: 15/11/2026 14:30 (día/mes/año hora:minuto, 24 horas).",
        "Las columnas grises (tiempos y mes) se calculan solas; no las escriba. Una celda roja indica horas fuera de orden; "
        "amarilla, espera de más de 24 horas para la aceptación.",
        "Entregue un archivo por mes (de un hospital o consolidado de la ESE) a más tardar el día 5 del mes siguiente. "
        "Nombre sugerido: RYC-FO-015_<HOSPITAL o ESE>_<AAAA-MM>.xlsx",
        "El archivo se carga en la herramienta de Costeo ABC de ambulancias (Cargas del mes › Referencia y contrarreferencia): "
        "allí se cruza cada remisión con la facturación para asignar la ambulancia y el recorrido a cada traslado y detectar "
        "traslados realizados que no se facturaron.",
        "Datos personales (Ley 1581 de 2012): el archivo contiene información de pacientes. Envíelo solo por canales "
        "institucionales. La herramienta no guarda el nombre del paciente y guarda el documento cifrado.",
    ]
    _titulo_tabla(instr, 6, "CÓMO DILIGENCIAR Y ENTREGAR EL FORMATO", 4)
    for i, p in enumerate(pasos, start=7):
        instr.cell(i, 1, i - 6).alignment = CENTRO
        instr.merge_cells(start_row=i, start_column=2, end_row=i, end_column=4)
        instr.cell(i, 2, p).alignment = Alignment(wrap_text=True, vertical="top")
        instr.row_dimensions[i].height = 32
    fila = 7 + len(pasos) + 1
    _titulo_tabla(instr, fila, "CAMPOS DE LA HOJA REGISTRO", 4)
    _encabezados_tabla(instr, fila + 1, ["N°", "Campo", "Obligatorio", "Cómo diligenciarlo"])
    instr.row_dimensions[fila + 1].height = 20
    for k, (clave, enc, ancho, tipo, oblig, ayuda) in enumerate(COLUMNAS, start=1):
        r = fila + 1 + k
        for j, v in enumerate([k, enc, "Sí" if oblig else ("Automático" if tipo == "calculo" else "No"), ayuda], start=1):
            c = instr.cell(r, j, v)
            c.border = BORDE
            c.alignment = Alignment(wrap_text=True, vertical="top")
    instr.sheet_view.showGridLines = False

    # --- INDICADOR (oportunidad) ---
    _encabezado(ind, "INDICADOR: SEGUIMIENTO Y REVISIÓN DE TIEMPOS DE LOS PACIENTES REMITIDOS", 9, logo)
    _titulo_tabla(ind, 6, "OPORTUNIDAD DE LA REMISIÓN POR MES (se calcula con la hoja REGISTRO)", 9)
    ind["A7"] = "AÑO:"
    ind["A7"].font = Font(bold=True)
    ind["B7"] = dt.date.today().year
    ind["B7"].fill = AMARILLO
    _encabezados_tabla(ind, 8, ["MES", "CLAVE", "REMISIONES", "TRASLADADAS", "ESPERA PROMEDIO HASTA ACEPTACIÓN (HORAS)",
                                "ESPERA PROMEDIO (DÍAS)", "ESPERA MÁXIMA (HORAS)", "% ACEPTADAS EN ≤ 24 H",
                                "RESPUESTA PROMEDIO DEL EQUIPO (MIN)"])
    rg = lambda clave: f"REGISTRO!${L[clave]}${FILA_ENC + 1}:${L[clave]}${ultima}"  # noqa: E731
    meses = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
    for k, mes in enumerate(meses, start=1):
        r = 8 + k
        ind.cell(r, 1, mes)
        ind.cell(r, 2, f"=$B$7*100+{k}")
        ind.cell(r, 3, f'=COUNTIFS({rg("mes_clave")},B{r},{rg("tipo_registro")},"REFERENCIA")')
        ind.cell(r, 4, f'=COUNTIFS({rg("mes_clave")},B{r},{rg("estado")},"TRASLADADO")')
        ind.cell(r, 5, f'=IFERROR(AVERAGEIFS({rg("espera_h")},{rg("mes_clave")},B{r}),"")')
        ind.cell(r, 6, f'=IF(E{r}="","",ROUND(E{r}/24,2))')
        ind.cell(r, 7, f'=IF(COUNTIFS({rg("mes_clave")},B{r},{rg("espera_h")},">=0")=0,"",'
                       f'IFERROR(_xlfn.MAXIFS({rg("espera_h")},{rg("mes_clave")},B{r}),""))')
        ind.cell(r, 8, f'=IFERROR(COUNTIFS({rg("mes_clave")},B{r},{rg("espera_h")},"<=24")/COUNTIFS({rg("mes_clave")},B{r},{rg("espera_h")},">=0"),"")')
        ind.cell(r, 9, f'=IFERROR(AVERAGEIFS({rg("respuesta_min")},{rg("mes_clave")},B{r}),"")')
        for j in range(1, 10):
            c = ind.cell(r, j)
            c.border = BORDE
            if j in (5, 7, 9):
                c.number_format = "0.0"
            if j == 8:
                c.number_format = "0%"
    r = 21
    ind.cell(r, 1, "TOTAL AÑO").font = Font(bold=True)
    ind.cell(r, 3, "=SUM(C9:C20)")
    ind.cell(r, 4, "=SUM(D9:D20)")
    ind.cell(r, 5, f'=IFERROR(AVERAGEIFS({rg("espera_h")},{rg("mes_clave")},">="&($B$7*100+1),{rg("mes_clave")},"<="&($B$7*100+12)),"")')
    ind.cell(r, 5).number_format = "0.0"
    ind.cell(23, 1, "Meta institucional de oportunidad (horas):").font = Font(bold=True)
    ind.cell(23, 5, 24).fill = AMARILLO
    ind.column_dimensions["A"].width = 16
    ind.column_dimensions["B"].width = 9
    for col in "CDEFGHI":
        ind.column_dimensions[col].width = 17

    # --- MEDICIÓN POR EPS ---
    _encabezado(med, "MEDICIÓN POR EPS Y POR HOSPITAL", 8, logo)
    _titulo_tabla(med, 6, "REMISIONES POR EPS Y TIPO DE TRASLADO (hoja REGISTRO; escriba en amarillo el mes a medir)", 8)
    med["A7"] = "MES (AAAAMM):"
    med["A7"].font = Font(bold=True)
    med["B7"] = dt.date.today().year * 100 + dt.date.today().month
    med["B7"].fill = AMARILLO
    tipos = ["TAB", "TAM", "AÉREO", "FLUVIAL", "OTRO"]
    _encabezados_tabla(med, 8, ["EPS", "TOTAL DE REMISIONES"] + tipos + ["CON ACOMPAÑAMIENTO MÉDICO"])
    lista_eps = (eps or EPS_FRECUENTES) + [""] * 6
    for k, nombre in enumerate(lista_eps, start=9):
        med.cell(k, 1, nombre or None).fill = AMARILLO if not nombre else PatternFill()
        med.cell(k, 2, f'=IF(A{k}="","",COUNTIFS({rg("eps")},A{k},{rg("mes_clave")},$B$7))')
        for j, t in enumerate(tipos, start=3):
            med.cell(k, j, f'=IF(A{k}="","",COUNTIFS({rg("eps")},$A{k},{rg("tipo_traslado")},"{t}",{rg("mes_clave")},$B$7))')
        med.cell(k, 8, f'=IF(A{k}="","",COUNTIFS({rg("eps")},$A{k},{rg("acompanamiento")},"SI",{rg("mes_clave")},$B$7))')
        for j in range(1, 9):
            med.cell(k, j).border = BORDE
    tot = 9 + len(lista_eps)
    med.cell(tot, 1, "TOTAL").font = Font(bold=True)
    for j in range(2, 9):
        letra = get_column_letter(j)
        med.cell(tot, j, f"=SUM({letra}9:{letra}{tot - 1})").font = Font(bold=True)
    fila_h = tot + 2
    _titulo_tabla(med, fila_h, "REMISIONES POR HOSPITAL QUE REMITE (mismo mes)", 8)
    _encabezados_tabla(med, fila_h + 1, ["HOSPITAL", "TOTAL DE REMISIONES"] + tipos + ["CON ACOMPAÑAMIENTO MÉDICO"])
    for k, nombre in enumerate(sedes, start=fila_h + 2):
        med.cell(k, 1, nombre)
        med.cell(k, 2, f'=COUNTIFS({rg("sede")},A{k},{rg("mes_clave")},$B$7)')
        for j, t in enumerate(tipos, start=3):
            med.cell(k, j, f'=COUNTIFS({rg("sede")},$A{k},{rg("tipo_traslado")},"{t}",{rg("mes_clave")},$B$7)')
        med.cell(k, 8, f'=COUNTIFS({rg("sede")},$A{k},{rg("acompanamiento")},"SI",{rg("mes_clave")},$B$7)')
        for j in range(1, 9):
            med.cell(k, j).border = BORDE
    med.column_dimensions["A"].width = 40
    for col in "BCDEFGH":
        med.column_dimensions[col].width = 14

    # --- CONTROL DE CAMBIOS ---
    _encabezado(ctrl, TITULO, 6, logo)
    _titulo_tabla(ctrl, 7, "APROBACIÓN Y CONTROL DE CAMBIOS DEL DOCUMENTO", 6)
    _encabezados_tabla(ctrl, 8, ["", "", "ELABORÓ", "REVISÓ", "", "APROBÓ"])
    for k, t in enumerate(["FIRMA:", "NOMBRE:", "CARGO:", "FECHA:"], start=9):
        ctrl.cell(k, 2, t).font = Font(bold=True)
        for j in range(2, 7):
            ctrl.cell(k, j).border = BORDE
        ctrl.row_dimensions[k].height = 24
    _encabezados_tabla(ctrl, 14, ["", "VERSIÓN", "FECHA", "DESCRIPCIÓN DEL CAMBIO", "", ""])
    ctrl.merge_cells("D14:F14")
    cambios = [
        (1, "06 de marzo de 2023", "Emisión del documento."),
        (2, "noviembre de 2026",
         "Hoja INSTRUCCIONES; listas desplegables (hospital, EPS, tipo de documento, sexo, especialidad, tipo de traslado, placa, "
         "municipio destino, estado); fechas con hora validadas en todas las etapas; nuevos campos: tipo de registro "
         "(referencia/contrarreferencia), régimen, CIE-10, IPS y municipio destino, km, autorización, factura o ingreso y estado; "
         "cálculo automático de espera, respuesta y duración; INDICADOR mensual y MEDICIÓN POR EPS y por hospital con fórmulas; "
         "formato cargable en la herramienta de costeo ABC de ambulancias."),
    ]
    for k, (v, f, d) in enumerate(cambios, start=15):
        ctrl.cell(k, 2, v).alignment = CENTRO
        ctrl.cell(k, 3, f)
        ctrl.merge_cells(start_row=k, start_column=4, end_row=k, end_column=6)
        ctrl.cell(k, 4, d).alignment = Alignment(wrap_text=True, vertical="top")
        ctrl.row_dimensions[k].height = 20 if v == 1 else 92
        for j in range(2, 7):
            ctrl.cell(k, j).border = BORDE
    for col, w in zip("ABCDEF", (3, 12, 30, 30, 30, 30)):
        ctrl.column_dimensions[col].width = w

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Lectura (v002 y v001)
# ---------------------------------------------------------------------------


def _fechahora(v):
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.date):
        return dt.datetime.combine(v, dt.time())
    if isinstance(v, (int, float)) and 20000 < v < 80000:  # número de serie de Excel
        return dt.datetime(1899, 12, 30) + dt.timedelta(days=float(v))
    if isinstance(v, str) and v.strip():
        s = re.sub(r"\s+", " ", v.strip().upper().replace(".", "").replace("A M", "AM").replace("P M", "PM"))
        for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %I:%M %p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                    "%d-%m-%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
            try:
                return dt.datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def _hora(v):
    if isinstance(v, dt.datetime):
        return v.time()
    if isinstance(v, dt.time):
        return v
    if isinstance(v, (int, float)) and 0 <= v < 1:
        minutos = round(float(v) * 1440)
        return dt.time(minutos // 60 % 24, minutos % 60)
    if isinstance(v, str) and v.strip():
        s = re.sub(r"\s+", " ", v.strip().upper().replace(".", "").replace("A M", "AM").replace("P M", "PM"))
        for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%H%M"):
            try:
                return dt.datetime.strptime(s, fmt).time()
            except ValueError:
                continue
    return None


def _texto(v, largo=120):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()[:largo]


def _encontrar_encabezado(ws):
    for r, fila in enumerate(ws.iter_rows(min_row=1, max_row=20, values_only=True), start=1):
        claves = {}
        for j, v in enumerate(fila):
            if isinstance(v, str):
                t = normalizar(v).rstrip(" *").strip()
                if t in SINONIMOS:
                    claves.setdefault(SINONIMOS[t], j)
        if "placa" in claves and ("documento" in claves or "tipo_traslado" in claves):
            return r, claves
    return None, None


def leer_referencia(archivo, nombre: str, sal: str) -> dict:
    """{"registros": [...], "hallazgos": [...], "version": "001"|"002", "filas_vacias": n}."""
    wb = load_workbook(archivo, data_only=True, read_only=False)
    salida = {"registros": [], "hallazgos": [], "version": None, "hoja": None}
    ws, r_enc, col = None, None, None
    for hoja in wb.worksheets:
        r_enc, col = _encontrar_encabezado(hoja)
        if col:
            ws = hoja
            break
    if ws is None:
        salida["hallazgos"].append(hallazgo(nombre, "libro", "ENCABEZADO",
                                            "No se encontró la tabla de remisiones (columnas PLACA, DOCUMENTO, TIPO DE TRASLADO)", "ALTA"))
        return salida
    v1 = "fecha_reporte_v1" in col
    salida["version"] = "001" if v1 else "002"
    salida["hoja"] = ws.title

    def val(fila, clave):
        j = col.get(clave)
        return fila[j] if j is not None and j < len(fila) else None

    for r, fila in enumerate(ws.iter_rows(min_row=r_enc + 1, values_only=True), start=r_enc + 1):
        doc = _texto(val(fila, "documento"), 30)
        base_fecha = val(fila, "fecha_reporte_v1") if v1 else val(fila, "fecha_reporte")
        if not doc and base_fecha in (None, ""):
            continue  # fila vacía del formato
        ubic = f"{ws.title}!fila {r}"
        obs = []
        if v1:
            f0 = _fechahora(val(fila, "fecha_reporte_v1"))
            h0 = _hora(val(fila, "hora_reporte_v1"))
            reporte = dt.datetime.combine(f0.date(), h0 or dt.time()) if f0 else None

            def etapa(clave, anterior):
                h = _hora(val(fila, clave))
                if h is None or reporte is None:
                    return None
                d = dt.datetime.combine(reporte.date(), h)
                if anterior and d < anterior:
                    d += dt.timedelta(days=1)  # el formato 001 solo trae la hora: se asume el día siguiente
                    obs.append(f"{clave.replace('hora_', '').replace('_v1', '')}: hora menor que la anterior, se tomó el día siguiente")
                return d
            llamada = etapa("hora_llamada_v1", reporte)
            llegada_eq = etapa("hora_llegada_equipo_v1", llamada or reporte)
            aceptacion = etapa("hora_aceptacion_v1", reporte)
            salida_t = etapa("hora_salida_v1", aceptacion or reporte)
            llegada = etapa("hora_llegada_v1", salida_t)
        else:
            reporte = _fechahora(val(fila, "fecha_reporte"))
            llamada, llegada_eq = _fechahora(val(fila, "fecha_llamada")), _fechahora(val(fila, "fecha_llegada_equipo"))
            aceptacion, salida_t, llegada = (_fechahora(val(fila, "fecha_aceptacion")), _fechahora(val(fila, "fecha_salida")),
                                             _fechahora(val(fila, "fecha_llegada")))
        if reporte is None:
            salida["hallazgos"].append(hallazgo(nombre, ubic, "FALTANTE", "Remisión sin fecha de reporte válida; no se carga", "ALTA"))
            continue
        orden = [("reporte", reporte), ("aceptación", aceptacion), ("salida", salida_t), ("llegada al destino", llegada)]
        presentes = [(n, d) for n, d in orden if d]
        for (n1, d1), (n2, d2) in zip(presentes, presentes[1:]):
            if d2 < d1:
                obs.append(f"{n2} ({d2:%d/%m %H:%M}) antes de {n1} ({d1:%d/%m %H:%M})")
        if llamada and llegada_eq and llegada_eq < llamada:
            obs.append("llegada del equipo antes de la llamada")
        tipo_doc = normalizar(val(fila, "tipo_documento"))[:5]
        edad = val(fila, "edad")
        try:
            edad = int(float(edad)) if edad not in (None, "") else None
        except (TypeError, ValueError):
            obs.append(f"edad no numérica: {edad}")
            edad = None
        destino_txt = _texto(val(fila, "destino_v1"), 150) if v1 else ""
        municipio = normalizar(val(fila, "municipio_destino")) if not v1 else ""
        ips_destino = _texto(val(fila, "ips_destino"), 150) if not v1 else destino_txt
        km = val(fila, "km")
        try:
            km = float(km) if km not in (None, "") else None
        except (TypeError, ValueError):
            km = None
        salida["registros"].append({
            "fila": r, "ubicacion": ubic,
            "tipo_registro": normalizar(val(fila, "tipo_registro")) or "REFERENCIA",
            "sede_texto": _texto(val(fila, "sede"), 150),
            "eps": normalizar(val(fila, "eps"))[:80],
            "regimen": normalizar(val(fila, "regimen"))[:40],
            "tipo_documento": tipo_doc,
            "documento_hash": hash_documento(tipo_doc, doc, sal) if doc else "",
            "edad": edad, "unidad_edad": normalizar(val(fila, "unidad_edad"))[:10] or ("AÑOS" if edad is not None else ""),
            "sexo": normalizar(val(fila, "sexo"))[:1],
            "especialidad": normalizar(val(fila, "especialidad"))[:80],
            "cie10": re.sub(r"[^A-Z0-9]", "", normalizar(val(fila, "cie10")))[:6] if not v1 else "",
            "diagnostico": _texto(val(fila, "diagnostico_v1") if v1 else val(fila, "diagnostico"), 200),
            "tipo_traslado": normalizar(val(fila, "tipo_traslado")).replace("AEREO", "AÉREO")[:15],
            "acompanamiento": normalizar(val(fila, "acompanamiento")) in ("SI", "S", "X", "1", "TRUE"),
            "placa": re.sub(r"[^A-Z0-9]", "", normalizar(val(fila, "placa")))[:10],
            "ips_destino": ips_destino, "municipio_destino": municipio[:80], "destino_texto": destino_txt,
            "fecha_reporte": reporte, "fecha_llamada": llamada, "fecha_llegada_equipo": llegada_eq,
            "fecha_aceptacion": aceptacion, "fecha_salida": salida_t, "fecha_llegada": llegada,
            "km": km, "autorizacion": _texto(val(fila, "autorizacion"), 60), "factura": _texto(val(fila, "factura"), 30).upper(),
            "estado": normalizar(val(fila, "estado"))[:40] or ("TRASLADADO" if salida_t else ""),
            "observaciones": _texto(val(fila, "observaciones"), 500), "responsable": _texto(val(fila, "responsable"), 120),
            "alertas": obs,
        })
    return salida

"""Plantillas de recolección en Excel, generadas a partir de la estructura común.

Una plantilla por ítem. Cada una trae la hoja ``Datos`` (con listas desplegables y
formatos), ``Instrucciones`` y ``Catálogo`` (códigos de sede vigentes). Como salen de
``esquema.PLANTILLAS``, un cambio en la estructura se refleja aquí sin tocar este archivo.
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from . import db, esquema
from .config import COMBUSTIBLE, ENTIDAD, ITEMS, MANTENIMIENTO, SERVICIOS

FILA_ENCABEZADO = 4
FILAS_DATOS = 600
AZUL, AZUL_CLARO, GRIS = "1C5CAB", "E8F1FC", "F3F4F6"
_BORDE = Border(*(Side(style="thin", color="C9CED6"),) * 4)

NOMBRE_ARCHIVO = {SERVICIOS: "Plantilla_Servicios_Publicos.xlsx", COMBUSTIBLE: "Plantilla_Combustible.xlsx",
                  MANTENIMIENTO: "Plantilla_Mantenimientos.xlsx"}

_EJEMPLOS = {
    SERVICIOS: [["2026-10", "TAM-01", "Hospital San Antonio de Tame", "Energía eléctrica", 13638, "kWh", 15656907,
                 15656910, "Empresa de energía", "FE-123456", "05/11/2026", ""],
                ["2026-10", "TAM-01", "Hospital San Antonio de Tame", "Acueducto", 702, "m3", 1454618, 9109087,
                 "Empresa de acueducto", "A-98765", "03/11/2026", "El total se escribe una sola vez por factura"],
                ["2026-10", "TAM-01", "Hospital San Antonio de Tame", "Aseo", "", "N/A", 60719, "",
                 "Empresa de acueducto", "A-98765", "03/11/2026", ""]],
    COMBUSTIBLE: [["15/10/2026", "", "", esquema.CAT_VEHICULOS, "ABC123", "Diésel (ACPM)", 9.5, 107350, 245712,
                   "Estación de servicio", "FV-4521", ""],
                  ["19/10/2026", "PRN-01", "Hospital San Juan de Dios de Puerto Rondón", esquema.CAT_PLANTAS,
                   "PLANTA ELECTRICA DIESEL", "Diésel (ACPM)", 60, 756000, "", "Estación de servicio", "FV-4630", ""]],
    MANTENIMIENTO: [["12/10/2026", "TAM-01", "Hospital San Antonio de Tame", esquema.CAT_VEHICULOS, "ABC123",
                     "Correctivo", "Cambio de embrague y rectificación de volante", 3234014, "Taller autorizado",
                     "OT-0457", ""],
                    ["20/10/2026", "FOR-01", "Hospital San Francisco de Fortul", "Equipo biomédico", "INV-00231",
                     "Preventivo", "Mantenimiento preventivo autoclave", 850000, "Proveedor biomédico", "FV-881", ""]],
}

_REGLAS = {
    SERVICIOS: ["Un renglón por cada servicio de cada factura (energía, acueducto, alcantarillado, aseo…).",
                "El periodo es el mes de consumo facturado, no el mes en que llegó o se pagó la factura.",
                "«Valor facturado del periodo» es solo lo del mes. El saldo anterior NO va ahí: va incluido en "
                "«Total a pagar de la factura», que se escribe una sola vez por factura."],
    COMBUSTIBLE: ["Un renglón por cada tanqueo o suministro, con la fecha real del suministro.",
                  "Para vehículos escriba la placa sin espacios ni guiones (ABC123); la herramienta ubica la sede por la placa.",
                  "Para plantas eléctricas, guadañas u otros equipos indique el código de sede y el nombre del equipo.",
                  "La cantidad va en galones. Si la estación factura en litros, divida entre 3,785."],
    MANTENIMIENTO: ["Un renglón por cada orden de trabajo o factura de mantenimiento.",
                    "Clasifique cada intervención como preventiva o correctiva.",
                    "Para vehículos escriba la placa; para otros equipos, la placa de inventario o la serie."],
}


def generar(item: str, hospital: str | None = None, ruta=None) -> bytes:
    """Devuelve la plantilla del ítem. Con ``hospital`` el catálogo trae solo sus sedes."""
    campos = esquema.PLANTILLAS[item]
    sedes = db.consulta("SELECT sede_id, hospital, sede, municipio FROM sedes WHERE activo=1 "
                        + ("AND hospital=? " if hospital else "") + "ORDER BY hospital, es_principal DESC, sede",
                        (hospital,) if hospital else (), ruta)
    wb = Workbook()
    ws = wb.active
    ws.title = "Datos"

    ws["A1"] = f"{ENTIDAD} · Plantilla de {ITEMS[item].lower()}" + (f" · {hospital}" if hospital else "")
    ws["A1"].font = Font(bold=True, size=14, color=AZUL)
    ws["A2"] = ("Diligencie a partir de la fila 5, un renglón por registro. No cambie los encabezados ni deje filas "
                "en blanco intermedias. Las columnas con * son obligatorias. Vea la hoja «Instrucciones».")
    ws["A2"].font = Font(italic=True, color="52514E")
    ws.row_dimensions[FILA_ENCABEZADO].height = 34

    # Listas largas van en una hoja oculta (Excel limita las listas escritas a 255 caracteres).
    listas = wb.create_sheet("Listas")
    listas.sheet_state = "hidden"
    listas["A1"], listas["B1"], listas["C1"] = "plantilla", item, esquema.VERSION_PLANTILLA
    col_lista = 4

    ultima = FILA_ENCABEZADO + FILAS_DATOS
    for j, c in enumerate(campos, start=1):
        letra = get_column_letter(j)
        celda = ws.cell(row=FILA_ENCABEZADO, column=j, value=c.titulo + (" *" if c.obligatorio else ""))
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor=AZUL if c.obligatorio else "5B6470")
        celda.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        celda.border = _BORDE
        if c.ayuda:
            celda.comment = Comment(c.ayuda, ENTIDAD, width=320, height=90)
        ws.column_dimensions[letra].width = c.ancho
        rango = f"{letra}{FILA_ENCABEZADO + 1}:{letra}{ultima}"
        formato = {"moneda": '"$" #,##0', "numero": "#,##0.###", "fecha": "DD/MM/YYYY", "periodo": "@",
                   "texto": "@", "lista": "@"}[c.tipo]
        for i in range(FILA_ENCABEZADO + 1, ultima + 1):
            cd = ws.cell(row=i, column=j)
            cd.number_format = formato
            cd.border = _BORDE
        dv = None
        if c.tipo == "lista":
            lc = get_column_letter(col_lista)
            for k, op in enumerate(c.opciones, start=1):
                listas.cell(row=k, column=col_lista, value=op)
            dv = DataValidation(type="list", formula1=f"Listas!${lc}$1:${lc}${len(c.opciones)}", allow_blank=True,
                                showErrorMessage=True, errorTitle="Valor no permitido",
                                error="Elija una opción de la lista.")
            col_lista += 1
        elif c.tipo in ("numero", "moneda"):
            dv = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1="0", allow_blank=True,
                                showErrorMessage=True, errorTitle="Número no válido",
                                error="Escriba solo el número, sin letras ni símbolos.")
        elif c.tipo == "fecha":
            dv = DataValidation(type="date", operator="greaterThan", formula1="42005", allow_blank=True,
                                showErrorMessage=True, errorTitle="Fecha no válida", error="Use el formato DD/MM/AAAA.")
        elif c.nombre == "codigo" and len(sedes):
            # Lista de sugerencia: se permite escribir también un centro de costo o número de cuenta.
            dv = DataValidation(type="list", formula1=f"'Catálogo'!$A$2:$A${len(sedes) + 1}", allow_blank=True,
                                showErrorMessage=False)
        if dv is not None:
            if c.ayuda:
                dv.promptTitle, dv.prompt, dv.showInputMessage = c.titulo[:32], c.ayuda[:255], True
            ws.add_data_validation(dv)
            dv.add(rango)
    ws.freeze_panes = ws.cell(row=FILA_ENCABEZADO + 1, column=1)

    # Instrucciones
    ins = wb.create_sheet("Instrucciones")
    ins.column_dimensions["A"].width = 34
    ins.column_dimensions["B"].width = 14
    ins.column_dimensions["C"].width = 110
    ins["A1"] = f"Cómo diligenciar la plantilla de {ITEMS[item].lower()}"
    ins["A1"].font = Font(bold=True, size=13, color=AZUL)
    f = 3
    for regla in _REGLAS[item] + ["Cuando termine, guarde el archivo y cárguelo en la herramienta: menú «Cargar datos». "
                                  "La herramienta le mostrará los errores antes de guardar."]:
        ins.cell(row=f, column=1, value="•")
        ins.cell(row=f, column=1).alignment = Alignment(horizontal="right")
        ins.merge_cells(start_row=f, start_column=2, end_row=f, end_column=3)
        ins.cell(row=f, column=2, value=regla).alignment = Alignment(wrap_text=True, vertical="top")
        ins.row_dimensions[f].height = 30
        f += 1
    f += 1
    for j, t in enumerate(["Columna", "Obligatoria", "Qué se escribe"], start=1):
        c = ins.cell(row=f, column=j, value=t)
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor=AZUL)
    for c in campos:
        f += 1
        ins.cell(row=f, column=1, value=c.titulo)
        ins.cell(row=f, column=2, value="Sí" if c.obligatorio else "No")
        texto = c.ayuda + (" Opciones: " + ", ".join(c.opciones) + "." if c.opciones else "")
        ins.cell(row=f, column=3, value=texto.strip()).alignment = Alignment(wrap_text=True, vertical="top")
    f += 2
    ins.cell(row=f, column=1, value="Ejemplos (no se cargan: son solo guía)").font = Font(bold=True, color=AZUL)
    f += 1
    for j, c in enumerate(campos, start=1):
        cd = ins.cell(row=f, column=j + 3, value=c.titulo)
        cd.font, cd.fill = Font(bold=True), PatternFill("solid", fgColor=GRIS)
        ins.column_dimensions[get_column_letter(j + 3)].width = max(c.ancho, 14)
    for ej in _EJEMPLOS[item]:
        f += 1
        for j, v in enumerate(ej, start=1):
            ins.cell(row=f, column=j + 3, value=v if v != "" else None)

    # Catálogo
    cat = wb.create_sheet("Catálogo")
    for j, (t, ancho) in enumerate([("Código de sede", 16), ("Hospital / centro", 48), ("Sede", 48), ("Municipio", 18)],
                                   start=1):
        c = cat.cell(row=1, column=j, value=t)
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor=AZUL)
        cat.column_dimensions[get_column_letter(j)].width = ancho
    for i, fila in enumerate(sedes.itertuples(index=False), start=2):
        for j, v in enumerate(fila, start=1):
            cat.cell(row=i, column=j, value=v)
    cat.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

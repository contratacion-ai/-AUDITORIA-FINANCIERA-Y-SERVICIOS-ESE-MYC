"""Informe en Excel con múltiples hojas: por ítem, por hospital y por sede.

Hojas: Resumen · Servicios públicos · Combustible · Mantenimientos · una hoja por
hospital · Alertas · Análisis · Detalle · Trazabilidad.
"""
from __future__ import annotations

import datetime as dt
import io
import re

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import analisis, cargues, db, esquema
from .config import COLOR_ITEM, COMBUSTIBLE, ENTIDAD, ITEMS, MANTENIMIENTO, SERVICIOS
from .extraccion.comunes import nombre_periodo

AZUL, GRIS, CLARO = "1C5CAB", "F3F4F6", "E8F1FC"
_LINEA = Side(style="thin", color="D5D9E0")
_BORDE = Border(bottom=_LINEA)
MONEDA, NUMERO, DECIMAL = '"$" #,##0', "#,##0", "#,##0.0"
COLOR_SEV = {"Alta": "F8D7D7", "Media": "FBE9C6", "Baja": "E9ECEF"}
COLOR_POR_NOMBRE = {ITEMS[i]: c.strip("#").upper() for i, c in COLOR_ITEM.items()}


def _titulo(ws, texto: str, subtitulo: str = "") -> int:
    ws["A1"] = texto
    ws["A1"].font = Font(bold=True, size=14, color=AZUL)
    ws["A2"] = subtitulo
    ws["A2"].font = Font(italic=True, color="52514E")
    return 4


def _tabla(ws, df: pd.DataFrame, fila: int, titulo: str = "", formatos: dict | None = None, defecto: str | None = None,
           total: bool = False, anchos: dict | None = None) -> int:
    """Escribe un DataFrame con encabezado y devuelve la siguiente fila libre."""
    formatos, anchos = formatos or {}, anchos or {}
    if titulo:
        ws.cell(row=fila, column=1, value=titulo).font = Font(bold=True, size=11, color=AZUL)
        fila += 1
    if df.empty:
        ws.cell(row=fila, column=1, value="Sin datos para el filtro seleccionado.").font = Font(italic=True)
        return fila + 2
    for j, col in enumerate(df.columns, start=1):
        c = ws.cell(row=fila, column=j, value=str(col))
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=AZUL)
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    numericas = [pd.api.types.is_numeric_dtype(df[c]) for c in df.columns]
    for i, valores in enumerate(df.itertuples(index=False), start=fila + 1):
        for j, v in enumerate(valores, start=1):
            if v is not None and not isinstance(v, str) and pd.isna(v):
                v = None
            c = ws.cell(row=i, column=j, value=v)
            c.border = _BORDE
            col = df.columns[j - 1]
            if numericas[j - 1]:
                c.number_format = formatos.get(col, defecto or NUMERO)
            elif isinstance(v, str) and len(v) > 60:
                c.alignment = Alignment(wrap_text=True, vertical="top")
    fin = fila + len(df)
    if total:
        fin += 1
        ws.cell(row=fin, column=1, value="TOTAL").font = Font(bold=True)
        for j, col in enumerate(df.columns, start=1):
            if numericas[j - 1]:
                letra = get_column_letter(j)
                c = ws.cell(row=fin, column=j, value=f"=SUM({letra}{fila + 1}:{letra}{fin - 1})")
                c.number_format = formatos.get(col, defecto or NUMERO)
                c.font = Font(bold=True)
            ws.cell(row=fin, column=j).fill = PatternFill("solid", fgColor=CLARO)
    for j, col in enumerate(df.columns, start=1):
        letra = get_column_letter(j)
        if col in anchos:
            ancho = anchos[col]
        elif numericas[j - 1]:
            ancho = 16
        else:
            ancho = min(max([len(str(col))] + [len(str(v)) for v in df[col].head(400) if v is not None]) + 2, 46)
        ws.column_dimensions[letra].width = max(ws.column_dimensions[letra].width or 0, ancho)
    return fin + 2


def _pivote(df: pd.DataFrame, filas: list[str], valor: str, periodos: list[str]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    p = df.pivot_table(index=filas, columns="periodo", values=valor, aggfunc="sum", fill_value=0)
    p = p.reindex(columns=[x for x in periodos if x in p.columns])
    p["Total"] = p.sum(axis=1)
    p = p.rename(columns={x: nombre_periodo(x) for x in periodos}).reset_index()
    return p.rename(columns={"hospital": "Hospital", "sede": "Sede", "categoria": "Categoría", "concepto": "Concepto",
                             "referencia": "Referencia", "item_nombre": "Ítem", "unidad": "Unidad"})


def _nombre_hoja(texto: str, usados: set) -> str:
    base = re.sub(r"[\[\]\*\?/\\:]", "", texto)
    base = re.sub(r"^(Hospital|Centro de Salud) ", "", base)[:28].strip()
    nombre, k = base, 2
    while nombre.lower() in usados:
        nombre = f"{base[:25]} {k}"
        k += 1
    usados.add(nombre.lower())
    return nombre


def generar(ruta=None, desde: str | None = None, hasta: str | None = None, hospital: str | None = None,
            periodo_control: str | None = None, hoy: dt.date | None = None, nota: str = "") -> bytes:
    df = analisis.datos(ruta, hospital)
    despl = analisis.desplazamientos(ruta, hospital)
    veh = analisis.vehiculos(ruta)
    todos = analisis.periodos(df)
    desde, hasta = desde or (todos[0] if todos else ""), hasta or (todos[-1] if todos else "")
    df = df[(df["periodo"] >= desde) & (df["periodo"] <= hasta)]
    pers = analisis.periodos(df)
    control = periodo_control or analisis.periodo_de_control(df, hoy)
    al = analisis.alertas(df, despl, veh, db.parametros(ruta), control,
                          0 if hospital else analisis.pendientes_catalogo(ruta))
    texto = analisis.analisis_texto(df, despl, veh, al, control)
    rango = f"{nombre_periodo(desde)} a {nombre_periodo(hasta)}" if pers else "sin datos"
    sub = (f"{hospital or 'Consolidado ' + ENTIDAD} · Periodos: {rango} · Generado el "
           f"{dt.datetime.now():%d/%m/%Y %H:%M} · Solo registros vigentes" + (f" · {nota}" if nota else ""))
    mes_fmt = {nombre_periodo(p): MONEDA for p in pers} | {"Total": MONEDA}

    wb = Workbook()
    # ---------------- Resumen ----------------
    ws = wb.active
    ws.title = "Resumen"
    f = _titulo(ws, f"Control de servicios públicos, combustible y mantenimientos · {ENTIDAD}", sub)
    por_item = _pivote(df, ["item_nombre"], "valor", pers)
    f0 = f
    f = _tabla(ws, por_item, f, "Gasto mensual por ítem", mes_fmt, total=True, anchos={"Ítem": 44})
    if len(por_item) and len(pers) >= 2:
        g = BarChart()
        g.type, g.grouping, g.overlap = "col", "stacked", 100
        g.title, g.height, g.width = "Gasto mensual por ítem", 8.5, 22
        g.y_axis.numFmt, g.y_axis.majorGridlines = '#,##0,,"M"', None
        datos = Reference(ws, min_col=1, max_col=1 + len(pers), min_row=f0 + 2, max_row=f0 + 1 + len(por_item))
        g.add_data(datos, from_rows=True, titles_from_data=True)
        g.set_categories(Reference(ws, min_col=2, max_col=1 + len(pers), min_row=f0 + 1))
        for serie, nombre in zip(g.series, por_item["Ítem"]):     # mismos colores que el tablero
            serie.graphicalProperties.solidFill = COLOR_POR_NOMBRE.get(nombre, "5B6470")
        ws.add_chart(g, f"A{f}")
        f += 19
    por_h = df.pivot_table(index="hospital", columns="item_nombre", values="valor", aggfunc="sum", fill_value=0) \
        if len(df) else pd.DataFrame()
    if len(por_h):
        por_h["Total"] = por_h.sum(axis=1)
        por_h = por_h.sort_values("Total", ascending=False).reset_index().rename(columns={"hospital": "Hospital"})
    f = _tabla(ws, por_h, f, f"Gasto por hospital e ítem ({rango})", defecto=MONEDA, total=True, anchos={"Hospital": 44})
    f = _tabla(ws, _pivote(df, ["hospital"], "valor", pers), f, "Gasto mensual por hospital", mes_fmt, total=True)
    ws.freeze_panes = "B4"

    # ---------------- Hojas por ítem ----------------
    s = df[df["item"] == SERVICIOS]
    ws = wb.create_sheet("Servicios públicos")
    f = _titulo(ws, "Servicios públicos por hospital, sede y servicio", sub)
    f = _tabla(ws, _pivote(s, ["hospital", "sede", "categoria", "concepto"], "valor", pers), f,
               "Valor facturado del periodo ($)", mes_fmt, total=True)
    cons = s[s["cantidad"].notna() & s["unidad"].notna()]
    f = _tabla(ws, _pivote(cons, ["hospital", "sede", "concepto", "unidad"], "cantidad", pers), f,
               "Consumo (kWh de energía, m3 de acueducto)", defecto=NUMERO)
    mora = analisis.mora_servicios(s)
    mora = mora[mora["total_a_pagar"].notna()]
    if len(mora):
        m = mora.rename(columns={"valor": "Facturado en el mes", "total_a_pagar": "Total a pagar", "saldo": "Saldo anterior"})
        m["Periodo"] = m["periodo"].map(nombre_periodo)
        m = m[m["periodo"] == m["periodo"].max()].sort_values("Saldo anterior", ascending=False)
        f = _tabla(ws, m[["Periodo", "hospital", "sede", "categoria", "Facturado en el mes", "Total a pagar",
                          "Saldo anterior"]].rename(columns={"hospital": "Hospital", "sede": "Sede", "categoria": "Servicio"}),
                   f, "Total a pagar y saldo de periodos anteriores (último periodo)", defecto=MONEDA, total=True)
    ws.freeze_panes = "C4"

    c = df[df["item"] == COMBUSTIBLE]
    ws = wb.create_sheet("Combustible")
    f = _titulo(ws, "Combustible por hospital, destino y vehículo o equipo", sub)
    f = _tabla(ws, _pivote(c, ["hospital", "categoria", "referencia", "concepto"], "valor", pers), f,
               "Valor del combustible ($)", mes_fmt, total=True)
    f = _tabla(ws, _pivote(c[c["cantidad"].notna()], ["hospital", "categoria", "referencia", "concepto"], "cantidad", pers),
               f, "Galones suministrados", defecto=DECIMAL, total=True)
    r = analisis.rendimiento(df, despl, veh)
    r = r[r["periodo"].isin(pers)]
    if len(r):
        r["Periodo"] = r["periodo"].map(nombre_periodo)
        r = r.rename(columns={"hospital": "Hospital", "placa": "Placa", "descripcion": "Vehículo", "tanqueos": "Tanqueos",
                              "galones": "Galones", "valor": "Valor ($)", "viajes": "Viajes", "km": "Km recorridos",
                              "km_por_galon": "Km por galón", "rendimiento_esperado": "Esperado (km/gal)",
                              "pct_esperado": "% del esperado"})
        f = _tabla(ws, r[["Periodo", "Hospital", "Placa", "Vehículo", "Tanqueos", "Galones", "Valor ($)", "Viajes",
                          "Km recorridos", "Km por galón", "Esperado (km/gal)", "% del esperado"]], f,
                   "Rendimiento por vehículo (combustible frente a desplazamientos registrados)",
                   formatos={"Valor ($)": MONEDA, "Galones": DECIMAL, "Km por galón": DECIMAL, "Esperado (km/gal)": DECIMAL,
                             "% del esperado": "0"})
    ws.freeze_panes = "C4"

    m = df[df["item"] == MANTENIMIENTO]
    ws = wb.create_sheet("Mantenimientos")
    f = _titulo(ws, "Mantenimientos por hospital, tipo de activo y activo", sub)
    f = _tabla(ws, _pivote(m, ["hospital", "sede", "categoria", "referencia"], "valor", pers), f,
               "Valor de mantenimiento ($)", mes_fmt, total=True)
    f = _tabla(ws, _pivote(m, ["hospital", "concepto"], "valor", pers), f, "Por tipo de mantenimiento ($)", mes_fmt,
               total=True)
    ws.freeze_panes = "C4"

    # ---------------- Una hoja por hospital ----------------
    usados = {x.lower() for x in wb.sheetnames} | {"alertas", "análisis", "detalle", "trazabilidad"}
    for h in sorted(df["hospital"].unique()):
        dh = df[df["hospital"] == h]
        ws = wb.create_sheet(_nombre_hoja(h, usados))
        f = _titulo(ws, h, sub)
        f0 = f
        tot = _pivote(dh, ["item_nombre"], "valor", pers)
        f = _tabla(ws, tot, f, "Gasto mensual por ítem", mes_fmt, total=True, anchos={"Ítem": 40})
        if len(tot) and len(pers) >= 2:
            g = LineChart()
            g.title, g.height, g.width = "Evolución mensual por ítem", 7.5, 20
            g.y_axis.numFmt = '#,##0,,"M"'
            g.add_data(Reference(ws, min_col=1, max_col=1 + len(pers), min_row=f0 + 2, max_row=f0 + 1 + len(tot)),
                       from_rows=True, titles_from_data=True)
            g.set_categories(Reference(ws, min_col=2, max_col=1 + len(pers), min_row=f0 + 1))
            ws.add_chart(g, f"A{f}")
            f += 17
        for item in ITEMS:
            di = dh[dh["item"] == item]
            if di.empty:
                continue
            filas = ["sede", "categoria", "concepto"] if item == SERVICIOS else ["sede", "categoria", "referencia"]
            f = _tabla(ws, _pivote(di, filas, "valor", pers), f, f"{ITEMS[item]} por sede ($)", mes_fmt, total=True)
        ah = al[al["hospital"] == h]
        if len(ah):
            f = _tabla(ws, ah[["severidad", "item", "tipo", "sede", "mensaje", "recomendacion"]].rename(columns=str.capitalize),
                       f, f"Alertas de {nombre_periodo(control)}", anchos={"Mensaje": 46, "Recomendacion": 46})
        ws.freeze_panes = "B4"

    # ---------------- Alertas y análisis ----------------
    ws = wb.create_sheet("Alertas")
    f = _titulo(ws, f"Alertas del periodo de control: {nombre_periodo(control) if control else '-'}",
                "Se evalúa el último mes cerrado. ▲ Alta · ◆ Media · ● Baja. Umbrales configurables en la herramienta.")
    vista = al.rename(columns={"severidad": "Severidad", "item": "Ítem", "tipo": "Tipo de alerta", "hospital": "Hospital",
                               "sede": "Sede", "indicador": "Indicador", "valor": "Valor", "referencia": "Referencia",
                               "mensaje": "Qué se observa", "recomendacion": "Recomendación"}).drop(columns=["periodo"],
                                                                                                 errors="ignore")
    f1 = f
    _tabla(ws, vista, f, formatos={"Valor": "#,##0.0", "Referencia": "#,##0.0"},
           anchos={"Qué se observa": 46, "Recomendación": 46, "Hospital": 34, "Sede": 30})
    for i, sev in enumerate(al["severidad"], start=f1 + 1):
        ws.cell(row=i, column=1).fill = PatternFill("solid", fgColor=COLOR_SEV.get(sev, GRIS))
        ws.cell(row=i, column=1).value = {"Alta": "▲ Alta", "Media": "◆ Media", "Baja": "● Baja"}.get(sev, sev)
    ws.freeze_panes = ws.cell(row=f1 + 1, column=2)
    if len(al):
        ws.auto_filter.ref = f"A{f1}:{get_column_letter(len(vista.columns))}{f1 + len(al)}"

    ws = wb.create_sheet("Análisis")
    f = _titulo(ws, "Análisis interpretativo y recomendaciones", sub)
    ws.column_dimensions["A"].width = 130
    for titulo, parrafos in texto:
        ws.cell(row=f, column=1, value=titulo).font = Font(bold=True, size=12, color=AZUL)
        f += 1
        for p in parrafos:
            c = ws.cell(row=f, column=1, value=p)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[f].height = 15 * (len(p) // 125 + 1)
            f += 1
        f += 1

    # ---------------- Detalle y trazabilidad ----------------
    ws = wb.create_sheet("Detalle")
    f = _titulo(ws, "Detalle de registros vigentes", sub)
    det = df.sort_values(["item", "periodo", "hospital", "sede"])[
        ["item_nombre", "periodo", "fecha", "hospital", "sede", "categoria", "concepto", "referencia", "descripcion",
         "cantidad", "unidad", "valor", "total_a_pagar", "proveedor", "cargue_id", "hoja", "fila"]]
    det.columns = ["Ítem", "Periodo", "Fecha", "Hospital", "Sede", "Categoría", "Concepto", "Referencia", "Descripción",
                   "Cantidad", "Unidad", "Valor", "Total a pagar", "Proveedor", "Cargue", "Hoja origen", "Fila origen"]
    _tabla(ws, det, f, formatos={"Valor": MONEDA, "Total a pagar": MONEDA, "Cantidad": "#,##0.##", "Cargue": "0",
                                 "Fila origen": "0"}, anchos={"Descripción": 40})
    ws.freeze_panes = ws.cell(row=f + 1, column=1)
    if len(det):
        ws.auto_filter.ref = f"A{f}:{get_column_letter(len(det.columns))}{f + len(det)}"

    ws = wb.create_sheet("Trazabilidad")
    f = _titulo(ws, "Trazabilidad de cargues", "Qué archivo ingresó, cuándo, quién lo cargó, a qué periodos corresponde "
                                               "y qué reemplazó.")
    car = cargues.listar(ruta, hospital)
    if len(car):
        car = car[["cargue_id", "item_nombre", "archivo_nombre", "fecha_carga", "usuario", "ambito", "periodos", "modo",
                   "estado", "reemplazo_de", "filas_leidas", "filas_cargadas", "filas_rechazadas", "advertencias",
                   "valor_total", "archivo_sha256", "observacion"]]
        car.columns = ["Cargue", "Ítem", "Archivo", "Fecha de carga", "Usuario", "Ámbito", "Periodos", "Modo", "Estado",
                       "Reemplazó a", "Filas leídas", "Filas cargadas", "Filas rechazadas", "Advertencias", "Valor total",
                       "Huella SHA-256", "Observación"]
    f = _tabla(ws, car, f, formatos={"Valor total": MONEDA, "Cargue": "0"}, anchos={"Periodos": 40, "Archivo": 46})
    _tabla(ws, cargues.bitacora(ruta).rename(columns=str.capitalize), f, "Bitácora de eventos", anchos={"Detalle": 110})

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

"""Informe ejecutivo PDF para alta gerencia (reportlab)."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..analisis import Resultado
from .recomendaciones import generar

AZUL = colors.HexColor("#1F4E79")


def _p(v) -> str:
    return "$" + f"{float(v or 0):,.0f}".replace(",", ".")


def _n(v) -> str:
    return f"{float(v or 0):,.0f}".replace(",", ".")


def _tabla(filas, anchos=None):
    t = Table(filas, colWidths=anchos, repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), AZUL), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EBF3FB")]),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 1), (-1, -1), "RIGHT")]))
    return t


def escribir(r: Resultado, destino: Path | BytesIO) -> Path | BytesIO:
    doc = SimpleDocTemplate(destino if isinstance(destino, BytesIO) else str(destino), pagesize=landscape(letter),
                            leftMargin=1.2 * cm, rightMargin=1.2 * cm, topMargin=1 * cm, bottomMargin=1 * cm,
                            title="Informe ejecutivo RIPS – E.S.E. Moreno y Clavijo")
    st = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=st["Heading1"], textColor=AZUL, fontSize=15)
    h2 = ParagraphStyle("h2", parent=st["Heading2"], textColor=AZUL, fontSize=11.5)
    tx = ParagraphStyle("tx", parent=st["BodyText"], fontSize=8.5, leading=11)
    el = [Paragraph("E.S.E. Moreno y Clavijo · Informe ejecutivo CAPITA · EVENTO (Nueva EPS)", h1),
          Paragraph(f"Generado {r.generado} · Meses {', '.join(r.alcance['meses'])} · Municipios "
                    f"{', '.join(r.alcance['municipios'])} · Hospitales {', '.join(r.alcance['hospitales'])}", tx),
          Spacer(1, 6)]
    k = r.indicadores["capita"]
    ind = r.indicadores
    el.append(_tabla([["Costo CAPITA total", "Promedio/mes", "Crecimiento reciente", "Pacientes (suma mes)",
                       "Asistencias", "EVENTO total", "Valor en riesgo"],
                      [_p(k["total"]), _p(k["promedio_mes"]), f"{k['crecimiento_pct']:.1f} %", _n(k["pacientes_suma"]),
                       _n(k["asistencias"]), _p(ind["evento_total"]), _p(ind["valor_en_riesgo"])]]))
    det = r.tablas["detalle_mensual"]
    if not det.empty and det["costo"].sum() > 0:
        d = Drawing(700, 150)
        bc = VerticalBarChart()
        bc.x, bc.y, bc.height, bc.width = 40, 20, 110, 640
        bc.data = [[float(v) / 1e6 for v in det["costo"]]]
        bc.categoryAxis.categoryNames = list(det["mes_nombre"])
        bc.bars[0].fillColor = colors.HexColor("#2E75B6")
        bc.valueAxis.labelTextFormat = "%0.0f M"
        d.add(bc)
        el += [Paragraph("Evolución mensual del costo CAPITA (millones)", h2), d]
    fm = r.tablas.get("frecuencias_nt_municipio_mes")
    if fm is not None and not fm.empty:
        el.append(Paragraph("Frecuencias Nota Técnica Nueva EPS vs RIPS (por municipio y mes)", h2))
        el.append(_tabla([["Municipio", "Mes", "Códigos NT", "Con RIPS", "Previsto", "RIPS", "Ejecución", "Estado"]] +
                         [[x.municipio, x.mes, _n(x.codigos_nt), _n(x.codigos_con_rips), _n(x.frecuencia_prevista),
                           _n(x.frecuencia_rips), f"{x.ejecucion_pct:.1f} %" if x.ejecucion_pct == x.ejecucion_pct else "—",
                           x.estado] for x in fm.itertuples()]))
    el.append(Paragraph("Recomendaciones", h2))
    for i, (t, txt) in enumerate(generar(r), start=1):
        el.append(Paragraph(f"<b>{i}. {t}.</b> {txt}", tx))
    h = r.tablas["hallazgos"]
    if not h.empty:
        el.append(Paragraph("Principales hallazgos de auditoría", h2))
        el.append(_tabla([["Tipo", "Sev.", "Periodo", "Código", "Descripción", "Valor en riesgo"]] +
                         [[x.tipo, x.severidad, x.periodo or "", x.codigo or "", Paragraph(str(x.descripcion or x.detalle or "")[:90], tx),
                           _p(x.valor_en_riesgo)] for x in h.head(15).itertuples()],
                         [6.2 * cm, 1.2 * cm, 1.8 * cm, 2.4 * cm, 9.5 * cm, 3 * cm]))
    t20 = r.tablas["top20_evento"]
    if not t20.empty:
        el.append(Paragraph("Top 10 EVENTO por frecuencia", h2))
        el.append(_tabla([["#", "Código", "Servicio", "Frecuencia", "Valor", "Marca catálogo"]] +
                         [[x.n, x.codigo, str(x.descripcion or "")[:55], _n(x.frecuencia), _p(x.valor), x.estado]
                          for x in t20.head(10).itertuples()]))
    el.append(Paragraph("Supuestos", h2))
    for s in r.supuestos + r.advertencias:
        el.append(Paragraph("• " + s, tx))
    doc.build(el)
    return destino

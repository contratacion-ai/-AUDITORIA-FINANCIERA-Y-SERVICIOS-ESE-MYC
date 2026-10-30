"""Exportación a Excel con la misma estructura de hojas y nombres de los dashboards de referencia."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pandas as pd

from ..analisis import Resultado
from .recomendaciones import generar

# (clave de tabla, nombre de hoja ≤31 caracteres, título)
HOJAS = [
    ("detalle_mensual", "Detalle Mensual CAPITA", "Detalle mensual CAPITA – RIPS"),
    ("capita_grupos", "CAPITA Grupos", "Servicios bajo CAPITA · costos por grupo (costo unitario NT × frecuencia RIPS)"),
    ("escenarios", "Escenarios", "3 escenarios CAPITA, giros y PGP (valor mensual)"),
    ("pgp_mensual", "PGP Mensual", "Modelo PGP mes a mes"),
    ("corredor_evento", "Corredor EVENTO", "PGP-EVENTO: run-rate y corredor estadístico"),
    ("top20_evento", "TOP 20 EVENTO", "Top 20 servicios EVENTO · aplicabilidad CAPITA"),
    ("migrables", "Simulación E→C", "Simulación económica EVENTO → CAPITA"),
    ("riesgo", "Riesgo", "Servicios de riesgo (traslados con tope contractual)"),
    ("frecuencias_municipio", "Frecuencias Municipio", "Frecuencias RIPS por municipio/hospital y mes"),
    ("frecuencias_mes", "Frecuencias Mes", "Frecuencias, CUPS, pacientes y valor por mes y modalidad"),
    ("servicios", "Servicios", "Servicios y CUPS: cantidad, valor y participación"),
    ("pareto_valor", "Pareto Valor", "Pareto por valor"),
    ("pareto_frecuencia", "Pareto Frecuencia", "Pareto por frecuencia"),
    ("top_costo_capita", "Top Costo CAPITA", "Servicios CAPITA de mayor costo"),
    ("costo_paciente", "Costo Paciente", "Costo por paciente y mes"),
    ("cruce_nota_tecnica", "NT vs RIPS", "Cruce frecuencias RIPS vs Nota Técnica"),
    ("cruce_tarifario", "EVENTO vs Tarifario", "Cruce facturación EVENTO vs tarifario pactado"),
    ("cruce_facturacion", "RIPS vs Facturación", "Conciliación RIPS vs facturación por ámbito"),
    ("conciliacion_facturas", "Conciliación Facturas", "AF vs servicios RIPS por factura"),
    ("hallazgos", "Hallazgos", "Hallazgos de auditoría"),
    ("validacion", "Validación", "Incidencias de validación por regla"),
    ("trazabilidad_clasificacion", "Trazabilidad", "Trazabilidad de la clasificación CAPITA/EVENTO y grupo"),
    ("sin_costo_nt", "Sin costo NT", "Códigos CAPITA sin costo unitario en la Nota Técnica"),
    ("comparacion_referencia", "Comparación Referencia", "RIPS cargados vs control oficial (libro vigente)"),
    ("conciliacion_informe", "Conciliación Informe", "Libro INFORME DE SERVICIOS vs control oficial y dashboard FINAL (histórico)"),
]
MONEDA = ("valor", "costo", "giro", "glosa", "tarifa", "pgp", "evento", "run_rate", "limite", "dif_valor",
          "acumulado", "promedio_mes", "2026-")


def escribir(r: Resultado, destino: Path | BytesIO) -> Path | BytesIO:
    with pd.ExcelWriter(destino, engine="xlsxwriter") as xw:
        wb = xw.book
        f_tit = wb.add_format({"bold": True, "font_size": 13, "font_color": "#1F4E79"})
        f_cab = wb.add_format({"bold": True, "bg_color": "#1F4E79", "font_color": "white", "text_wrap": True,
                               "valign": "vcenter", "border": 1})
        f_mon = wb.add_format({"num_format": "$ #,##0"})
        f_num = wb.add_format({"num_format": "#,##0.##"})
        f_txt = wb.add_format({"text_wrap": True, "valign": "top"})
        # Resumen
        ws = wb.add_worksheet("Resumen")
        ws.write(0, 0, "E.S.E. Moreno y Clavijo · Auditoría RIPS CAPITA · EVENTO · PGP (Nueva EPS EPS037)", f_tit)
        ws.write(1, 0, f"Generado {r.generado} · Meses: {', '.join(r.alcance['meses'])} · Municipios: "
                       f"{', '.join(r.alcance['municipios'])}")
        k = r.indicadores["capita"]
        filas = [("Costo CAPITA total", k["total"]), ("Costo CAPITA promedio/mes", k["promedio_mes"]),
                 ("Periodo inicial (total)", k["periodo_temprano"]), ("Periodo reciente (total)", k["periodo_reciente"]),
                 ("Crecimiento reciente vs inicial (%)", k["crecimiento_pct"]),
                 ("Pacientes únicos (suma mensual)", k["pacientes_suma"]), ("Asistencias", k["asistencias"]),
                 ("Costo/usuario (prom. mensual)", k["costo_usuario_prom"]),
                 ("Costo/atención (prom. mensual)", k["costo_atencion_prom"]),
                 ("Facturación EVENTO total", r.indicadores["evento_total"]),
                 ("EVENTO promedio/mes", r.indicadores["evento_promedio_mes"]),
                 ("Migrables EVENTO→CAPITA (valor/mes)", r.indicadores["migrables_mes"]),
                 ("Hallazgos de severidad ALTA", r.indicadores["hallazgos_alta"]),
                 ("Valor en riesgo (hallazgos)", r.indicadores["valor_en_riesgo"])]
        ws.write_row(3, 0, ["Indicador", "Valor"], f_cab)
        for i, (n, v) in enumerate(filas, start=4):
            ws.write(i, 0, n)
            ws.write_number(i, 1, float(v or 0), f_num if "%" in n or "Pacientes" in n or "Asist" in n or "Hallazgos" in n else f_mon)
        fila = len(filas) + 6
        ws.write(fila, 0, "Recomendaciones", f_tit)
        recs = generar(r)
        f_neg = wb.add_format({"bold": True, "valign": "top", "text_wrap": True})
        for j, (tit, txt) in enumerate(recs, start=fila + 1):
            ws.write(j, 0, tit, f_neg)
            ws.write(j, 1, txt, f_txt)
        fila += len(recs) + 2
        ws.write(fila, 0, "Supuestos y advertencias", f_tit)
        for j, txt in enumerate(r.supuestos + r.advertencias, start=fila + 1):
            ws.write(j, 0, "•")
            ws.write(j, 1, txt, f_txt)
        ws.set_column(0, 0, 42)
        ws.set_column(1, 1, 110)
        for clave, hoja, titulo in HOJAS:
            df = r.tablas.get(clave)
            if df is None or df.empty:
                df = pd.DataFrame({"sin datos": ["Sin información para este cruce (cargue el maestro correspondiente)"]})
            df = df.copy()
            df.columns = [str(c) for c in df.columns]
            df.to_excel(xw, sheet_name=hoja, startrow=2, index=False)
            sh = xw.sheets[hoja]
            sh.write(0, 0, titulo, f_tit)
            for ci, c in enumerate(df.columns):
                sh.write(2, ci, c, f_cab)
                col = df.iloc[:, ci]
                largo = col.astype(str).str.len().quantile(0.9) if len(df) else 8
                ancho = min(max(len(c), int(largo) if pd.notna(largo) else 8) + 2, 60)
                es_num = pd.api.types.is_numeric_dtype(col)
                fmt = f_mon if es_num and any(m in c.lower() for m in MONEDA) else (f_num if es_num else None)
                sh.set_column(ci, ci, ancho, fmt)
            sh.freeze_panes(3, 1)
            sh.autofilter(2, 0, 2 + len(df), len(df.columns) - 1)
        ws = wb.add_worksheet("Supuestos")
        ws.write(0, 0, "Supuestos explícitos del cálculo", f_tit)
        for i, s in enumerate(r.supuestos, start=2):
            ws.write(i, 0, s, f_txt)
        ws.set_column(0, 0, 140)
    return destino

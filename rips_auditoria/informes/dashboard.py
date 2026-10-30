"""Dashboard HTML con la MISMA estructura visual del dashboard de referencia FINAL (estilos, encabezado,
pestañas, tarjetas KPI, tablas con fila TOTAL/PROMEDIO y gráficos Chart.js), alimentado con los RIPS
de la base consolidada y cruzado con las cifras de Nueva EPS (libro INFORME DE SERVICIOS / Nota Técnica).

Pestañas: Resumen · CAPITA Actual · Cruce Nueva EPS · Frecuencias NT · TOP 20 EVENTO · Riesgo ·
Hallazgos · Recomendaciones. (PGP, escenarios y simulador retirados por decisión del usuario.)

El archivo es autocontenido (Chart.js incluido) y funciona sin internet.
"""
from __future__ import annotations

import html
import json
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

from ..analisis import Resultado
from ..config import DIR_REFERENCIAS, RAIZ
from ..constantes import MESES_AB
from ..referencias import oficial
from ..referencias.dashboards import ARCHIVO_FINAL
from .recomendaciones import generar

CHART_JS = RAIZ / "api" / "static" / "vendor" / "chart.umd.min.js"
EXTRA_CSS = """
.stit{font-size:.95rem;font-weight:900;color:var(--a1);margin:4px 0 12px}
.nota{font-size:.64rem;color:#666;margin-top:6px}
.tw_{max-height:520px;overflow:auto}
.pos{color:var(--v1);font-weight:800}.neg{color:var(--r1);font-weight:800}
"""


@lru_cache(maxsize=1)
def _css_referencia() -> str:
    h = (DIR_REFERENCIAS / ARCHIVO_FINAL).read_text(encoding="utf-8")
    m = re.search(r"<style>(.*?)</style>", h, re.S)
    return (m.group(1) if m else "") + EXTRA_CSS


def _e(v) -> str:
    return html.escape("" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))


def _num(v) -> float | None:
    try:
        f = float(v)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def F(v) -> str:
    f = _num(v)
    return "—" if f is None else "$" + f"{round(f):,}".replace(",", ".")


def FM(v) -> str:
    f = _num(v)
    return "—" if f is None else "$" + f"{f / 1e6:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".") + " M"


def FN(v, d=0) -> str:
    f = _num(v)
    if f is None:
        return "—"
    s = f"{f:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def P(v, d=1) -> str:
    f = _num(v)
    return "—" if f is None else FN(f, d) + "%"


def _mes(m: str) -> str:
    return MESES_AB[int(m[5:7]) - 1] + " " + m[:4] if isinstance(m, str) and len(m) == 7 else _e(m)


def _kpis(items) -> str:
    out = []
    for clase, lb, vl, sb, *tr in items:
        t = f'<div class="tr {tr[1] if len(tr) > 1 else "tu"}">{_e(tr[0])}</div>' if tr and tr[0] else ""
        out.append(f'<div class="kc {clase}"><div class="lb">{_e(lb)}</div><div class="vl">{_e(vl)}</div>'
                   f'<div class="sb">{_e(sb)}</div>{t}</div>')
    return f'<div class="kg">{"".join(out)}</div>'


def _tabla(df: pd.DataFrame | None, cols: list[tuple[str, str, str]], tot: dict | None = None,
           prom: dict | None = None, limite: int | None = None) -> str:
    """cols = [(columna, título, formato)] con formato L (texto izq.), $, M, N, N1, P, X (texto)."""
    if df is None or df.empty:
        return '<p class="nota">Sin información para este cruce (cargue RIPS o el maestro correspondiente).</p>'
    fmt = {"$": F, "M": FM, "N": FN, "N1": lambda v: FN(v, 1), "N2": lambda v: FN(v, 2), "P": P,
           "L": _e, "X": _e, "MES": _mes}
    cab = "".join(f'<th class="{"L" if f == "L" else ""}">{_e(t)}</th>' for _, t, f in cols)
    filas = []
    d = df.head(limite) if limite else df
    for r in d.to_dict("records"):
        celdas = []
        for c, _, f in cols:
            v = r.get(c)
            txt = fmt[f](v)
            clase = "L" if f == "L" else ""
            if c in ("estado", "severidad", "nivel"):
                b = {"SOBREEJECUCION": "bN", "SUBEJECUCION": "bP", "DENTRO": "bS", "SIN_NT": "bME", "ALTA": "bN",
                     "MEDIA": "bP", "BAJA": "bS", "EXTREMO": "bEX", "MUY ALTO": "bMA", "ALTO": "bAL", "MEDIO": "bME",
                     "OK": "bS", "DIFERENTE": "bN", "OFERTADO": "bS", "NO OFERTADO": "bN"}.get(str(v), "bP")
                txt = f'<span class="b_ {b}">{_e(v)}</span>'
            celdas.append(f'<td class="{clase}">{txt}</td>')
        filas.append("<tr>" + "".join(celdas) + "</tr>")
    for clase, extra in (("TOT", tot), ("PROM", prom)):
        if extra:
            filas.append(f'<tr class="{clase}">' + "".join(
                f'<td class="{"L" if f == "L" else ""}">{extra.get(c, "")}</td>' for c, _, f in cols) + "</tr>")
    return f'<div class="tw_"><table><thead><tr>{cab}</tr></thead><tbody>{"".join(filas)}</tbody></table></div>'


def _card(titulo: str, cuerpo: str, nota: str = "") -> str:
    n = f'<p class="nota">{nota}</p>' if nota else ""
    return f'<div class="card"><div class="ct">{titulo}</div>{cuerpo}{n}</div>'


def _canvas(id_: str, alto: str = "ch") -> str:
    return f'<div class="{alto}"><canvas id="{id_}"></canvas></div>'


def generar_html(r: Resultado, chart_js_inline: bool = True) -> str:
    t, ind = r.tablas, r.indicadores
    k = ind["capita"]
    det = t.get("detalle_mensual", pd.DataFrame())
    gr = t.get("capita_grupos", pd.DataFrame())
    meses = list(det["mes"]) if not det.empty else []
    of = {x["mes"]: x for x in oficial.detalle_mensual()}
    ev_libro = dict(zip(oficial.cargar()["meses"], oficial.cargar()["evento_mensual"]))
    per = f"{_mes(meses[0])} – {_mes(meses[-1])}" if meses else "sin datos"
    municipios = ", ".join(r.alcance.get("municipios", [])) or "—"
    hosp = ", ".join(r.alcance.get("hospitales", [])) or "—"
    evm = t.get("evento_mensual", pd.DataFrame())
    ej = ind.get("ejecucion_nt_pct")

    # ------------------------------------------------------------------ datos para gráficos
    datos = {
        "meses": [_mes(m) for m in meses],
        "costo": [float(x) for x in det.get("costo", [])], "pac": [float(x) for x in det.get("pacientes", [])],
        "costo_libro": [float(of[m]["costo"]) if m in of else None for m in meses],
        "cups": [float(x) for x in det.get("cups", [])],
        "cups_libro": [float(of[m]["cups"]) if m in of else None for m in meses],
        "grupos": [x for x in gr.get("grupo_nombre", [])], "grupos_pct": [float(x) for x in gr.get("participacion_pct", [])],
        "ev_mes": [_mes(m) for m in evm.get("mes", [])], "ev": [float(x) for x in evm.get("valor", [])],
        "ev_libro": [float(ev_libro[m]) if m in ev_libro else None for m in evm.get("mes", [])],
    }
    fm = t.get("frecuencias_nt_municipio_mes", pd.DataFrame())
    if not fm.empty:
        piv = fm.pivot_table(index="mes", columns="municipio", values="ejecucion_pct", aggfunc="first")
        datos["nt_meses"] = [_mes(m) for m in piv.index]
        datos["nt_series"] = {m: [None if pd.isna(v) else float(v) for v in piv[m]] for m in piv.columns}
    t20 = t.get("top20_evento", pd.DataFrame())
    tv = t20.sort_values("valor", ascending=False).head(10) if not t20.empty else t20
    datos["t10v_lb"] = [str(x)[:24] for x in tv.get("descripcion", tv.get("codigo", []))]
    datos["t10v"] = [float(x) for x in tv.get("valor", [])]
    tf = t20.head(10) if not t20.empty else t20
    datos["t10f_lb"] = [str(x)[:24] for x in tf.get("descripcion", tf.get("codigo", []))]
    datos["t10f"] = [float(x) for x in tf.get("frecuencia", [])]
    rie = t.get("riesgo", pd.DataFrame())
    datos["rie_lb"] = [str(x)[:22] for x in rie.get("servicio", [])]
    datos["rie"] = [float(x) for x in rie.get("valor_mes", [])]

    # ------------------------------------------------------------------ Resumen
    lectura = (f"<strong>LECTURA GERENCIAL · {per}:</strong> costo CAPITA calculado desde RIPS (costo unitario NT × "
               f"frecuencia RIPS) de <strong>{FM(k['total'])}</strong> (prom. {FM(k['promedio_mes'])}/mes); "
               f"facturación EVENTO Nueva EPS <strong>{FM(ind.get('evento_total'))}</strong>. "
               + (f"Los RIPS de cápita ejecutan el <strong>{P(ej)}</strong> de la frecuencia mensual prevista por la "
                  f"Nota Técnica Nueva EPS en los municipios cargados. " if ej is not None else "")
               + f"Municipios: {_e(municipios)} · Sedes: {_e(hosp)}.")
    res = (f'<div class="stit">📊 Resumen Ejecutivo · E.S.E. Moreno y Clavijo · {per}</div>'
           f'<div class="al ai"><span class="ai_">ℹ️</span><div>{lectura}</div></div>'
           + _kpis([
               ("", "Costo Total CAPITA", FM(k["total"]), per, f"{P(k['crecimiento_pct'])}" if k["periodo_temprano"] else ""),
               ("ve", "Costo Promedio/Mes", FM(k["promedio_mes"]), f"{k['n_meses']} meses"),
               ("na", "Pacientes Únicos", FN(k["pacientes_suma"]), "suma mensual"),
               ("", "RIPS CUPS · CUMS", f"{FN(det['cups'].sum()) if not det.empty else '—'}",
                f"CUMS {FN(det['cums'].sum()) if not det.empty else '—'}"),
               ("ve", "Costo/Usuario", F(k["costo_usuario_prom"]), "prom. mensual"),
               ("or", "Costo/Atención", F(k["costo_atencion_prom"]), "prom. mensual"),
               ("ro", "Facturación EVENTO", FM(ind.get("evento_total")), "Nueva EPS (RIPS)"),
               ("mo", "Ejecución Frecuencias NT", P(ej) if ej is not None else "—", "RIPS / Nota Técnica Nueva EPS"),
               ("ro", "Hallazgos ALTA", FN(ind.get("hallazgos_alta")), FM(ind.get("valor_en_riesgo")) + " en riesgo"),
           ])
           + '<div class="g2">' + _card("📅 Evolución Mensual CAPITA – Costo y Pacientes", _canvas("cEvo"))
           + _card("🥧 Distribución CAPITA por Grupo", _canvas("cGru")) + "</div>")
    for a in r.advertencias:
        res += f'<div class="al an"><span class="ai_">⚠️</span><div>{_e(a)}</div></div>'

    # ------------------------------------------------------------------ CAPITA
    cols_g = [("grupo_nombre", "Grupo", "L"), ("unidades_rips", "RIPS", "N")] + \
             [(m, _mes(m).split()[0].upper(), "$") for m in meses] + \
             [("acumulado", "Acumulado", "$"), ("promedio_mes", "Prom/Mes", "$"), ("participacion_pct", "%", "P")]
    tot_g = {"grupo_nombre": "TOTAL", "unidades_rips": FN(gr["unidades_rips"].sum()) if not gr.empty else "",
             **{m: F(gr[m].sum()) for m in meses if m in gr}, "acumulado": F(k["total"]),
             "promedio_mes": F(k["promedio_mes"]), "participacion_pct": "100%"}
    cols_d = [("mes", "Mes", "MES"), ("cups", "CUPS", "N"), ("cums", "CUMS", "N"), ("total", "Total", "N"),
              ("pacientes", "Pac.Únicos", "N"), ("asistencias", "Asistencias", "N"), ("costo", "Costo Mes", "$"),
              ("costo_usuario", "Costo/Usu.", "$"), ("costo_atencion", "Costo/Atenc.", "$")]
    sumd = (lambda c: FN(det[c].sum())) if not det.empty else (lambda c: "")
    tot_d = {"mes": "TOTAL", **{c: sumd(c) for c in ("cups", "cums", "total", "pacientes", "asistencias")},
             "costo": F(k["total"]), "costo_usuario": "—", "costo_atencion": "—"}
    n = max(k["n_meses"], 1)
    prom_d = {"mes": "PROMEDIO", **{c: FN(det[c].sum() / n) if not det.empty else "" for c in
                                    ("cups", "cums", "total", "pacientes", "asistencias")},
              "costo": F(k["promedio_mes"]), "costo_usuario": F(k["costo_usuario_prom"]),
              "costo_atencion": F(k["costo_atencion_prom"])}
    cap = (f'<div class="stit">🏷️ Servicios Bajo CAPITA · Costos por Grupo · {per}</div>'
           + _kpis([("", "Total CAPITA", FM(k["total"]), f"{k['n_meses']} meses"),
                    ("ve", "Periodo inicial", FM(k["periodo_temprano"]), f"prom. {FM(k['promedio_temprano'])}"),
                    ("na", "Periodo reciente", FM(k["periodo_reciente"]), f"prom. {FM(k['promedio_reciente'])}",
                     P(k["crecimiento_pct"])),
                    ("", "Promedio/Mes", FM(k["promedio_mes"]), "costo NT × RIPS"),
                    ("mo", "RIPS CAPITA", FN(det["total"].sum()) if not det.empty else "—", "CUPS + CUMS")])
           + _card("📋 Costos por Grupo – costo unitario NT × frecuencia RIPS", _tabla(gr, cols_g, tot_g))
           + '<div class="g2">' + _card("📊 Peso % por Grupo", _canvas("cCG", "cht"))
           + _card("📅 Detalle Mensual CAPITA – RIPS", _tabla(det, cols_d, tot_d, prom_d)) + "</div>")

    # ------------------------------------------------------------------ Cruce Nueva EPS
    comp = t.get("comparacion_referencia", pd.DataFrame())
    if not comp.empty:
        comp = comp[comp["periodo"].isin(meses)]
    cols_c = [("periodo", "Mes", "MES"), ("indicador", "Indicador", "L"), ("control_oficial", "Nueva EPS / libro", "N"),
              ("calculado", "RIPS cargados", "N"), ("diferencia", "Diferencia", "N"), ("diferencia_pct", "Dif. %", "P")]
    conc = t.get("conciliacion_informe", pd.DataFrame())
    cols_k = [("indicador", "Indicador", "L"), ("informe", "Libro", "N2"), ("control_oficial", "Control oficial", "N2"),
              ("diferencia_vs_oficial", "Diferencia", "N2"), ("estado", "Estado", "X"), ("explicacion", "Explicación", "L")]
    cp = t.get("capita_pactada", pd.DataFrame())
    cols_cp = [("municipio", "Municipio", "L"), ("afiliados", "Afiliados", "N"), ("valor_pactado_mes", "Cápita pactada/mes", "$"),
               ("mes", "Mes", "MES"), ("costo_rips", "Costo NT × RIPS", "$"), ("uso_pct", "Uso %", "P"),
               ("pacientes", "Pacientes", "N"), ("cobertura_pacientes_pct", "Pacientes/afiliados", "P")]
    cru_cp = _card("📑 Cápita pactada (Actas 4131 RS y RC) vs costo de los RIPS por municipio", _tabla(cp, cols_cp),
                   "Valor mensual pactado = afiliados × valor costo usuario (Arauquita, Fortul, Tame $40.000 RS; Cravo Norte "
                   "y Puerto Rondón $45.000; contributivo $45.000). Actas precontractuales sujetas a aprobación del CNC.")
    cru = (f'<div class="stit">🔁 Cruce con Nueva EPS · RIPS cargados vs cifras Nueva EPS (libro INFORME DE SERVICIOS)</div>'
           '<div class="al ai"><span class="ai_">ℹ️</span><div>Compara, mes a mes, lo que traen los RIPS cargados en la base '
           'con las cifras de Nueva EPS del libro vigente (costo CAPITA, CUPS, CUMS, pacientes y asistencias). Una diferencia '
           'negativa indica RIPS aún no cargados o no reportados; positiva, servicios adicionales frente al libro.</div></div>'
           + '<div class="g2">' + _card("💰 Costo CAPITA mensual · Nueva EPS vs RIPS", _canvas("cCruCosto"))
           + _card("🧾 CUPS mensuales · Nueva EPS vs RIPS", _canvas("cCruCups")) + "</div>"
           + cru_cp + _card("📋 Comparación mensual por indicador", _tabla(comp, cols_c))
           + _card("🧮 Conciliación del libro con el control oficial", _tabla(conc, cols_k)))

    # ------------------------------------------------------------------ Frecuencias NT
    cols_f = [("municipio", "Municipio", "L"), ("mes", "Mes", "MES"), ("codigos_nt", "Códigos NT", "N"),
              ("codigos_con_rips", "Con RIPS", "N"), ("frecuencia_prevista", "Previsto NT", "N1"),
              ("frecuencia_rips", "RIPS", "N"), ("ejecucion_pct", "Ejecución", "P"), ("valor_exceso", "Valor exceso", "$"),
              ("estado", "Estado", "X")]
    fg = t.get("frecuencias_nt_grupo", pd.DataFrame())
    cols_fg = [("municipio", "Municipio", "L"), ("grupo_nombre", "Grupo", "L"), ("meses", "Meses", "N"),
               ("frecuencia_prevista", "Previsto NT", "N1"), ("frecuencia_rips", "RIPS", "N"),
               ("ejecucion_pct", "Ejecución", "P"), ("valor_exceso", "Valor exceso", "$"), ("estado", "Estado", "X")]
    fc = t.get("frecuencias_nt_codigo", pd.DataFrame())
    cols_fc = [("municipio", "Municipio", "L"), ("codigo", "Código", "L"), ("descripcion", "Servicio", "L"),
               ("meses", "Meses", "N"), ("frecuencia_prevista_mes", "NT/mes", "N2"), ("frecuencia_prevista", "Previsto", "N1"),
               ("frecuencia_rips", "RIPS", "N"), ("desviacion", "Desviación", "N1"), ("ejecucion_pct", "Ejecución", "P"),
               ("valor_exceso", "Valor exceso", "$"), ("estado", "Estado", "X")]
    sobre = fc[fc["estado"] == "SOBREEJECUCION"] if not fc.empty else fc
    sub = fc[fc["estado"] == "SUBEJECUCION"].sort_values("desviacion") if not fc.empty else fc
    fre = ('<div class="stit">📊 Frecuencias Nota Técnica Nueva EPS vs RIPS</div>'
           '<div class="al ai"><span class="ai_">ℹ️</span><div>Frecuencia MENSUAL prevista por la Nota Técnica CAPITA de Nueva '
           'EPS para cada municipio frente a la frecuencia registrada en los RIPS de cápita del mismo municipio y mes. '
           'Ejecución = RIPS / previsto. Tolerancia ±10 %.</div></div>'
           + _card("📈 Ejecución mensual por municipio (%)", _canvas("cNT", "cht"))
           + _card("📋 Ejecución por municipio y mes", _tabla(fm, cols_f))
           + _card("🧩 Ejecución acumulada por grupo", _tabla(fg, cols_fg))
           + '<div class="g2">' + _card("⬆️ Servicios por encima de la Nota Técnica", _tabla(sobre, cols_fc, limite=40))
           + _card("⬇️ Servicios por debajo de la Nota Técnica", _tabla(sub, cols_fc, limite=40)) + "</div>")

    # ------------------------------------------------------------------ EVENTO
    cols_t = [("n", "N°", "N"), ("codigo", "Código", "L"), ("descripcion", "Servicio", "L"), ("grupo_nombre", "Grupo", "L"),
              ("valor", "Val.Total", "$"), ("frecuencia", "Frec.", "N"), ("valor_unitario", "Val.Unit.", "$"),
              ("pacientes", "Pacientes", "N"), ("estado", "Marca catálogo", "X")]
    cols_e = [("mes", "Mes", "MES"), ("facturas", "Facturas", "N"), ("servicios", "Servicios", "N"),
              ("pacientes", "Pacientes", "N"), ("valor", "Valor RIPS", "$")]
    evm2 = evm.copy()
    if not evm2.empty:
        evm2["libro"] = evm2["mes"].map(ev_libro)
        cols_e.append(("libro", "Facturación Nueva EPS (libro)", "$"))
    eve = (f'<div class="stit">⚡ Top 20 Servicios EVENTO · Nueva EPS · {per}</div>'
           + _kpis([("ro", "Fact. Total EVENTO", FM(ind.get("evento_total")), "RIPS cargados"),
                    ("na", "EVENTO Prom/Mes", FM(ind.get("evento_promedio_mes")), "promedio"),
                    ("or", "Traslados", P((ind.get("participacion_traslados") or 0) * 100), "del EVENTO")])
           + '<div class="g2">' + _card("📊 Top 10 por Valor Facturado", _canvas("cT10V", "cht"))
           + _card("📊 Top 10 por Frecuencia", _canvas("cT10F", "cht")) + "</div>"
           + _card("📋 Top 20 – Servicios EVENTO", _tabla(t20, cols_t))
           + '<div class="g2">' + _card("📅 Facturación EVENTO mensual · RIPS vs Nueva EPS", _canvas("cEvM"))
           + _card("📋 EVENTO mensual", _tabla(evm2, cols_e)) + "</div>")

    # ------------------------------------------------------------------ Riesgo, hallazgos, recomendaciones
    cols_r = [("codigo", "Código", "L"), ("servicio", "Servicio", "L"), ("valor_mes", "Costo/Mes", "$"),
              ("frecuencia_mes", "Frec./Mes", "N1"), ("valor_unitario", "Val.Unit.", "$"), ("nivel", "Riesgo", "X"),
              ("estado_catalogo", "Marca", "X")]
    rie_h = ('<div class="stit">⚠️ Servicios de Riesgo · Traslados y alto costo (EVENTO)</div>'
             + '<div class="g14">' + _card("📊 Costo mensual", _canvas("cRie", "cht"))
             + _card("📋 Matriz de riesgo", _tabla(rie, cols_r)) + "</div>")
    h = t.get("hallazgos", pd.DataFrame())
    cols_h = [("severidad", "Sev.", "X"), ("tipo", "Tipo", "L"), ("periodo", "Mes", "MES"), ("municipio", "Municipio", "L"),
              ("codigo", "Código", "L"), ("descripcion", "Descripción", "L"), ("cantidad", "Cant.", "N1"),
              ("valor_en_riesgo", "Valor en riesgo", "$"), ("accion_recomendada", "Acción", "L")]
    hal = ('<div class="stit">🔎 Hallazgos de Auditoría</div>'
           + _card(f"📋 {len(h)} hallazgos (se muestran 300)", _tabla(h, cols_h, limite=300)))
    recs = generar(r)
    rec = ('<div class="stit">✅ Recomendaciones</div>'
           + _card("🎯 Acciones sugeridas", '<ul class="rl">' + "".join(
               f'<li><div class="rn">{i}</div><div class="rt"><strong>{_e(a)}</strong>{_e(b)}</div></li>'
               for i, (a, b) in enumerate(recs, start=1)) + "</ul>")
           + _card("📌 Supuestos", '<ul class="rl">' + "".join(f"<li><div class=\"rt\">{_e(s)}</div></li>"
                                                              for s in r.supuestos) + "</ul>"))

    tabs = [("res", "📈 Resumen", res), ("cap", "🏷️ CAPITA Actual", cap), ("cru", "🔁 Cruce Nueva EPS", cru),
            ("fre", "📊 Frecuencias NT", fre), ("ev", "⚡ TOP 20 EVENTO", eve), ("rie", "⚠️ Riesgo", rie_h),
            ("hal", "🔎 Hallazgos", hal), ("rec", "✅ Recomendaciones", rec)]
    nav = "".join(f'<button class="tb{" on" if i == 0 else ""}" onclick="T(\'{k_}\',this)">{_e(lb)}</button>'
                  for i, (k_, lb, _) in enumerate(tabs))
    secs = "".join(f'<div id="s-{k_}" class="sec{" on" if i == 0 else ""}"><div class="wrap">{c}</div></div>'
                   for i, (k_, _, c) in enumerate(tabs))
    chart = (f"<script>{CHART_JS.read_text(encoding='utf-8')}</script>" if chart_js_inline and CHART_JS.exists()
             else '<script src="/static/vendor/chart.umd.min.js"></script>')
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard CAPITA · EVENTO · E.S.E. Moreno y Clavijo</title><style>{_css_referencia()}</style></head><body>
<div class="hdr"><div class="hdr-l"><h1>🏥 ESE MORENO Y CLAVIJO · CAPITA · EVENTO · Dashboard RIPS {_e(per)}</h1>
<p>NIT 900034131 · Nueva EPS (EPS037) · {_e(municipios)} · Generado {_e(r.generado)} desde la base RIPS consolidada</p></div>
<div class="hdr-r"><span class="badge b-na">📊 RIPS CARGADOS</span><span class="badge b-ve">🔁 CRUCE NUEVA EPS</span></div></div>
<div class="nav">{nav}</div>{secs}
{chart}
<script>
const D={json.dumps(datos, ensure_ascii=False)};
const nf=(n,d=0)=>n==null?'—':Number(n).toLocaleString('es-CO',{{minimumFractionDigits:d,maximumFractionDigits:d}});
const FM=n=>'$'+nf(n/1e6,1)+' M';
function T(id,btn){{document.querySelectorAll('.sec').forEach(s=>s.classList.remove('on'));
document.querySelectorAll('.tb').forEach(b=>b.classList.remove('on'));document.getElementById('s-'+id).classList.add('on');
btn.classList.add('on');window.dispatchEvent(new Event('resize'));}}
const GCOL=['#1F4E79','#C00000','#2E75B6','#70AD47','#375623','#7030A0','#F4B942','#FF6B35','#BDD7EE','#BFBFBF','#8E6E53','#4A90C4'];
const fs={{font:{{size:9}}}};const yM={{ticks:{{callback:v=>FM(v),font:{{size:9}}}}}};
function mk(id,cfg){{const el=document.getElementById(id);if(!el||typeof Chart==='undefined')return;new Chart(el,cfg);}}
const O=(ex)=>Object.assign({{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{labels:{{font:{{size:10}}}}}}}}}},ex||{{}});
mk('cEvo',{{type:'bar',data:{{labels:D.meses,datasets:[{{label:'Costo CAPITA (RIPS)',data:D.costo,backgroundColor:'rgba(46,117,182,.75)',yAxisID:'y',borderRadius:4}},
{{label:'Pac. únicos',data:D.pac,type:'line',borderColor:'#E8A020',backgroundColor:'rgba(232,160,32,.18)',yAxisID:'y1',tension:.4,pointRadius:4}}]}},
options:O({{scales:{{y:yM,y1:{{position:'right',grid:{{drawOnChartArea:false}},ticks:fs}}}}}})}});
mk('cGru',{{type:'doughnut',data:{{labels:D.grupos,datasets:[{{data:D.grupos_pct,backgroundColor:GCOL,borderWidth:2,borderColor:'#fff'}}]}},
options:O({{plugins:{{legend:{{position:'right',labels:{{font:{{size:9}},padding:5}}}}}}}})}});
mk('cCG',{{type:'bar',data:{{labels:D.grupos,datasets:[{{label:'% costo',data:D.grupos_pct,backgroundColor:GCOL,borderRadius:4}}]}},
options:O({{indexAxis:'y',plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{callback:v=>v+'%',font:{{size:9}}}}}},y:{{ticks:fs}}}}}})}});
mk('cCruCosto',{{type:'bar',data:{{labels:D.meses,datasets:[{{label:'Nueva EPS (libro)',data:D.costo_libro,backgroundColor:'rgba(112,48,160,.65)',borderRadius:3}},
{{label:'RIPS cargados',data:D.costo,backgroundColor:'rgba(46,117,182,.75)',borderRadius:3}}]}},options:O({{scales:{{y:yM}}}})}});
mk('cCruCups',{{type:'bar',data:{{labels:D.meses,datasets:[{{label:'Nueva EPS (libro)',data:D.cups_libro,backgroundColor:'rgba(112,48,160,.65)',borderRadius:3}},
{{label:'RIPS cargados',data:D.cups,backgroundColor:'rgba(112,173,71,.8)',borderRadius:3}}]}},options:O()}});
if(D.nt_series){{mk('cNT',{{type:'line',data:{{labels:D.nt_meses,datasets:Object.entries(D.nt_series).map(([m,v],i)=>(
{{label:m,data:v,borderColor:GCOL[i%GCOL.length],backgroundColor:'transparent',tension:.3,pointRadius:4}}))
.concat([{{label:'100 % NT',data:D.nt_meses.map(()=>100),borderColor:'#C00000',borderDash:[6,4],pointRadius:0}}])}},
options:O({{scales:{{y:{{ticks:{{callback:v=>v+'%',font:{{size:9}}}}}}}}}})}});}}
mk('cT10V',{{type:'bar',data:{{labels:D.t10v_lb,datasets:[{{label:'Val.Fact.',data:D.t10v,backgroundColor:GCOL,borderRadius:4}}]}},
options:O({{indexAxis:'y',plugins:{{legend:{{display:false}}}},scales:{{x:yM,y:{{ticks:fs}}}}}})}});
mk('cT10F',{{type:'bar',data:{{labels:D.t10f_lb,datasets:[{{label:'Frecuencia',data:D.t10f,backgroundColor:GCOL,borderRadius:4}}]}},
options:O({{indexAxis:'y',plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:fs}},y:{{ticks:fs}}}}}})}});
mk('cEvM',{{type:'bar',data:{{labels:D.ev_mes,datasets:[{{label:'RIPS cargados',data:D.ev,backgroundColor:'rgba(192,0,0,.6)',borderRadius:3}},
{{label:'Nueva EPS (libro)',data:D.ev_libro,type:'line',borderColor:'#7030A0',tension:.3,pointRadius:4}}]}},options:O({{scales:{{y:yM}}}})}});
mk('cRie',{{type:'bar',data:{{labels:D.rie_lb,datasets:[{{label:'Costo/Mes',data:D.rie,backgroundColor:['#C00000','#C00000','#FF6B35','#FF6B35','#F4B942','#F4B942','#BDD7EE'],borderRadius:4}}]}},
options:O({{plugins:{{legend:{{display:false}}}},scales:{{y:yM,x:{{ticks:fs}}}}}})}});
</script></body></html>"""


def escribir(r: Resultado, destino: Path) -> Path:
    destino.write_text(generar_html(r), encoding="utf-8")
    return destino

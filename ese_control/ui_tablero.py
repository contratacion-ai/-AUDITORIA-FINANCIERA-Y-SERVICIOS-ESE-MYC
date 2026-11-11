"""Tablero de control y página de alertas."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import analisis, db, esquema
from .config import COLOR_ITEM, COLOR_SERIES, COLOR_SEVERIDAD, COMBUSTIBLE, ICONO_SEVERIDAD, ITEMS, MANTENIMIENTO, SERVICIOS
from .extraccion.comunes import nombre_periodo, periodo_anterior
from .ui_comun import (AZUL, ESCALA_AZUL, REJILLA, TINTA_SUAVE, cargar, corto, estilo, grafico, margen_etiquetas, numero, pesos, tabla,
                       version_datos)

HOVER_PESOS = "%{y:$,.0f}"


def _filtros(u, df: pd.DataFrame):
    """Una sola fila de filtros encima de los gráficos."""
    pers = analisis.periodos(df)
    c1, c2, c3 = st.columns([2.2, 2, 2])
    with c1:
        if len(pers) > 1:
            ini = pers[max(0, len(pers) - 12)]
            desde, hasta = st.select_slider("Periodos", options=pers, value=(ini, pers[-1]), format_func=nombre_periodo)
        else:
            desde = hasta = pers[0]
            st.caption(f"Periodo disponible: {nombre_periodo(desde)}")
    with c2:
        hospitales = sorted(df["hospital"].unique())
        if u.es_central:
            hosp = st.selectbox("Hospital o centro", ["Toda la ESE"] + hospitales)
            hosp = None if hosp == "Toda la ESE" else hosp
        else:
            hosp = u.hospital
            st.selectbox("Hospital o centro", [hosp], disabled=True)
    with c3:
        sedes = sorted(df.loc[df["hospital"] == hosp, "sede"].unique()) if hosp else []
        sede = st.selectbox("Sede", ["Todas"] + sedes, disabled=not hosp)
        sede = None if sede == "Todas" else sede
    f = df[(df["periodo"] >= desde) & (df["periodo"] <= hasta)]
    if hosp:
        f = f[f["hospital"] == hosp]
    if sede:
        f = f[f["sede"] == sede]
    return f, desde, hasta, hosp, sede


def _barras_mes(df, por: str, colores: dict | list, titulo: str, valor: str = "valor", moneda: bool = True, clave=None):
    pers = analisis.periodos(df)
    g = df.groupby(["periodo", por])[valor].sum().unstack(fill_value=0).reindex(pers, fill_value=0)
    orden = list(colores) if isinstance(colores, dict) else list(g.columns)
    fig = go.Figure()
    for i, serie in enumerate([s for s in orden if s in g.columns]):
        color = colores[serie] if isinstance(colores, dict) else colores[i % len(colores)]
        fig.add_bar(x=[nombre_periodo(p) for p in g.index], y=g[serie], name=ITEMS.get(serie, serie),
                    marker=dict(color=color, line=dict(color="white", width=1.5)),
                    hovertemplate=(HOVER_PESOS if moneda else "%{y:,.0f}") + "<extra>" + ITEMS.get(serie, serie) + "</extra>")
    fig.update_layout(barmode="stack")
    grafico(estilo(fig, titulo, moneda_y=moneda, leyenda=len(g.columns) > 1), clave)


def _barras_h(serie: pd.Series, titulo: str, moneda: bool = True, n: int = 12, clave=None):
    s = serie.sort_values(ascending=False).head(n)[::-1]
    fig = go.Figure(go.Bar(x=s.values, y=[corto(i) for i in s.index], orientation="h", marker_color=AZUL,
                           text=[pesos(v, True) if moneda else numero(v) for v in s.values], textposition="outside",
                           cliponaxis=False, customdata=[str(i) for i in s.index],
                           hovertemplate="%{customdata}<br>" + ("%{x:$,.0f}" if moneda else "%{x:,.0f}") + "<extra></extra>"))
    fig = estilo(fig, titulo, alto=max(240, 34 * len(s) + 90), leyenda=False)
    fig.update_layout(hovermode="closest", margin=dict(r=80, b=16))
    margen_etiquetas(fig, [corto(i) for i in s.index])
    fig.update_xaxes(type="linear", showgrid=True, gridcolor=REJILLA, tickprefix="$" if moneda else "", tickformat="~s",
                     showticklabels=False)
    fig.update_yaxes(type="category", showgrid=False, tickprefix="", tickformat="")
    grafico(fig, clave)


def _linea(x, series: dict, titulo: str, moneda: bool = False, sufijo: str = "", clave=None):
    fig = go.Figure()
    for i, (nombre, y) in enumerate(series.items()):
        fig.add_scatter(x=x, y=y, name=nombre, mode="lines+markers", line=dict(color=COLOR_SERIES[i], width=2),
                        marker=dict(size=8, line=dict(color="white", width=2)),
                        hovertemplate=(HOVER_PESOS if moneda else "%{y:,.1f}" + sufijo) + "<extra>" + nombre + "</extra>")
    grafico(estilo(fig, titulo, moneda_y=moneda, leyenda=len(series) > 1), clave)


def pagina_tablero(u):
    st.title("Tablero de control")
    df, despl, veh = cargar(version_datos(), u.ambito)
    if df.empty:
        st.info("Aún no hay datos cargados. Vaya a **Cargar datos** para subir el primer archivo.")
        return
    f, desde, hasta, hosp, sede = _filtros(u, df)
    if f.empty:
        st.warning("No hay datos para el filtro seleccionado.")
        return
    hoy = dt.date.today()
    control = analisis.periodo_de_control(f, hoy)
    previo = periodo_anterior(control)
    d_h = despl[despl["hospital"] == hosp] if hosp else despl
    al = analisis.alertas(f, d_h, veh, db.parametros(), control)

    act, ant = f[f["periodo"] == control]["valor"].sum(), f[f["periodo"] == previo]["valor"].sum()
    mora = analisis.mora_servicios(f[f["periodo"] == control])["saldo"].sum()
    k = st.columns(4)
    k[0].metric(f"Gasto de {nombre_periodo(control)}", pesos(act, True),
                f"{(act - ant) / ant * 100:+.1f}% vs {nombre_periodo(previo)}".replace(".", ",") if ant else None,
                delta_color="inverse", help="Último mes cerrado con datos: es el periodo que se controla.")
    k[1].metric(f"Acumulado {nombre_periodo(desde)} – {nombre_periodo(hasta)}", pesos(f["valor"].sum(), True))
    k[2].metric("Alertas del periodo", f"{len(al)}", f"{(al['severidad'] == 'Alta').sum()} de severidad alta",
                delta_color="off")
    k[3].metric("Saldo anterior en servicios", pesos(mora, True),
                help="Total a pagar de las facturas menos lo facturado en el mes: saldos de periodos anteriores.")
    if control != hasta:
        st.caption(f"El mes en curso ({nombre_periodo(hasta)}) aparece en los gráficos pero está incompleto: "
                   f"los indicadores y las alertas se calculan sobre {nombre_periodo(control)}.")

    t = st.tabs(["Resumen", "Servicios públicos", "Combustible", "Mantenimientos", "Gráfico de control", "Datos"])
    with t[0]:
        _barras_mes(f, "item", COLOR_ITEM, "Gasto mensual por ítem", clave="r1")
        c1, c2 = st.columns(2)
        with c1:
            nivel = "sede" if hosp else "hospital"
            _barras_h(f.groupby(nivel)["valor"].sum(), f"Gasto por {nivel} en el rango seleccionado", clave="r2")
        with c2:
            _mapa_calor(f, "sede" if hosp else "hospital")
    with t[1]:
        _tab_servicios(f[f["item"] == SERVICIOS], control)
    with t[2]:
        _tab_combustible(f, d_h, veh, control)
    with t[3]:
        _tab_mantenimiento(f[f["item"] == MANTENIMIENTO])
    with t[4]:
        _tab_control(f)
    with t[5]:
        vista = f[["item_nombre", "periodo", "fecha", "hospital", "sede", "categoria", "concepto", "referencia",
                   "cantidad", "unidad", "valor", "total_a_pagar", "proveedor", "cargue_id"]]
        tabla(vista, moneda=("valor", "total_a_pagar"), height=460)
        st.download_button("Descargar estos datos (CSV)", vista.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                           "datos_control.csv", "text/csv")


def _mapa_calor(f: pd.DataFrame, nivel: str):
    p = f.pivot_table(index=nivel, columns="periodo", values="valor", aggfunc="sum", fill_value=0)
    p = p.loc[p.sum(axis=1).sort_values(ascending=False).head(14).index[::-1]]
    fig = go.Figure(go.Heatmap(z=p.values, x=[nombre_periodo(c) for c in p.columns], y=[corto(i) for i in p.index],
                               customdata=[[str(i)] * p.shape[1] for i in p.index], colorscale=[[i / 5, c] for i, c in enumerate(ESCALA_AZUL)], xgap=2, ygap=2,
                               colorbar=dict(tickprefix="$", tickformat="~s", thickness=10, outlinewidth=0),
                               hovertemplate="%{customdata}<br>%{x}: %{z:$,.0f}<extra></extra>"))
    fig = estilo(fig, f"Gasto por {nivel} y mes", alto=max(240, 34 * len(p) + 90), leyenda=False)
    fig.update_layout(hovermode="closest")
    margen_etiquetas(fig, [corto(i) for i in p.index])
    fig.update_yaxes(type="category", showgrid=False, tickprefix="", tickformat="")
    grafico(fig, "r3")


def _tab_servicios(s: pd.DataFrame, control: str):
    if s.empty:
        st.info("Sin datos de servicios públicos en el filtro.")
        return
    pers = analisis.periodos(s)
    x = [nombre_periodo(p) for p in pers]
    colores = {esquema.CAT_ENERGIA: COLOR_SERIES[0], esquema.CAT_AGUA: COLOR_SERIES[1], esquema.CAT_OTROS_SP: COLOR_SERIES[2]}
    _barras_mes(s, "categoria", colores, "Valor facturado por servicio", clave="s1")
    c1, c2 = st.columns(2)
    en = s[s["concepto"] == "Energía eléctrica"].groupby("periodo").agg(v=("valor", "sum"), q=("cantidad", "sum")).reindex(pers)
    ag = s[s["concepto"] == "Acueducto"].groupby("periodo").agg(v=("valor", "sum"), q=("cantidad", "sum")).reindex(pers)
    with c1:
        if en["q"].sum() > 0:
            _linea(x, {"kWh": en["q"]}, "Consumo de energía (kWh)", sufijo=" kWh", clave="s2")
            _linea(x, {"$/kWh": (en["v"] / en["q"].replace(0, np.nan))}, "Costo medio de la energía ($ por kWh)",
                   moneda=True, clave="s3")
    with c2:
        if ag["q"].sum() > 0:
            _linea(x, {"m3": ag["q"]}, "Consumo de acueducto (m3)", sufijo=" m3", clave="s4")
            _linea(x, {"$/m3": (ag["v"] / ag["q"].replace(0, np.nan))}, "Costo medio del acueducto ($ por m3)",
                   moneda=True, clave="s5")
    m = analisis.mora_servicios(s[s["periodo"] == control])
    m = m[m["saldo"] > 0].sort_values("saldo", ascending=False)
    if len(m):
        st.subheader(f"Saldos de periodos anteriores en {nombre_periodo(control)}")
        st.caption("Total a pagar de la factura menos lo facturado en el mes. Un saldo alto indica mora acumulada "
                   "o pagos sin aplicar: conviene conciliarlo con la empresa prestadora.")
        tabla(m[["hospital", "sede", "categoria", "valor", "total_a_pagar", "saldo"]].rename(columns={
            "hospital": "Hospital", "sede": "Sede", "categoria": "Servicio", "valor": "Facturado en el mes",
            "total_a_pagar": "Total a pagar", "saldo": "Saldo anterior"}),
            moneda=("Facturado en el mes", "Total a pagar", "Saldo anterior"))


def _tab_combustible(f: pd.DataFrame, despl: pd.DataFrame, veh: pd.DataFrame, control: str):
    c = f[f["item"] == COMBUSTIBLE]
    if c.empty:
        st.info("Sin datos de combustible en el filtro.")
        return
    colores = {esquema.CAT_VEHICULOS: COLOR_SERIES[0], esquema.CAT_PLANTAS: COLOR_SERIES[1]}
    c1, c2 = st.columns(2)
    with c1:
        _barras_mes(c, "categoria", colores, "Valor del combustible por destino", clave="c1")
    with c2:
        _barras_mes(c[c["cantidad"].notna()], "categoria", colores, "Galones suministrados", valor="cantidad",
                    moneda=False, clave="c2")
    cob = analisis.cobertura_combustible(c)
    cortos = cob[cob["dias"] < 25]
    if len(cortos):
        st.caption("Meses con registros parciales: " + "; ".join(
            f"{nombre_periodo(r.periodo)} · {r.categoria.lower()} ({r.dias} días)" for r in cortos.itertuples()))
    c1, c2 = st.columns(2)
    with c1:
        _barras_h(c.groupby("referencia")["valor"].sum(), "Vehículos y equipos con mayor gasto", clave="c3")
    with c2:
        _barras_h(c.groupby("hospital")["valor"].sum(), "Combustible por hospital", clave="c4")
    r = analisis.rendimiento(f, despl, veh)
    r = r[r["periodo"].isin(c["periodo"].unique())]
    if len(r) and r["km"].sum() > 0:
        st.subheader("Rendimiento por vehículo")
        dias = analisis.cobertura_desplazamientos(despl)
        st.caption("Kilómetros del cuadro de desplazamientos frente a galones tanqueados. "
                   + " · ".join(f"{nombre_periodo(p)}: desplazamientos de {d} días" for p, d in sorted(dias.items())
                                if p in set(r["periodo"])))
        r = r.assign(Periodo=r["periodo"].map(nombre_periodo))
        tabla(r[["Periodo", "hospital", "placa", "descripcion", "tanqueos", "galones", "valor", "viajes", "km",
                 "km_por_galon", "rendimiento_esperado", "pct_esperado"]].rename(columns={
                     "hospital": "Hospital", "placa": "Placa", "descripcion": "Vehículo", "tanqueos": "Tanqueos",
                     "galones": "Galones", "valor": "Valor", "viajes": "Viajes", "km": "Km", "km_por_galon": "Km/galón",
                     "rendimiento_esperado": "Esperado", "pct_esperado": "% del esperado"}),
              moneda=("Valor",), entero=("Tanqueos", "Viajes", "Km", "% del esperado"),
              decimal=("Galones", "Km/galón", "Esperado"), height=380)


def _tab_mantenimiento(m: pd.DataFrame):
    if m.empty:
        st.info("Sin datos de mantenimiento en el filtro.")
        return
    cats = list(m.groupby("categoria")["valor"].sum().sort_values(ascending=False).index)
    _barras_mes(m, "categoria", {c: COLOR_SERIES[i % 8] for i, c in enumerate(cats[:8])},
                "Mantenimiento mensual por tipo de activo", clave="m1")
    c1, c2 = st.columns(2)
    with c1:
        _barras_h(m.groupby("hospital")["valor"].sum(), "Mantenimiento por hospital", clave="m2")
    with c2:
        ref = m[m["referencia"].notna()].groupby("referencia")["valor"].sum()
        if len(ref):
            _barras_h(ref, "Activos con mayor gasto acumulado", clave="m3")
    tipos = m.groupby("concepto")["valor"].sum()
    if set(tipos.index) - {"Sin clasificar"}:
        prev, corr = tipos.get("Preventivo", 0), tipos.get("Correctivo", 0)
        if prev + corr:
            st.caption(f"Preventivo: {pesos(prev)} ({prev / (prev + corr) * 100:.0f}%) · Correctivo: {pesos(corr)} "
                       f"({corr / (prev + corr) * 100:.0f}%). Un peso alto del correctivo indica mantenimiento reactivo.")
    else:
        st.caption("Los registros no distinguen preventivo de correctivo. Use la plantilla de mantenimientos para "
                   "clasificarlos y poder medir esa relación.")


def _tab_control(f: pd.DataFrame):
    """Gráfico de control de individuos (XmR): media y límites a ±3 sigma estimada con el rango móvil."""
    st.caption("Elija una serie. Los puntos fuera de los límites de control señalan meses cuyo comportamiento no se "
               "explica por la variación normal de esa serie.")
    c1, c2, c3 = st.columns(3)
    item = c1.selectbox("Ítem", [i for i in ITEMS if i in set(f["item"])], format_func=ITEMS.get)
    fi = f[f["item"] == item]
    cat = c2.selectbox("Categoría", ["Todas"] + sorted(fi["categoria"].unique()))
    if cat != "Todas":
        fi = fi[fi["categoria"] == cat]
    medidas = {"Valor ($)": "valor"}
    if fi["cantidad"].notna().any() and cat != "Todas":
        medidas["Cantidad (" + str(fi["unidad"].dropna().mode().iloc[0]) + ")"] = "cantidad"
    medida = c3.selectbox("Medida", list(medidas))
    col = medidas[medida]
    s = fi.groupby("periodo")[col].sum()
    if len(s) < 4:
        st.info(f"Se necesitan al menos 4 meses para calcular límites de control; esta serie tiene {len(s)}.")
        return
    media = s.mean()
    sigma = s.diff().abs().dropna().mean() / 1.128
    lsc, lic = media + 3 * sigma, max(media - 3 * sigma, 0)
    fuera = (s > lsc) | (s < lic)
    x = [nombre_periodo(p) for p in s.index]
    es_pesos = col == "valor"
    fig = go.Figure()
    fig.add_scatter(x=x, y=s.values, mode="lines+markers", name=medida, line=dict(color=AZUL, width=2),
                    marker=dict(size=8, line=dict(color="white", width=2)),
                    hovertemplate=(HOVER_PESOS if es_pesos else "%{y:,.0f}") + "<extra></extra>")
    if fuera.any():
        fig.add_scatter(x=[x[i] for i in np.where(fuera)[0]], y=s[fuera].values, mode="markers",
                        name="▲ Fuera de control", hoverinfo="skip",
                        marker=dict(size=13, color=COLOR_SEVERIDAD["Alta"], symbol="triangle-up",
                                    line=dict(color="white", width=2)))
    for y, nombre, dash in ((media, "Media", "solid"), (lsc, "Límite superior", "dash"), (lic, "Límite inferior", "dash")):
        fig.add_hline(y=y, line=dict(color=TINTA_SUAVE, width=1, dash=dash), annotation_text=nombre,
                      annotation_position="top right", annotation_font=dict(size=11, color=TINTA_SUAVE))
    fig = estilo(fig, f"{ITEMS[item]} · {cat} · {medida}", alto=400, moneda_y=es_pesos, leyenda=bool(fuera.any()))
    fig.update_yaxes(rangemode="normal")   # una línea no necesita partir de cero; los límites dan la escala
    grafico(fig, "gc")
    fmt = pesos if es_pesos else numero
    st.caption(f"Media {fmt(media)} · Límite superior {fmt(lsc)} · Límite inferior {fmt(lic)} · "
               + (f"Meses fuera de control: {', '.join(x[i] for i in np.where(fuera)[0])}." if fuera.any()
                  else "Ningún mes fuera de los límites."))


def pagina_alertas(u):
    st.title("Alertas, recomendaciones y análisis")
    df, despl, veh = cargar(version_datos(), u.ambito)
    if df.empty:
        st.info("Aún no hay datos cargados.")
        return
    pers = analisis.periodos(df)
    defecto = analisis.periodo_de_control(df, dt.date.today())
    c1, c2, c3 = st.columns([1.4, 2, 2])
    P = c1.selectbox("Periodo a evaluar", pers, index=pers.index(defecto), format_func=nombre_periodo,
                     help="Por defecto, el último mes cerrado con datos.")
    al = analisis.alertas(df, despl, veh, db.parametros(), P, analisis.pendientes_catalogo() if u.es_central else 0)
    sev = c2.multiselect("Severidad", ["Alta", "Media", "Baja"], default=["Alta", "Media", "Baja"])
    items = c3.multiselect("Ítem", sorted(al["item"].unique()), default=sorted(al["item"].unique()))
    vista = al[al["severidad"].isin(sev) & al["item"].isin(items)]

    k = st.columns(3)
    for col, s in zip(k, ["Alta", "Media", "Baja"]):
        col.metric(f"{ICONO_SEVERIDAD[s]} Severidad {s.lower()}", int((al["severidad"] == s).sum()))

    t1, t2 = st.tabs(["Alertas", "Análisis del periodo"])
    with t1:
        if vista.empty:
            st.success("No hay alertas con ese filtro.")
        for tipo, g in vista.groupby("tipo", sort=False):
            with st.expander(f"{ICONO_SEVERIDAD[g['severidad'].iloc[0]]} {tipo} · {len(g)}", expanded=g["severidad"].iloc[0] == "Alta"):
                st.markdown(f"**Qué hacer:** {g['recomendacion'].iloc[0]}")
                for r in g.itertuples():
                    donde = r.hospital + (f" · {r.sede}" if r.sede and r.sede != r.hospital else "")
                    st.markdown(f"{ICONO_SEVERIDAD[r.severidad]} **{r.severidad}** · {donde} — " + r.mensaje.replace("$", "\\$"))
        if len(vista):
            st.download_button("Descargar alertas (CSV)", vista.to_csv(index=False, sep=";").encode("utf-8-sig"),
                               f"alertas_{P}.csv", "text/csv")
    with t2:
        for titulo, parrafos in analisis.analisis_texto(df, despl, veh, al, P):
            st.subheader(titulo)
            for p in parrafos:
                st.markdown("- " + p.replace("$", "\\$"))
    st.caption("Las reglas y umbrales de las alertas se ajustan en Administración → Parámetros.")

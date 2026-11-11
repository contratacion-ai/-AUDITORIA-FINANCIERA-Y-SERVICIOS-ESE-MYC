"""Piezas compartidas de la interfaz: formato colombiano, caché y estilo de gráficos."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import analisis, db
from .config import COLOR_SERIES

_V = tuple(int(x) for x in st.__version__.split(".")[:2])
ANCHO = {"width": "stretch"} if _V >= (1, 50) else {"use_container_width": True}

TINTA, TINTA_SUAVE, REJILLA = "#0b0b0b", "#52514e", "#e6e6e3"
AZUL = COLOR_SERIES[0]
ESCALA_AZUL = ["#f3f8fe", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]


def pesos(v, corto: bool = False) -> str:
    if v is None or pd.isna(v):
        return "—"
    if corto and abs(v) >= 1e9:
        return "$" + f"{v / 1e9:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " mil M"
    if corto and abs(v) >= 1e6:
        return "$" + f"{v / 1e6:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".") + " M"
    return "$" + f"{v:,.0f}".replace(",", ".")


def numero(v, dec: int = 0) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def tabla(df: pd.DataFrame, moneda: tuple = (), entero: tuple = (), decimal: tuple = (), **kw):
    """Tabla con números en formato colombiano (se ordena por el valor real, no por el texto)."""
    fmt = {c: pesos for c in moneda if c in df.columns}
    fmt |= {c: numero for c in entero if c in df.columns}
    fmt |= {c: (lambda v: numero(v, 1)) for c in decimal if c in df.columns}
    st.dataframe(df.style.format(fmt, na_rep="—") if fmt else df, hide_index=True, **ANCHO, **kw)


def version_datos() -> int:
    """Cambia con cada cargue, eliminación o cambio de catálogo: invalida la caché."""
    return int(db.consulta("SELECT COALESCE(MAX(evento_id),0) AS v FROM eventos")["v"].iloc[0])


@st.cache_data(show_spinner=False)
def cargar(version: int, hospital: str | None):
    return (analisis.datos(hospital=hospital), analisis.desplazamientos(hospital=hospital), analisis.vehiculos())


def estilo(fig: go.Figure, titulo: str = "", alto: int = 340, moneda_y: bool = False, leyenda: bool = True) -> go.Figure:
    fig.update_layout(
        title=dict(text=titulo, font=dict(size=15, color=TINTA), x=0, xanchor="left"),
        height=alto, margin=dict(l=64, r=16, t=64 if titulo else 20, b=44),
        paper_bgcolor="white", plot_bgcolor="white", font=dict(color=TINTA_SUAVE, size=12),
        separators=",.", hovermode="x unified", showlegend=leyenda, barcornerradius=4, bargap=0.35,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, title_text=""),
        hoverlabel=dict(bgcolor="white", font_size=12),
    )
    fig.update_xaxes(showgrid=False, linecolor=REJILLA, ticks="", type="category")
    fig.update_yaxes(gridcolor=REJILLA, zeroline=False, tickprefix="$" if moneda_y else "",
                     tickformat="~s" if moneda_y else ",", rangemode="tozero")
    return fig


def corto(texto, n: int = 30) -> str:
    """Etiqueta de eje: sin el prefijo repetido y recortada (el nombre completo va en el detalle al pasar el cursor)."""
    t = str(texto)
    for prefijo in ("Hospital ", "Centro de Salud ", "Puesto de Salud "):
        if t.startswith(prefijo):
            t = t[len(prefijo):]
            break
    return t if len(t) <= n else t[:n - 1] + "…"


def margen_etiquetas(fig: go.Figure, etiquetas) -> go.Figure:
    """Deja a la izquierda el espacio que necesitan las etiquetas de un gráfico horizontal."""
    largo = max((len(str(e)) for e in etiquetas), default=8)
    fig.update_layout(margin=dict(l=min(240, int(6.4 * largo) + 16)))
    return fig


def grafico(fig: go.Figure, clave: str | None = None):
    st.plotly_chart(fig, theme=None, config={"displayModeBar": False, "locale": "es"}, key=clave, **ANCHO)

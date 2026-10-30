"""Cruces y conciliaciones.

1. RIPS vs Nota Técnica: frecuencia observada vs prevista por servicio, mes y municipio.
2. EVENTO vs tarifario pactado: diferencia de valor unitario y total; servicios sin tarifa.
3. RIPS vs facturación por ámbito: cantidades y valores; facturado sin RIPS y RIPS sin factura.
4. AF vs servicios RIPS por factura (conciliación interna del envío).
"""
from __future__ import annotations

import pandas as pd

from ..constantes import nombre_grupo


def rips_vs_nota_tecnica(servicios: pd.DataFrame, nota_tecnica: pd.DataFrame | None,
                         tolerancia_pct: float = 10.0) -> pd.DataFrame:
    """Frecuencia RIPS (CAPITA) vs frecuencia mensual prevista por la NT.

    Si la NT trae frecuencia por municipio se cruza por (código, municipio, mes); si solo trae
    'TODOS', se cruza contra el total de la red. Previsto mensual = frecuencia_prevista (si la NT
    trae el campo ``mes`` vacío se asume que la frecuencia es mensual y se repite cada mes).
    """
    if nota_tecnica is None or nota_tecnica.empty or nota_tecnica["frecuencia_prevista"].isna().all():
        return pd.DataFrame()
    cap = servicios[servicios["modalidad"].eq("CAPITA") & ~servicios["es_episodio"]]
    nt = nota_tecnica.dropna(subset=["frecuencia_prevista"]).copy()
    nt["municipio"] = nt["municipio"].fillna("TODOS")
    meses = sorted(cap["mes"].dropna().unique())
    por_mun = (nt["municipio"] != "TODOS").any()
    obs_keys = ["codigo", "municipio", "mes"] if por_mun else ["codigo", "mes"]
    obs = cap.groupby(obs_keys)["cantidad"].sum().rename("frecuencia_rips").reset_index()
    obs["mes"] = obs["mes"].astype(str)
    if nt["mes"].notna().any():
        nt["mes"] = nt["mes"].astype(str)
        prev = nt
    else:
        prev = nt.drop(columns=["mes"]).merge(pd.DataFrame({"mes": meses}), how="cross")
    if not por_mun:
        prev = prev.drop(columns=["municipio"])
    prev["mes"] = prev["mes"].astype(str)
    prev = prev.groupby(obs_keys).agg(descripcion=("descripcion", "first"), grupo=("grupo", "first"),
                                      frecuencia_prevista=("frecuencia_prevista", "sum")).reset_index()
    x = prev.merge(obs, on=obs_keys, how="outer")
    x["frecuencia_prevista"] = x["frecuencia_prevista"].fillna(0)
    x["frecuencia_rips"] = x["frecuencia_rips"].fillna(0)
    x["desviacion"] = x["frecuencia_rips"] - x["frecuencia_prevista"]
    x["desviacion_pct"] = (x["desviacion"] / x["frecuencia_prevista"].where(x["frecuencia_prevista"] > 0)) * 100

    def estado(r):
        if r.frecuencia_prevista == 0:
            return "SIN_NT"
        if r.desviacion_pct > tolerancia_pct:
            return "SOBREPASO"
        if r.desviacion_pct < -tolerancia_pct:
            return "SUBEJECUCION"
        return "DENTRO"
    x["estado"] = x.apply(estado, axis=1)
    x["grupo_nombre"] = x["grupo"].map(lambda g: nombre_grupo(g) if isinstance(g, str) else None)
    return x.sort_values(["mes", "desviacion"], ascending=[True, False]).reset_index(drop=True)


def evento_vs_tarifario(servicios: pd.DataFrame, tarifario: pd.DataFrame | None,
                        tolerancia_pct: float = 1.0) -> pd.DataFrame:
    ev = servicios[servicios["modalidad"].isin(["EVENTO", "PGP"]) & ~servicios["es_episodio"] &
                   servicios["eps_objetivo"]].copy()
    if ev.empty:
        return pd.DataFrame()
    g = ev.groupby(["codigo", "mes"]).agg(descripcion=("descripcion", "first"), grupo=("grupo", "first"),
                                          cantidad=("cantidad", "sum"), valor_facturado=("valor_total", "sum"),
                                          vu_min=("valor_unitario", "min"),
                                          vu_max=("valor_unitario", "max")).reset_index()
    g["valor_unitario_facturado"] = g["valor_facturado"] / g["cantidad"].where(g["cantidad"] > 0)
    if tarifario is None or tarifario.empty:
        g["tarifa_pactada"] = None
        g["estado"] = "SIN_TARIFARIO_CARGADO"
        return g
    t = tarifario.copy()
    t["codigo"] = t["codigo"].astype(str)
    t["desde"] = pd.to_datetime(t["vigencia_desde"], errors="coerce", dayfirst=True)
    t["hasta"] = pd.to_datetime(t["vigencia_hasta"], errors="coerce", dayfirst=True)
    g["_f"] = pd.to_datetime(g["mes"].astype(str) + "-01", errors="coerce")

    def tarifa(cod, f):
        c = t[t["codigo"] == cod]
        if c.empty:
            return None
        c = c[(c["desde"].isna() | (c["desde"] <= f)) & (c["hasta"].isna() | (c["hasta"] >= f))]
        return float(c.sort_values("desde").iloc[-1]["tarifa"]) if not c.empty else None
    g["tarifa_pactada"] = [tarifa(c, f) for c, f in zip(g["codigo"], g["_f"])]
    g["dif_valor_unitario"] = g["valor_unitario_facturado"] - g["tarifa_pactada"]
    g["dif_valor_total"] = g["valor_facturado"] - g["tarifa_pactada"] * g["cantidad"]
    g["dif_pct"] = g["dif_valor_unitario"] / g["tarifa_pactada"] * 100

    def estado(r):
        if pd.isna(r.tarifa_pactada):
            return "SIN_TARIFA"
        if r.dif_pct > tolerancia_pct:
            return "SOBRE_TARIFA"
        if r.dif_pct < -tolerancia_pct:
            return "BAJO_TARIFA"
        return "CONFORME"
    g["estado"] = g.apply(estado, axis=1)
    return g.drop(columns=["_f"])


def rips_vs_facturacion(servicios: pd.DataFrame, facturacion: pd.DataFrame | None,
                        tolerancia: float = 1.0) -> pd.DataFrame:
    """Concilia por (periodo, municipio, ámbito, código). Si la facturación no trae ámbito o
    municipio, esas llaves se omiten del cruce."""
    if facturacion is None or facturacion.empty:
        return pd.DataFrame()
    f = facturacion.copy()
    f["codigo"] = f["codigo"].astype(str)
    s = servicios[~servicios["es_episodio"] & servicios["eps_objetivo"]].rename(columns={"mes": "periodo_s"})
    s = s.assign(periodo=s["periodo_s"])
    llaves = ["periodo", "codigo"]
    for k in ("municipio", "ambito"):
        if f[k].notna().any():
            llaves.insert(1, k)
            f[k] = f[k].astype(str).str.upper()
            s[k] = s[k].astype(str).str.upper()
    r = s.groupby(llaves).agg(cantidad_rips=("cantidad", "sum"), valor_rips=("valor_total", "sum")).reset_index()
    fa = f.groupby(llaves).agg(descripcion=("descripcion", "first"), cantidad_facturada=("cantidad", "sum"),
                               valor_facturado=("valor", "sum")).reset_index()
    x = fa.merge(r, on=llaves, how="outer").fillna({"cantidad_rips": 0, "valor_rips": 0, "cantidad_facturada": 0,
                                                     "valor_facturado": 0})
    x["dif_cantidad"] = x["cantidad_facturada"] - x["cantidad_rips"]
    x["dif_valor"] = x["valor_facturado"] - x["valor_rips"]

    def estado(row):
        if row.cantidad_rips == 0 and row.valor_rips == 0:
            return "FACTURADO_SIN_RIPS"
        if row.cantidad_facturada == 0 and row.valor_facturado == 0:
            return "RIPS_SIN_FACTURA"
        if abs(row.dif_valor) <= tolerancia and abs(row.dif_cantidad) < 0.5:
            return "CONCILIADO"
        return "DIFERENCIA"
    x["estado"] = x.apply(estado, axis=1)
    # Glosa potencial: lo facturado que no tiene soporte RIPS (exceso de valor facturado).
    x["glosa_potencial"] = x["dif_valor"].clip(lower=0).where(x["estado"].isin(["FACTURADO_SIN_RIPS", "DIFERENCIA"]), 0)
    return x


def conciliacion_facturas(tablas: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """AF vs suma de servicios por factura (como la hoja Conciliacion_Facturas del consolidado)."""
    af = tablas["AF"]
    if af.empty:
        return pd.DataFrame()
    piezas = {"AC": "valor_consulta", "AP": "valor", "AM": "valor_total", "AT": "valor_total", "AD": "valor_total"}
    out = af[["envio_id", "hospital", "municipio", "periodo", "num_factura", "fecha_expedicion", "cod_entidad",
              "nombre_entidad", "num_contrato", "valor_neto", "valor_copago", "valor_descuentos"]].copy()
    for t, col in piezas.items():
        d = tablas[t]
        if d.empty:
            out[f"valor_{t.lower()}"] = 0.0
            continue
        v = d.assign(_v=pd.to_numeric(d[col], errors="coerce")).groupby(["envio_id", "num_factura"])["_v"].sum()
        out = out.merge(v.rename(f"valor_{t.lower()}").reset_index(), on=["envio_id", "num_factura"], how="left")
    vcols = [f"valor_{t.lower()}" for t in piezas]
    out[vcols] = out[vcols].fillna(0.0)
    out["total_servicios"] = out[vcols].sum(axis=1)
    num = lambda c: pd.to_numeric(out[c], errors="coerce").fillna(0.0)
    out["valor_neto_esperado"] = out["total_servicios"] - num("valor_copago") - num("valor_descuentos")
    out["diferencia"] = num("valor_neto") - out["valor_neto_esperado"]
    out["estado"] = out.apply(lambda r: "SIN_SERVICIOS" if r.total_servicios == 0 else
                              ("CONCILIADA" if abs(r.diferencia) <= 1 else "DIFERENCIA"), axis=1)
    return out

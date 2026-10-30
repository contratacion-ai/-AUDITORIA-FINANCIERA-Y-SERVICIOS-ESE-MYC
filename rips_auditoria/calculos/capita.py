"""Costo CAPITA y escenarios de negociación (réplica de los dashboards de referencia).

Modelo de cálculo (dashboard CAPITA·EVENTO, hoja NT COSTOS):

    costo CAPITA (mes, grupo) = Σ_servicios costo_unitario_NT(código[, municipio]) × frecuencia_RIPS

Opcionalmente el grupo MEDICAMENTOS se reemplaza por el despacho general de la institución
(``adj = costNT − medC + despacho``). Los KPI, cuatrimestres, escenarios y PGP siguen las
fórmulas del dashboard FINAL, que es el que fija los totales de control.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

import pandas as pd

from ..constantes import MESES_AB, nombre_grupo


# ----------------------------------------------------------------------------- costeo
def costear(servicios: pd.DataFrame, nota_tecnica: pd.DataFrame | None) -> pd.DataFrame:
    """Agrega ``costo_unitario_nt`` y ``costo_capita`` a los servicios CAPITA.

    Búsqueda del costo unitario: (código, municipio) -> (código, TODOS) -> (código sin sufijo CUM).
    Los servicios sin costo en la NT quedan con ``costo_capita = 0`` y ``sin_costo_nt = True``.
    """
    s = servicios.copy()
    s["costo_unitario_nt"] = pd.NA
    if nota_tecnica is not None and not nota_tecnica.empty and "costo_unitario" in nota_tecnica:
        nt = nota_tecnica.dropna(subset=["costo_unitario"]).copy()
        nt["codigo"] = nt["codigo"].astype(str)
        nt["municipio"] = nt.get("municipio", "TODOS")
        nt["municipio"] = nt["municipio"].fillna("TODOS")
        por_mun = nt.groupby(["codigo", "municipio"])["costo_unitario"].mean().to_dict()
        gen = nt.groupby("codigo")["costo_unitario"].mean().to_dict()

        def cu(cod, mun):
            if not isinstance(cod, str):
                return None
            for k in (cod, cod.split("-")[0]):
                v = por_mun.get((k, mun)) or por_mun.get((k, "TODOS")) or gen.get(k)
                if v:
                    return v
            return None
        s["costo_unitario_nt"] = [cu(c, m) for c, m in zip(s["codigo"], s["municipio"])]
    s["costo_unitario_nt"] = pd.to_numeric(s["costo_unitario_nt"], errors="coerce")
    es_cap = s["modalidad"].eq("CAPITA") & ~s["es_episodio"]
    s["sin_costo_nt"] = es_cap & s["costo_unitario_nt"].isna()
    s["costo_capita"] = (s["costo_unitario_nt"].fillna(0) * s["cantidad"]).where(es_cap, 0.0)
    return s


def costos_grupo_mes(costeado: pd.DataFrame, meses: list[str] | None = None,
                     despacho: pd.DataFrame | None = None) -> pd.DataFrame:
    """Matriz grupo × mes (estructura de la tabla 'Costos por Grupo' del dashboard)."""
    cap = costeado[costeado["modalidad"].eq("CAPITA") & ~costeado["es_episodio"]]
    piv = cap.pivot_table(index="grupo", columns="mes", values="costo_capita", aggfunc="sum", fill_value=0.0)
    rips = cap.groupby("grupo")["cantidad"].sum()
    if meses:
        piv = piv.reindex(columns=meses, fill_value=0.0)
    if despacho is not None and not despacho.empty:
        d = despacho.set_index("periodo")["valor"]
        if "MEDICAMENTOS" not in piv.index:
            piv.loc["MEDICAMENTOS"] = 0.0
        for m in piv.columns:
            if m in d.index and pd.notna(d[m]):
                piv.loc["MEDICAMENTOS", m] = float(d[m])
        if "unidades" in despacho:
            rips.loc["MEDICAMENTOS"] = float(despacho["unidades"].sum())
    out = piv.copy()
    out.insert(0, "unidades_rips", rips.reindex(out.index).fillna(0))
    out["acumulado"] = piv.sum(axis=1)
    n = max(len(piv.columns), 1)
    out["promedio_mes"] = out["acumulado"] / n
    tot = out["acumulado"].sum()
    out["participacion_pct"] = out["acumulado"] / tot * 100 if tot else 0.0
    out = out.sort_values("acumulado", ascending=False)
    out.insert(0, "grupo_nombre", [nombre_grupo(g) for g in out.index])
    return out


def detalle_mensual(costeado: pd.DataFrame, costos_mes: pd.Series | None = None,
                    meses: list[str] | None = None) -> pd.DataFrame:
    """Tabla 'Detalle Mensual CAPITA': Mes, CUPS, CUMS, Total, Pac. únicos, Asistencias, Costo,
    Costo/usuario, Costo/atención.

    Definiciones (SUPUESTOS documentados):
    * CUPS = Σ cantidad de servicios CAPITA con código CUPS u otro (AC, AP, AT); CUMS = Σ unidades AM.
    * Pacientes únicos = pacientes distintos en el mes (el total del periodo es la SUMA mensual,
      como en el dashboard: 114.923).
    * Asistencias = pares distintos (paciente, fecha de atención) en el mes; si el registro no trae
      fecha (AM/AT en TXT) se asocia a la factura.
    * Costo/atención = costo / frecuencia RIPS costeada (servicios CAPITA con costo NT).
    """
    cap = costeado[costeado["modalidad"].eq("CAPITA")]
    srv = cap[~cap["es_episodio"]]
    g = srv.groupby("mes")
    d = pd.DataFrame({"cups": srv[srv["tipo_codigo"] != "CUMS"].groupby("mes")["cantidad"].sum(),
                      "cums": srv[srv["tipo_codigo"] == "CUMS"].groupby("mes")["cantidad"].sum()},
                     index=pd.Index(sorted(cap["mes"].dropna().unique()), name="mes"))
    d["total"] = d["cups"] + d["cums"]
    d["pacientes"] = cap.groupby("mes")["paciente"].nunique()
    contacto = cap["fecha"].astype(str).where(cap["fecha"].notna(), "F:" + cap["num_factura"].astype(str))
    d["asistencias"] = cap.assign(_c=cap["paciente"] + "|" + contacto).groupby("mes")["_c"].nunique()
    d["costo"] = costos_mes if costos_mes is not None else g["costo_capita"].sum()
    d["frecuencia_costeada"] = srv[srv["costo_unitario_nt"].notna()].groupby("mes")["cantidad"].sum()
    if meses is not None:
        d = d.reindex(meses)
        if costos_mes is not None:
            d["costo"] = costos_mes.reindex(meses)
    d = d.fillna(0)
    d.index.name = "mes"
    d["costo_usuario"] = d["costo"] / d["pacientes"].where(d["pacientes"] > 0)
    d["costo_atencion"] = d["costo"] / d["frecuencia_costeada"].where(d["frecuencia_costeada"] > 0)
    return d.reset_index()


# ----------------------------------------------------------------------------- KPI y escenarios
@dataclass
class KpiCapita:
    total: float
    promedio_mes: float
    n_meses: int
    periodo_temprano: float
    periodo_reciente: float
    promedio_temprano: float
    promedio_reciente: float
    crecimiento_pct: float
    pacientes_suma: float
    asistencias: float
    costo_usuario_prom: float      # promedio de los costos/usuario mensuales (dashboard FINAL)
    costo_atencion_prom: float
    costo_paciente_global: float   # total / suma de pacientes (dashboard CAPITA·EVENTO)
    costo_asistencia_global: float
    tendencia_mes: float           # pendiente lineal (pesos/mes)


def kpis(costos: list[float], pacientes: list[float], asistencias: list[float],
         frecuencia_costeada: list[float] | None = None, ventana: int = 4) -> KpiCapita:
    n = len(costos)
    w = min(ventana, n)
    temprano = costos[: n - w] if n - w > 0 else costos[:w]
    reciente = costos[n - w:]
    total = float(sum(costos))
    cu = [c / p for c, p in zip(costos, pacientes) if p]
    fc = frecuencia_costeada or []
    ca = [c / f for c, f in zip(costos, fc) if f]
    xs = list(range(n))
    pend = statistics.linear_regression(xs, costos).slope if n > 1 else 0.0
    return KpiCapita(total=total, promedio_mes=total / n if n else 0.0, n_meses=n,
                     periodo_temprano=float(sum(temprano)), periodo_reciente=float(sum(reciente)),
                     promedio_temprano=float(statistics.fmean(temprano)) if temprano else 0.0,
                     promedio_reciente=float(statistics.fmean(reciente)) if reciente else 0.0,
                     crecimiento_pct=(sum(reciente) / sum(temprano) - 1) * 100 if sum(temprano) else 0.0,
                     pacientes_suma=float(sum(pacientes)), asistencias=float(sum(asistencias)),
                     costo_usuario_prom=float(statistics.fmean(cu)) if cu else 0.0,
                     costo_atencion_prom=float(statistics.fmean(ca)) if ca else 0.0,
                     costo_paciente_global=total / sum(pacientes) if sum(pacientes) else 0.0,
                     costo_asistencia_global=total / sum(asistencias) if sum(asistencias) else 0.0,
                     tendencia_mes=pend)


def giro(params: dict) -> dict[str, float]:
    g = params["giro"]
    u = g["usuarios"]
    v0 = g["valor_usuario_mes"]
    v1 = round(v0 * (1 + g["incremento_upc"]))
    return {"usuarios": u, "valor_usuario": v0, "valor_usuario_upc": v1, "valor_usuario_actas": g["valor_usuario_actas"],
            "giro_sin_upc": u * v0, "giro_upc": u * v0 * (1 + g["incremento_upc"]),
            "giro_actas": u * g["valor_usuario_actas"]}


def escenarios(k: KpiCapita, migrables_mes: float, evento_promedio_mes: float, params: dict,
               giro_vals: dict | None = None) -> pd.DataFrame:
    """Los 3 escenarios CAPITA + referencias de pago + PGP (dashboard FINAL).

    * Esc.1 CAPITA ACTUAL      = promedio mensual del costo real.
    * Esc.2 CAPITA AMPLIADA    = Esc.1 + servicios migrables EVENTO→CAPITA (valor/mes).
    * Esc.3 CAPITA DEMANDA REAL= promedio del 2º cuatrimestre × (1 + ajuste poblacional) + nuevos servicios.
    * PGP-1 = giro vigente + EVENTO promedio; PGP-2 = giro con UPC + EVENTO promedio.
    """
    e = params["escenarios"]
    gv = giro_vals or giro(params)
    e1 = k.promedio_mes
    e2 = e1 + migrables_mes
    e3 = k.promedio_reciente * (1 + e["ajuste_poblacional"]) + e["nuevos_servicios_mes"]
    filas = [
        ("ESC.1 – CAPITA ACTUAL", e1, "Base contractual vigente"),
        ("ESC.2 – CAPITA AMPLIADA", e2, "Base + servicios migrables"),
        ("ESC.3 – CAPITA DEMANDA REAL", e3, "2º cuatrimestre + ajuste poblacional + nuevos servicios"),
        ("Giro directo sin UPC (vigente)", gv["giro_sin_upc"], f"{gv['usuarios']:,} × {gv['valor_usuario']:,}"),
        ("Giro + UPC 2026", gv["giro_upc"], f"+{params['giro']['incremento_upc'] * 100:.2f} %"),
        ("Actas firmadas Bogotá", gv["giro_actas"], f"{gv['usuarios']:,} × {gv['valor_usuario_actas']:,}"),
        ("PGP-1 Cápita vigente + EVENTO", gv["giro_sin_upc"] + evento_promedio_mes, "Piso PGP"),
        ("PGP-2 UPC + EVENTO", gv["giro_upc"] + evento_promedio_mes, "PGP óptimo"),
    ]
    df = pd.DataFrame(filas, columns=["escenario", "valor_mes", "detalle"])
    df["valor_anual"] = df["valor_mes"] * 12
    df["incremento_vs_esc1_pct"] = (df["valor_mes"] / e1 - 1) * 100 if e1 else 0.0
    df["valor_usuario_mes"] = df["valor_mes"] / gv["usuarios"]
    return df


def pgp_mensual(costos: list[float], evento: list[float], params: dict) -> pd.DataFrame:
    gv = giro(params)
    n = len(costos)
    return pd.DataFrame({
        "mes": MESES_AB[:n], "costo_fijo_capita": costos, "facturacion_evento": evento,
        "pgp1_cap_vigente_evento": [gv["giro_sin_upc"] + v for v in evento],
        "pgp2_upc_evento": [gv["giro_upc"] + v for v in evento],
        "pgp3_actas_evento": [gv["giro_actas"] + v for v in evento]})


def corredor_evento(evento: list[float], participacion_traslados: float, ventana: int = 4, k: float = 1.0) -> dict:
    """PGP-EVENTO del dashboard CAPITA·EVENTO: run-rate de los últimos meses ± k·desviación."""
    n = len(evento)
    w = min(ventana, n)
    rr_vals = evento[n - w:]
    rr = statistics.fmean(rr_vals) if rr_vals else 0.0
    sd = statistics.stdev(rr_vals) if len(rr_vals) > 1 else 0.0
    tr = rr * participacion_traslados
    return {"promedio": statistics.fmean(evento) if evento else 0.0, "run_rate": rr, "desviacion": sd,
            "cv_pct": sd / rr * 100 if rr else 0.0, "limite_inferior": rr - k * sd, "limite_superior": rr + k * sd,
            "traslados_fuera_pgp": tr, "nucleo_pgp": rr - tr}

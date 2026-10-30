"""Costo CAPITA (réplica de la lógica de los dashboards de referencia y del libro INFORME DE SERVICIOS).

Modelo de cálculo (dashboard CAPITA·EVENTO, hoja NT COSTOS):

    costo CAPITA (mes, grupo) = Σ_servicios costo_unitario_NT(código[, municipio]) × frecuencia_RIPS

Opcionalmente el grupo MEDICAMENTOS se reemplaza por el despacho general de la institución
(``adj = costNT − medC + despacho``). Los KPI y cuatrimestres siguen las fórmulas del dashboard
FINAL y del libro.
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
        if "homologo" in nt:   # el RIPS AM trae el CUM, que en la NT es el homólogo
            h = nt[nt["homologo"].notna()].copy()
            h["codigo"] = h["homologo"].astype(str)
            nt = pd.concat([nt, h], ignore_index=True)
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
        claves = s["codigo_nt"].where(s["codigo_nt"].notna(), s["codigo"]) if "codigo_nt" in s else s["codigo"]
        s["costo_unitario_nt"] = [cu(c, m) for c, m in zip(claves, s["municipio"])]
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

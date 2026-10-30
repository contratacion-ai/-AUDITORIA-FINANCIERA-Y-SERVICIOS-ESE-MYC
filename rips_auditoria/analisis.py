"""Orquestador del análisis: lotes RIPS + maestros + parámetros -> Resultado (tablas e indicadores).

Separado de la interfaz: lo usan la API FastAPI, el script de escritorio y las pruebas.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from . import maestros
from .calculos import capita, cruces, evento, hallazgos
from .clasificacion import clasificar
from .config import DIR_CONFIG, red_prestadores
from .constantes import CONTROL, MESES_AB, nombre_grupo
from .ingesta.lote import Lote, combinar
from .referencias.dashboards import cargar_final
from .servicios import construir

SUPUESTOS = [
    "Municipio = código DANE en los 5 primeros dígitos del código de habilitación del prestador; si falta, "
    "nombre de sede/carpeta (config/red_prestadores.yaml).",
    "Mes = carpeta 'NN. Mes' de la remisión; si no existe, mes más frecuente de fecha_inicio del AF. Año por "
    "defecto 2026 si la carpeta no lo indica.",
    "Nueva EPS = códigos EPS037 y EPSS37; las demás EPS se cargan pero se excluyen del análisis cápita/evento.",
    "Pacientes únicos del periodo = suma de pacientes únicos mensuales (igual que los dashboards).",
    "Asistencia = par distinto (paciente, fecha de atención) en el mes; sin fecha (AM/AT TXT) se usa la factura.",
    "Costo/atención = costo CAPITA / frecuencia RIPS costeada con la Nota Técnica.",
    "Modalidad contractual = catálogo de marcación (OFERTADO→CAPITA, NO OFERTADO→EVENTO) y luego Nota Técnica; "
    "sin marca: carpeta/contrato; si no hay, EVENTO (SIN MARCA).",
    "Modalidad efectiva para costos y facturación = la declarada por la remisión (carpeta/contrato) cuando existe; "
    "las diferencias con la modalidad contractual se reportan como hallazgos.",
    "Migrables EVENTO→CAPITA valorados a tarifa EVENTO / meses (método del dashboard CAPITA·EVENTO).",
]


@lru_cache(maxsize=1)
def parametros() -> dict:
    with open(DIR_CONFIG / "parametros_contrato.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@dataclass
class Resultado:
    generado: str
    alcance: dict
    indicadores: dict
    tablas: dict[str, pd.DataFrame] = field(default_factory=dict)
    supuestos: list[str] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)

    # ------------------------------------------------------------ guardado / restauración JSON
    def a_json(self) -> str:
        def conv(o):
            if isinstance(o, (pd.Timestamp, datetime)):
                return o.isoformat()
            if hasattr(o, "item"):
                return o.item()
            return str(o)
        t = {k: json.loads(v.to_json(orient="split", date_format="iso", force_ascii=False))
             for k, v in self.tablas.items()}
        return json.dumps({"version": 1, "generado": self.generado, "alcance": self.alcance,
                           "indicadores": self.indicadores, "supuestos": self.supuestos,
                           "advertencias": self.advertencias, "tablas": t}, ensure_ascii=False, default=conv)

    @staticmethod
    def desde_json(txt: str) -> "Resultado":
        o = json.loads(txt)
        tablas = {k: pd.DataFrame(v["data"], columns=v["columns"]) for k, v in o["tablas"].items()}
        return Resultado(generado=o["generado"], alcance=o["alcance"], indicadores=o["indicadores"], tablas=tablas,
                         supuestos=o.get("supuestos", []), advertencias=o.get("advertencias", []))

    def guardar(self, ruta: Path) -> Path:
        ruta.write_text(self.a_json(), encoding="utf-8")
        return ruta


def _maestros() -> dict[str, pd.DataFrame | None]:
    return {n: maestros.leer(n) for n in ("catalogo", "nota_tecnica", "tarifario", "facturacion",
                                           "despacho_medicamentos")}


def comparar_referencia(det: pd.DataFrame) -> pd.DataFrame:
    """RIPS cargados vs detalle mensual del dashboard FINAL (mismos meses)."""
    ref = cargar_final()
    filas = []
    for i, r in enumerate(ref.detalle_mensual):
        per = f"2026-{i + 1:02d}"
        x = det[det["mes"] == per]
        o = x.iloc[0] if not x.empty else None
        for k, nombre in (("cups", "CUPS"), ("pacientes", "Pacientes únicos"), ("asistencias", "Asistencias"),
                          ("costo", "Costo CAPITA")):
            v = float(o[k]) if o is not None else None
            filas.append({"periodo": per, "indicador": nombre, "referencia": r[k], "calculado": v,
                          "diferencia": (v - r[k]) if v is not None else None,
                          "diferencia_pct": ((v / r[k] - 1) * 100) if v is not None and r[k] else None})
    return pd.DataFrame(filas)


def analizar(lotes: list[Lote], solo_eps_objetivo: bool = True, mae: dict | None = None,
             params: dict | None = None) -> Resultado:
    params = params or parametros()
    mae = mae if mae is not None else _maestros()
    adv: list[str] = []
    if not lotes:
        raise ValueError("Seleccione al menos un lote")
    ids = [l.id for l in lotes]
    lotes = [combinar(lotes)]
    if len(ids) > 1 and not lotes[0].duplicados.empty and \
            (lotes[0].duplicados["motivo"] == "REGISTRO_REPETIDO_ENTRE_LOTES").any():
        adv.append("Algunos registros estaban en más de un lote; se contaron una sola vez.")
    s = construir(lotes[0])
    if s.empty:
        raise ValueError("No hay servicios RIPS en los lotes seleccionados")
    todos = s
    if solo_eps_objetivo:
        s = s[s["eps_objetivo"]]
        if s.empty:
            raise ValueError("Ningún servicio corresponde a Nueva EPS (EPS037/EPSS37)")
    cat, nt = mae.get("catalogo"), mae.get("nota_tecnica")
    if nt is None or "costo_unitario" not in nt or nt["costo_unitario"].isna().all():
        adv.append("La Nota Técnica cargada no tiene costos unitarios: el costo CAPITA será 0. Cargue el libro "
                   "INFORME DE SERVICIOS (hoja NT COSTOS) o la plantilla nota_tecnica.csv.")
    s = clasificar(s, cat, nt, params["clasificacion"]["modalidad_por_defecto"])
    s = capita.costear(s, nt)
    meses = sorted(s["mes"].dropna().unique().tolist())
    nm = len(meses)
    desp = mae.get("despacho_medicamentos") if params["clasificacion"]["reemplazar_medicamentos_por_despacho"] else None
    grupos = capita.costos_grupo_mes(s, meses, desp)
    costos_mes = grupos[meses].sum(axis=0)
    det = capita.detalle_mensual(s, costos_mes, meses)
    w = params["escenarios"]["meses_ventana"]
    k = capita.kpis(det["costo"].tolist(), det["pacientes"].tolist(), det["asistencias"].tolist(),
                    det["frecuencia_costeada"].tolist(), w)
    # EVENTO
    ev = s[s["modalidad"].isin(["EVENTO", "PGP"]) & ~s["es_episodio"]]
    ev_mes = ev.groupby("mes")["valor_total"].sum().reindex(meses, fill_value=0.0)
    cons_ev = evento.por_codigo(ev)
    tras = cons_ev[cons_ev["grupo"] == "TRASLADOS"]["valor"].sum()
    part_tr = tras / cons_ev["valor"].sum() if cons_ev["valor"].sum() else 0.0
    mig = evento.migrables(cons_ev, max(nm, 1), k.promedio_reciente)
    mig_mes = float(mig["valor_evento_mes"].sum()) if not mig.empty else 0.0
    rie = evento.riesgo(cons_ev, max(nm, 1), params["traslados"].get("tope_mensual"), params["traslados"]["codigos"])
    corr = capita.corredor_evento(ev_mes.tolist(), part_tr, w, params["corredor"]["k_desviaciones"])
    gv = capita.giro(params)
    esc = capita.escenarios(k, mig_mes, float(ev_mes.mean()) if nm else 0.0, params, gv)
    pgp = capita.pgp_mensual(det["costo"].tolist(), ev_mes.tolist(), params)
    pgp["mes"] = meses
    corr_tab = pd.DataFrame({"mes": meses, "evento": ev_mes.values})
    corr_tab["run_rate"], corr_tab["limite_inferior"], corr_tab["limite_superior"] = (
        corr["run_rate"], corr["limite_inferior"], corr["limite_superior"])
    corr_tab["posicion"] = corr_tab["evento"].map(
        lambda v: "Por debajo" if v < corr["limite_inferior"] else "Por encima" if v > corr["limite_superior"] else "Dentro")
    # Frecuencias y servicios
    base = s[~s["es_episodio"]]
    frec = (base.groupby(["municipio", "hospital", "mes", "modalidad"], dropna=False)
            .agg(cups_distintos=("codigo", "nunique"), frecuencia=("cantidad", "sum"), registros=("codigo", "size"),
                 pacientes=("paciente", "nunique"), valor=("valor_total", "sum"), costo_capita=("costo_capita", "sum"))
            .reset_index())
    frec_piv = base.pivot_table(index=["municipio", "hospital"], columns="mes", values="cantidad", aggfunc="sum",
                                fill_value=0, dropna=False)
    frec_piv["TOTAL"] = frec_piv.sum(axis=1)
    serv = evento.por_codigo(base)
    serv["modalidad"] = serv["codigo"].map(base.groupby("codigo")["modalidad"].agg(lambda x: x.mode().iat[0]))
    serv["costo_capita"] = serv["codigo"].map(base.groupby("codigo")["costo_capita"].sum())
    par_val = evento.pareto(serv, "valor")
    par_frec = evento.pareto(serv, "frecuencia")
    top20 = evento.top20(cons_ev, "frecuencia")
    top_costo = serv[serv["modalidad"] == "CAPITA"].sort_values("costo_capita", ascending=False).head(20)
    cpa = (s.groupby(["mes", "municipio"]).agg(pacientes=("paciente", "nunique"),
                                               costo_capita=("costo_capita", "sum"),
                                               valor_evento=("valor_total", lambda x: x[s.loc[x.index, "modalidad"] != "CAPITA"].sum()))
           .reset_index())
    cpa["costo_capita_paciente"] = cpa["costo_capita"] / cpa["pacientes"].where(cpa["pacientes"] > 0)
    # Cruces
    tol = params["cruces"]
    nt_x = cruces.rips_vs_nota_tecnica(s, nt, tol["tolerancia_frecuencia_pct"])
    tar_x = cruces.evento_vs_tarifario(s, mae.get("tarifario"), tol["tolerancia_tarifa_pct"])
    fac_x = cruces.rips_vs_facturacion(s, mae.get("facturacion"), tol["tolerancia_valor_pesos"])
    conc = cruces.conciliacion_facturas(lotes[0].tablas)
    if solo_eps_objetivo and not conc.empty:
        conc = conc[conc["cod_entidad"].astype(str).str.upper().isin(
            [e.upper() for e in red_prestadores()["eps_objetivo"]])]
    inc = lotes[0].incidencias
    hall = hallazgos.consolidar(s, nt_x, tar_x, fac_x, inc, conc, rie)
    traz = (s.groupby(["codigo", "tipo_archivo", "modalidad", "fuente_modalidad", "modalidad_catalogo",
                       "regla_modalidad", "estado_catalogo", "en_nota_tecnica",
                       "grupo", "subgrupo", "regla_grupo"], dropna=False)
            .agg(descripcion=("descripcion", "first"), cantidad=("cantidad", "sum"), registros=("codigo", "size"),
                 discrepancias=("discrepancia_modalidad", "sum")).reset_index())
    sin_costo = s[s["sin_costo_nt"]].groupby("codigo").agg(descripcion=("descripcion", "first"),
                                                           frecuencia=("cantidad", "sum")).reset_index()
    if not sin_costo.empty and nt is not None and nt["costo_unitario"].notna().any():
        adv.append(f"{len(sin_costo)} códigos CAPITA sin costo unitario en la Nota Técnica (ver hoja Sin costo NT).")
    grupos_out = grupos.reset_index().rename(columns={"index": "grupo"})
    det_out = det.copy()
    det_out.insert(1, "mes_nombre", [MESES_AB[int(m[5:7]) - 1] if isinstance(m, str) and len(m) == 7 else m
                                     for m in det_out["mes"]])
    ind = {"capita": asdict(k), "giro": gv, "corredor_evento": corr, "migrables_mes": mig_mes,
           "participacion_traslados": part_tr, "evento_total": float(ev_mes.sum()),
           "evento_promedio_mes": float(ev_mes.mean()) if nm else 0.0, "meses": meses,
           "servicios": int(len(base)), "pacientes_distintos_periodo": int(s["paciente"].nunique()),
           "riesgo_traslados_mes": rie.attrs.get("traslados_mes") if not rie.empty else 0.0,
           "hallazgos_alta": int((hall["severidad"] == "ALTA").sum()),
           "valor_en_riesgo": float(hall["valor_en_riesgo"].fillna(0).sum())}
    alcance = {"lotes": ids, "solo_eps_objetivo": solo_eps_objetivo,
               "municipios": sorted(s["municipio"].dropna().unique().tolist()),
               "hospitales": sorted(s["hospital"].dropna().unique().tolist()), "meses": meses,
               "servicios_otras_eps_excluidos": int((~todos["eps_objetivo"]).sum()) if solo_eps_objetivo else 0}
    tablas = {"detalle_mensual": det_out, "capita_grupos": grupos_out, "escenarios": esc, "pgp_mensual": pgp,
              "corredor_evento": corr_tab, "top20_evento": top20, "migrables": mig, "riesgo": rie,
              "frecuencias_mes": frec, "frecuencias_municipio": frec_piv.reset_index(),
              "servicios": serv, "pareto_valor": par_val, "pareto_frecuencia": par_frec, "top_costo_capita": top_costo,
              "costo_paciente": cpa, "cruce_nota_tecnica": nt_x, "cruce_tarifario": tar_x,
              "cruce_facturacion": fac_x, "conciliacion_facturas": conc, "hallazgos": hall,
              "trazabilidad_clasificacion": traz, "sin_costo_nt": sin_costo,
              "comparacion_referencia": comparar_referencia(det),
              "conciliacion_informe": pd.DataFrame((maestros.leer_resumen_informe() or {}).get("conciliacion", [])),
              "validacion": inc.groupby(["severidad", "regla"]).size().rename("cantidad").reset_index()}
    return Resultado(generado=datetime.now().isoformat(timespec="seconds"), alcance=alcance, indicadores=ind,
                     tablas=tablas, supuestos=SUPUESTOS, advertencias=adv)


def controles_referencia() -> dict:
    """Totales de control del dashboard FINAL y su recálculo con las fórmulas de este paquete."""
    ref = cargar_final()
    p = parametros()
    det = ref.detalle_mensual
    k = capita.kpis([r["costo"] for r in det], [r["pacientes"] for r in det], [r["asistencias"] for r in det],
                    ref.pgp["RIPS_CAP"], p["escenarios"]["meses_ventana"])
    ev = ref.pgp["FACT_EV"]
    esc = capita.escenarios(k, ref.textos["migrables_esc2"], sum(ev) / len(ev), p)
    t = ref.totales()
    inf = maestros.leer_resumen_informe()
    return {"conciliacion_informe": (inf or {}).get("conciliacion", []), "control": CONTROL, "referencia": {**t, "capita_total_html": ref.textos["total_capita_html"],
                                               "migrables_mes": ref.textos["migrables_mes_texto"],
                                               "migrables_mes_esc2": ref.textos["migrables_esc2"]},
            "kpi": asdict(k), "escenarios": esc.set_index("escenario")["valor_mes"].to_dict(),
            "grupos": {nombre_grupo(g): pct for g, pct in zip(ref.grupos, ref.grupos_pct)}}

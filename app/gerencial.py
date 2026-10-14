"""INFORME GERENCIAL: indicadores con semáforo, hallazgos priorizados por valor en riesgo, narrativa ejecutiva y plan de acción
(responsable y plazo sugeridos). Reúne facturación por ámbito, facturas generadas, RIPS, costos, notas técnicas y medicamentos/insumos."""
from datetime import datetime
import pandas as pd
from . import analysis as A, costos as C, facturas as F, medicamentos as MD
from .db import AUTOR, CARGO, INSTITUCION, NIT, VERSION, META_GLOSA, META_RADICACION, UMBRAL_OK, UMBRAL_ALERTA

RESPONSABLE = {"Facturas generadas": "Coordinación de facturación y cartera", "Medicamentos e insumos": "Servicio farmacéutico y almacén",
               "Facturación / RIPS / costos": "Auditoría de cuentas médicas · Costos · Estadística (RIPS)"}
PLAZO = {"rojo": "Inmediato (≤ 8 días)", "naranja": "Corto plazo (≤ 30 días)", "info": "Seguimiento mensual", "verde": "Seguimiento mensual"}
PRIORIDAD = {"rojo": 1, "naranja": 2, "info": 3, "verde": 4}

def _sem(valor, ok, alerta, mayor_es_mejor=False):
    if valor is None: return "SIN DATO"
    if mayor_es_mejor: return "OK" if valor >= ok else "ALERTA" if valor >= alerta else "CRÍTICO"
    return "OK" if valor <= ok else "ALERTA" if valor <= alerta else "CRÍTICO"

def informe(mes=None):
    ind, riesgos, txt = [], [], []
    fg = F.resumen(mes); r = A.resumen(mes); md = MD.resumen(mes); co = C.resumen(mes)
    kf, kr, km, kc = fg.get("kpis", {}), r["kpis"], md.get("kpis", {}), co.get("kpis", {})
    periodo = mes or "todos los periodos cargados"

    if kf:
        ind += [dict(indicador="Valor facturado (facturas generadas)", valor=kf["valor"], formato="m", estado="INFO", meta="—",
                     lectura=f"{kf['facturas']:,} facturas · ticket promedio ${kf['ticket']:,.0f}"),
                dict(indicador="% del valor en facturas glosadas", valor=kf["pct_glosa"], formato="p", estado=_sem(kf["pct_glosa"], META_GLOSA, 2 * META_GLOSA),
                     meta=f"≤ {META_GLOSA:.0%} (meta interna)", lectura=f"{kf['n_glosadas']:,} facturas glosadas por ${kf['glosado']:,.0f}"),
                dict(indicador=f"% radicado dentro de {kf['plazo']} días hábiles", valor=kf["pct_en_plazo"], formato="p",
                     estado=_sem(kf["pct_en_plazo"], META_RADICACION, META_RADICACION - 0.10, True), meta=f"≥ {META_RADICACION:.0%}",
                     lectura=f"promedio {kf['dias_radicacion'] or 0:.1f} días hábiles entre expedición y radicación"),
                dict(indicador="Pendiente por radicar", valor=kf["pendiente"], formato="m", estado="OK" if not kf["pendiente"] else "ALERTA" if kf["pendiente"] / kf["valor"] < 0.05 else "CRÍTICO",
                     meta="$0 al cierre", lectura=f"{kf['n_pendientes']:,} facturas (corte {kf['corte']})"),
                dict(indicador="Facturas con alguna alerta (F01–F12)", valor=kf["con_alerta"] / kf["facturas"], formato="p",
                     estado=_sem(kf["con_alerta"] / kf["facturas"], 0.05, 0.15), meta="≤ 5 %", lectura=f"${kf['valor_con_alerta']:,.0f} comprometidos")]
        txt.append(f"En {periodo} la ESE expidió {kf['facturas']:,} facturas por ${kf['valor']:,.0f} a {kf['terceros']} pagadores en {kf['sedes']} sedes, "
                   f"atendiendo a {kf['pacientes']:,} pacientes únicos. El {kf['radicado'] / kf['valor']:.1%} del valor está radicado, el {kf['pct_glosa']:.1%} en facturas glosadas "
                   f"y el {kf['pendiente'] / kf['valor']:.1%} pendiente por radicar.")
        if kf["pct_ultimos_5_dias"] > 0.3:
            txt.append(f"El {kf['pct_ultimos_5_dias']:.0%} del valor se facturó en los últimos cinco días con facturación del periodo, lo que evidencia "
                       "concentración al cierre y aumenta el riesgo de errores y glosas.")
        for e in fg["por_eps"][:5]:
            if e["glosado"]: riesgos.append(dict(fuente="Glosas por EPS", hallazgo=f"{e['tercero']}: {e['pct_glosa']:.1%} del valor en facturas glosadas", valor=e["glosado"], estado="CRÍTICO" if e["pct_glosa"] > 0.1 else "ALERTA"))
        if kf["pendiente"]: riesgos.append(dict(fuente="Radicación", hallazgo=f"{kf['n_pendientes']:,} facturas pendientes por radicar", valor=kf["pendiente"], estado="CRÍTICO"))
    if kr.get("facturado"):
        ind.append(dict(indicador="Valor facturado (facturación por ámbito)", valor=kr["facturado"], formato="m", estado="INFO", meta="—", lectura=f"{kr['lineas']:,} líneas de servicio"))
        if kr.get("rips"):
            ind.append(dict(indicador="Brecha facturación vs RIPS (evento)", valor=abs(kr["pct_brecha"]), formato="p", estado=_sem(abs(kr["pct_brecha"]), UMBRAL_OK, UMBRAL_ALERTA),
                            meta=f"≤ {UMBRAL_OK:.0%}", lectura=f"${kr['brecha_fact_rips']:,.0f}"))
        if kr.get("costos"):
            pm = kr["margen"] / kr["facturado"]
            ind.append(dict(indicador="Margen facturado − costos", valor=pm, formato="p", estado="OK" if pm > 0.1 else "ALERTA" if pm > 0 else "CRÍTICO", meta="> 10 %", lectura=f"${kr['margen']:,.0f}"))
        if kr["valor_hallazgo"]:
            riesgos.append(dict(fuente="Reglas de línea R01–R09", hallazgo=f"{kr['lineas_hallazgo']:,} líneas con inconsistencias", valor=kr["valor_hallazgo"], estado="ALERTA"))
        txt.append(f"La facturación por ámbito registra ${kr['facturado']:,.0f} en {kr['lineas']:,} líneas"
                   + (f"; frente al RIPS la brecha en eventos es de ${kr['brecha_fact_rips']:,.0f} ({kr['pct_brecha']:.1%})" if kr.get("rips") else "")
                   + (f" y el margen frente a costos es de ${kr['margen']:,.0f}." if kr.get("costos") else "."))
    if km:
        ind.append(dict(indicador="Medicamentos e insumos / facturado", valor=km["pct_facturado"], formato="p", estado="INFO", meta="seguimiento",
                        lectura=f"${km['valor']:,.0f} · {km['codigos']:,} códigos · {km['items_a']} ítems clase A"))
        txt.append(f"Medicamentos, insumos y suplementos suman ${km['valor']:,.0f} ({km['pct_facturado']:.1%} de lo facturado): medicamentos ${km['medicamentos']:,.0f}, "
                   f"insumos ${km['insumos']:,.0f}; ${km['valor_capita']:,.0f} corresponden a contratos cápita.")
        cf = MD.cruce_farmacia(mes)
        if not cf.empty and cf.valor_no_facturado.sum():
            riesgos.append(dict(fuente="Farmacia vs facturación", hallazgo="Dispensado no facturado", valor=float(cf.valor_no_facturado.sum()), estado="CRÍTICO"))
    if kc.get("cups"):
        riesgos += [dict(fuente="Costos", hallazgo=f"CUPS con estado «{e['estado']}»", valor=None, estado="ALERTA") for e in co["estados"] if "NEGATIVO" in e["estado"]][:1]

    recs = [dict(x, modulo=x.get("modulo", "Facturación / RIPS / costos")) for x in A.recomendaciones(mes)
            if not (x["nivel"] == "info" and "No hay RIPS" in x["texto"] and kf)]
    recs += F.recomendaciones(mes) + MD.recomendaciones(mes)
    recs = [dict(r, prioridad=PRIORIDAD.get(r["nivel"], 3), responsable=RESPONSABLE.get(r["modulo"], "Gerencia"), plazo=PLAZO.get(r["nivel"], "—")) for r in recs]
    recs.sort(key=lambda r: r["prioridad"])
    riesgos.sort(key=lambda r: -(r["valor"] or 0))
    if not txt: txt.append("Aún no hay información cargada. Cargue el informe de facturas generadas o la facturación por ámbito de Software 360 en la pestaña «Cargas».")
    criticos = sum(1 for i in ind if i["estado"] == "CRÍTICO")
    txt.append(f"Resultado: {criticos} indicador(es) en estado crítico y {sum(1 for r in recs if r['prioridad'] == 1)} acción(es) de prioridad alta.")
    return dict(encabezado=dict(institucion=INSTITUCION, nit=NIT, periodo=periodo, generado=f"{datetime.now():%d/%m/%Y %H:%M}", autor=AUTOR, cargo=CARGO, version=VERSION),
                indicadores=ind, riesgos=riesgos[:10], narrativa=txt, recomendaciones=recs)

def _fmt(v, f):
    if v is None: return "—"
    return f"{v:.1%}" if f == "p" else f"${v:,.0f}" if f == "m" else f"{v:,.0f}"

def tabla_excel(mes=None):
    """Informe gerencial en formato tabla (para la hoja «Informe_Gerencial» del libro consolidado)."""
    g = informe(mes); filas = [dict(seccion="RESUMEN EJECUTIVO", item=f"{i + 1}", detalle=t, valor=None, estado="", responsable="", plazo="") for i, t in enumerate(g["narrativa"])]
    filas += [dict(seccion="INDICADORES", item=i["indicador"], detalle=f"{i['lectura']} · meta {i['meta']}", valor=_fmt(i["valor"], i["formato"]), estado=i["estado"], responsable="", plazo="") for i in g["indicadores"]]
    filas += [dict(seccion="RIESGOS (valor)", item=r["fuente"], detalle=r["hallazgo"], valor=_fmt(r["valor"], "m"), estado=r["estado"], responsable="", plazo="") for r in g["riesgos"]]
    filas += [dict(seccion="PLAN DE ACCIÓN", item=f"P{r['prioridad']} · {r['modulo']}", detalle=r["texto"], valor=None, estado={1: "CRÍTICO", 2: "ALERTA"}.get(r["prioridad"], "INFO"),
                   responsable=r["responsable"], plazo=r["plazo"]) for r in g["recomendaciones"]]
    return pd.DataFrame(filas)

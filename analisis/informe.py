"""Informe gerencial de costos ABC con la estructura del informe de referencia de la institución.

Secciones: portada · introducción · metodología · análisis de costos (elementos y recursos) · unidades de negocio
(finales, intermedias, servicios, estructura) · distribución por sede · benchmarking · hallazgos · recomendaciones
· conclusiones. Todo se calcula con la información cargada; los textos libres vienen de ConfiguracionInforme.
"""
from decimal import Decimal

from modelo.models import CentroCosto, Clase, ConfiguracionInforme, IndicadorReferencia, ReglaDistribucion

from core.templatetags.formato import pct as _fpct
from core.templatetags.formato import pct_signo as _fpct_signo
from core.templatetags.formato import pesos as _fpesos

from . import consultas as Q
from .cruces import costo_vs_tarifa, inconsistencias

CERO = Decimal("0")


def _pct(a, b):
    return (a / b * 100) if b else None


def calcular_indicador(formula, ctx):
    tipo, _, arg = formula.partition(":")
    total = ctx["total"]
    if tipo == "crecimiento_total":
        return Q.var(total, ctx["total_anterior"])[1] if ctx["total_anterior"] else None
    if tipo == "pct_elemento":
        return _pct(ctx["por_elemento"].get(arg, CERO), total)
    if tipo == "pct_recurso":
        return _pct(ctx["por_recurso"].get(arg, CERO), total)
    if tipo == "pct_recurso_en_elemento":
        elem = ctx["elemento_de_recurso"].get(arg)
        return _pct(ctx["por_recurso"].get(arg, CERO), ctx["por_elemento"].get(elem, CERO))
    if tipo == "pct_un":
        return _pct(ctx["por_un"].get(arg, CERO), total)
    if tipo == "pct_un_en_finales":
        return _pct(ctx["por_un"].get(arg, CERO), ctx["total_finales_directo"])
    if tipo == "valor_recurso":
        return ctx["por_recurso"].get(arg, CERO)
    return None


def estado_indicador(valor, ind):
    if valor is None:
        return "SIN DATO"
    if ind.minimo is not None and valor < ind.minimo:
        return "BAJO"
    if ind.maximo is not None and valor > ind.maximo:
        return "ALTO" if not ind.es_valor else "CRÍTICO"
    return "NORMAL"


def construir(institucion, periodo, periodo_comp=None):
    corrida = Q.corrida_vigente(periodo)
    corrida_comp = Q.corrida_vigente(periodo_comp)
    config, _ = ConfiguracionInforme.objects.get_or_create(institucion=institucion)
    recursos, total, total_ant = Q.tabla_recursos(institucion, corrida, corrida_comp)
    elementos, _, _ = Q.tabla_elementos(institucion, corrida, corrida_comp)
    unidades, totales_un = Q.tabla_unidades(institucion, corrida, corrida_comp, "directo")
    finales_total, _ = Q.tabla_unidades(institucion, corrida, corrida_comp, "final")
    rec_sede, sedes, totales_sede = Q.tabla_sedes(institucion, corrida,
                                                  [c.strip().upper() for c in config.recursos_por_sede.split(",") if c.strip()])
    ctx_ind = {
        "total": total, "total_anterior": total_ant,
        "por_recurso": {f["recurso"].codigo: f["actual"] for f in recursos},
        "por_elemento": {f["elemento"].codigo: f["actual"] for f in elementos},
        "elemento_de_recurso": {f["recurso"].codigo: f["elemento"].codigo for f in recursos},
        "por_un": {f["unidad"].codigo: f["actual"] for clase in unidades.values() for f in clase},
        "total_finales_directo": totales_un[Clase.FINALES][0],
    }
    indicadores = []
    for ind in IndicadorReferencia.objects.de(institucion):
        valor = calcular_indicador(ind.formula, ctx_ind)
        indicadores.append({"indicador": ind, "valor": valor, "estado": estado_indicador(valor, ind)})

    umbral = config.umbral_variacion
    hallazgos = []
    if corrida_comp is not None:
        for f in recursos:
            if f["anterior"] == 0 and f["actual"] > 0:
                hallazgos.append(("CRÍTICO" if f["actual"] >= total * Decimal("0.01") else "ATENCIÓN",
                                  f"{f['recurso'].nombre.title()}: rubro nuevo en {periodo}",
                                  f"Aparece por primera vez con {_fpesos(f['actual'])}; no registró valor en {periodo_comp}."))
            elif f["actual"] == 0 and f["anterior"] > 0:
                hallazgos.append(("ATENCIÓN", f"{f['recurso'].nombre.title()}: rubro sin registro",
                                  f"Pasó de {_fpesos(f['anterior'])} a $0. Verificar causación o reclasificación."))
            elif f["pct"] is not None and abs(f["pct"]) >= umbral and abs(f["dif"]) >= total * Decimal("0.005"):
                sentido = "creció" if f["dif"] > 0 else "disminuyó"
                hallazgos.append(("CRÍTICO" if abs(f["pct"]) >= 2 * umbral else "ATENCIÓN",
                                  f"{f['recurso'].nombre.title()} {_fpct_signo(f['pct'])}",
                                  f"{sentido.capitalize()} de {_fpesos(f['anterior'])} a {_fpesos(f['actual'])}."))
        for clase, filas in unidades.items():
            for f in filas:
                if f["pct"] is not None and abs(f["pct"]) >= umbral * 3 and abs(f["dif"]) >= total * Decimal("0.01"):
                    hallazgos.append(("CRÍTICO", f"{f['unidad'].codigo} {f['unidad'].nombre.title()} {_fpct_signo(f['pct'], 0)}",
                                      f"Variación atípica ({_fpesos(f['anterior'])} → {_fpesos(f['actual'])}): posible reclasificación "
                                      "contable o error de imputación; auditar."))
    for i in indicadores:
        if i["estado"] in ("ALTO", "CRÍTICO", "BAJO"):
            v = i["valor"]
            texto = _fpesos(v) if i["indicador"].es_valor else _fpct(v)
            fmt = _fpesos if i["indicador"].es_valor else (lambda x: _fpct(x, 0))
            ref = " – ".join(fmt(x) for x in (i["indicador"].minimo, i["indicador"].maximo) if x is not None)
            hallazgos.append(("CRÍTICO" if i["estado"] == "CRÍTICO" else "ATENCIÓN",
                              f"{i['indicador'].nombre}: {texto} ({i['estado']})",
                              f"Referencia {ref}. {i['indicador'].accion_recomendada}"))
    costos_c = Q.costo_centros(corrida)
    sin_costo = [c for c in CentroCosto.objects.de(institucion).filter(activo=True, clase=Clase.FINALES)
                 if not costos_c.get(c.pk, {}).get("final")]
    if corrida and sin_costo:
        hallazgos.append(("ATENCIÓN", f"{len(sin_costo)} centros finales activos sin costo asignado",
                          "Servicios que no se están prestando o cuya producción/costo no se registra: "
                          + ", ".join(c.nombre.title() for c in sin_costo[:8]) + ("…" if len(sin_costo) > 8 else "")))
    if corrida and Decimal(corrida.resumen.get("pendiente_asignacion", "0")) != 0:
        hallazgos.append(("CRÍTICO", "Costo pendiente de asignación",
                          f"{_fpesos(corrida.resumen['pendiente_asignacion'])} no llegaron a centros finales (ver Motor → pendientes)."))
    perdidas = [f for f in costo_vs_tarifa(institucion, periodo, corrida) if f["resultado"] == "PÉRDIDA"] if corrida else []
    if perdidas:
        hallazgos.append(("ATENCIÓN", f"{len(perdidas)} CUPS con tarifa contratada inferior al costo",
                          "Revisar notas técnicas: " + ", ".join(f"{p['cups'].codigo} ({p['eps'].nombre})" for p in perdidas[:6])))
    incons = inconsistencias(institucion, periodo, corrida) if corrida else []
    altas = [i for i in incons if i.severidad == "ALTA"]
    if altas:
        hallazgos.append(("ATENCIÓN", f"{len(altas)} inconsistencias de severidad alta en el cruce RIPS / facturación / costos",
                          "Ver Cruces → Inconsistencias."))
    orden = {"CRÍTICO": 0, "ATENCIÓN": 1}
    hallazgos.sort(key=lambda h: orden.get(h[0], 2))

    recomendaciones = []
    for linea in (config.recomendaciones or "").splitlines():
        partes = [p.strip() for p in linea.split("|")]
        if any(partes):
            recomendaciones.append((partes + [""] * 6)[:6])
    for i in indicadores:
        if i["estado"] in ("ALTO", "CRÍTICO", "BAJO") and i["indicador"].accion_recomendada:
            recomendaciones.append(["AUTOMÁTICA", i["indicador"].nombre, i["indicador"].accion_recomendada, "", "", ""])

    metodologia = []
    for d, nombre in ReglaDistribucion.Distribucion.choices:
        inductores = sorted({r.inductor.nombre for r in ReglaDistribucion.objects.de(institucion).filter(distribucion=d, activo=True)
                             .select_related("inductor")})
        metodologia.append((nombre, inductores))
    variacion = Q.var(total, total_ant)[1] if corrida_comp else None
    personal = next((f for f in elementos if f["elemento"].codigo == "CP"), None)
    return {
        "institucion": institucion, "periodo": periodo, "periodo_comp": periodo_comp, "corrida": corrida,
        "corrida_comp": corrida_comp, "config": config, "total": total, "total_anterior": total_ant, "variacion": variacion,
        "dif_total": total - total_ant,
        "recursos": recursos, "elementos": elementos, "unidades": unidades, "totales_un": totales_un,
        "finales_total": finales_total, "rec_sede": rec_sede, "sedes": sedes, "totales_sede": totales_sede, "total_sedes": sum(totales_sede, CERO),
        "indicadores": indicadores, "hallazgos": hallazgos, "recomendaciones": recomendaciones, "metodologia": metodologia,
        "n_unidades": sum(1 for filas in unidades.values() for f in filas if f["actual"]),
        "pct_personal": personal["part_actual"] if personal else None,
        "conciliaciones": corrida.resumen.get("conciliaciones", []) if corrida else [],
    }

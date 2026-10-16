"""Construcción de perfiles desde el estudio y verificación de sus cifras."""

from __future__ import annotations

from . import datos_estudio as D
from .calculo import PerfilAmbulancia, Precios, indicadores_flota


def precios_estudio() -> Precios:
    return Precios(gasolina=D.PRECIO_GASOLINA, acpm=D.PRECIO_ACPM)


def perfiles_estudio(tripulacion_dia: float = D.TRIPULACION_DIA) -> list[PerfilAmbulancia]:
    return [
        PerfilAmbulancia(
            placa=placa,
            sede=sede,
            tipo_combustible=comb,
            km_dia=km,
            horas_dia=h,
            combustible_dia=c,
            mantenimiento_dia=m,
            tripulacion_dia=tripulacion_dia,
            excluida_promedios=excl,
        )
        for placa, sede, comb, km, h, c, m, _t, _ckm, _ch, excl in D.ANEXO_A
    ]


def verificar_estudio(tolerancia_relativa: float = 0.01) -> list[dict]:
    """Recalcula las cifras del informe y devuelve las que no cuadran.

    Cada elemento: {ubicacion, indicador, reportado, calculado, diferencia}.
    No corrige nada: solo deja constancia para revisión.
    """
    precios = precios_estudio()
    perfiles = perfiles_estudio()
    hallazgos: list[dict] = []

    def comparar(ubicacion, indicador, reportado, calculado, tol=tolerancia_relativa):
        if reportado in (None, 0) or calculado is None:
            return
        dif = calculado - reportado
        if abs(dif) / abs(reportado) > tol:
            hallazgos.append(
                {
                    "ubicacion": ubicacion,
                    "indicador": indicador,
                    "reportado": reportado,
                    "calculado": round(calculado, 2),
                    "diferencia": round(dif, 2),
                }
            )

    # Anexo A fila por fila: total = combustible + mantenimiento + tripulación, $/km y $/hora.
    for (placa, _s, _c, km, h, comb, mant, total, ckm, ch, _e), p in zip(D.ANEXO_A, perfiles):
        comparar(f"Anexo A {placa}", "Total/día", total, p.total_dia, 0.000005)
        comparar(f"Anexo A {placa}", "$/km", ckm, p.costo_km, 0.005)
        # horas redondeadas a 0,1 en el anexo: tolerancia mayor
        comparar(f"Anexo A {placa}", "$/hora oper.", ch, p.costo_hora_operacion, 0.06)

    ind = indicadores_flota(perfiles, precios)
    R = D.REPORTADO
    comparar("Tabla 3", "Recorrido promedio (km/día)", R["promedio_km_dia"], ind["km_dia"]["promedio"])
    comparar("Tabla 3", "Combustible real promedio (gal/día)", R["promedio_gal_dia"], ind["galones_dia"]["promedio"], 0.02)
    comparar("Tabla 3", "Rendimiento real promedio (km/gal)", R["promedio_rendimiento"], ind["rendimiento_km_gal"]["promedio"], 0.02)
    comparar("Tabla 3", "Rendimiento real mínimo (km/gal)", R["rendimiento_min"][0], ind["rendimiento_km_gal"]["min"], 0.02)
    comparar("Tabla 3", "Rendimiento real máximo (km/gal)", R["rendimiento_max"][0], ind["rendimiento_km_gal"]["max"], 0.02)
    comparar("Tabla 3", "Combustible promedio ($/día)", R["promedio_combustible_dia"], ind["combustible_dia"]["promedio"])
    comparar("Tabla 3", "Mantenimiento promedio ($/día)", R["promedio_mantenimiento_dia"], ind["mantenimiento_dia"]["promedio"])
    comparar("Tabla 3", "Costo total promedio ($/día)", R["promedio_total_dia"], ind["total_dia"]["promedio"])
    comparar("Tabla 3", "Costo por km promedio", R["promedio_costo_km"], ind["costo_km"]["promedio"], 0.02)
    comparar("Tabla 3", "Costo por hora de disponibilidad", R["promedio_costo_hora_disponibilidad"], ind["costo_hora_disponibilidad"]["promedio"])
    comparar("Num. 5.4", "Costo por hora ponderado", R["costo_hora_ponderado"], ind["costo_hora_ponderado"], 0.02)
    comparar("Num. 5.3", "Costo flota 21 ambulancias ($/día)", R["flota_total_dia"], ind["costo_flota_dia"])
    comparar("Num. 5.3", "Costo flota mensual (30 días)", R["flota_total_mes"], ind["costo_flota_dia"] * 30)
    gas, die = ind["por_tipo"]["GASOLINA"], ind["por_tipo"]["ACPM"]
    comparar("Tabla 4", "Gasolina: costo total ($/día)", R["gasolina_total_dia"], gas["total_dia"])
    comparar("Tabla 4", "Diésel: costo total ($/día)", R["acpm_total_dia"], die["total_dia"])
    comparar("Tabla 4", "Gasolina: combustible por km", R["gasolina_combustible_km"], gas["combustible_km"], 0.02)
    comparar("Tabla 4", "Diésel: combustible por km", R["acpm_combustible_km"], die["combustible_km"], 0.02)

    # Tabla 2: suma por sede = total.
    suma_valor = sum(v for _s, _g, _a, v, _fg, _fa in D.COMBUSTIBLE_SEDE)
    comparar("Tabla 2", "Total valor combustible", D.COMBUSTIBLE_TOTAL["valor"], suma_valor, 0.000001)
    suma_gas = sum(g for _s, g, _a, _v, _fg, _fa in D.COMBUSTIBLE_SEDE)
    comparar("Tabla 2", "Total galones gasolina", D.COMBUSTIBLE_TOTAL["gasolina"], suma_gas, 0.000001)
    suma_acpm = sum(a for _s, _g, a, _v, _fg, _fa in D.COMBUSTIBLE_SEDE)
    comparar("Tabla 2", "Total galones ACPM", D.COMBUSTIBLE_TOTAL["acpm"], suma_acpm, 0.000001)
    for sede, g, a, v, _fg, _fa in D.COMBUSTIBLE_SEDE:
        comparar(f"Tabla 2 {sede}", "Valor = gal × precio", v, g * D.PRECIO_GASOLINA + a * D.PRECIO_ACPM, 0.000001)
    comparar("Num. 5.2", "Valor diario (÷27 días)", D.COMBUSTIBLE_TOTAL["valor_dia"], D.COMBUSTIBLE_TOTAL["valor"] / D.DIAS_COMBUSTIBLE, 0.0001)
    comparar(
        "Num. 5.2",
        "Combustible atribuido a ambulancias (mes certificado)",
        R["combustible_asignado_ambulancias_mes"],
        sum(p.combustible_dia for p in perfiles) * D.DIAS_COMBUSTIBLE,
        0.02,
    )
    comparar("Num. 4.4", "Tripulación $/día = mensual ÷ 30", D.TRIPULACION_DIA, D.TRIPULACION_MES / 30, 0.00001)
    comparar("Num. 4.4", "Tripulación mensual = suma salarios", D.TRIPULACION_MES, sum(D.SALARIOS_TRIPULACION.values()), 0.00001)
    return hallazgos

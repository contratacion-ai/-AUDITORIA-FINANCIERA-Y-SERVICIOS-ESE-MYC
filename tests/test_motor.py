"""Validación del motor con el caso pequeño calculado a mano (docs/CASO_DE_PRUEBA.md)."""
from decimal import Decimal as D

import pytest
from django.db.models import Sum

from modelo.models import DistribucionManoObra, ReglaDistribucion, ValorInductor
from motor.models import Asignacion, CostoCups
from motor.motor import ErrorMotor, Motor, ejecutar, prorratear


def total_centro(corrida, centro):
    return Asignacion.objects.filter(corrida=corrida, centro_destino=centro).aggregate(s=Sum("valor"))["s"] or D(0)


def salida_centro(corrida, centro):
    return Asignacion.objects.filter(corrida=corrida, centro_origen=centro).aggregate(s=Sum("valor"))["s"] or D(0)


def test_prorratear_es_exacto():
    partes = prorratear(D("100"), [D(1), D(1), D(1)])
    assert sum(partes) == D("100")
    assert partes == [D("33.34"), D("33.33"), D("33.33")]
    assert sum(prorratear(D("-10.01"), [D(3), D(7)])) == D("-10.01")
    with pytest.raises(ErrorMotor):
        prorratear(D(1), [D(0)])


def test_caso_pequeno_cuatro_distribuciones(caso):
    corrida = ejecutar(caso.periodo)
    r = corrida.resumen
    assert r["total_entrada"] == "2500.00"
    assert r["total_finales"] == "2500.00"
    assert r["diferencia"] == "0.00" and r["conservacion_ok"]

    # 1ª distribución: energía 400 por m² (10/10/20/30/30)
    energia = {a.centro_destino.codigo: a.valor for a in Asignacion.objects.filter(corrida=corrida, distribucion=1, recurso=caso.R14)}
    assert energia == {"E1": D("40"), "S1": D("40"), "I1": D("80"), "F1": D("120"), "F2": D("120")}
    a = Asignacion.objects.get(corrida=corrida, distribucion=1, recurso=caso.R14, centro_destino=caso.F1)
    assert a.tasa == D("4") and a.cantidad_inductor == D("30") and a.total_inductor == D("100")
    assert a.movimiento.fila == 4 and a.movimiento.carga.nombre_original == "ef.xlsx"

    # 2ª: E1 (640) por personas 2/2/4/2 → tasa 64 por persona
    assert salida_centro(corrida, caso.E1) == D("640")
    recibido = lambda d, c: Asignacion.objects.filter(corrida=corrida, distribucion=d, centro_destino=c).aggregate(s=Sum("valor"))["s"]
    assert (recibido(2, caso.S1), recibido(2, caso.I1), recibido(2, caso.F1), recibido(2, caso.F2)) == (D(128), D(128), D(256), D(128))
    # 3ª: S1 (240 + 128 = 368) por m² 20/30/30
    assert salida_centro(corrida, caso.S1) == D("368")
    assert (recibido(3, caso.I1), recibido(3, caso.F1), recibido(3, caso.F2)) == (D(92), D(138), D(138))
    # 4ª: I1 (380 + 128 + 92 = 600) por solicitudes 1/2
    assert salida_centro(corrida, caso.I1) == D("600")
    assert (recibido(4, caso.F1), recibido(4, caso.F2)) == (D(200), D(400))
    # Finales
    assert total_centro(corrida, caso.F1) == D("1714")
    assert total_centro(corrida, caso.F2) == D("786")
    # Costeo CUPS: F1 1714 en 100 u (peso 1) + 50 u (peso 2) = 200 unidades ponderadas
    cups = {c.cups.codigo: c for c in CostoCups.objects.filter(corrida=corrida)}
    assert cups["890201"].costo_total == D("857") and cups["890201"].costo_unitario == D("8.57")
    assert cups["890301"].costo_total == D("857") and cups["890301"].costo_unitario == D("17.14")
    assert cups["890701"].costo_total == D("786") and cups["890701"].costo_unitario == D("78.60")
    assert sum(c.costo_total for c in cups.values()) == D("2500")


def test_conciliacion_area_contra_estado_financiero(caso):
    """Nómina (área) 900 distribuye; el estado financiero dice 1000 → se prorratea la diferencia."""
    from cargas.models import MovimientoContable

    MovimientoContable.objects.filter(recurso=caso.R1).delete()
    caso.mov(caso.R1, 1000, None, 10)  # EF sin centro
    caso.mov(caso.R1, 600, caso.F1, 11, fuente="AREA")
    caso.mov(caso.R1, 300, caso.F2, 12, fuente="AREA")
    corrida = ejecutar(caso.periodo)
    d1 = {a.centro_destino.codigo: (a.valor, a.ajuste_conciliacion)
          for a in Asignacion.objects.filter(corrida=corrida, distribucion=1, recurso=caso.R1)}
    assert d1 == {"F1": (D("666.67"), D("66.67")), "F2": (D("333.33"), D("33.33"))}
    assert corrida.resumen["conciliaciones"][0]["diferencia"] == "100.00"
    assert corrida.resumen["conservacion_ok"]


def test_distribucion_mano_obra_ops(caso):
    caso.mov(caso.R3, 1000, None, 20, doc="900123")
    DistribucionManoObra.objects.create(institucion=caso.inst, tipo="OPS", documento="900123", centro=caso.F1, porcentaje=60)
    DistribucionManoObra.objects.create(institucion=caso.inst, tipo="OPS", documento="900123", centro=caso.F2, porcentaje=40)
    corrida = ejecutar(caso.periodo)
    d1 = {a.centro_destino.codigo: a.valor for a in Asignacion.objects.filter(corrida=corrida, distribucion=1, recurso=caso.R3)}
    assert d1 == {"F1": D("600"), "F2": D("400")}
    assert set(Asignacion.objects.filter(corrida=corrida, recurso=caso.R3, distribucion=1).values_list("metodo", flat=True)) == {"MANO_OBRA"}


def test_precedencia_de_niveles_regla_del_periodo(caso):
    """Una regla de nivel 4 (solo este periodo) prevalece sobre la general."""
    ReglaDistribucion.objects.create(institucion=caso.inst, distribucion=1, recurso=caso.R14, inductor=caso.PERS,
                                     clases_destino="FINALES", periodo=caso.periodo)
    motor = Motor(caso.periodo)
    motor._cargar()
    regla = motor.regla(1, recurso_id=caso.R14.pk)
    assert regla.nivel == 4 and regla.inductor == caso.PERS
    corrida = ejecutar(caso.periodo)
    energia = {a.centro_destino.codigo: a.valor for a in Asignacion.objects.filter(corrida=corrida, distribucion=1, recurso=caso.R14)}
    assert energia == {"F1": D("266.67"), "F2": D("133.33")}


def test_pendiente_sin_regla_y_conservacion(caso):
    ReglaDistribucion.objects.filter(institucion=caso.inst, distribucion=4).delete()
    corrida = ejecutar(caso.periodo)
    assert corrida.estado == "PENDIENTES"
    assert corrida.resumen["pendiente_asignacion"] == "600.00"
    assert corrida.resumen["conservacion_ok"]
    assert corrida.pendientes.get().centro == caso.I1


def test_valores_del_periodo_prevalecen_sobre_permanentes(caso):
    for centro, m2 in ((caso.F1, 1), (caso.F2, 1)):
        ValorInductor.objects.create(institucion=caso.inst, inductor=caso.M2, centro=centro, periodo=caso.periodo, cantidad=m2)
    corrida = ejecutar(caso.periodo)
    energia = {a.centro_destino.codigo: a.valor for a in Asignacion.objects.filter(corrida=corrida, distribucion=1, recurso=caso.R14)}
    assert energia == {"F1": D("200"), "F2": D("200")}


def test_periodo_cerrado_no_ejecuta(caso):
    caso.periodo.estado = "CERRADO"
    caso.periodo.save()
    with pytest.raises(ErrorMotor):
        ejecutar(caso.periodo)


def test_corrida_vigente_unica(caso):
    c1 = ejecutar(caso.periodo)
    c2 = ejecutar(caso.periodo)
    c1.refresh_from_db()
    assert not c1.vigente and c2.vigente

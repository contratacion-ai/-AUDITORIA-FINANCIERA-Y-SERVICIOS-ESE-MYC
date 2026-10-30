"""Reproduce los totales de control de los artefactos de referencia con la lógica del paquete."""
import pytest

from rips_auditoria.analisis import controles_referencia
from rips_auditoria.calculos import capita
from rips_auditoria.constantes import CONTROL, CONTROL_FINAL_HISTORICO
from rips_auditoria.referencias import oficial
from rips_auditoria.referencias.dashboards import cargar_evento, cargar_final


def test_historico_totales_del_dashboard_final():
    ref = cargar_final()
    t = ref.totales()
    assert ref.textos["total_capita_html"] == CONTROL_FINAL_HISTORICO["capita_total"] == 8_053_016_408
    # La suma de los 8 costos mensuales redondeados difiere en $1 del total publicado.
    assert abs(t["capita_total"] - CONTROL_FINAL_HISTORICO["capita_total"]) <= 1
    assert t["cups"] == CONTROL_FINAL_HISTORICO["cups"] == 748_058
    assert t["pacientes_unicos"] == CONTROL_FINAL_HISTORICO["pacientes_unicos"] == 114_923
    assert t["asistencias"] == CONTROL_FINAL_HISTORICO["asistencias"] == 213_814


def test_historico_kpis_capita_reproducen_dashboard_final():
    ref = cargar_final()
    d = ref.detalle_mensual
    k = capita.kpis([r["costo"] for r in d], [r["pacientes"] for r in d], [r["asistencias"] for r in d],
                    ref.pgp["RIPS_CAP"], 4)
    assert round(k.promedio_mes) == CONTROL_FINAL_HISTORICO["capita_promedio_mes"]
    assert round(k.periodo_temprano / 1e6) == 3_420          # Cuatrimestre 1 $3.420 M
    assert round(k.periodo_reciente / 1e6) == 4_633          # Cuatrimestre 2 $4.633 M
    assert round(k.crecimiento_pct, 1) == 35.5               # ↑ +35,5 %
    assert round(k.costo_usuario_prom) == 69_781              # Costo/usuario
    assert round(k.costo_atencion_prom) == 11_130             # Costo/atención
    # Costo/usuario y costo/atención mensuales publicados en la tabla del dashboard
    for r, rc in zip(d, ref.pgp["RIPS_CAP"]):
        assert round(r["costo"] / r["pacientes"]) == r["costo_usuario"]
        assert round(r["costo"] / rc) == r["costo_atencion"]


def test_regla_medicamentos_por_despacho_dashboard_evento():
    """adj = costNT − medC + despacho general (dashboard CAPITA·EVENTO)."""
    d = cargar_evento().datos
    adj = [c - m + g for c, m, g in zip(d["nt"]["costNT"], d["nt"]["medC"], d["med"]["gen_v"])]
    assert round(sum(d["nt"]["costNT"])) == 8_264_369_795
    assert round(sum(adj)) == 8_548_188_101
    # Mismos pacientes, CUPS y asistencias que el dashboard FINAL
    assert sum(r["cups"] for r in d["det"]) == CONTROL_FINAL_HISTORICO["cups"]
    assert sum(r["pac"] for r in d["det"]) == CONTROL_FINAL_HISTORICO["pacientes_unicos"]
    assert sum(r["asis"] for r in d["det"]) == CONTROL_FINAL_HISTORICO["asistencias"]


def test_grupos_final_mismos_nombres_y_participacion():
    ref = cargar_final()
    assert ref.grupos[0] == "PROMOCION Y MANTENIMIENTO DE LA SALUD"
    assert len(ref.grupos) == 10
    assert sum(ref.grupos_pct) == pytest.approx(100, abs=0.2)


# ----------------------------------------------------------------------------- control OFICIAL
def test_control_oficial_totales_libro_vigente():
    """Totales oficiales: libro INFORME DE SERVICIOS vigente con la cifra actual de medicamentos."""
    det = oficial.detalle_mensual()
    assert round(sum(r["costo"] for r in det)) == CONTROL["capita_total"] == 8_264_369_795
    assert round(sum(r["medicamentos"] for r in det)) == CONTROL["medicamentos"] == 236_095_610
    assert sum(r["cups"] for r in det) == CONTROL["cups"] == 748_058
    assert sum(r["cums"] for r in det) == CONTROL["cums"] == 554_587
    assert sum(r["pacientes"] for r in det) == CONTROL["pacientes_unicos"] == 114_923
    assert sum(r["asistencias"] for r in det) == CONTROL["asistencias"] == 213_814


def test_control_oficial_kpis():
    c = controles_referencia()
    calc = c["calculado"]
    for clave, v in CONTROL.items():
        assert abs(calc[clave] - v) <= 1, (clave, calc[clave], v)
    assert round(c["kpi"]["crecimiento_pct"], 1) == 33.0
    assert "escenarios" not in c        # PGP y escenarios retirados
    # Sin medicamentos, el control oficial y el dashboard FINAL son el mismo valor
    assert abs(CONTROL["capita_sin_medicamentos"]
               - (CONTROL_FINAL_HISTORICO["capita_total"] - CONTROL_FINAL_HISTORICO["medicamentos"])) <= 1

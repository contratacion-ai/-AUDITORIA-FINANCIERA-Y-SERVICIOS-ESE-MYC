"""Reproduce los totales de control de los artefactos de referencia con la lógica del paquete."""
import pytest

from rips_auditoria.analisis import controles_referencia, parametros
from rips_auditoria.calculos import capita
from rips_auditoria.constantes import CONTROL
from rips_auditoria.referencias.dashboards import cargar_evento, cargar_final


def test_totales_de_control_del_dashboard_final():
    ref = cargar_final()
    t = ref.totales()
    assert ref.textos["total_capita_html"] == CONTROL["capita_total"] == 8_053_016_408
    # La suma de los 8 costos mensuales redondeados difiere en $1 del total publicado.
    assert abs(t["capita_total"] - CONTROL["capita_total"]) <= 1
    assert t["cups"] == CONTROL["cups"] == 748_058
    assert t["pacientes_unicos"] == CONTROL["pacientes_unicos"] == 114_923
    assert t["asistencias"] == CONTROL["asistencias"] == 213_814


def test_kpis_capita_reproducen_dashboard():
    ref = cargar_final()
    d = ref.detalle_mensual
    k = capita.kpis([r["costo"] for r in d], [r["pacientes"] for r in d], [r["asistencias"] for r in d],
                    ref.pgp["RIPS_CAP"], 4)
    assert round(k.promedio_mes) == CONTROL["capita_promedio_mes"]
    assert round(k.periodo_temprano / 1e6) == 3_420          # Cuatrimestre 1 $3.420 M
    assert round(k.periodo_reciente / 1e6) == 4_633          # Cuatrimestre 2 $4.633 M
    assert round(k.crecimiento_pct, 1) == 35.5               # ↑ +35,5 %
    assert round(k.costo_usuario_prom) == 69_781              # Costo/usuario
    assert round(k.costo_atencion_prom) == 11_130             # Costo/atención
    # Costo/usuario y costo/atención mensuales publicados en la tabla del dashboard
    for r, rc in zip(d, ref.pgp["RIPS_CAP"]):
        assert round(r["costo"] / r["pacientes"]) == r["costo_usuario"]
        assert round(r["costo"] / rc) == r["costo_atencion"]


def test_escenarios_y_pgp():
    c = controles_referencia()
    e = c["escenarios"]
    assert round(e["ESC.1 – CAPITA ACTUAL"]) == CONTROL["capita_promedio_mes"]
    assert round(e["ESC.2 – CAPITA AMPLIADA"]) == CONTROL["capita_ampliada_mes"] == 1_112_074_196
    assert round(e["ESC.3 – CAPITA DEMANDA REAL"]) == CONTROL["capita_ajustada_mes"] == 1_253_045_148
    assert round(e["PGP-2 UPC + EVENTO"]) == CONTROL["pgp2_mes"] == 3_045_623_529
    ref = cargar_final().escenarios_mes
    for clave, nombre in [("giro_sin_upc", "Giro directo sin UPC (vigente)"), ("giro_upc", "Giro + UPC 2026"),
                          ("actas_bogota", "Actas firmadas Bogotá"), ("pgp1_cap_evento", "PGP-1 Cápita vigente + EVENTO")]:
        assert round(e[nombre]) == ref[clave]


def test_pgp_mensual_igual_a_tabla_del_dashboard():
    ref = cargar_final()
    df = capita.pgp_mensual(ref.pgp["COSTO_FIJ"], ref.pgp["FACT_EV"], parametros())
    # El libro de origen redondea el giro por celda: se admite ±1 peso.
    for col, clave in (("pgp1_cap_vigente_evento", "P1"), ("pgp2_upc_evento", "P2"), ("pgp3_actas_evento", "P3")):
        assert all(abs(a - b) <= 1 for a, b in zip(df[col], ref.pgp[clave])), col


def test_regla_medicamentos_por_despacho_dashboard_evento():
    """adj = costNT − medC + despacho general (dashboard CAPITA·EVENTO)."""
    d = cargar_evento().datos
    adj = [c - m + g for c, m, g in zip(d["nt"]["costNT"], d["nt"]["medC"], d["med"]["gen_v"])]
    assert round(sum(d["nt"]["costNT"])) == 8_264_369_795
    assert round(sum(adj)) == 8_548_188_101
    # Mismos pacientes, CUPS y asistencias que el dashboard FINAL
    assert sum(r["cups"] for r in d["det"]) == CONTROL["cups"]
    assert sum(r["pac"] for r in d["det"]) == CONTROL["pacientes_unicos"]
    assert sum(r["asis"] for r in d["det"]) == CONTROL["asistencias"]


def test_grupos_final_mismos_nombres_y_participacion():
    ref = cargar_final()
    assert ref.grupos[0] == "PROMOCION Y MANTENIMIENTO DE LA SALUD"
    assert len(ref.grupos) == 10
    assert sum(ref.grupos_pct) == pytest.approx(100, abs=0.2)

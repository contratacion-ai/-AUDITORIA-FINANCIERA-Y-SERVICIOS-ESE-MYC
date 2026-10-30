"""Pruebas con el libro real INFORME DE SERVICIOS 2026 (no se versiona).

    RIPS_INFORME_XLSX=ruta/INFORME_DE_SERVICIOS_2026_BASE_DE_DATOS_ANALISIS.xlsx python -m pytest -q
"""
from pathlib import Path

import pandas as pd
import pytest

from rips_auditoria import maestros
from rips_auditoria.calculos import capita
from rips_auditoria.clasificacion import clasificar
from rips_auditoria.constantes import CONTROL, CONTROL_FINAL_HISTORICO, CONTROL_NT_MUNICIPIO
from rips_auditoria.referencias.dashboards import cargar_final
from tests.conftest import ARCHIVO_REAL_INFORME

pytestmark = pytest.mark.skipif(not ARCHIVO_REAL_INFORME, reason="Defina RIPS_INFORME_XLSX")


@pytest.fixture
def informe():
    return maestros.cargar("informe", Path(ARCHIVO_REAL_INFORME).read_bytes(), "INFORME.xlsx")


def test_maestros_y_totales_de_control_del_libro(informe):
    cat = maestros.leer("catalogo")
    assert len(cat) == 1551 and (cat.estado == "OFERTADO").sum() == 370
    nt = maestros.leer("nota_tecnica")
    tot = nt.groupby("municipio")["frecuencia_prevista"].sum()
    for m, v in CONTROL_NT_MUNICIPIO.items():
        assert tot[m] == pytest.approx(v, abs=0.01)
    c = {x["indicador"]: x for x in informe["conciliacion_control"]}
    for k in ("CUPS", "Pacientes únicos (suma mensual)", "Asistencias", "RIPS NT subsidiado – RIPS",
              "RIPS NT subsidiado – facturación", "RIPS NT contributivo – RIPS", "RIPS NT contributivo – facturación",
              "Facturación EVENTO (hoja FACTURACION EVENTO)", "Costo CAPITA sin medicamentos",
              "Costo CAPITA total ene–ago", "Medicamentos (grupo)", "CUMS", "Costo CAPITA promedio/mes"):
        assert c[k]["estado"] == "OK", k


def _servicios_desde_nt_costos():
    c = maestros.leer("nt_costos")
    c = c[c.frecuencia_rips > 0]
    med = c.grupo == "MEDICAMENTOS"
    return pd.DataFrame({
        "tipo_archivo": ["AM" if m else "AP" for m in med],
        "codigo": [h if m else k for m, h, k in zip(med, c.homologo, c.codigo)],   # AM llega con el CUM
        "cantidad": c.frecuencia_rips.values, "mes": [f"2026-{m:02d}" for m in c.mes], "municipio": "TAME",
        "descripcion": None, "finalidad": "1", "tipo_servicio_at": None, "es_episodio": False,
        "modalidad_declarada": "CAPITA", "tipo_codigo": ["CUMS" if m else "CUPS" for m in med]})


def test_motor_reproduce_libro_y_control(informe):
    nt = maestros.leer("nota_tecnica")
    s = capita.costear(clasificar(_servicios_desde_nt_costos(), maestros.leer("catalogo"), nt), nt)
    meses = sorted(s.mes.unique())
    g = capita.costos_grupo_mes(s, meses)
    # costo unitario NT × frecuencia RIPS = total oficial (libro vigente, medicamentos actuales)
    assert abs(g["acumulado"].sum() - CONTROL["capita_total"]) <= 1
    assert abs(g.loc["MEDICAMENTOS", "acumulado"] - CONTROL["medicamentos"]) <= 1
    # Histórico: con los medicamentos de la versión anterior se reproduce el dashboard FINAL
    fin = cargar_final()
    no_med = g.drop(index="MEDICAMENTOS")[meses].sum()
    med_final = [r["costo"] - v for r, v in zip(fin.detalle_mensual, no_med)]
    g2 = capita.costos_grupo_mes(s, meses, pd.DataFrame({"periodo": meses, "valor": med_final}))
    assert g2[meses].sum().round().tolist() == [r["costo"] for r in fin.detalle_mensual]
    assert abs(g2["acumulado"].sum() - CONTROL_FINAL_HISTORICO["capita_total"]) <= 1

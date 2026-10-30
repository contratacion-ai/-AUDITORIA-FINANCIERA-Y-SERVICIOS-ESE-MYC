"""Dashboard con estructura de los dashboards de referencia, contratos→modalidad y errores legibles."""
import shutil

import pytest
from fastapi.testclient import TestClient

from rips_auditoria import base, config
from rips_auditoria.analisis import analizar
from rips_auditoria.informes import dashboard
from rips_auditoria.ingesta.lote import procesar
from tests.conftest import TXT, zip_bytes


def _lote_sin_modalidad():
    z = zip_bytes({f"Hospital San Jose de Cravo Norte/08. Agosto/NUEVA SUBS/{t}000123.txt": v.replace("CTO-EVENTO-1", "02-01-09-00457-2023")
                   for t, v in TXT.items()})
    return procesar([("c.zip", z)])


def test_dashboard_conserva_estructura_y_retira_pgp():
    h = dashboard.generar_html(analizar([_lote_sin_modalidad()]))
    for pestaña in ("📈 Resumen", "🏷️ CAPITA Actual", "🔁 Cruce Nueva EPS", "📊 Frecuencias NT", "⚡ TOP 20 EVENTO",
                    "⚠️ Riesgo", "🔎 Hallazgos", "✅ Recomendaciones"):
        assert pestaña in h
    for retirado in ("Modelo PGP", "Simulador", "3 Escenarios", "Costos vs Pagos"):
        assert retirado not in h
    assert ".kc{" in h and "Chart.js v4.4.1" in h          # estilos del FINAL y Chart.js incluido (sin internet)
    assert "1000001" not in h and "PEREZ" not in h          # sin datos personales


@pytest.fixture
def cfg_tmp(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    shutil.copytree(config.DIR_CONFIG, cfg)
    monkeypatch.setattr(config, "DIR_CONFIG", cfg)
    config.red_prestadores.cache_clear()
    yield
    config.red_prestadores.cache_clear()


def test_regla_nueva_eps_sin_evento_es_capita_y_excepcion_por_contrato(cfg_tmp):
    l = _lote_sin_modalidad()                                    # carpeta "NUEVA SUBS": no dice EVENTO
    r = analizar([l])
    assert r.indicadores["evento_total"] == 0
    assert set(r.tablas["trazabilidad_clasificacion"]["modalidad"]) == {"CAPITA"}
    z = zip_bytes({f"Hospital Prueba/08. Agosto/NUEVA EPS SUB EVENTO/{t}000123.txt": v for t, v in TXT.items()})
    assert analizar([procesar([("c.zip", z)])]).indicadores["evento_total"] > 0     # dice EVENTO
    config.guardar_contratos({"02-01-09-00457-2023": "EVENTO"})                    # excepción por contrato
    assert analizar([l]).indicadores["evento_total"] > 0


def test_api_contratos_y_errores_legibles(cfg_tmp, tmp_path, monkeypatch):
    import api.app as m
    monkeypatch.setattr(m, "DIR_LOTES", tmp_path / "lotes")
    monkeypatch.setattr(m, "DIR_RES", tmp_path / "res")
    cli = TestClient(m.app, raise_server_exceptions=False)
    z = zip_bytes({f"Hospital Prueba/08. Agosto/NUEVA SUBS/{t}000123.txt": v for t, v in TXT.items()})
    cli.post("/api/lotes", files=[("archivos", ("c.zip", z, "application/zip"))])
    c = cli.get("/api/contratos").json()["contratos"]
    assert c[0]["contrato"] == "CTO-EVENTO-1" and c[0]["facturas"] == 2
    assert cli.put("/api/contratos", json={"mapa": {"CTO-EVENTO-1": "EVENTO"}}).json()["contratos"][0]["modalidad"] == "EVENTO"
    an = cli.post("/api/analisis", json={}).json()
    d = cli.get(f"/api/analisis/{an['id']}/dashboard.html")
    assert d.status_code == 200 and "Cruce con Nueva EPS" in d.text
    monkeypatch.setattr(m, "procesar", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falla de prueba")))
    e = cli.post("/api/lotes", files=[("archivos", ("c.zip", z, "application/zip"))])
    assert e.status_code == 500 and "falla de prueba" in e.json()["detail"]
    assert any("falla de prueba" in x for x in cli.get("/api/registro").json()["lineas"])

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TXT, zip_bytes


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    import api.app as m
    monkeypatch.setattr(m, "DIR_LOTES", tmp_path / "lotes")
    monkeypatch.setattr(m, "DIR_RES", tmp_path / "res")
    return TestClient(m.app)


def test_flujo_carga_correccion_analisis_informes(cliente):
    assert cliente.get("/").status_code == 200
    z = zip_bytes({f"Hospital Prueba/08. Agosto/NUEVA EPS EVENTO/{t}000123.txt": v for t, v in TXT.items()})
    r = cliente.post("/api/lotes", files=[("archivos", ("carga.zip", z, "application/zip"))])
    assert r.status_code == 200, r.text
    lote = r.json()
    assert lote["registros"]["AF"] == 2
    env = lote["remisiones"][0]["envio_id"]
    r = cliente.patch(f"/api/lotes/{lote['id']}/remisiones", json=[{"envio_id": env, "municipio": "TAME"}])
    assert r.json()["registros_actualizados"] > 0
    assert cliente.get(f"/api/lotes/{lote['id']}/incidencias.xlsx").status_code == 200
    r = cliente.post("/api/analisis", json={"lotes": [lote["id"]], "solo_eps_objetivo": True})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert cliente.get(f"/api/analisis/{rid}/informe.xlsx").content[:2] == b"PK"
    assert cliente.get(f"/api/analisis/{rid}/informe.pdf").content[:4] == b"%PDF"
    est = cliente.get(f"/api/analisis/{rid}/estado.json").content
    r = cliente.post("/api/analisis/restaurar", files=[("archivo", ("e.json", est, "application/json"))])
    assert r.status_code == 200


def test_carpeta_con_rutas_relativas(cliente):
    files, rutas = [], []
    for t, v in TXT.items():
        files.append(("archivos", (f"{t}000123.txt", v.encode(), "text/plain")))
        rutas.append(("rutas", f"Hospital San Jose de Cravo Norte/03. Marzo/NUEVA EPS/{t}000123.txt"))
    r = cliente.post("/api/lotes", files=files, data={"rutas": [x[1] for x in rutas]})
    rem = r.json()["remisiones"][0]
    assert rem["hospital"] == "Hospital San José de Cravo Norte" and rem["periodo"] == "2026-03"


def test_referencia_y_maestros(cliente):
    ref = cliente.get("/api/referencia").json()
    assert ref["control"]["capita_total"] == 8_264_369_795
    assert ref["historico_dashboard_final"]["capita_total"] == 8_053_016_408
    assert cliente.get("/api/maestros").status_code == 200
    assert cliente.get("/api/maestros/plantillas/tarifario.csv").text.startswith("codigo")
    assert cliente.get("/referencias/DASHBOARD_CAPITA_PGP_ESE_MYC_2026_FINAL.html").status_code == 200


def test_base_y_drive_por_api(cliente, tmp_path):
    z = zip_bytes({f"Hospital Prueba/08. Agosto/NUEVA EPS EVENTO/{t}000123.txt": v for t, v in TXT.items()})
    r = cliente.post("/api/lotes", files=[("archivos", ("carga.zip", z, "application/zip"))]).json()
    assert len(r["base"]["nuevas"]) == 1
    r = cliente.post("/api/lotes", files=[("archivos", ("carga.zip", z, "application/zip"))]).json()
    assert len(r["base"]["reemplazadas"]) == 1                    # recargar no duplica
    b = cliente.get("/api/base").json()
    assert b["resumen"]["particiones"] == 1 and b["filtros"]["periodos"] == ["2026-08"]
    an = cliente.post("/api/analisis", json={"periodos": ["2026-08"]})
    assert an.status_code == 200 and "frecuencias_nt_municipio_mes" in an.json()
    drive = tmp_path / "Drive" / "Hospital Dos" / "07. Julio" / "NUEVA EPS"
    drive.mkdir(parents=True)
    for t, v in TXT.items():
        (drive / f"{t}000123.txt").write_text(v.replace("FT000", "FT070").replace("/08/2026", "/07/2026"), encoding="utf-8")
    assert cliente.post("/api/fuentes", json={"ruta": str(tmp_path / "Drive" / "Hospital Dos")}).status_code == 200
    s = cliente.post("/api/fuentes/sincronizar", json={"forzar": False}).json()
    assert len(s["procesadas"]) == 1
    assert cliente.get("/api/base").json()["resumen"]["particiones"] == 2
    clave = cliente.get("/api/base").json()["particiones"][0]["clave"]
    assert cliente.delete(f"/api/base?clave={clave}").json()["particiones_eliminadas"] == 1
    assert cliente.post("/api/base/vaciar", json={"confirmar": True}).json()["particiones_eliminadas"] == 1

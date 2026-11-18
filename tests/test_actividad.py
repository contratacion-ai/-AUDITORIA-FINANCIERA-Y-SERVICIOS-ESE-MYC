"""Pestaña «Facturación por actividad»: datos propios (act_det), cruce integral por factura y por código, reglas y convivencia con la facturación por ámbito."""
import os, sys, tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("AUDIT_DATA_DIR", tempfile.mkdtemp(prefix="auditoria_test_"))

from fastapi.testclient import TestClient      # noqa: E402
import generar_datos_demo as G                 # noqa: E402
from app import db                             # noqa: E402
from app.main import app                       # noqa: E402

cli = TestClient(app)
CARPETA = tempfile.mkdtemp(prefix="actividad_")
RUTAS, ESP = G.actividad_y_rips(CARPETA)
DEMO = G.generar(os.path.join(CARPETA, "demo"))

@pytest.fixture(scope="module", autouse=True)
def base_propia():
    previo = db.DB_PATH; db.DB_PATH = os.path.join(CARPETA, "act.db"); db.init()
    yield
    db.DB_PATH = previo

def _subir(tipo, ruta):
    with open(ruta, "rb") as f:
        assert cli.post(f"/api/cargas/{tipo}", files={"file": (os.path.basename(ruta), f)}).status_code == 200
    c = cli.get("/api/cargas").json()["filas"][0]; assert c["estado"] == "ok", c
    return c

def _n(t, mes="2026-01"): return int(db.df(f"SELECT COUNT(*) n FROM {t} WHERE mes=?", (mes,)).n.iloc[0])

def test_actividad_no_pisa_la_facturacion_por_ambito():
    _subir("facturacion", DEMO["facturacion_ambito_demo.xlsx"])
    antes = _n("fact")
    c = _subir("actividad", RUTAS["ENERO_actividad.xlsx"])
    assert c["tipo"] == "actividad" and "ya tiene facturación por ámbito" in c["mensaje"]
    assert _n("fact") == antes and _n("act_det") > 0

def test_cruce_integral_por_factura():
    _subir("rips_fev", RUTAS["rips_json.zip"])
    f = {x["factura"]: x["estado"] for x in cli.get("/api/actividad/facturas?mes=2026-01").json()["filas"]}
    assert "SIN RIPS" in f[ESP["sin_rips"]]
    assert "RIPS ≠ VALOR FACTURA" in f[ESP["cantidad"]]
    assert "Σ LÍNEAS ≠ VALOR FACTURA" in f[ESP["r10"]]
    assert "PACIENTE DIFERENTE AL RIPS" in f[ESP["paciente"]]
    assert "XML (FEV) ≠ VALOR FACTURA" in f[ESP["xml_difiere"]]
    assert "NO ESTÁ EN FACTURACIÓN POR ÁMBITO" in f["FA00000"]           # las facturas demo por ámbito son otras (FE…)
    r = cli.get("/api/actividad/resumen?mes=2026-01").json()
    assert r["kpis"]["facturas"] == 30 and r["kpis"]["con_rips"] == 29 and r["kpis"]["con_xml"] == 29

def test_por_codigo_reglas_y_detalle():
    maestro = os.path.join(CARPETA, "MAESTRO.xlsx"); G.maestro_medicamentos(maestro)
    _subir("maestro_med", maestro)                                               # homologa el CUM del medicamento demo
    s = cli.get("/api/actividad/servicios?mes=2026-01").json()["filas"]
    assert s and any("CANTIDAD DIFIERE" in x["estado"] for x in s if x["factura"] == ESP["cantidad"])
    assert cli.get("/api/actividad/reglas?mes=2026-01").status_code == 200
    d = cli.get("/api/actividad/detalle?factura=FA00000").json()
    assert d["total"] >= 3 and {x["factura"] for x in d["filas"]} == {"FA00000"}
    a = cli.get("/api/actividad/ambito?mes=2026-01").json(); assert a["filas"]
    for u in ("/api/actividad/medicamentos?mes=2026-01", "/api/gerencial?mes=2026-01", "/api/export?mes=2026-01"): assert cli.get(u).status_code == 200, u

def test_eliminar_carga_de_actividad():
    c = next(x for x in cli.get("/api/cargas").json()["filas"] if x["tipo"] == "actividad" and x["estado"] == "ok")
    assert cli.delete(f"/api/cargas/{c['id']}").status_code == 200
    assert _n("act_det") == 0 and _n("fact") > 0

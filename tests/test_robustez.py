"""Todas las vistas responden (sin error 500) con la base vacía y con solo RIPS cargados (sin facturación de 360)."""
import os, sys, tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("AUDIT_DATA_DIR", tempfile.mkdtemp(prefix="auditoria_test_"))

from fastapi.testclient import TestClient      # noqa: E402
import generar_datos_demo as G                 # noqa: E402
from app import db                             # noqa: E402
from app.main import app                       # noqa: E402

cli = TestClient(app, raise_server_exceptions=False)
CARPETA = tempfile.mkdtemp(prefix="robustez_")
URLS = ["/api/gerencial", "/api/resumen", "/api/comparativos", "/api/resumen/empresas", "/api/conciliacion/fact-rips", "/api/conciliacion/tri-eje",
        "/api/conciliacion/radicacion", "/api/conciliacion/contratos", "/api/conciliacion/hallazgos", "/api/conciliacion/validacion-cruce",
        "/api/conciliacion/notas-tecnicas", "/api/costos/resumen", "/api/costos/cruce", "/api/informes/3_servicio", "/api/facturas/resumen",
        "/api/facturas/cruce", "/api/facturas/detalle", "/api/medicamentos/resumen", "/api/medicamentos/agrupado", "/api/medicamentos/rips",
        "/api/medicamentos/homologacion", "/api/medicamentos/mes-a-mes", "/api/medicamentos/farmacia", "/api/medicamentos/sede",
        "/api/rips-fev/resumen", "/api/rips-fev/facturas", "/api/rips-fev/servicios", "/api/rips-fev/validacion", "/api/estados/radicacion",
        "/api/comparativo/mensual", "/api/export", "/api/filtros", "/api/meses"]

@pytest.fixture(scope="module", autouse=True)
def base_propia():
    previo = db.DB_PATH; db.DB_PATH = os.path.join(CARPETA, "robustez.db"); db.init()
    yield
    db.DB_PATH = previo

def _barrido():
    return [(u, r.status_code) for m in ("", "?mes=2026-01") for u in URLS if (r := cli.get(u + m)).status_code != 200]

def test_base_vacia():
    assert _barrido() == []

def test_solo_rips():
    rutas, _ = G.actividad_y_rips(os.path.join(CARPETA, "demo"))
    with open(rutas["rips_json.zip"], "rb") as f:
        assert cli.post("/api/cargas/rips_fev", files={"file": ("rips.zip", f)}).status_code == 200
    assert cli.get("/api/cargas").json()["filas"][0]["estado"] == "ok"
    assert _barrido() == []

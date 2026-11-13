"""Maestro de medicamentos 360 con CUM: homologación automática por genérico (no por número), validación y cruce con RIPS sin homologar a mano."""
import os, sys, tempfile
import openpyxl, pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("AUDIT_DATA_DIR", tempfile.mkdtemp(prefix="auditoria_test_"))

from fastapi.testclient import TestClient      # noqa: E402
import generar_datos_demo as G                 # noqa: E402
from app import db                             # noqa: E402
from app.loaders import cum_norm               # noqa: E402
from app.main import app                       # noqa: E402

cli = TestClient(app)
CARPETA = tempfile.mkdtemp(prefix="maestro_")
RUTAS, ESP = G.actividad_y_rips(CARPETA)
MAESTRO = os.path.join(CARPETA, "MAESTRO.xlsx"); G.maestro_medicamentos(MAESTRO)

@pytest.fixture(scope="module", autouse=True)
def base_propia():
    previo = db.DB_PATH; db.DB_PATH = os.path.join(CARPETA, "maestro.db"); db.init()
    yield
    db.DB_PATH = previo

def _subir(tipo, ruta):
    with open(ruta, "rb") as f:
        assert cli.post(f"/api/cargas/{tipo}", files={"file": (os.path.basename(ruta), f)}).status_code == 200
    c = cli.get("/api/cargas").json()["filas"][0]; assert c["estado"] == "ok", c
    return c

def test_cum_norm():
    assert cum_norm("19935124-02") == "19935124-2" and cum_norm("053560-06") == "53560-6" and cum_norm("G-0052") == "G-0052" and cum_norm("890201") == "890201"

def test_maestro_homologa_por_generico():
    _subir("actividad", RUTAS["ENERO_actividad.xlsx"])
    c = _subir("maestro_med", MAESTRO)
    assert "2 CUM" in c["mensaje"]                                                  # dos marcas del mismo genérico
    h = db.df("SELECT * FROM hom_codigo")
    assert set(h.codigo_rips) == {"19942561-6", "20055558-8"} and set(h.codigo_360) == {"G-0052"} and set(h.fuente) == {"maestro:EXACTO"}
    v = {x["codigo"]: x for x in cli.get("/api/maestro/validacion?mes=2026-01").json()["filas"]}
    assert v["G-0052"]["estado"] == "OK" and v["G-0052"]["n_cum"] == 2
    assert v["D-0082"]["estado"].startswith("INFO: INSUMO")
    k = cli.get("/api/maestro/calidad").json()["kpis"]; assert k["productos"] == 3 and k["sin_cum"] == 1

def test_rips_cruza_sin_homologacion_manual():
    _subir("rips_fev", RUTAS["rips_json.zip"])
    s = cli.get("/api/rips-fev/servicios?mes=2026-01").json()["filas"]
    assert not any(x["estado"] == "CÓDIGO RIPS SIN HOMOLOGAR" for x in s)
    assert any("CANTIDAD DIFIERE" in x["estado"] for x in s if x["factura"] == ESP["cantidad"])
    r = cli.get("/api/maestro/rips?mes=2026-01").json()["filas"]
    assert r and all(x["estado"] == "OK" for x in r)

def test_homologacion_manual_se_respeta_y_recarga():
    ruta = os.path.join(CARPETA, "cum.xlsx"); wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["CODIGO_RIPS", "CODIGO_SOFTWARE360", "DESCRIPCION"]); ws.append(["20055558-08", "G-9999", "MANUAL"]); wb.save(ruta)
    _subir("codigos", ruta)
    _subir("maestro_med", MAESTRO)                                                  # recargar el maestro no pisa lo manual
    h = db.df("SELECT * FROM hom_codigo").set_index("codigo_rips")
    assert h.loc["20055558-8", "codigo_360"] == "G-9999" and h.loc["20055558-8", "fuente"] == "manual"
    assert cli.get("/api/export?mes=2026-01").status_code == 200

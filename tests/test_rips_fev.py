"""Facturación por actividad (360, sin columnas de EPS) + RIPS en JSON y TXT dentro de .zip: carga, cruces y reglas de estructura."""
import os, sys, tempfile
import openpyxl, pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("AUDIT_DATA_DIR", tempfile.mkdtemp(prefix="auditoria_test_"))

from fastapi.testclient import TestClient      # noqa: E402
import generar_datos_demo as G                 # noqa: E402
from app import db                             # noqa: E402
from app.main import app                       # noqa: E402

cli = TestClient(app)
CARPETA = tempfile.mkdtemp(prefix="ripsfev_")
RUTAS, ESP = G.actividad_y_rips(CARPETA)

@pytest.fixture(scope="module", autouse=True)
def base_propia():
    previo = db.DB_PATH; db.DB_PATH = os.path.join(CARPETA, "rips.db"); db.init()
    yield
    db.DB_PATH = previo

def _subir(tipo, ruta):
    with open(ruta, "rb") as f:
        r = cli.post(f"/api/cargas/{tipo}", files={"file": (os.path.basename(ruta), f, "application/octet-stream")})
    assert r.status_code == 200, r.text
    c = cli.get("/api/cargas").json()["filas"][0]
    assert c["estado"] == "ok", c
    return r.json(), c

def _estado(filas, factura): return next(x["estado"] for x in filas if x["factura"] == factura)

def test_facturacion_por_actividad():
    r, c = _subir("actividad", RUTAS["ENERO_actividad.xlsx"])
    assert c["tipo"] == "facturacion" and c["mes"] == "2026-01"                     # hoja sin mes: se toma de FECHA DE FACTURACION
    eps = {x["empresa"] for x in cli.get("/api/resumen/empresas?mes=2026-01").json()["empresas"]["filas"]}
    assert eps == {"NUEVA EMPRESA PROMOTORA DE SALUD", "ENTIDAD PROMOTORA DE SALUD SANITAS S A S"}   # deducida del contrato
    h = cli.get("/api/conciliacion/hallazgos?mes=2026-01").json()["filas"]
    assert any(x["regla"] == "R10" for x in h)                                      # Σ líneas ≠ VALOR FACTURA sembrado
    k = cli.get("/api/medicamentos/resumen?mes=2026-01").json()["kpis"]
    assert k["medicamentos"] > 0 and k["insumos"] > 0

def test_rips_json_zip():
    r, c = _subir("rips_fev", RUTAS["rips_json.zip"])
    assert c["tipo"] == "rips_fev" and "29 facturas" in c["mensaje"]
    f = cli.get("/api/rips-fev/facturas?mes=2026-01").json()["filas"]
    assert _estado(f, ESP["sin_rips"]) == "SIN RIPS"
    assert "PACIENTE DIFERENTE" in _estado(f, ESP["paciente"])
    for k in ("cantidad", "cero", "r10"): assert "VALOR DIFIERE" in _estado(f, ESP[k]), k
    assert _estado(f, "FA00000") == "OK"
    s = cli.get("/api/rips-fev/servicios?mes=2026-01").json()["filas"]
    assert any(x["estado"] == "CÓDIGO RIPS SIN HOMOLOGAR" for x in s)             # CUM del medicamento aún sin homologar
    v = {x["regla"]: x for x in cli.get("/api/rips-fev/validacion?mes=2026-01").json()["filas"]}
    assert v["RV03"]["facturas"] == 1 and v["RV02"]["facturas"] == 1
    det = cli.get("/api/rips-fev/validacion?mes=2026-01&regla=RV03").json()["filas"]
    assert {x["factura"] for x in det} == {ESP["sin_dx"]}

def test_homologacion_permite_cruzar_medicamentos():
    ruta = os.path.join(CARPETA, "cum.xlsx"); wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["CODIGO_RIPS", "CODIGO_SOFTWARE360", "DESCRIPCION"]); ws.append(["19942561-6", "G-0052", "CLORURO"]); wb.save(ruta)
    _subir("codigos", ruta)
    s = cli.get("/api/rips-fev/servicios?mes=2026-01").json()["filas"]
    assert not any(x["estado"] == "CÓDIGO RIPS SIN HOMOLOGAR" for x in s)
    assert any("CANTIDAD DIFIERE" in x["estado"] for x in s if x["factura"] == ESP["cantidad"])

def test_rips_txt_zip_detectado_desde_rips():
    r, c = _subir("rips", RUTAS["rips_txt.zip"])                                    # subido como «RIPS»: se detecta TXT
    assert c["tipo"] == "rips_fev" and r["nota"]
    f = cli.get("/api/rips-fev/facturas?mes=2026-01").json()["filas"]
    assert _estado(f, ESP["sin_rips"]) == "SIN RIPS" and "PACIENTE DIFERENTE" in _estado(f, ESP["paciente"])
    assert _estado(f, "FA00000") == "OK"
    assert cli.get("/api/rips-fev/resumen?mes=2026-01").json()["kpis"]["txt"] > 0
    # el TXT reemplazó al JSON del mismo mes (no se acumula)
    assert cli.get("/api/rips-fev/resumen?mes=2026-01").json()["kpis"]["json"] == 0

def test_un_json_suelto_y_conciliacion():
    r, c = _subir("rips", RUTAS["una_factura.json"])
    assert c["tipo"] == "rips_fev"
    assert cli.get("/api/conciliacion/fact-rips?mes=2026-01&nivel=eps").status_code == 200
    assert cli.get("/api/export?mes=2026-01").status_code == 200

def test_mes_a_mes_y_version():
    d = cli.get("/api/medicamentos/mes-a-mes?nivel=codigo&metrica=usuarios").json()
    assert "2026-01" in d["columnas"] and d["filas"]
    h = cli.get("/api/health").json(); assert h["version"] == db.VERSION
    assert cli.post("/api/apagar").json()["ok"]                                     # desde el cliente de prueba no cierra el proceso

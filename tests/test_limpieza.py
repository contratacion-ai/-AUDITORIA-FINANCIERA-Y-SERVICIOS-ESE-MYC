"""Limpieza de cargas: recargar un mes reemplaza (no acumula), eliminar una carga, limpiar un mes o todo, y borrado del archivo subido."""
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
CARPETA = tempfile.mkdtemp(prefix="limpieza_")

@pytest.fixture(scope="module", autouse=True)
def base_propia():
    previo = db.DB_PATH; db.DB_PATH = os.path.join(CARPETA, "limpieza.db"); db.init()
    yield
    db.DB_PATH = previo

def _subir(tipo, ruta):
    with open(ruta, "rb") as f:
        r = cli.post(f"/api/cargas/{tipo}", files={"file": (os.path.basename(ruta), f, "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()["cargas"]

def _n(tabla, mes="2026-01"): return int(db.df(f"SELECT COUNT(*) n FROM {tabla} WHERE mes=?", (mes,)).n.iloc[0])
def _subidos(): return [f for f in os.listdir(db.UPLOADS)]

def test_recarga_reemplaza_y_borra_archivo():
    rutas = G.generar(os.path.join(CARPETA, "demo"))
    antes = set(_subidos())
    _subir("facturacion", rutas["facturacion_ambito_demo.xlsx"])
    n1 = _n("fact"), _n("med_det")
    _subir("facturacion", rutas["facturacion_ambito_demo.xlsx"])          # mismo archivo otra vez: no se duplica
    assert (_n("fact"), _n("med_det")) == n1
    estados = {c["id"]: c["estado"] for c in cli.get("/api/cargas").json()["filas"]}
    assert sorted(estados.values()) == ["ok", "ok", "reemplazada", "reemplazada"]   # enero y febrero de la primera carga
    assert set(_subidos()) == antes                                        # el archivo subido se borró al procesarlo

def test_eliminar_carga():
    c = next(c for c in cli.get("/api/cargas").json()["filas"] if c["estado"] == "ok" and c["mes"] == "2026-02")
    r = cli.delete(f"/api/cargas/{c['id']}").json(); assert r["registros"] > 0
    assert _n("fact", "2026-02") == 0 and _n("fact", "2026-01") > 0
    assert cli.delete("/api/cargas/99999").status_code == 404

def test_limpiar_mes_y_todo():
    rutas = G.generar(os.path.join(CARPETA, "demo2"))
    _subir("farmacia", rutas["farmacia_demo.xlsx"])
    r = cli.post("/api/limpiar", data={"mes": "2026-01", "tipo": "facturacion"}).json()
    assert r["cargas"] >= 1 and _n("fact") == 0 and _n("farmacia") > 0     # solo se borró la facturación de enero
    r = cli.post("/api/limpiar", data={"todo": "true"}).json()
    assert _n("farmacia") == 0 and _n("farmacia", "2026-02") == 0
    assert cli.post("/api/limpiar", data={}).status_code == 422

def test_no_borra_archivos_fuera_de_la_carpeta_de_cargas():
    from app import processing as P
    ruta = os.path.join(CARPETA, "mio.xlsx"); open(ruta, "w").write("x")
    P.borrar_archivo(ruta)
    assert os.path.exists(ruta)

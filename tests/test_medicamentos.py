"""Medicamentos desde el reporte de 360 filtrado (hoja «ENERO» sin año): carga, agrupaciones, cruce con RIPS y homologación CUM.
Usa una base de datos propia para no mezclar el mes de enero con las pruebas de test_app.py."""
import io, os, sys, tempfile
import openpyxl, pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("AUDIT_DATA_DIR", tempfile.mkdtemp(prefix="auditoria_test_"))

from fastapi.testclient import TestClient      # noqa: E402
import generar_datos_demo as G                 # noqa: E402
from app import db                             # noqa: E402
from app.main import app                       # noqa: E402

cli = TestClient(app)

@pytest.fixture(scope="module")
def datos():
    previo = db.DB_PATH; carpeta = tempfile.mkdtemp(prefix="meds_")
    db.DB_PATH = os.path.join(carpeta, "meds.db"); db.init()
    rutas = G.generar(os.path.join(carpeta, "demo"))
    lineas = G.medicamentos_360(rutas["medicamentos_360_demo.xlsx"])     # mismas líneas (semilla fija) para calcular lo esperado
    yield rutas, lineas
    db.DB_PATH = previo

def _subir(tipo, ruta):
    with open(ruta, "rb") as f:
        r = cli.post(f"/api/cargas/{tipo}", files={"file": (os.path.basename(ruta), f, "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()

def test_reporte_360_cargado_como_farmacia_se_procesa_como_facturacion(datos):
    rutas, lineas = datos
    r = _subir("farmacia", rutas["medicamentos_360_demo.xlsx"])
    assert r["nota"] and r["cargas"][0]["mes"] == "2026-01"              # hoja «ENERO» sin año: el año sale de FECHA DE FACTURACION
    c = cli.get("/api/cargas").json()["filas"][0]
    assert c["tipo"] == "facturacion" and c["estado"] == "ok", c

def test_agrupaciones_cuadran(datos):
    rutas, lineas = datos
    total = sum(l[4] for l in lineas)
    for nivel in ("contrato", "codigo", "servicio", "sede", "eps", "diagnostico", "atc", "usuario"):
        d = cli.get(f"/api/medicamentos/agrupado?mes=2026-01&nivel={nivel}").json()
        assert abs(sum(x["valor"] for x in d["filas"]) - total) < 1, nivel
    usuarios = len({r[3] for r in openpyxl.load_workbook(rutas["medicamentos_360_demo.xlsx"], read_only=True)["ENERO"].iter_rows(min_row=2, values_only=True)})
    k = cli.get("/api/medicamentos/resumen?mes=2026-01").json()["detalle"]
    assert k["usuarios"] == usuarios and k["codigos_360"] == 4 and k["sin_cum"] == 4
    cod = cli.get("/api/medicamentos/agrupado?mes=2026-01&nivel=codigo").json()["filas"]
    assert all(x["tipo_cod_medicamento"] == "ATC" and x["grupo_atc"] != "SIN ATC" for x in cod)
    ct = cli.get("/api/medicamentos/agrupado?mes=2026-01&nivel=contrato").json()["filas"]
    for x in ct:                                                              # sin PARAM CONTRATO: se deduce del nombre del contrato
        assert x["modalidad"] == next((m for m in ("CAPITA", "EVENTO") if m in x["contrato"].split()), "SIN DATO"), x

def test_cruce_rips_y_homologacion(datos):
    rutas, lineas = datos
    _subir("rips", rutas["rips_medicamentos_demo.xlsx"]); _subir("codigos", rutas["homologacion_codigos_demo.xlsx"])
    assert all(c["estado"] == "ok" for c in cli.get("/api/cargas").json()["filas"])
    cr = cli.get("/api/medicamentos/rips?mes=2026-01").json()["filas"]
    est = lambda e: sum(1 for x in cr if x["estado"] == e)
    hom = lambda g: g != "G-0002"
    en_rips = {l[0] for i, l in enumerate(lineas) if i % 11 != 5}            # facturas con al menos una línea en el RIPS
    esperado_sin = sum(1 for i, l in enumerate(lineas) if (i % 11 == 5 or not hom(l[1])) and l[0] in en_rips)
    assert est("FACTURA SIN RIPS") == sum(1 for l in lineas if l[0] not in en_rips)
    esperado_cant = sum(1 for i, l in enumerate(lineas) if i % 11 != 5 and i % 13 == 7 and hom(l[1]))
    esperado_cum = sum(1 for i, l in enumerate(lineas) if i % 11 != 5 and not hom(l[1]))
    assert est("MEDICAMENTO FACTURADO SIN RIPS") == esperado_sin
    assert est("CANTIDAD DIFIERE") == esperado_cant
    assert est("CUM DEL RIPS SIN HOMOLOGAR") == esperado_cum
    assert est("OK") > 0
    h = cli.get("/api/medicamentos/homologacion?mes=2026-01").json()["filas"]
    sin = {x["codigo"] for x in h if x["estado"].startswith("SIN CUM")}
    assert sin == {"G-0002"}
    assert any(x["origen"] == "RIPS" and x["codigo_rips"] == "19900001-2" for x in h)
    r = cli.get("/api/medicamentos/homologacion/plantilla?mes=2026-01"); assert r.status_code == 200
    ws = openpyxl.load_workbook(io.BytesIO(r.content))["PLANTILLA"]
    assert ws["A4"].value == "CODIGO_RIPS" and ws.max_row >= 6

def test_excel_con_hojas_de_medicamentos(datos):
    r = cli.get("/api/export?mes=2026-01"); assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True)
    for h in ("MED_Contrato", "MED_Codigo_360_ATC_CUM", "MED_Servicio", "MED_Usuario", "MED_Cruce_RIPS", "MED_Homologacion_CUM"):
        assert h in wb.sheetnames, h

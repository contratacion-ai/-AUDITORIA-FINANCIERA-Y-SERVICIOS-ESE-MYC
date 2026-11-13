"""La misma facturación de medicamentos cargada en distintos formatos (CSV, ODS, XLS-HTML, ZIP, 7Z, RAR, TAR.GZ) da el mismo resultado."""
import csv, os, shutil, subprocess, sys, tarfile, tempfile, zipfile
from datetime import date, datetime
import openpyxl, pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("AUDIT_DATA_DIR", tempfile.mkdtemp(prefix="auditoria_test_"))

import pandas as pd                             # noqa: E402
from fastapi.testclient import TestClient      # noqa: E402
import generar_datos_demo as G                 # noqa: E402
from app import db                             # noqa: E402
from app.main import app                       # noqa: E402

cli = TestClient(app)
CARPETA = tempfile.mkdtemp(prefix="formatos_")
XLSX = os.path.join(CARPETA, "ENERO_MEDICAMENTOS.xlsx")
LINEAS = G.medicamentos_360(XLSX)
TOTAL = sum(l[4] for l in LINEAS)

def _filas():
    ws = openpyxl.load_workbook(XLSX, read_only=True)["ENERO"]
    return [list(r) for r in ws.iter_rows(values_only=True)]

def _csv(ruta):
    """CSV como lo exporta Excel en Colombia: «;», Windows-1252, fechas dd/mm/aaaa y decimales con coma; nombre sin mes."""
    with open(ruta, "w", newline="", encoding="cp1252") as f:
        w = csv.writer(f, delimiter=";")
        for i, r in enumerate(_filas()):
            w.writerow([(v.strftime("%d/%m/%Y") if isinstance(v, (date, datetime)) else
                         (f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if isinstance(v, (int, float)) and i and j in (16, 17) else v))
                        for j, v in enumerate(r)])

def _df():
    f = _filas(); return pd.DataFrame(f[1:], columns=f[0])

def _ods(ruta): _df().to_excel(ruta, sheet_name="ENERO", index=False, engine="odf")

def _html_xls(ruta):
    with open(ruta, "w", encoding="utf-8") as f: f.write("<html><body>" + _df().to_html(index=False) + "</body></html>")

def _zip(ruta):
    with zipfile.ZipFile(ruta, "w") as z: z.write(XLSX, "reportes/ENERO_MEDICAMENTOS.xlsx")

def _7z(ruta):
    import py7zr
    with py7zr.SevenZipFile(ruta, "w") as z: z.write(XLSX, "ENERO_MEDICAMENTOS.xlsx")

def _rar(ruta): subprocess.run(["rar", "a", "-ep", "-idq", ruta, XLSX], check=True)

def _tgz(ruta):
    with tarfile.open(ruta, "w:gz") as t: t.add(XLSX, "ENERO_MEDICAMENTOS.xlsx")

CASOS = [("medicamentos.csv", _csv), ("ENERO.ods", _ods), ("ENERO_360.xls", _html_xls), ("meds.zip", _zip), ("meds.7z", _7z),
         pytest.param("meds.rar", _rar, marks=pytest.mark.skipif(not shutil.which("rar"), reason="sin el programa rar para crear el archivo de prueba")),
         ("meds.tar.gz", _tgz)]

@pytest.fixture(scope="module", autouse=True)
def base_propia():
    previo = db.DB_PATH; db.DB_PATH = os.path.join(CARPETA, "formatos.db"); db.init()
    yield
    db.DB_PATH = previo

@pytest.mark.parametrize("nombre,crear", CASOS)
def test_formato(nombre, crear):
    ruta = os.path.join(CARPETA, nombre); crear(ruta)
    with open(ruta, "rb") as f:
        r = cli.post("/api/cargas/farmacia", files={"file": (nombre, f, "application/octet-stream")})   # también se reconoce desde «Farmacia»
    assert r.status_code == 200, r.text
    assert r.json()["cargas"][0]["mes"] == "2026-01"
    c = cli.get("/api/cargas").json()["filas"][0]
    assert c["tipo"] == "facturacion" and c["estado"] == "ok", c
    k = cli.get("/api/medicamentos/resumen?mes=2026-01").json()
    assert abs(k["kpis"]["valor"] - TOTAL) < 1 and k["detalle"]["codigos_360"] == 4, nombre

def test_formato_no_soportado():
    r = cli.post("/api/cargas/facturacion", files={"file": ("informe.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 422 and ".rar" in r.json()["detail"]

def test_comprimido_vacio():
    ruta = os.path.join(CARPETA, "vacio.zip")
    with zipfile.ZipFile(ruta, "w") as z: z.writestr("leeme.pdf", b"x")
    with open(ruta, "rb") as f:
        r = cli.post("/api/cargas/facturacion", files={"file": ("vacio.zip", f, "application/zip")})
    assert r.status_code == 422 and "no contiene" in r.json()["detail"]

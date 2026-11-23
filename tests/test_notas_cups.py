"""Notas técnicas en formato nativo (Sanitas por municipio, Nueva EPS estudio de servicios, Coosalud matriz PYM + servicios) y la pestaña
«CUPS mes a mes · notas técnicas» (informe por empresa, cruce RIPS ↔ ámbito ↔ actividad y ejecución de la nota)."""
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
CARPETA = tempfile.mkdtemp(prefix="notas_")
RUTAS, ESP = G.actividad_y_rips(CARPETA)

@pytest.fixture(scope="module", autouse=True)
def base_propia():
    previo = db.DB_PATH; db.DB_PATH = os.path.join(CARPETA, "nt.db"); db.init()
    yield
    db.DB_PATH = previo

def _subir(tipo, ruta):
    with open(ruta, "rb") as f:
        r = cli.post(f"/api/cargas/{tipo}", files={"file": (os.path.basename(ruta), f)})
    assert r.status_code == 200, r.text
    c = cli.get("/api/cargas").json()["filas"][0]; assert c["estado"] == "ok", c
    return c

def _sanitas(ruta):
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    for mun, cont in (("TAME", "IBUGCU1529"), ("FORTUL", "IBUGCU1527")):
        ws = wb.create_sheet(f"FORMATO CAPITA {mun}")
        ws.append([]); ws.append(["NOTA TÉCNICA - Atención capitada"]); ws.append([])
        ws.append(["CONTRATANTE:", "ENTIDAD PROMOTORA DE SALUD SANITAS S.A.S", "FECHA DE VIGENCIA"]); ws.append(["CONTRATISTA:", "ESE MORENO Y CLAVIJO", "DESDE:", "2026-01-01"])
        ws.append(["CONSECUTIVO DEL CONTRATO:", cont]); ws.append(["REGIONAL:", "BUCARAMANGA"]); ws.append(["DEPARTAMENTO:", "ARAUCA"])
        ws.append(["MUNICIPIO DE COBERTURA", mun]); ws.append(["POBLACIÓN PROYECTADA:", 11300]); ws.append(["REGIMEN:", "CONTRIBUTIVO - SUBSIDIADO"]); ws.append([])
        ws.append(["Agrupador", "CUPS", "CODIGO NT", "DESCRIPCIÓN ", " Frecuencia \nanual", " Actividades año", " Actividades \nmes", "Promedio CxE", "Valor Mes", "CxU"])
        ws.append(["CONSULTA MEDICINA GENERAL", "890201", "890201", "CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL", 1.2, 1200, 100, 30000, 3000000, 265])
        ws.append(["Total CONSULTA MEDICINA GENERAL", None, None, None, None, None, 100, None, 3000000])
        ws.append(["LABORATORIO CLINICO", "903895", "903895", "CREATININA", 0.1, 120, 10, 5000, 50000, 4])
    wb.save(ruta)

def _nueva(ruta, reg):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Servicios_Estudio"
    ws.append(["Homologo", "CodigoCUPS", "DescripcionCUPS", "Grupo", "FrecuenciaUso", "TipoServicio", "Regimen", "Zonal", "TipoRed", "Eventos", "Valor",
               "Subgrupo", "Ruta", "FrecuenciaUso_Propuesta", "Eventos_Propuestos_ESE"])
    ws.append(["890201", "890201", "CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL", "MEDICINA GENERAL", 0.1, "328-MEDICINA GENERAL", reg, "ZONAL ARAUCA", "Publica", 200, 23935, "", "", 0.09, 180])
    ws.append(["19942561-6", "MD000099", "CLORURO DE SODIO", "MEDICAMENTOS", 0.01, "MEDICAMENTOS", reg, "ZONAL ARAUCA", "Publica", 20, 1500, "", "", 0.01, 20])
    ws.append([None, None, None, None, None, None, None, None, None, "270279,563958054", "19.804.371 €"])        # fila de totales
    wb.save(ruta)

def _coosalud(ruta):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "NT 2025 PYM"
    for r in (["NOMBRE DE LA EPS", "COOSALUD EPS SA"], ["CÓDIGO DE LA EPS", "ESS024"], ["NOMBRE DEL DEPARTAMENTO", "ARAUQUITA"], ["CODIGO DANE DEL MUNICIPIO", "81065"],
              ["NOMBRE DE LA IPS", "ESE MORENO Y CLAVIJO"], ["NIT DE LA IPS", "900034131"], ["NÚMERO DEL CONTRATO EN", "81065S00122068-25"], ["REGIMEN", "SUBSIDIADO"],
              ["FECHA INICIO VIGENCIA DEL CONTRATO", "2025-05-01"], ["FECHA FINALIZACION DE VIGENCIA", "2025-12-31"], ["NÚMERO DE MESES DE VIGENCIA", 8],
              ["VALOR TOTAL", 1], ["FECHA DE DILIGENCIAMIENTO", "2025-02-21"], ["TOTAL POBLACIÓN ASIGNADA", 6077]):
        ws.append(r)
    for _ in range(4): ws.append([])
    ws.append([None] * 15 + ["Factor ponderal", "Valor unitario de la actividad", "Valor total de la actividad"])
    ws.append(["Clave", "Pobacion", "GRUPO", "CONTRATADO (SI/NO)", "TOTAL ACTIVIDADES DURANTE LOS 8 MESES", "NÚMERO DE ACTIVIDADES AL MES", None, "Archivo",
               "Código CUPS o REPS\n(NOTA: …)", "Finalidad", "Variable", "Valor de la variable", "Talento Humano", "Modalidad", "Observaciones"])
    ws.append(["1", "RUTA INTEGRAL DE ATENCIÓN"])
    ws.append(["3", "PRIMERA INFANCIA", "Atención por medicina general", "SI", 240, 30, 0.9, "AC", "890201\n890301", 4, "52", "", "Médico", "", "", 1.2, 60000, 14400000])
    ws.append(["4", "INFANCIA", "Atención por medicina general", "SI", 160, 20, 0.9, "AC", "890201", 4, "52", "", "Médico", "", "", 1.2, 60000, 9600000])
    ws.append(["5", "VEJEZ", "Vacunación", "NO", 0, 0, 0, "AP", "993510", 4])
    s2 = wb.create_sheet("NT_SERVICIOS SUB")
    s2.append(["DIVIPOLA", "MUNICIPIO", "DEPARTAMENTO", "REGIMEN", "TOTAL_AFI", "CodigoCUPS", "DescripcionCUPS", "Modulos", "Agrupador"])
    s2.append([81065, "ARAUQUITA", "ARAUQUITA", "SUBSIDIADO", 6077, "903895", "CREATININA", "BASICA", "LABORATORIO CLINICO"])
    wb.save(ruta)

def test_carga_notas_tecnicas_nativas():
    p = {k: os.path.join(CARPETA, k) for k in ("NOTAS_TECNICAS_POR_MUNCIPIO_CAPITA_SANITAS.xlsx", "NOTA_TECNICA_COOSALUD.xlsx",
                                                "NOTA_TECNICA_CONTRIBUTIVO_NUEVA_EPS_vs_PROPUESTA.xlsx", "NOTA_TECNICA_SUBSIDIADO_NUEVA_EPS_vs_PROPUESTA.xlsx")}
    _sanitas(p["NOTAS_TECNICAS_POR_MUNCIPIO_CAPITA_SANITAS.xlsx"]); _coosalud(p["NOTA_TECNICA_COOSALUD.xlsx"])
    _nueva(p["NOTA_TECNICA_CONTRIBUTIVO_NUEVA_EPS_vs_PROPUESTA.xlsx"], "CONTRIBUTIVO"); _nueva(p["NOTA_TECNICA_SUBSIDIADO_NUEVA_EPS_vs_PROPUESTA.xlsx"], "SUBSIDIADO")
    c = _subir("notas", p["NOTAS_TECNICAS_POR_MUNCIPIO_CAPITA_SANITAS.xlsx"]); assert "TAME" in c["mensaje"] and "FORTUL" in c["mensaje"]
    _subir("notas", p["NOTA_TECNICA_COOSALUD.xlsx"])
    _subir("notas", p["NOTA_TECNICA_CONTRIBUTIVO_NUEVA_EPS_vs_PROPUESTA.xlsx"]); _subir("notas", p["NOTA_TECNICA_SUBSIDIADO_NUEVA_EPS_vs_PROPUESTA.xlsx"])
    n = db.df("SELECT * FROM nota")
    s = n[n.eps.str.contains("SANITAS")]
    assert set(s.municipio) == {"TAME", "FORTUL"} and len(s) == 4 and s.eventos_mes.sum() == 220          # sin la fila «Total …»
    ne = n[n.eps.str.contains("NUEVA")]
    assert set(ne.regimen) == {"CONTRIBUTIVO", "SUBSIDIADO"} and len(ne) == 4                                # el contributivo no se borró al cargar el subsidiado
    assert ne[ne.cups == "890201"].eventos_propuesta.tolist() == [180, 180]
    co = n[n.eps.str.contains("COOSALUD")]
    pym = co[co.formato.str.contains("PYM")]
    assert set(pym.cups) == {"890201", "890301"} and pym.grupo.str.startswith("PYM bloque").all()            # 993510 no contratado
    assert co[co.formato.str.contains("SERVICIOS")].contrato.iloc[0] == "81065S00122068-25"

def test_pestana_cups():
    _subir("actividad", RUTAS["ENERO_actividad.xlsx"]); _subir("rips_fev", RUTAS["rips_json.zip"])
    r = cli.get("/api/cups/resumen").json()
    assert r["fuente"] == "actividad" and "2026-01" in r["meses"] and r["notas"]["filas"]
    m = cli.get("/api/cups/mes-a-mes?nivel=eps&metrica=cantidad").json()
    assert "cant_2026-01" in m["columnas"] and "nt_eventos_mes" in m["columnas"] and m["filas"]
    v = cli.get("/api/cups/mes-a-mes?nivel=municipio&metrica=valor&fuente=rips").json(); assert "2026-01" in v["columnas"]
    c = cli.get("/api/cups/cruce?mes=2026-01").json()
    assert {"cantidad_rips", "cantidad_actividad", "estado"} <= set(c["columnas"]) and any(x["estado"] != "OK" for x in c["filas"])
    e = cli.get("/api/cups/nota-tecnica").json()
    bloque = next(x for x in e["filas"] if str(x["cups_o_bloque"]).startswith("PYM bloque"))
    assert bloque["eventos_mes"] == 50                                                                        # 30 + 20: cada actividad cuenta una vez
    assert cli.get("/api/export/CUPS_").status_code == 200
    g = cli.get("/api/gerencial").json(); assert g["recomendaciones"]

def test_url_sin_signo_de_pregunta_en_medicamentos():
    """«Not Found» en Medicamentos con «Todos los meses» + clase: la pantalla debe corregir «/resumen&clase=…» a «/resumen?clase=…»."""
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "index.html"), encoding="utf-8").read()
    assert "const URLQ=" in html and "u=URLQ(u)" in html
    assert cli.get("/api/medicamentos/resumen?clase=MEDICAMENTO").status_code == 200

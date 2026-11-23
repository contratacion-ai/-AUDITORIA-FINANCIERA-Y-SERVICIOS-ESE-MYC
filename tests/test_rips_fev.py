"""Facturación por actividad (360, sin columnas de EPS) + RIPS en JSON y TXT dentro de .zip: carga, cruces y reglas de estructura."""
import os, sys, tempfile, zipfile
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
    assert c["tipo"] == "actividad" and c["mes"] == "2026-01" and "también alimenta" in c["mensaje"]   # hoja sin mes: FECHA DE FACTURACION
    eps = {x["empresa"] for x in cli.get("/api/resumen/empresas?mes=2026-01").json()["empresas"]["filas"]}
    assert eps == {"NUEVA EMPRESA PROMOTORA DE SALUD", "ENTIDAD PROMOTORA DE SALUD SANITAS S A S"}   # deducida del contrato
    h = cli.get("/api/conciliacion/hallazgos?mes=2026-01").json()["filas"]
    assert any(x["regla"] == "R10" for x in h)                                      # Σ líneas ≠ VALOR FACTURA sembrado
    k = cli.get("/api/medicamentos/resumen?mes=2026-01").json()["kpis"]
    assert k["medicamentos"] > 0 and k["insumos"] > 0

def test_rips_json_zip():
    r, c = _subir("rips_fev", RUTAS["rips_json.zip"])
    assert c["tipo"] == "rips_fev" and "29 facturas" in c["mensaje"] and "29 XML" in c["mensaje"]
    f = cli.get("/api/rips-fev/facturas?mes=2026-01").json()["filas"]
    assert _estado(f, ESP["sin_rips"]) == "SIN RIPS"
    assert "PACIENTE DIFERENTE" in _estado(f, ESP["paciente"])
    for k in ("cantidad", "cero", "r10"): assert "VALOR DIFIERE" in _estado(f, ESP[k]), k
    assert _estado(f, "FA00000") == "OK"
    assert "XML (FEV) ≠ RIPS" in _estado(f, ESP["xml_difiere"])                     # XML de la factura con valor distinto
    fx = next(x for x in f if x["factura"] == "FA00000")
    assert fx["estado_dian"] == "VALIDADO DIAN" and fx["modalidad"] == "EVENTO"
    assert cli.get("/api/rips-fev/resumen?mes=2026-01").json()["kpis"]["xml"] == 29
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

def test_zip_con_rutas_largas_y_carpetas_con_espacio():
    """Descargas de Google Drive: carpeta raíz con espacio final y rutas de más de 260 caracteres (fallaban en Windows)."""
    from app.formatos import extraer, nombre_interno
    largo = "Hospital San Francisco /RIPS JSON/9 SEPTIEMBRE/11301_NUEVA EPS CONTRIBUTIVO EVENTO COVID-19 " + "X" * 60 + "/FHSF35921/"
    ruta = os.path.join(CARPETA, "Hospital San Francisco-20261117T191415Z-1-001.zip")
    with zipfile.ZipFile(RUTAS["rips_json.zip"]) as z, zipfile.ZipFile(ruta, "w") as o:
        for i, m in enumerate(n for n in z.namelist() if not n.endswith("/")):
            o.writestr(largo + f"ResultadosMSPS_FHSF35921_ID{i:06d}_" + "A" * 80 + "_" + os.path.basename(m), z.read(m))
    destino = os.path.join(CARPETA, "ext_largo"); rutas = extraer(ruta, destino)
    assert rutas and all(len(os.path.relpath(r, destino)) <= 70 for r in rutas)          # NNN/nombre (≤ 60) sin importar la profundidad
    assert all(not p.endswith((" ", ".")) for r in rutas for p in os.path.relpath(r, destino).split(os.sep))
    assert nombre_interno(rutas[0], destino).startswith("Hospital San Francisco/RIPS JSON")
    r, c = _subir("rips_fev", ruta)
    assert "29 facturas" in c["mensaje"]

def test_reglas_rips_cubren_global_y_fecha_posterior():
    from app import rips_fev as RF
    assert {"RV09", "RV10"} <= set(RF.REGLAS_RIPS)
    import pandas as pd
    d = pd.DataFrame(dict(mes="2026-02", factura=["F1"] * 12 + ["F2", "F2"], tipo_servicio="Consultas", codigo="890201", valor=[0.0] * 12 + [0.0, 1.0],
                          paciente_id=[f"p{i}" for i in range(12)] + ["a", "b"], fecha=["2026-02-01"] * 12 + ["2026-01-05", "2026-03-02"]))
    d["glob"] = RF.es_global(d)                                              # F1: 12 usuarios sin modalidad → global (cápita/PGP)
    assert d.glob.tolist() == [True] * 12 + [False, False]
    R = RF.REGLAS_RIPS
    assert R["RV02"][1](d).sum() == 1 and R["RV08"][1](d).sum() == 2              # cero y varios usuarios: solo en la factura de evento
    assert R["RV05"][1](d).tolist()[-2:] == [True, False] and R["RV09"][1](d).tolist()[-2:] == [False, True]

def test_comparativo_rips_vs_tres_facturaciones():
    r = cli.get("/api/comparativo/resumen?mes=2026-01").json()
    assert r["kpis"]["facturas"] > 0 and r["kpis"]["con_rips"] > 0
    fuentes = {x["fuente"] for x in r["filas"]}
    assert {"RIPS", "Facturación por actividad"} <= fuentes
    f = cli.get("/api/comparativo/facturas?mes=2026-01").json()
    fila = next(x for x in f["filas"] if x["factura"] == ESP["sin_rips"])
    assert "SIN RIPS" in fila["estado"] and "C" in fila["fuentes"] and "R" not in fila["fuentes"]
    assert any("RIPS ≠ POR ACTIVIDAD" in x["estado"] for x in f["filas"])
    m = cli.get("/api/comparativo/medicamentos?mes=2026-01").json()
    assert m["filas"] and "estado" in m["columnas"]
    assert cli.get("/api/export/COMP_RIPS?mes=2026-01").status_code == 200

def test_json_grande_en_streaming(monkeypatch):
    """Un JSON por encima del límite se lee usuario por usuario (ijson), con BOM y numFactura después de «usuarios»."""
    from app import rips_fev as RF
    import json as js
    monkeypatch.setattr(RF, "LIMITE_JSON_MEMORIA", 10)
    ruta = os.path.join(CARPETA, "capita_grande.json")
    us = [{"numDocumentoIdentificacion": str(i), "servicios": {"consultas": [{"codConsulta": "890201", "fechaInicioAtencion": "2026-01-03 08:00", "vrServicio": 0}] * 3}} for i in range(50)]
    with open(ruta, "wb") as f: f.write(b"\xef\xbb\xbf" + js.dumps({"usuarios": us, "numFactura": "FCAP1"}).encode())
    val = []; filas = list(RF.leer_json(RF.Fuente.ruta(ruta), val))
    assert len(filas) == 150 and {x[0] for x in filas} == {"FCAP1"} and len({x[7] for x in filas}) == 50
    with open(ruta, "wb") as f: f.write(js.dumps([{"numFactura": "FA1", "usuarios": us[:2]}, {"numFactura": "FA2", "usuarios": us[2:3]}]).encode())
    filas = list(RF.leer_json(RF.Fuente.ruta(ruta), val))
    assert [x[0] for x in filas].count("FA1") == 6 and [x[0] for x in filas].count("FA2") == 3

def _zip_danado(origen, destino, modo):
    """Zip como los de Google Drive con problemas: índice con posiciones erradas, encabezados sin tamaño (data descriptor) o truncado."""
    import io, struct
    with zipfile.ZipFile(origen) as z: datos = [(n, z.read(n)) for n in z.namelist() if not n.endswith("/")]
    class NoSeek(io.RawIOBase):                                         # obliga a zipfile a escribir «data descriptors» (bit 3)
        def __init__(self): self.b = bytearray()
        def writable(self): return True
        def write(self, x): self.b += x; return len(x)
    w = NoSeek()
    with zipfile.ZipFile(w, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in datos: z.writestr("Hospital San Antonio /RIPS/" + n, d)
    b = bytes(w.b)
    if modo == "indice":                                                # posiciones del directorio central desplazadas
        b = bytearray(b); i = b.find(b"PK\x01\x02")
        while i >= 0:
            off = struct.unpack("<I", b[i + 42:i + 46])[0]; b[i + 42:i + 46] = struct.pack("<I", off + 7); i = b.find(b"PK\x01\x02", i + 4)
        b = bytes(b)
    if modo == "truncado": b = b[:b.find(b"PK\x01\x02")]
    with open(destino, "wb") as f: f.write(b)
    return len(datos)

@pytest.mark.parametrize("modo", ["indice", "descriptor", "truncado"])
def test_zip_danado_se_lee_secuencialmente(modo):
    from app import rips_fev as RF
    ruta = os.path.join(CARPETA, f"danado_{modo}.zip"); n = _zip_danado(RUTAS["rips_json.zip"], ruta, modo)
    fs = RF.fuentes(ruta); assert len(fs) == n
    val = []; filas = [x for f in fs if f.base.endswith(".json") for x in RF.leer_json(f, val)]
    assert filas and {x[0] for x in filas} >= {"FA00000"}
    xml = [RF.leer_xml(f)[0]["factura"] for f in fs if f.base.endswith(".xml")]
    assert "FA00000" in xml

def test_zip_danado_por_la_api():
    ruta = os.path.join(CARPETA, "Hospital San Antonio-20261117T191416Z-1-001.zip"); _zip_danado(RUTAS["rips_json.zip"], ruta, "indice")
    r, c = _subir("rips_fev", ruta)
    assert c["tipo"] == "rips_fev" and "29 facturas" in c["mensaje"], c

def test_zip_con_un_archivo_corrupto_carga_el_resto():
    """«Error -3 while decompressing data»: un JSON dañado se omite y se informa; los demás se cargan."""
    ruta = os.path.join(CARPETA, "Hospital San Francisco-20261117T191415Z-1-001.zip")
    with zipfile.ZipFile(RUTAS["rips_json.zip"]) as z, zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as o:
        for n in z.namelist():
            if not n.endswith("/"): o.writestr(n, z.read(n))
    with zipfile.ZipFile(ruta) as z:
        m = next(i for i in z.infolist() if i.filename.endswith(".json") and i.compress_size > 200)
        ini = m.header_offset + 30 + len(m.filename.encode()) + len(m.extra)
    with open(ruta, "r+b") as f:                                        # se dañan bytes en medio del contenido comprimido
        f.seek(ini + m.compress_size // 3); f.write(b"\xff" * 24)
    r, c = _subir("rips_fev", ruta)
    assert c["estado"] == "ok" and "dañados u omitidos" in c["mensaje"] and "28 facturas" in c["mensaje"], c

def test_bloqueo_de_windows_al_borrar_no_cambia_el_resultado(monkeypatch):
    """Windows no deja borrar un archivo abierto (WinError 32): la carga debe quedar «ok» y el .zip cerrado."""
    from app import rips_fev as RF
    real = os.remove
    def bloqueado(p):
        if p.endswith(".zip"): raise PermissionError(32, "El proceso no tiene acceso al archivo porque está siendo utilizado por otro proceso", p)
        return real(p)
    monkeypatch.setattr(os, "remove", bloqueado)
    r, c = _subir("rips_fev", RUTAS["rips_json.zip"])
    assert c["estado"] == "ok" and "29 facturas" in c["mensaje"], c
    assert not [k for k in RF.ABIERTOS if k.startswith(db.UPLOADS)]       # ningún .zip subido queda abierto

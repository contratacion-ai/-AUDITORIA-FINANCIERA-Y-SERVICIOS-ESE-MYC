"""Vínculo con la carpeta de Google Drive (JSON + XML + CUV por hospital y mes) y envío al aplicativo."""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd
import pytest

from rips_auditoria import aplicativo, cuv, drive
from rips_auditoria import tablero as tb

from .test_json_cargue import _factura, _fev_xml


def _cuv(numero, valido=True):
    return json.dumps({
        "ResultState": valido, "ProcesoId": 151738772, "NumFactura": numero,
        "CodigoUnicoValidacion": "f3555a5d36d134a33976851bfcdd6867" if valido else None,
        "FechaRadicacion": "2026-02-05T17:34:45.1264932-05:00", "ModalidadPago": "Pago por evento.",
        "PeriodoAtencion": {"FechaInicio": "2026-01-30T00:00:00", "FechaFin": "2026-01-30T00:00:00"},
        "ResultadosValidacion": [
            {"Clase": "NOTIFICACION", "Codigo": "RVC019", "Descripcion": "x", "Observaciones": "", "PathFuente": ""},
            *([] if valido else [{"Clase": "RECHAZADO", "Codigo": "RVG02", "Descripcion": "y"}])]}).encode()


def _factura_drive(raiz, hospital, mes, carpeta_json, numero, fecha, valido=True, valor=30000):
    d = raiz / hospital / mes / carpeta_json / numero
    d.mkdir(parents=True)
    (d / f"{numero}.json").write_text(json.dumps(_factura(numero, fecha=fecha, valor=valor)), encoding="utf-8")
    (d / f"{numero}.xml").write_bytes(_fev_xml(numero, "NUEVA EMPRESA PROMOTORA DE SALUD S.A.", "900156264", valor))
    (d / f"ResultadosMSPS_{numero}_ID1517_{'A' if valido else 'R'}_CUV.txt").write_bytes(_cuv(numero, valido))


def test_leer_cuv_y_texto_de_validacion():
    v, r = cuv.leer_cuv(_cuv("FCSJ1")), cuv.leer_cuv(_cuv("FHSA2", valido=False))
    assert v["estado"] == "VALIDADA" and v["notificaciones"] == 1 and v["fecha_radicacion"] == "2026-02-05 17:34:45"
    assert r["estado"] == "RECHAZADA" and r["rechazos"] == 1 and "RVG02 (1)" in r["codigos_rechazo"]
    assert cuv.texto_validacion(v).startswith("CUV MSPS VALIDADA 2026-02-05")
    assert cuv.texto_validacion(r).startswith("CUV MSPS RECHAZADA · 1 rechazos")
    assert cuv.leer_cuv(b"no es json") is None and cuv.es_resultado_msps("x/ResultadosMSPS_A_ID1_A_CUV.txt")


def test_id_de_enlace():
    assert drive.id_de_enlace("https://drive.google.com/drive/folders/1AbC_def-123456789?usp=sharing") == \
        "1AbC_def-123456789"
    assert drive.id_de_enlace("https://drive.google.com/open?id=1AbC_def-123456789") == "1AbC_def-123456789"
    assert drive.id_de_enlace(" 1AbC_def-123456789 ") == "1AbC_def-123456789"


@pytest.fixture
def enviados(monkeypatch):
    lista = []

    def falso_envio(archivos, cfg=None, aviso=None):
        lista.extend(archivos)
        return [{"mes": a.stem[-7:], "estado": "ok"} for a in archivos]

    monkeypatch.setattr(aplicativo, "enviar_meses", falso_envio)
    monkeypatch.setattr(aplicativo, "resumen_cruce", lambda cfg=None: [])
    return lista


def test_sincroniza_la_carpeta_local_por_hospital_y_mes(tmp_path, enviados):
    raiz = tmp_path / "Rips TXT y JSON 2026"
    _factura_drive(raiz, "Centro de Salud Jesus Coronel", "01. Enero", "JSON ENERO", "FCSJ1", "2026-01-30 08:00")
    _factura_drive(raiz, "Hospital San Antonio", "02. Febrero", "JSON FEBRERO", "FHSA2", "2026-02-03 09:00", valido=False)
    drive.guardar_configuracion({"modo": "local", "carpeta_local": str(raiz)})

    r = drive.sincronizar()
    assert {(u["hospital"], u["mes"]) for u in r["unidades"]} == {
        ("Centro de Salud Jesus Coronel", "01. Enero"), ("Hospital San Antonio", "02. Febrero")}
    assert r["meses_enviados"] == ["2026-01", "2026-02"]
    assert sorted(a.name for a in enviados) == ["RIPS_AUDITORIA_2026-01.xlsx", "RIPS_AUDITORIA_2026-02.xlsx"]
    df = tb.cargar(tb.carpeta_acumulado())[0].set_index("FACTURA_FEV")
    assert df.loc["FCSJ1", "SEDE"] == "CENTRO DE SALUD JESUS CORONEL" and df.loc["FCSJ1", "MES"] == "2026-01"
    assert df.loc["FCSJ1", "VALIDACION"].startswith("CUV MSPS VALIDADA")
    assert df.loc["FHSA2", "VALIDACION"].startswith("CUV MSPS RECHAZADA")
    assert "NUEVA EMPRESA PROMOTORA" in df.loc["FCSJ1", "EPS"]
    reporte = pd.read_excel(next(a for a in r["archivos"] if a.endswith("VALIDACION_MSPS.xlsx")))
    assert set(reporte["ESTADO"]) == {"VALIDADA", "RECHAZADA"}
    assert sum(p["validadas_msps"] for p in r["procesadas"]) == 1

    # segunda revisión: nada nuevo, nada se envía
    enviados.clear()
    r = drive.sincronizar()
    assert r["unidades"] == [] and enviados == []

    # llega una factura nueva de enero: sólo se procesa ese hospital-mes y se reenvía enero completo
    _factura_drive(raiz, "Centro de Salud Jesus Coronel", "01. Enero", "JSON ENERO", "FCSJ3", "2026-01-31 10:00")
    r = drive.sincronizar()
    assert [(u["hospital"], u["mes"]) for u in r["unidades"]] == [("Centro de Salud Jesus Coronel", "01. Enero")]
    assert [a.name for a in enviados] == ["RIPS_AUDITORIA_2026-01.xlsx"]
    enero = pd.read_excel(enviados[0], sheet_name=aplicativo.HOJA_DATOS, header=3)
    assert {"FCSJ1", "FCSJ3"} <= set(enero["FACTURA_FEV"])  # el mes va completo, sin duplicar FCSJ1
    assert (enero["FACTURA_FEV"] == "FCSJ1").sum() == 1


def test_sin_carpeta_da_un_mensaje_claro(tmp_path):
    drive.guardar_configuracion({"modo": "local", "carpeta_local": str(tmp_path / "no_existe")})
    with pytest.raises(drive.ErrorDrive, match="Google Drive para escritorio"):
        drive.sincronizar()


class _DriveFalso(BaseHTTPRequestHandler):
    """API mínima de Drive v3 + token, para probar el espejo sin internet."""
    arbol = {
        "RAIZ": [{"id": "H1", "name": "Hospital San Lorenzo", "mimeType": drive.CARPETA}],
        "H1": [{"id": "M1", "name": "03. Marzo", "mimeType": drive.CARPETA}],
        "M1": [{"id": "F1", "name": "FHSL1.json", "mimeType": "application/json", "md5Checksum": "a1"},
               {"id": "F2", "name": "FHSL1.xml", "mimeType": "text/xml", "md5Checksum": "b2"},
               {"id": "F3", "name": "soporte.pdf", "mimeType": "application/pdf", "md5Checksum": "c3"},
               {"id": "S1", "name": "atajo", "mimeType": drive.ACCESO_DIRECTO,
                "shortcutDetails": {"targetId": "M2", "targetMimeType": drive.CARPETA}}],
        "M2": [{"id": "F4", "name": "ResultadosMSPS_FHSL1_ID9_A_CUV.txt", "mimeType": "text/plain", "md5Checksum": "d4"}],
    }
    contenidos = {"F1": b'{"x": 1}', "F2": b"<xml/>", "F3": b"%PDF", "F4": b"{}"}
    descargas: list[str] = []
    tokens = 0

    def log_message(self, *a):
        pass

    def _json(self, datos):
        cuerpo = json.dumps(datos).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_POST(self):  # token
        type(self).tokens += 1
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._json({"access_token": "tok", "expires_in": 3600})

    def do_GET(self):
        assert self.headers["Authorization"] == "Bearer tok"
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path.endswith("/files"):
            padre = q["q"][0].split("'")[1]
            return self._json({"files": self.arbol.get(padre, [])})
        fid = u.path.rsplit("/", 1)[-1]
        if q.get("alt") == ["media"]:
            type(self).descargas.append(fid)
            datos = self.contenidos[fid]
            self.send_response(200)
            self.send_header("Content-Length", str(len(datos)))
            self.end_headers()
            self.wfile.write(datos)
            return None
        return self._json({"id": fid, "name": "Rips TXT y JSON 2026", "mimeType": drive.CARPETA})


def test_espejo_por_la_api_descarga_solo_lo_nuevo(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _DriveFalso)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        drive.guardar_credenciales({"installed": {"client_id": "id", "client_secret": "s", "token_uri": base + "/token"}})
        drive._guardar("token.json", {"refresh_token": "r", "access_token": "", "vence": 0})
        assert drive.estado_api()["autorizado"]
        cliente = drive.ClienteDrive(api=base + "/drive/v3")
        destino = tmp_path / "espejo"
        r = drive.espejo(cliente, "RAIZ", destino)
        assert r == {"descargados": 3, "archivos": 3}  # el PDF no se descarga; el acceso directo sí se sigue
        assert (destino / "Hospital San Lorenzo" / "03. Marzo" / "FHSL1.json").read_bytes() == b'{"x": 1}'
        assert (destino / "Hospital San Lorenzo" / "03. Marzo" / "atajo" / "ResultadosMSPS_FHSL1_ID9_A_CUV.txt").exists()
        assert _DriveFalso.tokens == 1
        _DriveFalso.descargas.clear()
        assert drive.espejo(cliente, "RAIZ", destino)["descargados"] == 0  # nada cambió
        _DriveFalso.arbol["M1"][0]["md5Checksum"] = "a1-nuevo"
        assert drive.espejo(cliente, "RAIZ", destino)["descargados"] == 1 and _DriveFalso.descargas == ["F1"]
        unidades, _f = drive.novedades(destino)
        assert list(unidades) == [("Hospital San Lorenzo", "03. Marzo")]
    finally:
        srv.shutdown()


def test_ventana_configura_y_sincroniza(tmp_path, enviados, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from rips_auditoria.web import servidor

    monkeypatch.setenv("AUDITORIA_ESE_DIR", "/no/existe")
    raiz = tmp_path / "Rips"
    _factura_drive(raiz, "Hospital San Francisco", "04. Abril", "JSON ABRIL", "FHSF1", "2026-04-02 08:00")
    c = TestClient(servidor.app, client=("127.0.0.1", 50000))
    r = c.post("/api/drive/config", json={"modo": "local", "carpeta_local": str(raiz), "revisar_cada_min": 0})
    assert r.status_code == 200 and r.json()["carpeta_local"] == str(raiz)
    tid = c.post("/api/drive/sincronizar", json={}).json()["id"]
    for _ in range(300):
        t = c.get(f"/api/cargues/{tid}").json()
        if t["estado"] in ("terminado", "error"):
            break
        time.sleep(0.2)
    assert t["estado"] == "terminado", t["error"]
    assert t["resultado"]["drive"]["meses_enviados"] == ["2026-04"]
    assert "APLICATIVO_AUDITORIA/RIPS_AUDITORIA_2026-04.xlsx" in t["resultado"]["archivos"]
    e = c.get("/api/drive/estado").json()
    assert e["ultima"]["meses_enviados"] == ["2026-04"] and e["archivos_procesados"] == 3
    assert c.get("/api/drive/conectar").status_code == 400  # sin credenciales: mensaje claro
    r = c.post("/api/drive/credenciales", files={"archivo": ("c.json", b'{"installed": {"client_id": "x"}}')})
    assert r.status_code == 400 and "client_secret" in r.json()["detail"]


def _zip_drive(destino, nombre, carpeta):
    """ZIP como lo entrega «Descargar» de drive.google.com: con la carpeta raíz exportada adentro."""
    import zipfile

    with zipfile.ZipFile(destino / nombre, "w") as z:
        for f in sorted(carpeta.rglob("*")):
            if f.is_file():
                z.write(f, f"{carpeta.name}/{f.relative_to(carpeta).as_posix()}")


def test_zip_descargados_de_drive_sin_drive_para_escritorio(tmp_path, enviados):
    origen = tmp_path / "origen" / "Rips TXT y JSON 2026"
    _factura_drive(origen, "Hospital San Lorenzo", "01. Enero", "1.ENERO JSON", "FHSL1", "2026-01-20 08:00")
    _factura_drive(origen, "Hospital San Ricardo Pampuri", "10. Octubre", "RIPS JSON OCTUBRE", "FHRP1",
                   "2026-10-05 08:00")
    descargas = tmp_path / "Descargas" / "RIPS DRIVE"
    descargas.mkdir(parents=True)
    (descargas / "Hospital San Lorenzo").mkdir()  # otras carpetas en Descargas no estorban
    _zip_drive(descargas, "Rips TXT y JSON 2026-20261123T140000Z-1-001.zip", origen)
    drive.guardar_configuracion({"modo": "local", "carpeta_local": str(descargas)})

    r = drive.sincronizar()
    assert {(u["hospital"], u["mes"]) for u in r["unidades"]} == {
        ("Hospital San Lorenzo", "01. Enero"), ("Hospital San Ricardo Pampuri", "10. Octubre")}
    assert r["meses_enviados"] == ["2026-01", "2026-10"]
    df = tb.cargar(tb.carpeta_acumulado())[0]
    assert set(df["SEDE"]) == {"HOSPITAL SAN LORENZO", "HOSPITAL SAN RICARDO PAMPURI"}

    # se vuelve a descargar todo de Drive con una factura más: sólo se procesa su hospital-mes
    _factura_drive(origen, "Hospital San Lorenzo", "01. Enero", "1.ENERO JSON", "FHSL2", "2026-01-21 08:00")
    for z in descargas.glob("*.zip"):
        z.unlink()
    _zip_drive(descargas, "Rips TXT y JSON 2026-20261124T090000Z-1-001.zip", origen)
    enviados.clear()
    r = drive.sincronizar()
    assert [(u["hospital"], u["mes"]) for u in r["unidades"]] == [("Hospital San Lorenzo", "01. Enero")]
    assert [a.name for a in enviados] == ["RIPS_AUDITORIA_2026-01.xlsx"]
    assert drive.sincronizar()["unidades"] == []


def test_busca_la_carpeta_en_mi_unidad_y_explica_si_falta(tmp_path, enviados, monkeypatch):
    casa = tmp_path / "casa"
    mi_unidad = casa / "Mi unidad"
    (mi_unidad / "Facturas viejas").mkdir(parents=True)
    monkeypatch.setattr(drive.Path, "home", classmethod(lambda cls: casa))
    # sin el acceso directo: el mensaje dice qué hay en «Mi unidad» y cómo agregarlo
    drive.guardar_configuracion({"modo": "local", "carpeta_local": "G:\\Mi unidad\\Rips TXT y JSON 2026"})
    with pytest.raises(drive.ErrorDrive) as e:
        drive.sincronizar()
    assert "Facturas viejas" in str(e.value) and "Agregar acceso directo" in str(e.value)
    # con el acceso directo: se encuentra sola aunque la ruta escrita esté mal, y se guarda
    raiz = mi_unidad / "Rips TXT y JSON 2026"
    _factura_drive(raiz, "Hospital San Jose de Cravo Norte", "03. Marzo", "JSON MARZO", "FHSJ1", "2026-03-03 08:00")
    assert drive.buscar_carpetas_locales() == [raiz]
    r = drive.sincronizar()
    assert r["carpeta"] == str(raiz) and r["meses_enviados"] == ["2026-03"]
    assert drive.configuracion()["carpeta_local"] == str(raiz)


def test_carpeta_dentro_de_unidad_compartida_y_enlace_web(tmp_path, enviados, monkeypatch):
    casa = tmp_path / "casa"
    raiz = casa / "Unidades compartidas" / "RIPS , TXT, JASON" / "RIPS TXT JASON" / "Rips TXT y JSON 2026"
    _factura_drive(raiz, "Hospital San Lorenzo", "05. Mayo", "JSON MAYO", "FHSL5", "2026-05-05 08:00")
    (casa / "Mi unidad").mkdir(parents=True)
    monkeypatch.setattr(drive.Path, "home", classmethod(lambda cls: casa))
    assert drive.buscar_carpetas_locales()[0] == raiz
    assert "no el enlace web" in drive.diagnostico_local("https://drive.google.com/drive/folders/abc")
    drive.guardar_configuracion({"modo": "local", "carpeta_local": "https://drive.google.com/drive/folders/abc"})
    assert drive.sincronizar()["carpeta"] == str(raiz)  # el enlace se ignora y se usa la carpeta encontrada


def test_se_vincula_sola_y_reenvia_si_el_aplicativo_estaba_cerrado(tmp_path, monkeypatch):
    casa = tmp_path / "casa"
    raiz = casa / "Mi unidad" / "Rips TXT y JSON 2026"
    _factura_drive(raiz, "Hospital San Antonio", "06. Junio", "JSON JUNIO", "FHSA6", "2026-06-06 08:00")
    monkeypatch.setattr(drive.Path, "home", classmethod(lambda cls: casa))
    e = drive.estado()
    assert e["autovinculada"] == str(raiz)
    assert e["config"]["carpeta_local"] == str(raiz) and e["config"]["revisar_cada_min"] == 60
    assert e["config"]["enviar_aplicativo"] is True
    assert drive.estado()["autovinculada"] == ""  # sólo la primera vez

    def cerrado(archivos, cfg=None, aviso=None):
        raise aplicativo.ErrorAplicativo("El aplicativo de auditoría no está abierto.")

    monkeypatch.setattr(aplicativo, "enviar_meses", cerrado)
    r = drive.sincronizar()
    assert "2026-06" in r["aviso_envio"] and drive.estado()["pendientes_envio"] == ["2026-06"]
    enviados = []
    monkeypatch.setattr(aplicativo, "enviar_meses", lambda a, cfg=None, aviso=None: enviados.extend(a) or [])
    monkeypatch.setattr(aplicativo, "resumen_cruce", lambda cfg=None: [])
    r = drive.sincronizar()  # sin archivos nuevos: igual reenvía lo pendiente
    assert r["unidades"] == [] and [a.name for a in enviados] == ["RIPS_AUDITORIA_2026-06.xlsx"]
    assert drive.estado()["pendientes_envio"] == []

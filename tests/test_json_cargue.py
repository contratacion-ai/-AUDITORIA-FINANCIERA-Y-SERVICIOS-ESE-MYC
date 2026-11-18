"""Carga de RIPS JSON (Res. 2275) desde la ventana de cargue, solos o con los TXT."""

from __future__ import annotations

import io
import json
import time

import pandas as pd
import pytest

from rips_auditoria import json_rips
from rips_auditoria import tablero as tb

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from rips_auditoria.web import servidor  # noqa: E402


def _factura(num, doc="123", fecha="2026-04-10 08:00", cod="890201", valor=30000):
    return {"numDocumentoIdObligado": "900034131", "numFactura": num, "tipoNota": None, "numNota": None,
            "usuarios": [{"tipoDocumentoIdentificacion": "CC", "numDocumentoIdentificacion": doc,
                          "fechaNacimiento": "1990-01-01", "consecutivo": 1,
                          "servicios": {"consultas": [{"codPrestador": "817940020603", "fechaInicioAtencion": fecha,
                                                       "codConsulta": cod, "vrServicio": valor, "consecutivo": 1}]}}]}


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("AUDITORIA_ESE_DIR", "/no/existe")
    return TestClient(servidor.app, client=("127.0.0.1", 50000))


def _esperar(c, tid):
    for _ in range(300):
        t = c.get(f"/api/cargues/{tid}").json()
        if t["estado"] in ("terminado", "error"):
            return t
        time.sleep(0.2)
    raise AssertionError("el cargue no terminó")


def test_formatos_de_json_2275():
    f1, f2 = _factura("A1"), _factura("A2")
    assert [d["numFactura"] for d in json_rips.documentos_2275(f1)] == ["A1"]
    assert [d["numFactura"] for d in json_rips.documentos_2275([f1, f2])] == ["A1", "A2"]
    assert [d["numFactura"] for d in json_rips.documentos_2275({"rips": f1, "CUV": "abc"})] == ["A1"]
    assert list(json_rips.documentos_2275({"otra": "cosa"})) == []


def test_cargue_solo_json_desde_la_ventana(cliente):
    archivos = [("archivos", (f"FEV{i}.json", json.dumps(_factura(f"FEV{i}", doc=str(i))).encode(), "application/json"))
                for i in range(3)]
    archivos.append(("archivos", ("FEV9.json", json.dumps({"rips": _factura("FEV9", doc="9")}).encode(), "application/json")))
    # JSON sueltos sin hospital: se pide la sede
    r = cliente.post("/api/cargues", files=archivos)
    assert r.status_code == 400 and "hospital" in r.json()["detail"]
    r = cliente.post("/api/cargues", files=archivos, data={"hospital": "Hospital San Lorenzo"})
    assert r.status_code == 200, r.text
    t = _esperar(cliente, r.json()["id"])
    assert t["estado"] == "terminado", t["error"]
    j = t["resultado"]["json"]
    assert j["archivos_json"] == 4 and j["registros_leidos"] == 4 and j["agregadas"] == 4 and j["meses"] == ["2026-04"]
    df = tb.cargar(tb.carpeta_acumulado())[0]
    assert set(df["SEDE"]) == {"HOSPITAL SAN LORENZO"} and len(df) == 4
    # volver a cargar los mismos JSON no duplica
    t = _esperar(cliente, cliente.post("/api/cargues", files=archivos, data={"hospital": "Hospital San Lorenzo"}).json()["id"])
    assert t["resultado"]["json"]["agregadas"] == 0 and t["resultado"]["json"]["ya_cargadas"] == 4


def test_json_en_carpeta_del_hospital_no_pide_sede(cliente):
    archivos = [("archivos", ("Hospital San Jose/04. Abril/FEV1.json", json.dumps(_factura("FEV1")).encode(), "application/json"))]
    t = _esperar(cliente, cliente.post("/api/cargues", files=archivos).json()["id"])
    assert t["estado"] == "terminado", t["error"]
    assert set(tb.cargar(tb.carpeta_acumulado())[0]["SEDE"]) == {"HOSPITAL SAN JOSE"}


def test_nota_tecnica_solo_con_json(cliente, tmp_path):
    nota = io.BytesIO()
    pd.DataFrame({"CodigoCUP": [890201, 890301], "Homologo": [890201, 890301], "ESTADO CUPS": ["ACTIVO", "ACTIVO"],
                  "DescripcionCUP": ["CONSULTA PRIMERA VEZ", "CONSULTA CONTROL"]}).to_excel(nota, index=False)
    archivos = [("archivos", (f"FEV{i}.json", json.dumps(_factura(f"FEV{i}", doc=d)).encode(), "application/json"))
                for i, d in enumerate(["1", "1", "2"])]  # paciente 1 en dos facturas del mismo mes = 1
    r = cliente.post("/api/cargues", files=[*archivos, ("nota_archivo", ("NUEVA_EPS.xlsx", nota.getvalue()))],
                     data={"hospital": "Hospital San Antonio", "nota_tecnica": "true"})
    t = _esperar(cliente, r.json()["id"])
    assert t["estado"] == "terminado", t["error"]
    nt = t["resultado"]["nota_tecnica"]
    assert nt["pacientes_unicos_periodo"] == 2 and nt["codigos_con_pacientes"] == 1 and nt["codigos_sin_registros"] == 1

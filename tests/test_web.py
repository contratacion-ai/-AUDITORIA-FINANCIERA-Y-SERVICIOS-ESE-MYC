"""Ventana de cargue local (FastAPI)."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from rips_auditoria.web import servidor  # noqa: E402

from . import fabrica  # noqa: E402


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("AUDITORIA_ESE_DIR", "/no/existe")
    return TestClient(servidor.app)


def _esperar(cliente, tid, maximo=120):
    fin = time.monotonic() + maximo
    while time.monotonic() < fin:
        t = cliente.get(f"/api/cargues/{tid}").json()
        if t["estado"] in ("terminado", "error"):
            return t
        time.sleep(0.3)
    raise AssertionError("el cargue no terminó")


def test_pagina_y_estado(cliente):
    r = cliente.get("/")
    assert r.status_code == 200 and "ESE Moreno y Clavijo" in r.text and "logo_ese.png" in r.text
    assert cliente.get("/static/logo_ese.png").status_code == 200
    e = cliente.get("/api/estado").json()
    assert e["aplicativo_instalado"] is False and "carpeta_datos" in e


def test_cargue_por_ruta_local(cliente, tmp_path):
    zip_lote = fabrica.zip_varios_hospitales(tmp_path / "lote.zip")
    r = cliente.post("/api/cargues", data={"ruta_local": str(zip_lote), "enviar": "false", "consolidado": "false"})
    t = _esperar(cliente, r.json()["id"])
    assert t["estado"] == "terminado", t["error"]
    res = t["resultado"]
    assert {(u["hospital"], u["periodo"]) for u in res["unidades"]} == {
        ("Hospital San Antonio", "2026-05"), ("Hospital San Antonio", "2026-06"),
        ("Hospital San Jose", "2026-05"), ("Hospital San Jose", "2026-06")}
    assert "APLICATIVO_AUDITORIA/RIPS_AUDITORIA_2026-05.xlsx" in res["archivos"]
    d = cliente.get(f"/api/cargues/{t['id']}/archivo", params={"ruta": "APLICATIVO_AUDITORIA/RIPS_AUDITORIA_2026-05.xlsx"})
    assert d.status_code == 200 and d.content[:2] == b"PK"
    assert cliente.get(f"/api/cargues/{t['id']}/archivo", params={"ruta": "../../etc/passwd"}).status_code == 404


def test_cargue_subiendo_carpeta(cliente):
    archivos = [("archivos", (f"Hospital San Lorenzo/05. Mayo/123__SANITAS/Rips/{n}", c.encode(), "text/plain"))
                for n, c in fabrica.envio_valido().items()]
    r = cliente.post("/api/cargues", files=archivos, data={"enviar": "false"})
    t = _esperar(cliente, r.json()["id"])
    assert t["estado"] == "terminado", t["error"]
    assert [(u["hospital"], u["periodo"]) for u in t["resultado"]["unidades"]] == [("Hospital San Lorenzo", "2026-05")]


def test_cargue_carpeta_de_mes_pide_hospital(cliente):
    archivos = [("archivos", (f"05. Mayo/r/{n}", c.encode(), "text/plain")) for n, c in fabrica.envio_valido().items()]
    r = cliente.post("/api/cargues", files=archivos)
    assert r.status_code == 400 and "hospital" in r.json()["detail"]


def test_entradas_invalidas(cliente):
    assert cliente.post("/api/cargues", data={}).status_code == 400
    assert cliente.post("/api/cargues", data={"ruta_local": "/no/existe.zip"}).status_code == 400
    r = cliente.post("/api/cargues", files=[("archivos", ("../../fuera.txt", b"x", "text/plain"))])
    assert r.status_code in (200, 400)  # el nombre se sanea dentro de la carpeta temporal
    assert cliente.post("/api/aplicativo/abrir").status_code in (200, 404)

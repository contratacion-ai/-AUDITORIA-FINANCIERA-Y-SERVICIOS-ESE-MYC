"""Red de sedes: reconocimiento por alias de carpeta y por código de habilitación."""
import shutil

import pytest

from rips_auditoria import config
from rips_auditoria.ingesta import contexto
from rips_auditoria.ingesta.lote import procesar
from tests.conftest import TXT, zip_bytes


@pytest.mark.parametrize("carpeta,sede,municipio", [
    ("HOSPITAL SAN LORENZO DE ARAUQUITA", "Hospital San Lorenzo de Arauquita", "ARAUQUITA"),
    ("Hospital San Juan de Dios de Puerto Rondón-20261027T135319Z-1-001", "Hospital San Juan de Dios de Puerto Rondón",
     "PUERTO RONDON"),
    ("hospital san francisco de fortul", "Hospital San Francisco de Fortul", "FORTUL"),
    ("Hospital San Jose de Cravo Norte", "Hospital San José de Cravo Norte", "CRAVO NORTE"),
    ("Hospital San Antonio de Tame", "Hospital San Antonio de Tame", "TAME"),
    ("Centro de Salud Jesus Coronel", "Centro de Salud Jesús Coronel", "TAME"),
    ("RIPS SEDE ARAUQUITA", "Hospital San Lorenzo de Arauquita", "ARAUQUITA"),
])
def test_sede_por_carpeta(carpeta, sede, municipio):
    c = contexto.desde_ruta(f"{carpeta}/08. Agosto/NUEVA EPS CAPITA/AF000123.txt", 2026)
    assert c.hospital == sede and c.municipio == municipio and c.fuente_hospital == "carpeta (sede)"


def test_cinco_municipios_tienen_sede():
    assert {s["municipio"] for s in config.sedes()} >= {"TAME", "ARAUQUITA", "FORTUL", "CRAVO NORTE", "PUERTO RONDON"}


def test_codigo_de_habilitacion_identifica_la_sede():
    """Carpeta con nombre libre pero RIPS con el código de Jesús Coronel."""
    txt = {t: v.replace("817940099901", "817940020607") for t, v in TXT.items()}
    l = procesar([("Carpeta de Pedro/08. Agosto/NUEVA EPS/" + f"{t}000123.txt", v.encode()) for t, v in txt.items()])
    r = l.remisiones.iloc[0]
    assert r.hospital == "Centro de Salud Jesús Coronel" and r.fuente_hospital == "contenido (sede)"
    assert not (l.incidencias.regla == "HOSPITAL_NO_DETERMINADO").any()


def test_editar_sedes_desde_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import api.app as m
    cfg = tmp_path / "config"
    shutil.copytree(config.DIR_CONFIG, cfg)
    monkeypatch.setattr(config, "DIR_CONFIG", cfg)
    config.red_prestadores.cache_clear()
    try:
        cli = TestClient(m.app)
        sedes = cli.get("/api/sedes").json()["sedes"]
        sedes.append({"nombre": "Puesto de Salud Panamá de Arauca", "municipio": "ARAUQUITA",
                      "alias": ["PUESTO DE SALUD PANAMA"], "cod_habilitacion": ["810650000001"], "verificado": False})
        assert cli.put("/api/sedes", json={"sedes": sedes}).status_code == 200
        assert contexto.sede_por_codigo("810650000001")["nombre"] == "Puesto de Salud Panamá de Arauca"
        c = contexto.desde_ruta("Puesto de Salud Panama/03. Marzo/NUEVA EPS/AF1.txt", 2026)
        assert c.hospital == "Puesto de Salud Panamá de Arauca" and c.municipio == "ARAUQUITA"
        assert cli.put("/api/sedes", json={"sedes": [{"nombre": "X", "municipio": ""}]}).status_code == 400
    finally:
        config.red_prestadores.cache_clear()

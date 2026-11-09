"""Lanzador de escritorio: puerto libre, reconocimiento de la propia instancia y carpeta de datos."""

import socket

import pytest

from escritorio import lanzador


def test_puerto_ocupado_por_otro_programa_se_salta():
    with socket.socket() as otro:
        otro.bind(("127.0.0.1", 0))
        otro.listen()
        ocupado = otro.getsockname()[1]
        assert lanzador.puerto_libre(ocupado) != ocupado


def test_otro_programa_no_se_confunde_con_la_herramienta():
    with socket.socket() as otro:
        otro.bind(("127.0.0.1", 0))
        otro.listen()
        assert lanzador.es_nuestra_instancia(otro.getsockname()[1]) is False


def test_carpeta_de_datos_configurable(monkeypatch, tmp_path):
    monkeypatch.setenv("CONCILIACION_RIPS_ESE_DATOS", str(tmp_path))
    assert lanzador.carpeta_datos_por_defecto() == tmp_path


@pytest.mark.django_db
def test_endpoint_salud(client):
    assert client.get("/salud/").content.decode() == lanzador.FIRMA_SALUD

from datetime import date, datetime

import pytest

from homologacion_ceco.normalizar import (ErrorValor, centavos, codigo_ceco, cuenta, formato_pesos, periodo,
                                          periodo_en_nombre)


@pytest.mark.parametrize("entrada, esperado", [
    ("1.234.567,89", 123456789), ("1,234,567.89", 123456789), ("$ (1.000)", -100000), ("1.234", 123400),
    ("12,5", 1250), (1500000.0, 150000000), (2500000.5, 250000050), ("-3.000,10", -300010), ("500-", -50000),
    ("COP 2.000", 200000), (0, 0), ("1.5E3", 150000), (" 1 000 ", 100000),
])
def test_centavos(entrada, esperado):
    assert centavos(entrada) == esperado


@pytest.mark.parametrize("vacio", [None, "", "  ", "N/A", "#N/A", "-", float("nan")])
def test_centavos_vacios(vacio):
    assert centavos(vacio) is None


@pytest.mark.parametrize("malo", ["ABC", "1.2.3,4,5", "12,34,5", "1.234.5", True, datetime(2026, 1, 1)])
def test_centavos_invalidos(malo):
    with pytest.raises(ErrorValor):
        centavos(malo)


def test_centavos_es_exacto():
    assert sum(centavos("0,10") for _ in range(10)) == 100  # sin errores de punto flotante


@pytest.mark.parametrize("entrada, esperado", [
    (1020201, "1020201"), (1020201.0, "1020201"), (" 1020201 ", "1020201"),
    ("1020201 - HSLA-ARAUQUITA-CONSULTA EXTERNA", "1020201"), ("102-02-01", "1020201"), ("a10", "A10"), ("", ""),
])
def test_codigo_ceco(entrada, esperado):
    assert codigo_ceco(entrada) == esperado


def test_cuenta():
    assert cuenta("5101-01") == "510101"
    assert cuenta(51010101.0) == "51010101"
    with pytest.raises(ErrorValor):
        cuenta("51A01")


@pytest.mark.parametrize("entrada, esperado", [
    (datetime(2026, 1, 31), "2026-01"), (date(2026, 2, 1), "2026-02"), (46053, "2026-01"), ("2026-01", "2026-01"),
    ("2026/03", "2026-03"), ("01/2026", "2026-01"), ("202604", "2026-04"), ("31/01/2026", "2026-01"),
    ("2026-05-31", "2026-05"), ("2026-05-31 00:00:00", "2026-05"), ("ENERO 2026", "2026-01"), ("ENE-2026", "2026-01"),
    ("Febrero de 2026", "2026-02"), ("Setiembre 2026", "2026-09"), (202612, "2026-12"),
])
def test_periodo(entrada, esperado):
    assert periodo(entrada) == esperado


@pytest.mark.parametrize("malo", ["31/13/2026", "2026-13", "periodo", 5])
def test_periodo_invalido(malo):
    with pytest.raises(ErrorValor):
        periodo(malo)


@pytest.mark.parametrize("nombre, esperado", [
    ("EF_2026-01.xlsx", "2026-01"), ("costos enero 2026.csv", "2026-01"), ("matriz_marzo_2026.xlsx", "2026-03"),
    ("202603.xlsx", "2026-03"), ("06-2026 nomina.xlsx", "2026-06"), ("archivo.xlsx", ""), ("1020201.xlsx", ""),
])
def test_periodo_en_nombre(nombre, esperado):
    assert periodo_en_nombre(nombre) == esperado


def test_formato_pesos():
    assert formato_pesos(123456789) == "$ 1.234.567,89"
    assert formato_pesos(-5000) == "-$ 50,00"

from datetime import date, datetime
from decimal import Decimal as D

import pytest

from cargas import limpieza as L
from cargas.esquemas import esquema
from cargas.lectores import indice_columna
from cargas.procesador import sugerir_mapeo


@pytest.mark.parametrize("entrada,esperado", [
    ("1.234.567,89", D("1234567.89")),
    ("1,234,567.89", D("1234567.89")),
    ("$ 1.234.567", D("1234567")),
    ("(1.000)", D("-1000")),
    ("1.500-", D("-1500")),
    ("-2.345,5", D("-2345.5")),
    ("12,5", D("12.5")),
    ("0.75", D("0.75")),
    ("1.234", D("1234")),
    (" 4500000 ", D("4500000")),
    (4773766736.25, D("4773766736.25")),
    (1500, D("1500")),
    ("1E+3", D("1E+3")),
])
def test_numero(entrada, esperado):
    assert L.numero(entrada) == esperado


def test_numero_separador_configurado():
    assert L.numero("1.234", ",") == D("1234")
    assert L.numero("1.234", ".") == D("1.234")
    assert L.numero("1,5", ",") == D("1.5")


@pytest.mark.parametrize("malo", ["abc", "1.2.3,4,5", "$"])
def test_numero_invalido(malo):
    with pytest.raises(L.ErrorValor):
        L.numero(malo)


@pytest.mark.parametrize("vacio", [None, "", "  ", "#N/A", "nan", float("nan"), "#REF!", "-"])
def test_vacios(vacio):
    assert L.es_vacio(vacio)
    assert L.numero(vacio) is None


@pytest.mark.parametrize("entrada,esperado", [
    ("15/09/2025", date(2025, 9, 15)), ("2025-09-15", date(2025, 9, 15)), ("15-09-2025", date(2025, 9, 15)),
    (datetime(2025, 9, 15, 10, 30), date(2025, 9, 15)), (45915, date(2025, 9, 15)), ("15/09/2025 08:00", date(2025, 9, 15)),
])
def test_fechas(entrada, esperado):
    assert L.fecha(entrada) == esperado


def test_fecha_invalida():
    with pytest.raises(L.ErrorValor):
        L.fecha("31/02/2025")


def test_tildes_y_claves():
    assert L.clave("  Centro de Costos MYC ") == L.clave("CENTRO DE COSTOS MYC")
    assert L.clave("Imagenología") == "IMAGENOLOGIA"
    assert L.texto("  Odontología  general ") == "Odontología general"
    assert L.codigo(890201.0) == "890201"
    assert L.solo_digitos("5101.01-02") == "51010102"


def test_seudonimo_irreversible_y_estable():
    a = L.seudonimo("1.098.765.432", "llave")
    assert a == L.seudonimo("1098765432", "llave")
    assert "1098765432" not in a and len(a) == 40
    assert a != L.seudonimo("1098765432", "otra-llave")
    assert L.seudonimo("", "llave") == ""


def test_columnas_por_nombre_y_letra():
    enc = ["Documento Tercero", "NombreCentroCosto", "Valor Nuevo Saldo"]
    assert indice_columna("documento tercero", enc) == 0
    assert indice_columna("#C", enc) == 2
    assert indice_columna("EPS", enc) is None


def test_sugerir_mapeo_con_sinonimos():
    enc = ["CODIGOCUENTA", "NombreCuentaContable", "NombreCentroCosto", "DocumentoTercero", "ValorNuevoSaldo"]
    m = sugerir_mapeo(enc, esquema("ESTADO_FINANCIERO"))
    assert m["cuenta"] == "CODIGOCUENTA"
    assert m["centro_costo"] == "NombreCentroCosto"
    assert m["documento_tercero"] == "DocumentoTercero"
    assert m["valor"] == "ValorNuevoSaldo"

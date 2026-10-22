import io

import pytest
from openpyxl import Workbook

from homologacion_ceco.ejemplo import lote_ejemplo
from homologacion_ceco.procesador import Opciones, procesar
from homologacion_ceco.referencia import cargar_referencia


@pytest.fixture(scope="session")
def ref():
    return cargar_referencia()


@pytest.fixture(scope="session")
def resultado_lote(ref):
    return procesar([], ref, Opciones(), contenidos=[("lote.zip", lote_ejemplo())])


def xlsx(filas, hoja="Hoja1"):
    wb = Workbook()
    ws = wb.active
    ws.title = hoja
    for f in filas:
        ws.append(list(f))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def pesos(cent):
    return round(cent / 100, 2)

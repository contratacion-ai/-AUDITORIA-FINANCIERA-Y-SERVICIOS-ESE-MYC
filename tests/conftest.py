"""Datos de prueba sintéticos: ningún dato real de la ESE entra al repositorio."""
import datetime as dt
import io
import sys
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ese_control import catalogo, db  # noqa: E402
from ese_control.validacion import Opciones  # noqa: E402

HOY = dt.date(2026, 11, 11)


@pytest.fixture
def bd(tmp_path):
    ruta = tmp_path / "prueba.db"
    db.inicializar(ruta, con_catalogo_inicial=False)
    sedes = pd.DataFrame([
        ["NOR-01", "Hospital del Norte", "Hospital del Norte", "Norte", "Hospital", "SI", "SI", "SI", ""],
        ["NOR-02", "Hospital del Norte", "Puesto de Salud La Vega", "Norte", "Puesto de salud", "NO", "SI", "SI", ""],
        ["SUR-01", "Hospital del Sur", "Hospital del Sur", "Sur", "Hospital", "SI", "SI", "SI", ""],
    ], columns=catalogo.HOJAS["Sedes"])
    codigos = pd.DataFrame([
        ["CUENTA_ENERGIA", "1001", "NOR-01", "", "SI"], ["CUENTA_ENERGIA", "1002", "NOR-02", "", "SI"],
        ["CUENTA_ENERGIA", "2001", "SUR-01", "", "SI"], ["CENTRO_COSTO", "CC-510", "SUR-01", "", "SI"],
        ["ALIAS", "Norte", "NOR-01", "", "SI"], ["ALIAS", "HOSPITAL SUR", "SUR-01", "", "SI"],
    ], columns=catalogo.HOJAS["Codigos"])
    vehiculos = pd.DataFrame([
        ["AAA111", "NOR-01", "Ambulancia", "Ambulancia", "Diésel (ACPM)", 40, "SI", ""],
        ["BBB222", "SUR-01", "Ambulancia", "Ambulancia", "Gasolina corriente", 20, "SI", ""],
    ], columns=catalogo.HOJAS["Vehiculos"])
    catalogo.aplicar({"Sedes": sedes, "Codigos": codigos, "Vehiculos": vehiculos}, "prueba", ruta=ruta)
    return ruta


@pytest.fixture
def opciones():
    return lambda **kw: Opciones(**{"alcance": "todos", "hoy": HOY, **kw})


def libro(hojas: dict) -> bytes:
    """Crea un .xlsx en memoria a partir de {nombre_hoja: [filas]}."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for nombre, filas in hojas.items():
        ws = wb.create_sheet(nombre)
        for fila in filas:
            ws.append(fila)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def matriz_energia(valores: dict, anio=2026, extra=()) -> bytes:
    """Cuadro anual de energía: {codigo: [(kwh, facturado, total) por mes]}."""
    meses = ["Enero", "febrero", "Marzo", "Abril"]
    f1 = [None, f"AÑO {anio}", None]
    f2 = ["ITEM", "CODIGO", "USUARIO"]
    for m in meses:
        f1 += [m, None, None]
        f2 += ["Kw/mes", "V.facturado", "V. Total"]
    filas = [f1, f2]
    for i, (cod, datos) in enumerate(valores.items(), 1):
        fila = [i, cod, f"Sede {cod}"]
        for trio in datos:
            fila += list(trio)
        filas.append(fila)
    total = ["TOTAL", None, None] + [sum(d[k // 3][k % 3] for d in valores.values() if k // 3 < len(d))
                                     for k in range(3 * max(len(d) for d in valores.values()))]
    filas += [total, [], [], [None, "1001", "nota suelta", 999999]] + list(extra)
    return libro({"Consumo Energia": filas})

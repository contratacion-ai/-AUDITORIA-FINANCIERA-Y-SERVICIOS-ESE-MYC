"""Cruce de medicamentos facturados en RIPS vs. portafolio de farmacia."""

from __future__ import annotations

import io

import openpyxl
import pandas as pd
import pytest

from rips_auditoria import catalogo_cups as cc
from rips_auditoria import cruce_medicamentos as cm
from rips_auditoria import tablero as tb
from rips_auditoria.aplicativo import COLUMNAS


def _med(cum, cant, valor, desc="", factura="F1", mes="2026-05"):
    return dict(zip(COLUMNAS, [mes, "NUEVA EPS", "", "CONTRATO", "", "EVENTO", "HOSPITAL SAN ANTONIO", factura, "",
                               "Medicamentos", cum, desc, cant, valor, valor, "CONCILIADA"], strict=True))


@pytest.fixture
def libro(tmp_path):
    """Libro con la misma estructura de CRUCE_PORTAFOLIO_FARMACIA_vs_MAESTRO.xlsx."""
    ruta = tmp_path / "CRUCE_PORTAFOLIO_FARMACIA_vs_MAESTRO.xlsx"
    portafolio = pd.DataFrame({
        "Ítem": [1, 2, 3, 4, 5],
        "Código Salud 360": ["G-0002", "G-0003", "G-00132", "G-00456", "G-0002"],
        "CUM farmacia": ["053560-05", "\t207411-4", "20065664-1", None, "53560-5"],
        "CUM normalizado": ["53560-5", "207411-4", "20065664-1", None, "53560-5"],  # 53560-5 repetido
        "Principio activo": ["ACETAMINOFEN", "ACETAMINOFEN", "ABACAVIR", "ALIMENTO F-75", "ACETAMINOFEN"],
        "Concentración": ["500mg", "150mg/5ml", "20mg/ml", None, "500mg"],
        "Forma farm.": ["TABLETA", "JARABE", "SOLUCIÓN ORAL", None, "TABLETA"],
        "Laboratorio": ["GENFAR", "LAPROFF", "HETERO LABS", "SANUTEAM", "OTRO"],
        "Precio compra": [77, 2500, 76530, None, 80],
        "Precio a usar": [104, 3375, 103316, None, 110],
        "Alerta precio": ["SIN PRECIO VENTA"] * 5,
        "RESULTADO CRUCE": ["ENCONTRADO", "ENCONTRADO", "ENCONTRADO", "SIN CUM EN PORTAFOLIO", "ENCONTRADO"],
        "Código(s) maestro": ["172", "1294", "1067", None, "2"],
        "Sugerencia / acción": [None, None, None, "Sin CUM", None],
    })
    with pd.ExcelWriter(ruta) as w:
        pd.DataFrame([["CRUCE PORTAFOLIO FARMACIA vs MAESTRO", None], ["HALLAZGOS Y ACCIONES RECOMENDADAS", None],
                      ["1. Depurar los CUM", None]]).to_excel(w, sheet_name="RESUMEN_EJECUTIVO", index=False, header=False)
        pd.DataFrame([["PARÁMETROS EDITABLES", None], ["Margen sugerido medicamentos", 0.35],
                      ["Margen mínimo aceptable", 0.2]]).to_excel(w, sheet_name="PARAMETROS", index=False, header=False)
        portafolio.to_excel(w, sheet_name="PORTAFOLIO_CRUZADO", index=False)
        pd.DataFrame({"CUM farmacia": ["19992832-1"], "Resultado": ["NO ENCONTRADO"]}).to_excel(
            w, sheet_name="NO_ENCONTRADOS", index=False)
    return ruta


@pytest.fixture
def acumulado():
    carpeta = tb.carpeta_acumulado()
    carpeta.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        _med("19935303-4", 10, 500),          # no está en el portafolio
        _med("053560-005", 100, 15000),       # 150 c/u: precio 104 -> sobre precio (CUM con ceros)
        _med("207411-4", 2, 3000, factura="F2"),  # 1.500 c/u < compra 2.500 -> bajo costo
        _med("20065664-1", 1, 90000, factura="F3"),  # sobre costo pero margen 15% < 20% -> margen bajo
    ]).to_csv(carpeta / "2026-05.csv", index=False)
    return carpeta


def test_lectura_del_libro_de_farmacia(libro):
    r = cm.cargar_cruce(libro)
    assert r["cum_portafolio"] == 3 and r["sin_cum"] == 1 and r["cum_repetidos"] == 1
    assert r["margen_minimo"] == 0.2
    assert {"RESUMEN_EJECUTIVO", "NO_ENCONTRADOS"} <= set(r["hojas"])
    assert cm.hoja("no_encontrados")["CUM farmacia"].tolist() == ["19992832-1"]
    assert cm.normalizar_cum(" \t019942561-06 ") == "19942561-6" and cm.normalizar_cum("VND") == ""


def test_cruce_de_medicamentos_facturados(libro, acumulado):
    cm.cargar_cruce(libro)
    df = tb.cargar(acumulado)[0]
    c = cm.cruzar(df).set_index("CUM")
    assert c.loc["19935303-4", "estado_cruce"] == "NO ESTÁ EN PORTAFOLIO"
    assert c.loc["053560-005", "estado_cruce"] == "FACTURADO SOBRE PRECIO"
    assert c.loc["053560-005", "principio_activo"] == "ACETAMINOFEN"  # CUM con ceros cruza
    assert c.loc["207411-4", "estado_cruce"] == "FACTURADO BAJO COSTO"
    assert c.loc["207411-4", "impacto"] == (3375 - 1500) * 2
    assert c.loc["20065664-1", "estado_cruce"] == "MARGEN BAJO"
    assert list(c["valor"]) == sorted(c["valor"], reverse=True)
    r = cm.resumen(df)
    assert r["cum_facturados"] == 4 and r["en_portafolio"] == 3 and r["valor_fuera_portafolio"] == 500
    assert r["portafolio_no_facturado"] == 0
    # alertas del tablero
    al = tb.alertas(df, pd.DataFrame(columns=tb.COLUMNAS_INDICADORES))
    cats = {a["categoria"] for a in al}
    assert {"Medicamento bajo costo", "Medicamento fuera de portafolio"} <= cats


def test_descripciones_de_cum_desde_el_portafolio(libro, acumulado):
    r = cm.cargar_cruce(libro)
    assert r["cum_portafolio"] == 3
    res = cc.actualizar_descripciones()
    assert res["completadas"].get("portafolio farmacia") == 3
    df, ind = tb.cargar(acumulado)
    g = tb.tabla(df, ind, "cups").set_index("CODIGO_CUPS_CUMS")
    assert g.loc["207411-4", "descripcion"] == "ACETAMINOFEN 150mg/5ml JARABE"
    assert g.loc["207411-4", "fuente_descripcion"] == "portafolio farmacia"


def test_api_y_excel_con_cruce_de_farmacia(libro, acumulado, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from rips_auditoria.web import servidor

    monkeypatch.setenv("AUDITORIA_ESE_DIR", "/no/existe")
    c = TestClient(servidor.app, client=("127.0.0.1", 50000))
    r = c.post("/api/catalogos/farmacia", files={"archivo": (libro.name, libro.read_bytes())})
    assert r.status_code == 200 and r.json()["cum_portafolio"] == 3
    assert c.get("/api/catalogos/estado").json()["farmacia"]["cum_portafolio"] == 3
    res = c.get("/api/farmacia/resumen").json()
    assert res["por_estado"]["FACTURADO BAJO COSTO"] == 1
    t = c.get("/api/tablero/tabla/farmacia").json()
    assert t["total"] == 4 and "estado_cruce" in t["columnas"]
    for nombre in ("farmacia_no_facturados", "farmacia_resumen", "farmacia_no_encontrados", "farmacia_insumos"):
        assert c.get(f"/api/tablero/tabla/{nombre}").status_code == 200
    x = c.get("/api/tablero/excel")
    wb = openpyxl.load_workbook(io.BytesIO(x.content), read_only=True)
    assert "Medicamentos vs farmacia" in wb.sheetnames

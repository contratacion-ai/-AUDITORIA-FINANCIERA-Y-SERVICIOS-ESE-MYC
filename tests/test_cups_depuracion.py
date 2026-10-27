"""Informe por CUPS enriquecido, descripciones del tarifario, limpieza, duplicados y JSON."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from rips_auditoria import catalogo_cups as cc
from rips_auditoria import depuracion as dp
from rips_auditoria import json_rips
from rips_auditoria import tablero as tb
from rips_auditoria.aplicativo import COLUMNAS


def _fila(mes, sede, factura, cups, valor, cant=1.0, eps="NUEVA EMPRESA PROMOTORA DE SALUD", modalidad="EVENTO",
          tipo="Procedimientos", desc=""):
    return dict(zip(COLUMNAS, [mes, eps, "", "CONTRATO X", "", modalidad, sede, factura, "", tipo, cups, desc,
                               cant, valor, valor, "CONCILIADA"], strict=True))


def _acumulado(filas: list[dict]):
    carpeta = tb.carpeta_acumulado()
    carpeta.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(filas)
    for mes, g in df.groupby("MES"):
        g.to_csv(carpeta / f"{mes}.csv", index=False, encoding="utf-8")
    pd.DataFrame([{"MES": m, "SEDE": s, "usuarios": 1} for m, s in df[["MES", "SEDE"]].drop_duplicates().itertuples(
        index=False)]).to_csv(carpeta / tb.ARCHIVO_INDICADORES, index=False)
    return carpeta


@pytest.fixture
def base():
    return _acumulado([
        _fila("2026-05", "HOSPITAL SAN ANTONIO", "F1", "890201", 1000, tipo="Consultas"),
        _fila("2026-05", "HOSPITAL SAN ANTONIO", "F2", "010101", 500, eps="ENTIDAD PROMOTORA DE SALUD SANITAS S A S",
              modalidad="CAPITA"),
        _fila("2026-05", "HOSPITAL SAN JOSE", "J1", "890201", 2000, tipo="Consultas", eps="COOSALUD EPS"),
        _fila("2026-06", "HOSPITAL SAN ANTONIO", "F3", "890201", 3000, tipo="Consultas", eps="OTRA EPS"),
        _fila("2026-06", "HOSPITAL SAN JOSE", "J2", "19942561-6", 70, cant=7, tipo="Medicamentos",
              desc="ACETAMINOFEN 500MG", eps="SALUD TOTAL"),
    ])


def _tabla_cups():
    df, ind = tb.cargar(tb.carpeta_acumulado())
    return tb.tabla(df, ind, "cups").set_index("CODIGO_CUPS_CUMS")


# ---------------------------------------------------------------------------
# 1. Campos del Excel unidos por código
# ---------------------------------------------------------------------------

def test_union_de_campos_del_excel_con_los_cups(base, tmp_path):
    antes = _tabla_cups()
    excel = tmp_path / "RIPS_cups.xlsx"
    pd.DataFrame({
        "CODIGO_CUPS_CUMS": [890201, "010101", "999999"],  # Excel convierte 890201 en número
        "descripcion": ["CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL", "", "NO ESTÁ EN LOS RIPS"],
        " valor ": [1, 2, 3],  # ya lo calcula el informe: no se duplica
        "grupo auditoria": ["P Y P", "ODONTOLOGIA", "X"],  # columna libre: se une por código
        "pormedio mes": ["", "", ""], "nueva eps": ["", "", ""], "sanitas ": ["", "", ""], "coosalud": ["", "", ""],
        "otras": ["", "", ""], "evento": ["", "", ""], "capita": ["", "", ""],
    }).to_excel(excel, index=False)
    r = cc.cargar_complemento(excel)
    assert r["codigos"] == 3
    g = _tabla_cups()
    # se conservan las columnas y el orden del informe; los campos nuevos van al final
    originales = [c for c in antes.reset_index().columns if c != "fuente_descripcion"]
    assert list(g.reset_index().columns[: len(originales)]) == originales
    assert g.loc["890201", "grupo auditoria"] == "P Y P" and g.loc["010101", "grupo auditoria"] == "ODONTOLOGIA"
    assert g.loc["19942561-6", "grupo auditoria"] == ""
    assert (g["valor"] == antes["valor"]).all() and " valor " not in g.columns
    # columnas calculadas con los datos: 890201 = 1000 (Nueva EPS) + 2000 (Coosalud) + 3000 (otra) en 2 meses
    assert g.loc["890201", "pormedio mes"] == 3000
    assert g.loc["890201", "nueva eps"] == 1000 and g.loc["890201", "coosalud"] == 2000
    assert g.loc["890201", "otras"] == 3000
    # nombres de columna sin espacios sobrantes ("sanitas " -> "sanitas")
    assert g.loc["010101", "sanitas"] == 500 and g.loc["010101", "capita"] == 500
    eps = ["nueva eps", "sanitas", "coosalud", "otras"]
    assert (g[eps].sum(axis=1) == g["valor"]).all()
    assert (g[["evento", "capita"]].sum(axis=1) == g["valor"]).all()
    # descripción vacía en los RIPS tomada del archivo
    assert g.loc["890201", "descripcion"] == "CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL"
    assert g.loc["890201", "fuente_descripcion"] == "archivo complementario"
    assert g.loc["19942561-6", "fuente_descripcion"] == "RIPS"


def test_complemento_csv_con_separador_punto_y_coma(base, tmp_path):
    csv = tmp_path / "cups.csv"
    csv.write_text("﻿CODIGO_CUPS_CUMS;descripcion;observacion\r\n890201;CONSULTA;REVISAR TARIFA\r\n",
                   encoding="utf-8")
    cc.cargar_complemento(csv)
    assert _tabla_cups().loc["890201", "observacion"] == "REVISAR TARIFA"


# ---------------------------------------------------------------------------
# 2. Descripciones faltantes desde el tarifario UVB (persistentes)
# ---------------------------------------------------------------------------

def test_descripciones_desde_el_tarifario_se_guardan(base, tmp_path):
    tarifario = tmp_path / "Tarifa_UVB.xlsx"
    pd.DataFrame({
        "MAPIISS": [890201, 10101, " AD2772", 890201],  # 10101 = 010101 sin el cero; 890201 repetido
        "DESCRIPCION": ["CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL", "PUNCION CISTERNAL", None, "OTRA"],
        "SOAT-2026-UVB": [54616.1, 120000, 0, 1],
    }).to_excel(tarifario, index=False)
    r = cc.cargar_tarifario(tarifario)
    assert r["codigos"] == 2 and r["codigos_repetidos"] == 1 and r["columna_tarifa"] == "SOAT-2026-UVB"
    res = r["descripciones"]  # al cargar el tarifario se completan y guardan
    assert res["completadas"] == {"tarifario UVB": 2} and res["sin_descripcion"] == 0
    guardadas = pd.read_csv(cc.carpeta_catalogos() / cc.ARCHIVO_DESCRIPCIONES, dtype=str)
    assert set(guardadas["codigo"]) == {"890201", "010101"}
    g = _tabla_cups()
    assert g.loc["010101", "descripcion"] == "PUNCION CISTERNAL"
    assert g.loc["010101", "SOAT-2026-UVB (tarifario)"] == 120000
    assert g.loc["19942561-6", "descripcion"] == "ACETAMINOFEN 500MG"  # la de los RIPS no se toca
    # persistentes: sin el tarifario siguen disponibles y no se recalculan
    (cc.carpeta_catalogos() / cc.ARCHIVO_TARIFARIO).unlink()
    cc.actualizar_descripciones()
    assert _tabla_cups().loc["010101", "descripcion"] == "PUNCION CISTERNAL"


def test_el_archivo_complementario_tiene_prioridad_sobre_el_tarifario(base, tmp_path):
    pd.DataFrame({"MAPIISS": ["890201"], "DESCRIPCION": ["DEL TARIFARIO"]}).to_csv(tmp_path / "t.csv", index=False)
    pd.DataFrame({"CODIGO_CUPS_CUMS": ["890201"], "descripcion": ["DEL EXCEL"]}).to_csv(tmp_path / "c.csv", index=False)
    cc.cargar_tarifario(tmp_path / "t.csv")
    cc.cargar_complemento(tmp_path / "c.csv")
    cc.actualizar_descripciones()
    assert _tabla_cups().loc["890201", "descripcion"] == "DEL EXCEL"


# ---------------------------------------------------------------------------
# 3. Limpieza por mes y por sede, con vista previa y confirmación
# ---------------------------------------------------------------------------

def _filas():
    return tb.cargar(tb.carpeta_acumulado())[0]


def test_limpieza_por_mes(base):
    v = dp.vista_limpieza(mes="2026-05")
    assert v["total_filas"] == 3 and v["indicadores"] == 2
    assert {(r["MES"], r["SEDE"]) for r in v["resumen"]} == {("2026-05", "HOSPITAL SAN ANTONIO"), ("2026-05", "HOSPITAL SAN JOSE")}
    assert len(_filas()) == 5  # la vista previa no borra
    r = dp.aplicar_limpieza(v["ficha"], mes="2026-05")
    assert r == {"eliminadas": 3, "indicadores_eliminados": 2}
    assert set(_filas()["MES"]) == {"2026-06"}
    assert not (base / "2026-05.csv").exists()
    assert list((base / "papelera").glob("*.csv"))  # copia de lo eliminado


def test_limpieza_por_sede_y_por_ambos(base):
    v = dp.vista_limpieza(sede="Hospital San José")  # se compara normalizado
    assert v["total_filas"] == 2
    v2 = dp.vista_limpieza(mes="2026-06", sede="hospital san jose")
    assert v2["total_filas"] == 1
    dp.aplicar_limpieza(v2["ficha"], mes="2026-06", sede="hospital san jose")
    d = _filas()
    assert len(d) == 4 and not ((d["MES"] == "2026-06") & (d["SEDE"] == "HOSPITAL SAN JOSE")).any()


def test_limpieza_exige_criterio_y_ficha_vigente(base):
    with pytest.raises(ValueError, match="mes, una sede"):
        dp.vista_limpieza()
    v = dp.vista_limpieza(mes="2026-06")
    with pytest.raises(dp.FichaVencida):
        dp.aplicar_limpieza("otra-ficha", mes="2026-06")
    # si los datos cambian después de la vista previa, no se borra
    _acumulado([_fila("2026-07", "HOSPITAL SAN ANTONIO", "F9", "890201", 10)])
    with pytest.raises(dp.FichaVencida):
        dp.aplicar_limpieza(v["ficha"], mes="2026-06")
    assert (_filas()["MES"] == "2026-06").sum() == 2


# ---------------------------------------------------------------------------
# Duplicados reales
# ---------------------------------------------------------------------------

def test_deteccion_y_depuracion_de_duplicados(base):
    carpeta = base
    mayo = pd.read_csv(carpeta / "2026-05.csv", dtype=str, keep_default_na=False)
    junio = pd.read_csv(carpeta / "2026-06.csv", dtype=str, keep_default_na=False)
    pd.concat([mayo, mayo.iloc[[0]]]).to_csv(carpeta / "2026-05.csv", index=False)  # exacto
    otra = mayo.iloc[[2]].assign(MES="2026-06", CANTIDAD="1")  # misma factura J1 cargada en junio ("1" == 1.0)
    corregida = mayo.iloc[[1]].assign(MES="2026-06", VALOR_SERVICIO="900")  # valor distinto: NO es duplicado
    pd.concat([junio, otra, corregida]).to_csv(carpeta / "2026-06.csv", index=False)
    v = dp.vista_duplicados()
    assert v["total_filas"] == 2
    assert v["por_tipo"] == {"exacto": 1, "factura repetida en otro mes": 1}
    repetida = [f for f in v["filas"] if f["tipo_duplicado"] == "factura repetida en otro mes"][0]
    assert repetida["MES"] == "2026-06" and repetida["FACTURA_FEV"] == "J1"  # se conserva la de mayo
    r = dp.aplicar_duplicados(v["ficha"])
    assert r["eliminadas"] == 2
    assert dp.vista_duplicados()["total_filas"] == 0
    d = _filas()
    assert len(d) == 6 and ((d["FACTURA_FEV"] == "F2") & (d["MES"] == "2026-06")).sum() == 1


# ---------------------------------------------------------------------------
# 4. JSON con registros repetidos
# ---------------------------------------------------------------------------

def _factura_2275(factura, consultas, medicamentos=()):
    return {"numDocumentoIdObligado": "900034131", "numFactura": factura, "tipoNota": None, "numNota": None,
            "usuarios": [{"tipoDocumentoIdentificacion": "CC", "numDocumentoIdentificacion": "123", "consecutivo": 1,
                          "servicios": {"consultas": list(consultas), "medicamentos": list(medicamentos),
                                        "urgencias": [{"fechaInicioAtencion": "2026-07-03 10:00"}]}}]}


def _consulta(cons, valor=30000, cod="890201"):
    return {"codPrestador": "810010000101", "fechaInicioAtencion": "2026-07-02 08:00", "codConsulta": cod,
            "vrServicio": valor, "consecutivo": cons}


def test_json_2275_con_registros_repetidos(base, tmp_path):
    carpeta = tmp_path / "Hospital San Antonio" / "07. Julio" / "10204 NUEVA EPS EVENTO"
    carpeta.mkdir(parents=True)
    med = {"codPrestador": "810010000101", "fechaDispensAdmon": "2026-07-02 09:00", "codTecnologiaSalud": "19942561-6",
           "nomTecnologiaSalud": "ACETAMINOFEN 500MG", "cantidadMedicamento": 10, "vrServicio": 1000, "consecutivo": 1}
    doc = _factura_2275("FJ100", [_consulta(1), _consulta(1), _consulta(2)], [med])  # consulta 1 repetida
    (carpeta / "FJ100.json").write_text(json.dumps(doc), encoding="utf-8")
    (carpeta / "FJ100_copia.json").write_text(json.dumps(doc), encoding="utf-8")  # mismo archivo dos veces
    (carpeta / "malo.json").write_text("{no es json", encoding="utf-8")
    r = json_rips.leer_json([tmp_path / "Hospital San Antonio"])
    assert r["resumen"]["archivos_json"] == 3 and r["resumen"]["registros_leidos"] == 8
    assert r["resumen"]["repetidos_en_json"] == 5  # 1 dentro del archivo + 4 del archivo copiado
    f = r["filas"].set_index("TIPO_SERVICIO")
    assert len(r["filas"]) == 2
    assert f.loc["Consultas", "CANTIDAD"] == 2 and f.loc["Consultas", "VALOR_SERVICIO"] == 60000
    assert f.loc["Medicamentos", "CANTIDAD"] == 10
    assert set(r["filas"]["MES"]) == {"2026-07"} and set(r["filas"]["SEDE"]) == {"HOSPITAL SAN ANTONIO"}
    assert set(r["filas"]["CONTRATO"]) == {"NUEVA EPS EVENTO"} and set(r["filas"]["MODALIDAD"]) == {"EVENTO"}
    assert any("JSON inválido" in a for a in r["avisos"])
    # integrar: la primera vez agrega; la segunda no duplica
    res = json_rips.cargar_json([tmp_path / "Hospital San Antonio"])
    assert res["agregadas"] == 2 and res["meses"] == ["2026-07"]
    res2 = json_rips.cargar_json([tmp_path / "Hospital San Antonio"])
    assert res2["agregadas"] == 0 and res2["ya_cargadas"] == 2
    assert (_filas()["MES"] == "2026-07").sum() == 2
    assert dp.vista_duplicados()["total_filas"] == 0


def test_json_homologado_no_duplica_lo_cargado_desde_txt(base, tmp_path):
    ya = _fila("2026-05", "Hospital San Antonio", "F1", "890201", 1000, tipo="Consultas")  # igual al TXT
    nueva = _fila("2026-05", "HOSPITAL SAN ANTONIO", "F7", "890301", 800, tipo="Consultas")
    (tmp_path / "filas.json").write_text(json.dumps({"filas": [ya, nueva, nueva]}), encoding="utf-8")
    r = json_rips.cargar_json([tmp_path / "filas.json"])
    assert r["registros_leidos"] == 3 and r["repetidos_en_json"] == 1
    assert r["agregadas"] == 1 and r["ya_cargadas"] == 1
    d = _filas()
    assert ((d["FACTURA_FEV"] == "F1")).sum() == 1 and ((d["FACTURA_FEV"] == "F7")).sum() == 1


def test_json_sin_sede_ni_mes_se_reporta(tmp_path):
    (tmp_path / "f.json").write_text(json.dumps(_factura_2275("X1", [{"codConsulta": "890201", "vrServicio": 5}])),
                                     encoding="utf-8")
    r = json_rips.leer_json([tmp_path / "f.json"])
    assert r["filas"].empty and r["resumen"]["sin_mes_o_sede"] == 1
    r = json_rips.leer_json([tmp_path / "f.json"], hospital="Hospital San Lorenzo", mes="2026-08")
    assert r["filas"][["MES", "SEDE"]].values.tolist() == [["2026-08", "HOSPITAL SAN LORENZO"]]


# ---------------------------------------------------------------------------
# Pantallas (API de la ventana)
# ---------------------------------------------------------------------------

def test_api_catalogos_limpieza_duplicados_y_json(base, tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from rips_auditoria.web import servidor

    monkeypatch.setenv("AUDITORIA_ESE_DIR", "/no/existe")
    c = TestClient(servidor.app, client=("127.0.0.1", 50000))
    tarifa = tmp_path / "uvb.csv"
    pd.DataFrame({"MAPIISS": ["890201"], "DESCRIPCION": ["CONSULTA MEDICINA GENERAL"], "SOAT-2026-UVB": [54616]}).to_csv(
        tarifa, index=False)
    r = c.post("/api/catalogos/tarifario", files={"archivo": ("uvb.csv", tarifa.read_bytes())})
    assert r.status_code == 200 and r.json()["descripciones"]["completadas"] == {"tarifario UVB": 1}
    assert c.post("/api/catalogos/otro", files={"archivo": ("x.csv", b"a")}).status_code == 404
    assert c.get("/api/catalogos/estado").json()["tarifario"]["registros"] == 1
    t = c.get("/api/tablero/tabla/cups_enriquecido").json()
    fila = next(f for f in t["filas"] if f["CODIGO_CUPS_CUMS"] == "890201")
    assert fila["descripcion"] == "CONSULTA MEDICINA GENERAL" and fila["fuente_descripcion"] == "tarifario UVB"
    # limpieza: vista previa, sin confirmación no borra, con confirmación sí
    v = c.post("/api/datos/limpieza/vista", json={"mes": "2026-06"}).json()
    assert v["total_filas"] == 2
    assert c.post("/api/datos/limpieza/confirmar", json={"mes": "2026-06", "ficha": v["ficha"]}).status_code == 400
    assert c.post("/api/datos/limpieza/confirmar", json={"mes": "2026-06", "ficha": "x", "confirmar": True}).status_code == 409
    assert c.post("/api/datos/limpieza/vista", json={}).status_code == 400
    r = c.post("/api/datos/limpieza/confirmar", json={"mes": "2026-06", "ficha": v["ficha"], "confirmar": True})
    assert r.json()["eliminadas"] == 2
    assert c.get("/api/tablero/opciones").json()["meses"] == ["2026-05"]
    # duplicados
    vd = c.post("/api/datos/duplicados/vista").json()
    assert vd["total_filas"] == 0
    # JSON por la pantalla
    doc = _factura_2275("FJ200", [_consulta(1), _consulta(1)])
    r = c.post("/api/datos/json", files=[("archivos", ("a.json", json.dumps(doc).encode())),
                                         ("archivos", ("b.json", json.dumps(doc).encode()))],
               data={"hospital": "Hospital San Antonio"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["registros_leidos"] == 4 and d["repetidos_en_json"] == 3 and d["agregadas"] == 1
    assert c.post("/api/datos/json", files=[("archivos", ("a.json", json.dumps(doc).encode()))],
                  data={"hospital": "Hospital San Antonio"}).json()["agregadas"] == 0


def test_cargue_con_json_en_la_misma_carpeta(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    import time

    from fastapi.testclient import TestClient

    from rips_auditoria.web import servidor

    from . import fabrica

    monkeypatch.setenv("AUDITORIA_ESE_DIR", "/no/existe")
    c = TestClient(servidor.app, client=("127.0.0.1", 50000))
    archivos = [("archivos", (f"Hospital San Lorenzo/05. Mayo/123__SANITAS/Rips/{n}", t.encode(), "text/plain"))
                for n, t in fabrica.envio_valido().items()]
    doc = _factura_2275("FJ300", [_consulta(1, cod="890301"), _consulta(1, cod="890301")])
    archivos.append(("archivos", ("Hospital San Lorenzo/05. Mayo/json/FJ300.json", json.dumps(doc).encode(), "application/json")))
    tid = c.post("/api/cargues", files=archivos, data={"enviar": "false"}).json()["id"]
    for _ in range(200):
        t = c.get(f"/api/cargues/{tid}").json()
        if t["estado"] in ("terminado", "error"):
            break
        time.sleep(0.3)
    assert t["estado"] == "terminado", t["error"]
    j = t["resultado"]["json"]
    assert j["repetidos_en_json"] == 1 and j["agregadas"] == 1 and j["meses"] == ["2026-05"]
    d = _filas()
    assert set(d["SEDE"]) == {"HOSPITAL SAN LORENZO"} and (d["FACTURA_FEV"] == "FJ300").sum() == 1

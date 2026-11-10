"""Carga de cada formato: TXT, JSON, ZIP, RAR y facturación (CSV/XLSX)."""

import io
from decimal import Decimal

import pandas as pd
import pytest

from rips_conciliacion.facturacion import leer_facturacion
from rips_conciliacion.lectores import leer_rips
from rips_conciliacion.modelos import VERSION_2275, VERSION_3374
from utilidades import (AC_TXT, AF_TXT, FACTURACION_CSV, PRESTADOR, archivos_3374, crear_rar, crear_zip,
                        json_2275)


# --- TXT -------------------------------------------------------------------

def test_txt_3374_carga_todos_los_tipos_y_conserva_nombres_oficiales():
    res = leer_rips([(n, c) for n, c in archivos_3374().items()])
    assert res.version == VERSION_3374
    assert {k: len(v) for k, v in res.registros.items()} == {"CT": 2, "AF": 2, "US": 1, "AC": 2, "AP": 1, "AM": 1, "AT": 1}
    am = res.registros["AM"][0]
    assert am["Código del medicamento"] == "19912345-1"          # código sin alterar
    assert am["Valor total de medicamento"] == "10000"            # valor original como texto
    assert am["_linea"] == 1 and am["_archivo"] == "AM000001.txt"
    assert not [i for i in res.incidencias if i.severidad == "ERROR"]


def test_txt_linea_con_campos_de_mas_se_reporta_con_numero_de_linea_y_no_detiene():
    contenido = AC_TXT + "FE1003,X,CC,1,10/01/2024,,890201,10\n" + AC_TXT.splitlines()[0] + "\n"
    res = leer_rips(("AC000002.txt", contenido.encode()))
    assert len(res.registros["AC"]) == 3
    error = [i for i in res.incidencias if i.severidad == "ERROR"]
    assert len(error) == 1
    assert error[0].linea == 3
    assert "8 campos" in error[0].mensaje and "exige 17" in error[0].mensaje


def test_txt_valor_numerico_invalido_indica_campo():
    malo = AC_TXT.splitlines()[0].replace(",30000,0,30000", ",30.000x,0,30000")
    res = leer_rips(("AC000003.txt", (malo + "\n").encode()))
    assert "AC" not in res.registros
    (inc,) = res.incidencias
    assert inc.linea == 1 and inc.campo == "Valor de la consulta" and inc.valor == "30.000x"


def test_txt_campo_obligatorio_vacio():
    malo = AC_TXT.splitlines()[0].replace(",890201,", ",,")
    res = leer_rips(("AC000004.txt", (malo + "\n").encode()))
    assert res.incidencias[0].campo == "Código de consulta"


def test_txt_latin1_y_fecha_invalida_es_advertencia():
    linea = AF_TXT.splitlines()[0].replace("ESE MORENO Y CLAVIJO", "E.S.E. MORENO Y CLAVIJO – ÑANDÚ").replace("15/01/2024", "2024/13/45")
    res = leer_rips(("AF000009.txt", (linea + "\n").encode("cp1252")))
    assert len(res.registros["AF"]) == 1
    assert "ÑANDÚ" in res.registros["AF"][0]["Razón social o apellidos y nombre del prestador"]
    assert res.incidencias[0].severidad == "ADVERTENCIA"


def test_control_ct_compara_total_de_registros():
    archivos = archivos_3374()
    archivos["CT000001.txt"] = f"{PRESTADOR},31/01/2024,AF000001,5\n".encode()
    res = leer_rips(("p.zip", crear_zip(archivos)))
    assert any("declara 5 registros para AF000001" in i.mensaje for i in res.incidencias)


# --- JSON ------------------------------------------------------------------

def test_json_2275_conserva_llaves_y_valores_exactos():
    res = leer_rips(("FE2001.json", json_2275(valor_med="7500.25")))
    assert res.version == VERSION_2275
    med = res.registros["medicamentos"][0]
    assert med["codTecnologiaSalud"] == "20012345-2"
    assert med["vrServicio"] == Decimal("7500.25")         # sin pasar por float
    assert med["numFactura"] == "FE2001"
    assert med["usuarios.numDocumentoIdentificacion"] == "2002"
    assert med["numDocumentoIdentificacion"] == "555"        # del servicio, no se sobrescribe
    assert len(res.registros["consultas"]) == 1 and len(res.registros["urgencias"]) == 1


def test_json_registro_sin_campo_obligatorio_indica_ruta():
    import json

    doc = json.loads(json_2275())
    del doc["usuarios"][0]["servicios"]["consultas"][0]["codConsulta"]
    res = leer_rips(("FE.json", json.dumps(doc).encode()))
    assert "consultas" not in res.registros
    assert res.incidencias[0].campo == "usuarios[0].servicios.consultas[0].codConsulta"
    assert len(res.registros["medicamentos"]) == 1      # el resto continúa


# --- ZIP -------------------------------------------------------------------

def test_zip_por_sede_con_meses_en_carpetas():
    paquete = crear_zip({
        **{f"ENERO_2024/{n}": c for n, c in archivos_3374().items()},
        "2024-02/FE2001.json": json_2275(),
        "__MACOSX/._basura": b"x",
    })
    res = leer_rips(("RIPS_SEDE_TAME.zip", paquete))
    ac = res.registros["AC"][0]
    assert ac["_sede_ruta"] == "TAME"
    assert ac["_mes_ruta"] == "2024-01"
    assert res.registros["consultas"][0]["_mes_ruta"] == "2024-02"
    assert ac["_archivo"] == "RIPS_SEDE_TAME.zip/ENERO_2024/AC000001.txt"


def test_zip_anidado_consolidado_sedes_y_meses():
    interno = crear_zip({n: c for n, c in archivos_3374().items()})
    paquete = crear_zip({"FORTUL/2024-03.zip": interno})
    res = leer_rips(("CONSOLIDADO_RIPS.zip", paquete))
    ac = res.registros["AC"][0]
    assert ac["_sede_ruta"] == "FORTUL" and ac["_mes_ruta"] == "2024-03"


def test_zip_danado_se_reporta():
    res = leer_rips(("roto.zip", b"PK\x03\x04basura"))
    assert res.incidencias[0].severidad == "ERROR"


def test_sede_asignada_tiene_prioridad():
    res = leer_rips(("x.zip", crear_zip(archivos_3374())), sede="SARAVENA")
    assert res.registros["AC"][0]["_sede_asignada"] == "SARAVENA"


# --- RAR -------------------------------------------------------------------

def test_rar_con_txt_y_json():
    rar = crear_rar({
        "2024-01/AC000001.txt": AC_TXT.encode(),
        "2024-02/FE2001.json": json_2275(),
    })
    res = leer_rips(("RIPS_SEDE_ARAUQUITA.rar", rar))
    assert len(res.registros["AC"]) == 2
    assert res.registros["AC"][0]["_sede_ruta"] == "ARAUQUITA"
    assert res.registros["AC"][0]["_mes_ruta"] == "2024-01"
    assert res.registros["consultas"][0]["_mes_ruta"] == "2024-02"


def test_rar_que_requiere_herramienta_externa_se_reporta(monkeypatch):
    import rarfile

    def falla(*a, **k):
        raise rarfile.RarCannotExec("Cannot find working tool")

    monkeypatch.setattr(rarfile.RarFile, "read", falla)
    res = leer_rips(("sede.rar", crear_rar({"AC000001.txt": AC_TXT.encode()})))
    assert "AC" not in res.registros
    assert "unrar" in res.incidencias[0].mensaje


# --- Facturación ------------------------------------------------------------

def test_facturacion_csv_formato_colombiano():
    res = leer_facturacion(("facturacion.csv", FACTURACION_CSV.encode()))
    regs = res.registros["facturacion"]
    assert len(regs) == 7
    glucosa = regs[1]
    assert glucosa["_valor"] == Decimal("15000.50")
    assert glucosa["_factura"] == "FE-1001"
    assert glucosa["Numero Factura"] == "FE-1001"           # columna original conservada
    assert glucosa["_mes"] == "2024-01" and glucosa["_sede"] == "TAME"


def test_facturacion_excel_una_hoja_por_ambito_encabezado_desplazado_y_totales():
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as w:
        pd.DataFrame([["REPORTE DE FACTURACIÓN ENERO 2024"], [None]]).to_excel(w, sheet_name="URGENCIAS", header=False, index=False)
        pd.DataFrame({"No Factura": ["FE1", "FE1", "TOTAL"], "CUPS": ["890701", "", None], "CUMS": [None, "19912345-1", None],
                      "Cant": [1, 2, None], "Vr Unitario": [45000, 1250.75, None], "Vr Total": [45000, 2501.5, 47501.5],
                      "Fecha": ["2024-01-05", "2024-01-05", None]}).to_excel(w, sheet_name="URGENCIAS", startrow=3, index=False)
        pd.DataFrame({"Factura": ["FE2"], "Codigo": ["890201"], "Valor": ["30.000"]}).to_excel(w, sheet_name="AMBULATORIO", index=False)
    res = leer_facturacion(("fact.xlsx", buffer.getvalue()))
    regs = res.registros["facturacion"]
    assert [r["_codigo"] for r in regs] == ["890701", "19912345-1", "890201"]
    assert regs[1]["_campo_codigo"] == "CUMS" and regs[0]["_campo_codigo"] == "CUPS"
    assert regs[1]["_valor"] == Decimal("2501.5") and regs[1]["_cantidad"] == Decimal("2")
    assert regs[0]["_ambito"] == "URGENCIAS" and regs[2]["_ambito"] == "AMBULATORIO"
    assert regs[2]["_valor"] == Decimal("30000")             # convención colombiana con advertencia
    assert regs[2]["_cantidad"] == Decimal(1)
    assert regs[0]["_linea"] == 5                            # fila real en Excel
    mensajes = " ".join(i.mensaje for i in res.incidencias)
    assert "total/subtotal" in mensajes and "convención colombiana" in mensajes and "cantidad" in mensajes


def test_facturacion_sin_columnas_obligatorias():
    res = leer_facturacion(("f.csv", b"Factura;Descripcion;Valor\nFE1;X;10\n"))
    assert "facturacion" not in res.registros
    assert res.incidencias[0].severidad == "ERROR"


def test_facturacion_valor_invalido_indica_fila_y_columna():
    res = leer_facturacion(("f.csv", b"Factura;Codigo;Valor Total\nFE1;890201;abc\nFE2;890201;10\n"))
    assert len(res.registros["facturacion"]) == 1
    inc = [i for i in res.incidencias if i.severidad == "ERROR"][0]
    assert inc.linea == 2 and inc.campo == "Valor Total" and inc.valor == "abc"


@pytest.mark.parametrize("texto,sep,esperado", [
    ("1.234.567,89", "auto", Decimal("1234567.89")),
    ("1,234,567.89", "auto", Decimal("1234567.89")),
    ("$ 30.000", ",", Decimal("30000")),
    ("(1.500,25)", ",", Decimal("-1500.25")),
    ("0.1", ".", Decimal("0.1")),
])
def test_decimales_exactos(texto, sep, esperado):
    from rips_conciliacion.decimales import a_decimal

    assert a_decimal(texto, sep) == esperado


@pytest.mark.parametrize("componentes,sede,mes", [
    (["Hospital San Antonio-20261027T135526Z-1-001", "Hospital San Antonio", "OCTUBRE 2026", "HSA202213"],
     "HOSPITAL SAN ANTONIO", "2026-10"),
    (["RIPS_SEDE_TAME_2024", "ENERO"], "TAME", "2024-01"),
    (["CONSOLIDADO", "FORTUL", "2024-03", "FE1001"], "FORTUL", "2024-03"),
    (["Hospital Sarare-20261101T101010Z-2-003"], "HOSPITAL SARARE", None),
])
def test_sede_y_mes_desde_carpetas_de_drive_y_por_factura(componentes, sede, mes):
    from rips_conciliacion.lectores import inferir_de_ruta

    datos = inferir_de_ruta(componentes)
    assert datos["_sede_ruta"] == sede and datos["_mes_ruta"] == mes

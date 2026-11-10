"""RIPS y facturación en cualquier formato: el tipo se reconoce por el contenido, no por la extensión."""

import gzip
import io
import json
import tarfile
import zipfile
from decimal import Decimal

import pandas as pd
import pytest

from rips_conciliacion import conciliar
from rips_conciliacion.facturacion import leer_facturacion
from rips_conciliacion.formatos import clasificar, clasificar_para_carga
from rips_conciliacion.lectores import leer_rips
from rips_conciliacion.modelos import VERSION_3374
from utilidades import AC_TXT, AM_TXT, FACTURACION_CSV, archivos_3374, crear_rar, crear_zip, json_2275


def crear_7z(archivos: dict[str, bytes]) -> bytes:
    import py7zr

    buffer = io.BytesIO()
    with py7zr.SevenZipFile(buffer, "w") as sz:
        for nombre, contenido in archivos.items():
            sz.writestr(contenido, nombre)
    return buffer.getvalue()


def crear_tar_gz(archivos: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        for nombre, contenido in archivos.items():
            info = tarfile.TarInfo(nombre)
            info.size = len(contenido)
            tf.addfile(info, io.BytesIO(contenido))
    return buffer.getvalue()


def crear_xlsx(hojas: dict[str, pd.DataFrame], encabezado=True) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as w:
        for nombre, df in hojas.items():
            df.to_excel(w, sheet_name=nombre, index=False, header=encabezado)
    return buffer.getvalue()


def crear_ods(filas: list[list]) -> bytes:
    celdas = []
    for fila in filas:
        xml = []
        for v in fila:
            if isinstance(v, (int, float, Decimal)):
                xml.append(f'<table:table-cell office:value-type="float" office:value="{v}"><text:p>{v}</text:p>'
                           "</table:table-cell>")
            else:
                xml.append(f'<table:table-cell office:value-type="string"><text:p>{v}</text:p></table:table-cell>')
        celdas.append("<table:table-row>" + "".join(xml) + "</table:table-row>")
    contenido = (
        '<?xml version="1.0" encoding="UTF-8"?><office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:spreadsheet>'
        '<table:table table:name="Facturacion">' + "".join(celdas) + "</table:table>"
        "</office:spreadsheet></office:body></office:document-content>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        zf.writestr("content.xml", contenido)
    return buffer.getvalue()


# --- Reconocimiento por contenido -------------------------------------------

def test_clasificar_por_contenido_aunque_la_extension_sea_incorrecta():
    assert clasificar("rips.dat", crear_zip({"AC.txt": b"x"})) == "zip"
    assert clasificar("rips.zip", crear_7z({"AC.txt": b"x"})) == "7z"
    assert clasificar("x", crear_rar({"a.txt": b"x"})) == "rar"
    assert clasificar("x.csv", crear_xlsx({"H": pd.DataFrame({"a": [1]})})) == "xlsx"
    assert clasificar("x.xls", crear_ods([["a"], [1]])) == "ods"
    assert clasificar("x.txt", json_2275()) == "json"
    assert clasificar("x", gzip.compress(b"hola")) == "gzip"
    assert clasificar("x.xlsx", b"%PDF-1.4\x00\x01\x02") == "binario"


@pytest.mark.parametrize("nombre,contenido,esperado", [
    ("RIPS_TAME.7z", crear_7z({"AC000001.txt": AC_TXT.encode()}), "RIPS"),
    ("paquete.zip", crear_zip({"enero/FE1.json": json_2275()}), "RIPS"),
    ("paquete.zip", crear_zip({"facturacion_enero.xlsx": b"x"}), "FACTURACION"),
    ("consultas.dat", AC_TXT.encode(), "RIPS"),
    ("reporte.xlsx", crear_xlsx({"Hoja1": pd.DataFrame({"numFactura": ["F1"], "codConsulta": ["890201"],
                                                       "vrServicio": [1]})}), "RIPS"),
    ("reporte.xlsx", crear_xlsx({"Hoja1": pd.DataFrame({"Factura": ["F1"], "Codigo": ["890201"],
                                                       "Valor": [1]})}), "FACTURACION"),
    ("sedes_ese.xlsx", b"x", "CATALOGO"),
    ("homologacion.csv", b"a;b", "EQUIVALENCIAS"),
])
def test_clasificar_para_carga(nombre, contenido, esperado):
    assert clasificar_para_carga(nombre, contenido) == esperado


# --- RIPS ---------------------------------------------------------------------

def test_rips_en_7z_tar_gz_y_gz():
    sietez = leer_rips(("RIPS_SEDE_TAME.7z", crear_7z({f"2024-01/{n}": c for n, c in archivos_3374().items()})))
    assert len(sietez.registros["AC"]) == 2 and sietez.registros["AC"][0]["_mes_ruta"] == "2024-01"
    assert sietez.registros["AC"][0]["_sede_ruta"] == "TAME"
    targz = leer_rips(("RIPS_FORTUL.tar.gz", crear_tar_gz({"ENERO_2024/FE2001.json": json_2275()})))
    assert len(targz.registros["consultas"]) == 1 and targz.registros["consultas"][0]["_mes_ruta"] == "2024-01"
    gz = leer_rips(("AC000001.txt.gz", gzip.compress(AC_TXT.encode())))
    assert len(gz.registros["AC"]) == 2


def test_rips_paquetes_anidados_de_distinto_tipo():
    interno = crear_7z({"AC000001.txt": AC_TXT.encode()})
    res = leer_rips(("CONSOLIDADO.rar", crear_rar({"SARAVENA/2024-03.7z": interno})))
    assert res.registros["AC"][0]["_sede_ruta"] == "SARAVENA" and res.registros["AC"][0]["_mes_ruta"] == "2024-03"


def test_rips_json_con_extension_cualquiera_y_utf16():
    assert len(leer_rips(("factura.dat", json_2275())).registros["consultas"]) == 1
    utf16 = AC_TXT.encode("utf-16")
    assert len(leer_rips(("AC000001.txt", utf16)).registros["AC"]) == 2


def test_rips_3374_en_excel_una_hoja_por_archivo_sin_encabezado():
    filas_ac = [ln.split(",") for ln in AC_TXT.strip().splitlines()]
    filas_am = [ln.split(",") for ln in AM_TXT.strip().splitlines()]
    libro = crear_xlsx({"AC": pd.DataFrame(filas_ac), "AM": pd.DataFrame(filas_am)}, encabezado=False)
    res = leer_rips(("RIPS_ENERO.xlsx", libro))
    assert len(res.registros["AC"]) == 2 and len(res.registros["AM"]) == 1
    assert res.registros["AM"][0]["Código del medicamento"] == "19912345-1"
    assert all(a["version"] == VERSION_3374 for a in res.archivos)


def test_rips_csv_3374_con_fila_de_titulos():
    from rips_conciliacion.esquemas import AC

    contenido = (",".join(AC.campos) + "\n" + AC_TXT).encode()
    res = leer_rips(("AC000001.csv", contenido))
    assert len(res.registros["AC"]) == 2
    assert any("títulos" in i.mensaje for i in res.incidencias)


def test_rips_2275_exportado_a_excel_con_encabezados():
    df = pd.DataFrame({"numFactura": ["FE2001", "FE2001"], "codPrestador": ["810010000101"] * 2,
                       "codConsulta": ["890201", "890301"], "fechaInicioAtencion": ["2024-02-03 08:00"] * 2,
                       "vrServicio": ["30000", "45000.50"]})
    res = leer_rips(("RIPS_consultas_febrero.xlsx", crear_xlsx({"consultas": df})))
    regs = res.registros["tabla"]
    assert len(regs) == 2 and regs[1]["_valor"] == Decimal("45000.50")
    assert regs[0]["_categoria"] == "CONSULTA" and "2275" in regs[0]["_version"]
    assert regs[0]["codConsulta"] == "890201"         # nombre de campo original conservado


def test_rips_tabla_sin_estructura_oficial_se_procesa_y_se_advierte():
    csv = "FACTURA|CUPS|CANTIDAD|VALOR\nFE1001|890201|1|30000\n"
    res = leer_rips(("rips_enero.txt", csv.encode()))
    assert len(res.registros["tabla"]) == 1
    assert "NO DETERMINADA" in res.registros["tabla"][0]["_version"]
    assert any("estructura oficial" in i.mensaje for i in res.incidencias)


def test_rips_pdf_dentro_del_paquete_se_ignora_con_aviso():
    res = leer_rips(("p.zip", crear_zip({"AC000001.txt": AC_TXT.encode(), "soporte.pdf": b"%PDF-1.4\x00\x01"})))
    assert len(res.registros["AC"]) == 2
    assert any("ignorado" in i.mensaje.lower() for i in res.incidencias)


# --- Facturación --------------------------------------------------------------

def _codigos(res):
    return [r["_codigo"] for r in res.registros["facturacion"]]


def test_facturacion_ods_json_xml_tsv_y_pipe():
    ods = crear_ods([["Factura", "Codigo", "Cantidad", "Valor"], ["F1", "890201", 1, 30000.5]])
    r = leer_facturacion(("facturacion.ods", ods))
    assert _codigos(r) == ["890201"] and r.registros["facturacion"][0]["_valor"] == Decimal("30000.5")

    datos = {"empresa": "ESE", "detalle": [{"numero_factura": "F1", "cups": "890201", "valor_total": 30000.25},
                                          {"numero_factura": "F1", "cups": "903841", "valor_total": 15000}]}
    r = leer_facturacion(("export.json", json.dumps(datos).encode()))
    assert _codigos(r) == ["890201", "903841"] and r.registros["facturacion"][0]["_valor"] == Decimal("30000.25")

    xml = ("<facturas><item><factura>F1</factura><codigo>890201</codigo><valor>30000</valor></item>"
           "<item><factura>F2</factura><codigo>890301</codigo><valor>45000</valor></item></facturas>")
    assert _codigos(leer_facturacion(("export.xml", xml.encode()))) == ["890201", "890301"]

    assert _codigos(leer_facturacion(("f.tsv", b"Factura\tCodigo\tValor\nF1\t890201\t100\n"))) == ["890201"]
    assert _codigos(leer_facturacion(("f.prn", b"Factura|Codigo|Valor\nF1|890201|100\n"))) == ["890201"]


def test_facturacion_comprimida_con_varias_hojas_y_archivos():
    libro = crear_xlsx({"Urgencias": pd.DataFrame({"Factura": ["F1"], "Codigo": ["890701"], "Valor": [45000]}),
                        "Farmacia": pd.DataFrame({"Factura": ["F1"], "CUMS": ["19912345-1"], "Valor": [1000]})})
    paquete = crear_7z({"facturacion_enero.xlsx": libro, "facturacion_febrero.csv": FACTURACION_CSV.encode()})
    r = leer_facturacion(("facturacion.7z", paquete))
    assert len(r.registros["facturacion"]) == 2 + 7
    ambitos = {x["_ambito"] for x in r.registros["facturacion"] if x["_archivo"].endswith(".xlsx")}
    assert ambitos == {"Urgencias", "Farmacia"}


def test_conciliacion_completa_con_formatos_mezclados():
    rips = crear_7z({f"2024-01/{n}": c for n, c in archivos_3374().items()})
    fact = crear_ods([["Numero Factura", "Codigo", "Cantidad", "Valor Total", "Sede"],
                      ["FE1002", "890201", 1, 32000, "TAME"]])
    r = conciliar([("RIPS_SEDE_TAME.7z", rips)], [("facturacion.ods", fact)])
    fila = r.detalle[(r.detalle["factura_clave"] == "FE1002") & (r.detalle["codigo_clave"] == "890201")].iloc[0]
    assert fila["estado"] == "DIFERENCIA_VALOR" and fila["dif_valor"] == Decimal("2000")


def test_facturacion_excel_97_2003_xls():
    xlwt = pytest.importorskip("xlwt")
    libro = xlwt.Workbook()
    hoja = libro.add_sheet("Facturacion")
    for j, titulo in enumerate(["Numero Factura", "Codigo", "Cantidad", "Valor Total"]):
        hoja.write(0, j, titulo)
    for j, valor in enumerate(["FE1002", "890201", 1, 32000.5]):
        hoja.write(1, j, valor)
    buffer = io.BytesIO()
    libro.save(buffer)
    contenido = buffer.getvalue()
    assert clasificar("facturacion.xls", contenido) == "xls"
    r = leer_facturacion(("facturacion.xls", contenido))
    assert _codigos(r) == ["890201"] and r.registros["facturacion"][0]["_valor"] == Decimal("32000.5")

"""Lote de ejemplo con datos SINTÉTICOS para probar la herramienta de punta a punta.

Genera un ZIP con formatos distintos a propósito: balance por CECO con títulos y subtotales (Excel), CSV con
separador ';', montos con formato colombiano y débito/crédito, un ZIP anidado con una matriz cuentas × CECO y un
archivo por CECO, un Excel dañado, un PDF no soportado y una copia duplicada. Los CECO son reales del maestro;
los valores son ficticios. Los totales esperados están en docs/HOMOLOGACION_CECO.md y en las pruebas.
"""
import io
import zipfile
from pathlib import Path

from openpyxl import Workbook


def _xlsx(filas, titulo="Hoja1"):
    wb = Workbook()
    ws = wb.active
    ws.title = titulo
    for f in filas:
        ws.append(list(f))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def ef_enero():
    return _xlsx([
        ["ESE DEPARTAMENTAL DE PRIMER NIVEL MORENO Y CLAVIJO"],
        ["BALANCE DE PRUEBA POR CENTRO DE COSTO - ENERO 2026"],
        [],
        ["Cód. Centro Costo", "Nombre Centro de Costo", "Código Cuenta", "Nombre Cuenta", "Saldo Anterior", "Débitos",
         "Créditos", "Nuevo Saldo"],
        [None, None, "5", "GASTOS", 0, 18550000.5, 0, 18550000.5],
        [None, None, "51", "DE ADMINISTRACIÓN Y OPERACIÓN", 0, 18550000.5, 0, 18550000.5],
        [None, None, "5101", "SUELDOS Y SALARIOS", 0, 16600000.5, 0, 16600000.5],
        [1020201, "HSLA-ARAUQUITA-CONSULTA EXTERNA", "510101", "SUELDOS", 0, 3000000, 0, 3000000],
        [1020201, "HSLA-ARAUQUITA-CONSULTA EXTERNA", "51010101", "SUELDO BÁSICO", 0, 3000000, 0, 3000000],
        [1020501, "HSLA-ARAUQUITA-LABORATORIO CLINICO", "51010101", "SUELDO BÁSICO", 0, 2500000.5, 0, 2500000.5],
        [4022208, "HSLA-ARAUQUITA-SERVICIOS PUBLICOS", "51110601", "ENERGÍA", 0, 800000, 0, 800000],
        [2012001, "ESE01-TAME-GERENCIA", "51010101", "SUELDO BÁSICO", 0, 9000000, 0, 9000000],
        [1020304, "HSLA-ARAUQUITA-RECIEN NACIDOS", "51010101", "SUELDO BÁSICO", 0, 400000, 0, 400000],
        [1020302, "HSLA-ARAUQUITA-CUIDADOS INTENSIVOS", "51110601", "ENERGÍA", 0, 150000, 0, 150000],
        [3012199, "ESE01-SALDOS INCIALES", "51010101", "SUELDO BÁSICO", 0, 1000000, 0, 1000000],
        [1100201, "PSLP-CONSULTA EXTERNA", "51010101", "SUELDO BÁSICO", 0, 700000, 0, 700000],
        [1020201, "HSLA-ARAUQUITA-CONSULTA EXTERNA", "11050501", "CAJA GENERAL", 0, 50000, 0, 50000],
        [None, "TOTAL GENERAL", None, None, 0, 18600000.5, 0, 18600000.5],
    ], "Balance por CECO")


def costos_febrero_csv():
    filas = [
        "CECO;DESCRIPCION CECO;CUENTA CONTABLE;DESCRIPCION CUENTA;DEBITO;CREDITO;FECHA",
        '"1020201 - HSLA-ARAUQUITA-CONSULTA EXTERNA";CONSULTA EXTERNA;51010101;SUELDO BÁSICO;"3.100.000,00";0;28/02/2026',
        "1020501;HSLA-ARAUQUITA-LABORATORIO CLINICO;51010101;SUELDO BÁSICO;2.400.000,00;100.000,00;28/02/2026",
        "1040201;HSAT-TAME-CONSULTA EXTERNA;51010101;SUELDO BÁSICO;$ 1.250.000;;2026-02-28",
        "1040201;HSAT-TAME-CONSULTA EXTERNA;51010101;SUELDO BÁSICO;$ 1.250.000;;2026-02-28",
        ";;51010101;SUELDO BÁSICO;500.000;;28/02/2026",
        "1040201;HSAT-TAME-CONSULTA EXTERNA;51010101;SUELDO BÁSICO;ABC;;28/02/2026",
        "1020501;HSLA-ARAUQUITA-LABORATORIO CLINICO;51010101;SUELDO BÁSICO;100.000;;31/13/2026",
        "1100401;PSLP-QUIROFANOS;51010101;SUELDO BÁSICO;300.000;;28/02/2026",
        "4022208;HSLA-ARAUQUITA-SERVICIOS PUBLICOS;51110601;=1+1;(50.000);;28/02/2026",
    ]
    return ("\r\n".join(filas) + "\r\n").encode("cp1252")


def matriz_marzo():
    return _xlsx([
        ["CUENTA", "NOMBRE CUENTA", "1020201", "1040201", "4022208", "TOTAL"],
        ["51010101", "SUELDO BÁSICO", 3200000, 1300000, None, 4500000],
        ["51110601", "ENERGÍA", None, None, 820000, 820000],
    ], "Matriz")


def por_ceco_imagenologia():
    return "CUENTA,VALOR\n51010101,600000\n51110601,90000\n".encode("utf-8")


def lote_ejemplo():
    """Bytes del ZIP de ejemplo."""
    anidado = io.BytesIO()
    with zipfile.ZipFile(anidado, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("matriz_marzo_2026.xlsx", matriz_marzo())
        z.writestr("1040502_imagenologia_2026-03.csv", por_ceco_imagenologia())
    buf = io.BytesIO()
    enero = ef_enero()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("EF_ENERO_2026.xlsx", enero)
        z.writestr("costos_febrero_2026.csv", costos_febrero_csv())
        z.writestr("soportes/soportes_marzo.zip", anidado.getvalue())
        z.writestr("corrupto.xlsx", b"esto no es un libro de Excel")
        z.writestr("LEAME.pdf", b"%PDF-1.4 documento de prueba")
        z.writestr("copia/EF_ENERO_2026.xlsx", enero)
        z.writestr("__MACOSX/._EF_ENERO_2026.xlsx", b"metadatos")
    return buf.getvalue()


def escribir_lote(ruta):
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(lote_ejemplo())
    return ruta


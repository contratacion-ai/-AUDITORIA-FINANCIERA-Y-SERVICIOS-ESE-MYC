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


COLUMNAS_CTRP = ["Mes", "OidCuentaContable", "CodigoCuentaContable", "OidCuentaProceso", "NombreCuentaContable", "Nivel",
                 "NivelOid", "NivelAuxiliar", "ClaseContable", "NaturalezaContable", "CuentaMayor", "DisponibilidadCuenta",
                 "OidTercero", "DocumentoTercero", "NombreTercero", "OidCentroCosto", "CodigoCentroCosto",
                 "NombreCentroCosto", "ValorSaldoDebito", "ValorSaldoCredito", "TotalSaldoDebito", "TotalSaldoCredito",
                 "SaldoAnterior", "ValorMovimientoDebito", "ValorMovimientoCredito", "ValorNuevoDebito",
                 "ValorNuevoCredito", "ValorNuevoSaldo"]


def ctrp_estado_resultados():
    """Reporte CTRPEstadoResultadosTerCen SINTÉTICO (misma estructura que el del software contable; terceros ficticios).

    Esperado (julio 2026): ingresos 1.000.000 (CECO 3012105 facturación); gastos 5101010101 = 600.000 en 2012003 (dos
    terceros con el mismo valor 300.000, no son duplicados); costos 6310500201 = 400.000 en 1040201 y 6310190101 =
    250.000 sin detalle por CECO (queda rechazado).
    """
    def cuenta_fila(oid, cod, nombre, nivel, n, hoja, clase, nat, mayor, deb, cre):
        return [None, oid, int(cod), None, nombre, nivel, n, "True" if hoja else "False", clase, nat, mayor, 0,
                None, None, None, None, None, None, deb, cre, deb, cre, deb - cre if nat == 1 else cre - deb, 0, 0, deb, cre,
                deb - cre if nat == 1 else cre - deb]

    def detalle(proceso, nombre, clase, nat, tercero, ceco, nombre_ceco, deb, cre):
        return [None, None, None, proceso, nombre, None, None, None, clase, nat, None, None, tercero, 900000000 + tercero,
                f"TERCERO FICTICIO {tercero}", 1, ceco, nombre_ceco, deb, cre, deb, cre, deb - cre if nat == 1 else cre - deb,
                0, 0, deb, cre, deb - cre if nat == 1 else cre - deb]

    filas = [COLUMNAS_CTRP,
             cuenta_fila(5, "4", "INGRESOS", "Clase", 1, False, 4, 2, None, 0, 1000000),
             cuenta_fila(33, "43", "VENTA DE SERVICIOS", "Grupo", 2, False, 4, 2, 5, 0, 1000000),
             cuenta_fila(191, "4312", "SERVICIOS DE SALUD", "Cuenta", 3, False, 4, 2, 33, 0, 1000000),
             cuenta_fila(900, "431296", "SERVICIOS DE SALUD PRESTADOS CON EL MECANISMO DE PAGO POR CAPITACIÃ“N", "Subcuenta",
                         4, False, 4, 2, 191, 0, 1000000),
             cuenta_fila(901, "43129601", "CAPITACIÃ“N", "Auxiliar", 5, False, 4, 2, 900, 0, 1000000),
             cuenta_fila(902, "4312960101", "CAPITACIÃ“N", "Subauxiliar", 6, True, 4, 2, 901, 0, 1000000),
             detalle(902, "CAPITACIÃ“N", 4, 2, 11, 3012105, "ESE01-TAME-FACTURACION", 0, 700000),
             detalle(902, "CAPITACIÃ“N", 4, 2, 12, 3012105, "ESE01-TAME-FACTURACION", 0, 300000),
             cuenta_fila(6, "5", "GASTOS", "Clase", 1, False, 5, 1, None, 600000, 0),
             cuenta_fila(40, "51", "ADMINISTRACION", "Grupo", 2, False, 5, 1, 6, 600000, 0),
             cuenta_fila(41, "5101", "SUELDOS Y SALARIOS", "Cuenta", 3, False, 5, 1, 40, 600000, 0),
             cuenta_fila(42, "510101", "SUELDOS", "Subcuenta", 4, False, 5, 1, 41, 600000, 0),
             cuenta_fila(43, "51010101", "SUELDOS DE PERSONAL", "Auxiliar", 5, False, 5, 1, 42, 600000, 0),
             cuenta_fila(44, "5101010101", "SUELDOS DE PERSONAL", "Subauxiliar", 6, True, 5, 1, 43, 600000, 0),
             detalle(44, "SUELDOS DE PERSONAL", 5, 1, 21, 2012003, "ESE01-TAME-RECURSOS HUMANOS", 300000, 0),
             detalle(44, "SUELDOS DE PERSONAL", 5, 1, 22, 2012003, "ESE01-TAME-RECURSOS HUMANOS", 300000, 0),
             cuenta_fila(7, "6", "COSTOS DE VENTAS", "Clase", 1, False, 6, 1, None, 650000, 0),
             cuenta_fila(60, "63", "COSTO DE VENTAS DE SERVICIOS", "Grupo", 2, False, 6, 1, 7, 650000, 0),
             cuenta_fila(61, "6310", "SERVICIOS DE SALUD", "Cuenta", 3, False, 6, 1, 60, 650000, 0),
             cuenta_fila(62, "631019", "SERV AMBUL-CONSULTA EXTERNA", "Subcuenta", 4, False, 6, 1, 61, 250000, 0),
             cuenta_fila(63, "63101901", "HONORARIOS", "Auxiliar", 5, False, 6, 1, 62, 250000, 0),
             cuenta_fila(64, "6310190101", "HONORARIOS", "Subauxiliar", 6, True, 6, 1, 63, 250000, 0),
             cuenta_fila(65, "631050", "APOYO TERAPE-REHABIL Y TERAPIAS", "Subcuenta", 4, False, 6, 1, 61, 400000, 0),
             cuenta_fila(66, "63105002", "HONORARIOS", "Auxiliar", 5, False, 6, 1, 65, 400000, 0),
             cuenta_fila(67, "6310500201", "HONORARIOS", "Subauxiliar", 6, True, 6, 1, 66, 400000, 0),
             detalle(67, "HONORARIOS", 6, 1, 31, 1040201, "HSAT-TAME-CONSULTA EXTERNA", 400000, 0),
             [None] * len(COLUMNAS_CTRP),
             ["TotalSaldoAnterirDebito", None, None, None, None, "TotalSaldoVuevoCredito", None, "GranTotalGeneral"]
             + [None] * 20,
             ["TAME", None, "DIRECCION FICTICIA", None, None, "900000000-0", "ESE DE PRUEBA", None, "usuario", "USUARIO",
              "CTRPEstadoResultadosPYG",
              "ESTADO DE RESULTADOS DEL MES JULIO DE 2026 DESDE EL CENTRO DE COSTO 1010204 HASTA EL CENTRO DE COSTO 7090101"]
             + [None] * 16,
             ]
    return _xlsx(filas, "CTRPEstadoResultadosTerCen")


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


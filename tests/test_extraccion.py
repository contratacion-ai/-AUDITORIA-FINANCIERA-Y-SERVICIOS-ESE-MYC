import datetime as dt
import io

import openpyxl
import pytest

from conftest import HOY, libro, matriz_energia
from ese_control import cargues, catalogo, esquema, plantillas
from ese_control.config import COMBUSTIBLE, DESPLAZAMIENTOS, MANTENIMIENTO, SERVICIOS
from ese_control.extraccion import extraer_archivo
from ese_control.extraccion.comunes import a_fecha, a_numero, a_periodo


def test_numeros_fechas_y_periodos_en_formato_colombiano():
    assert a_numero("$ 1.234.567,50") == 1234567.5
    assert a_numero("37,2") == 37.2
    assert a_numero("12.500") == 12500
    assert a_numero("14.57") == 14.57
    assert a_numero("No aplica") is None and a_numero("337352 no marca") is None
    assert a_fecha("25/10/0026")[0] == dt.date(2026, 10, 25)
    assert a_periodo("Octubre de 2026") == "2026-10" and a_periodo("octubre", 2026) == "2026-10"
    assert a_periodo("10/2026") == "2026-10" and a_periodo("2026-13") is None


def _llenar(item, filas, bd):
    wb = openpyxl.load_workbook(io.BytesIO(plantillas.generar(item, ruta=bd)))
    ws = wb["Datos"]
    for i, fila in enumerate(filas, start=plantillas.FILA_ENCABEZADO + 1):
        for j, v in enumerate(fila, start=1):
            ws.cell(row=i, column=j, value=v)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_cada_plantilla_se_lee_completa_y_sin_columnas_perdidas(bd, opciones):
    filas = {
        SERVICIOS: [["2026-10", "NOR-01", None, "Energía eléctrica", 1200, "kWh", 1400000, 1900000, "Empresa", "F1",
                     dt.date(2026, 11, 3), None],
                    ["2026-10", "1002", None, "Acueducto", 30, "m3", 80000, None, None, None, None, "obs"]],
        COMBUSTIBLE: [[dt.date(2026, 10, 5), None, None, esquema.CAT_VEHICULOS, "AAA-111", "Diésel (ACPM)", 10, 113000,
                       150000, "Estación", "F9", None],
                      [dt.date(2026, 10, 6), "SUR-01", None, esquema.CAT_PLANTAS, "Planta 1", "Gasolina corriente", 5,
                       80000, None, None, None, None]],
        MANTENIMIENTO: [[dt.date(2026, 10, 9), "CC-510", None, esquema.CAT_VEHICULOS, "BBB222", "Correctivo",
                         "Cambio de embrague", 2500000, "Taller", "OT-1", None]],
    }
    for item, datos in filas.items():
        tablas, inc = extraer_archivo("plantilla.xlsx", _llenar(item, datos, bd), item)
        assert len(tablas) == 1, inc
        # Todos los encabezados de la plantilla deben reconocerse como su propio campo.
        assert set(tablas[0].columnas_usadas) == {c.nombre for c in esquema.PLANTILLAS[item]}
        assert tablas[0].columnas_ignoradas == []
        prep = cargues.preparar("plantilla.xlsx", _llenar(item, datos, bd), item, opciones(), bd)
        assert prep.total_validas == len(datos), prep.incidencias_df().to_string()
        assert prep.conteo("Error") == 0


def test_matriz_de_energia_ignora_totales_y_notas_y_reporta_codigo_desconocido(bd, opciones):
    datos = {"1001": [(100, 110000, 110000), (120, 130000, 240000), (0, 0, 0)],
             "1002": [(10, 11000, 11000), (12, 13000, 13000), (0, 0, 0)],
             "9999": [(5, 5000, 5000), (0, 0, 0), (0, 0, 0)]}
    prep = cargues.preparar("CONSUMO ENERGIA 2026.xlsx", matriz_energia(datos), SERVICIOS, opciones(), bd)
    d = prep.resultados[SERVICIOS]
    assert sorted(d["periodo"].unique()) == ["2026-01", "2026-02"]        # marzo y abril vacíos no se cargan
    assert d["valor"].sum() == 110000 + 130000 + 11000 + 13000           # sin la fila TOTAL ni la nota
    assert d.loc[(d.sede_id == "NOR-01") & (d.periodo == "2026-02"), "total_a_pagar"].iloc[0] == 240000
    errores = [i for i in prep.incidencias if i.severidad == "Error"]
    assert len(errores) == 1 and errores[0].fila == 5 and "9999" in errores[0].motivo
    assert errores[0].hoja == "Consumo Energia" and errores[0].archivo == "CONSUMO ENERGIA 2026.xlsx"
    assert prep.valor_rechazado == 5000


def test_exportacion_de_tanqueos_con_datos_sucios(bd, opciones):
    enc = ["Marca temporal", "Fecha de suministro", "Placa del vehículo", "Numero de Cedula Conductor",
           "Kilometraje actual (Odómetro)", "Tipo de combustible", "Cantidad suministrada (galones)",
           "Valor total del tanqueo", "Estación de servicio", "Nombre Conductor", "Cantidad suministrada (litros)"]
    m = dt.datetime(2026, 10, 10, 8, 0)
    filas = [enc,
             [m, dt.datetime(2026, 10, 10), "AAA 111", 123, 1000, "Diesel", 10, 113000, "EDS", "X", 0],
             [m, "10/10/0026", "aaa-111", 123, "no marca", "Diesel", 9, 100000, "EDS", "X", None],        # año 0026
             [m, dt.datetime(2026, 10, 10), "BBB222", 123, 2000, "Gasolina Corriente", 12806, 200746, "EDS", "X", 0],
             [m, dt.datetime(2026, 10, 10), "ZZZ999", 123, 5, "Diesel", 5, 56000, "EDS", "X", 0],          # placa sin catálogo
             [m, dt.datetime(2026, 10, 10), "BBB222", 123, 5, "UREA", 0, 58000, "EDS", "X", 13],           # litros
             [m, dt.datetime(2026, 11, 29), "BBB222", 123, 5, "Gasolina Corriente", 5, 80000, "EDS", "X", 0]]  # fecha futura
    prep = cargues.preparar("tanqueos.xlsx", libro({"Hoja1": filas}), COMBUSTIBLE, opciones(), bd)
    d = prep.resultados[COMBUSTIBLE]
    assert len(d) == 5 and prep.rechazadas[COMBUSTIBLE] == 1 and prep.valor_rechazado == 56000
    assert set(d["referencia"]) == {"AAA111", "BBB222"}                   # placas normalizadas
    assert (d["periodo"] == "2026-10").all()                              # 0026 -> 2026; fecha futura -> diligenciamiento
    assert d.loc[d.valor == 200746, "cantidad"].isna().all()              # 12.806 galones no es plausible
    assert d.loc[d.concepto == "Urea (AdBlue)", "cantidad"].iloc[0] == pytest.approx(13 / 3.785, abs=0.001)
    assert "Numero de Cedula Conductor" in prep.tablas[0].columnas_ignoradas   # datos personales: no se guardan
    motivos = " ".join(i.motivo for i in prep.incidencias)
    assert "ZZZ999" in motivos and "0026" in motivos and "plausible" in motivos


def test_libro_con_una_hoja_por_sede_y_tablas_resumen(bd, opciones):
    enc = ["N°", "Placa", "Vehículo", "Mes", "Mantenimiento ($)", "Combustible asignado ($)"]
    norte = [["COSTOS DE OPERACIÓN – ENERO A OCTUBRE DE 2026"], [], enc, [1, "AAA111", "Ambulancia", "Enero", 1000000, "—"],
             [None, "AAA111", "Ambulancia", "Febrero", 0, "—"], [None, "AAA111", "Ambulancia", "Marzo", 250000, 5],
             [None, None, None, "TOTAL SEDE", 1250000, None], [], [None, None, None, "Mes", "Mantenimiento ($)"],
             [None, None, None, "Enero", 1000000]]
    resumen = [["Sede", "Placa", "Mantenimiento enero–octubre ($)"], ["Norte", "AAA111", 1250000]]
    prep = cargues.preparar("costos 2026.xlsx", libro({"Resumen": resumen, "Norte": norte}), MANTENIMIENTO, opciones(), bd)
    d = prep.resultados[MANTENIMIENTO]
    assert d["valor"].sum() == 1250000 and list(d["periodo"]) == ["2026-01", "2026-03"]   # sin doble conteo
    assert (d["sede_id"] == "NOR-01").all() and (d["categoria"] == esquema.CAT_VEHICULOS).all()


def test_desplazamientos_calcula_km_y_descarta_odometros_absurdos(bd, opciones):
    enc = ["Marca temporal", "Fecha del desplazamiento", "Placa del vehiculo", "Kilometraje inicial", "Kilometraje final",
           "Motivo del desplazamiento", "Municipio de Salida", "Municipio de Destino", "Nombre conductor"]
    m = dt.datetime(2026, 10, 20, 9)
    filas = [enc, [m, dt.datetime(2026, 10, 20), "AAA111", 1000, 1080, "Remisión", "A", "B", "X"],
             [m, dt.datetime(2026, 10, 20), "AAA111", 535226, 235322, "Remisión", "A", "B", "X"],
             [m, dt.datetime(2026, 10, 20), "AAA111", "N/a", "N/a", "Remisión", "A", "B", "X"]]
    prep = cargues.preparar("viajes.xlsx", libro({"Hoja1": filas}), COMBUSTIBLE, opciones(), bd)
    d = prep.resultados[DESPLAZAMIENTOS]
    assert len(d) == 3 and d["km"].sum() == 80 and d["km"].isna().sum() == 2


def test_factura_pdf_se_lee_del_texto_y_queda_para_revision(bd, opciones):
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i, linea in enumerate(["EMPRESA DE ENERGIA", "Factura No. FE-778899", "Cuenta: 1001",
                               "Periodo facturado: Octubre de 2026", "Consumo del mes 1.250 kWh",
                               "Valor del mes: $ 1.437.500", "Total a pagar: $ 2.100.000"]):
        c.drawString(72, 760 - 22 * i, linea)
    c.save()
    prep = cargues.preparar("factura.pdf", buf.getvalue(), SERVICIOS, opciones(), bd)
    d = prep.resultados[SERVICIOS]
    assert len(d) == 1
    r = d.iloc[0]
    assert (r.sede_id, r.periodo, r.cantidad, r.valor, r.total_a_pagar) == ("NOR-01", "2026-10", 1250, 1437500, 2100000)
    assert prep.tablas[0].formato == "pdf_texto" and prep.conteo("Advertencia") >= 1


def test_archivo_sin_tabla_reconocible_explica_el_motivo(bd, opciones):
    prep = cargues.preparar("notas.xlsx", libro({"Hoja1": [["hola", "mundo"], [1, 2]]}), SERVICIOS, opciones(), bd)
    assert prep.total_validas == 0 and prep.conteo("Error") == 1
    assert "No se reconoció ninguna tabla" in prep.incidencias[-1].motivo

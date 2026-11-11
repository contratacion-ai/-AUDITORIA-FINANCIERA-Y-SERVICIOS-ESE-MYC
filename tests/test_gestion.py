import datetime as dt
import io

import openpyxl
import pandas as pd
import pytest

from conftest import HOY, libro, matriz_energia
from ese_control import analisis, auth, cargues, catalogo, db, informe
from ese_control.config import COMBUSTIBLE, SERVICIOS

ENC = ["Fecha de suministro", "Placa del vehículo", "Tipo de combustible", "Cantidad suministrada (galones)",
       "Valor total del tanqueo"]


def _tanqueos(filas):
    return libro({"Hoja1": [ENC] + filas})


def _vigentes(bd):
    return db.consulta("SELECT * FROM registros WHERE estado='vigente'", ruta=bd)


def test_reemplazo_conserva_historial_y_trazabilidad(bd, opciones):
    v1 = {"1001": [(100, 110000, 110000)], "2001": [(50, 60000, 60000)]}
    v2 = {"1001": [(100, 125000, 125000)]}                                    # corrige solo la cuenta 1001
    id1 = cargues.confirmar(cargues.preparar("energia_v1.xlsx", matriz_energia(v1), SERVICIOS, opciones(), bd), "ana", ruta=bd)[0]
    prep = cargues.preparar("energia_v2.xlsx", matriz_energia(v2), SERVICIOS, opciones(), bd)
    assert len(prep.reemplazos) == 1 and prep.reemplazos["valor"].sum() == 110000   # se avisa antes de guardar
    id2 = cargues.confirmar(prep, "luis", "reemplazar", "corrección de lectura", bd)[0]

    vig = _vigentes(bd)
    assert vig["valor"].sum() == 125000 + 60000                                # la otra sede no se toca
    viejo = db.consulta("SELECT * FROM registros WHERE cargue_id=? AND sede_id='NOR-01'", (id1,), bd).iloc[0]
    assert viejo["estado"] == "reemplazado" and viejo["sustituido_por"] == id2  # nada se borra
    lista = cargues.listar(bd).set_index("cargue_id")
    assert lista.loc[id1, "estado"] == "Vigente en parte" and lista.loc[id2, "reemplazo_de"] == f"#{id1}"
    assert lista.loc[id2, "archivo_nombre"] == "energia_v2.xlsx" and lista.loc[id2, "usuario"] == "luis"
    assert len(lista.loc[id2, "archivo_sha256"]) == 64 and lista.loc[id2, "periodos"] == "2026-01"
    assert "Reemplazó registros de los cargues #1" in cargues.bitacora(bd)["detalle"].iloc[0]

    # Eliminar el segundo restaurando lo que había reemplazado.
    with pytest.raises(ValueError):
        cargues.eliminar(id2, "luis", "  ", ruta=bd)                           # el motivo es obligatorio
    cargues.eliminar(id2, "luis", "se cargó el archivo equivocado", restaurar=True, ruta=bd)
    assert _vigentes(bd)["valor"].sum() == 110000 + 60000
    assert cargues.listar(bd).set_index("cargue_id").loc[id2, "estado"] == "Eliminado"


def test_complementar_no_reemplaza(bd, opciones):
    a = _tanqueos([[dt.datetime(2026, 10, 1), "AAA111", "Diesel", 10, 113000]])
    b = _tanqueos([[dt.datetime(2026, 10, 2), "AAA111", "Diesel", 8, 90000]])
    cargues.confirmar(cargues.preparar("a.xlsx", a, COMBUSTIBLE, opciones(), bd), "ana", ruta=bd)
    cargues.confirmar(cargues.preparar("b.xlsx", b, COMBUSTIBLE, opciones(), bd), "ana", "complementar", ruta=bd)
    assert _vigentes(bd)["valor"].sum() == 203000


def test_el_periodo_sale_del_contenido_y_respeta_fechas_de_corte(bd, opciones):
    datos = _tanqueos([[dt.datetime(2026, 9, 28), "AAA111", "Diesel", 10, 100000],
                       [dt.datetime(2026, 10, 15), "AAA111", "Diesel", 10, 110000],
                       [dt.datetime(2026, 11, 2), "AAA111", "Diesel", 10, 120000],
                       [dt.datetime(2026, 11, 9), "AAA111", "Diesel", 10, 130000]])
    # Solo octubre calendario
    p = cargues.preparar("t.xlsx", datos, COMBUSTIBLE, opciones(periodo="2026-10", alcance="periodo"), bd)
    assert p.resultados[COMBUSTIBLE]["valor"].sum() == 110000
    # Corte del 28 de septiembre al 3 de noviembre: todo eso es el periodo de octubre
    p = cargues.preparar("t.xlsx", datos, COMBUSTIBLE, opciones(periodo="2026-10", alcance="periodo",
                         corte_desde=dt.date(2026, 9, 28), corte_hasta=dt.date(2026, 11, 3)), bd)
    d = p.resultados[COMBUSTIBLE]
    assert d["valor"].sum() == 330000 and (d["periodo"] == "2026-10").all()
    # Archivo acumulado: cada fila a su mes, sin importar cuándo se recibe
    p = cargues.preparar("t.xlsx", datos, COMBUSTIBLE, opciones(), bd)
    assert sorted(p.resultados[COMBUSTIBLE]["periodo"].unique()) == ["2026-09", "2026-10", "2026-11"]


def test_usuario_de_sede_solo_carga_y_elimina_lo_suyo(bd, opciones):
    datos = _tanqueos([[dt.datetime(2026, 10, 1), "AAA111", "Diesel", 10, 113000],       # Norte
                       [dt.datetime(2026, 10, 1), "BBB222", "Gasolina", 10, 160000]])    # Sur
    prep = cargues.preparar("t.xlsx", datos, COMBUSTIBLE, opciones(hospital="Hospital del Norte"), bd)
    d = prep.resultados[COMBUSTIBLE]
    assert list(d["sede_id"]) == ["NOR-01"] and prep.conteo("Error") == 0
    assert any("otros hospitales" in i.motivo for i in prep.incidencias)
    cid = cargues.confirmar(prep, "norte", ruta=bd)[0]
    with pytest.raises(PermissionError):
        cargues.eliminar(cid, "sur", "prueba", hospital="Hospital del Sur", ruta=bd)
    assert len(cargues.listar(bd, hospital="Hospital del Sur")) == 0


def test_catalogo_se_actualiza_con_excel_sin_tocar_codigo(bd, opciones):
    archivo = matriz_energia({"7777": [(10, 12000, 12000)]})
    assert cargues.preparar("e.xlsx", archivo, SERVICIOS, opciones(), bd).total_validas == 0   # código desconocido
    wb = openpyxl.load_workbook(io.BytesIO(catalogo.exportar_excel(bd)))
    wb["Codigos"].append(["CUENTA_ENERGIA", "7777", "SUR-01", "cuenta nueva", "SI"])
    buf = io.BytesIO()
    wb.save(buf)
    tablas, errores = catalogo.leer_excel(buf.getvalue())
    assert errores == []
    catalogo.aplicar(tablas, "admin", "catalogo.xlsx", bd)
    prep = cargues.preparar("e.xlsx", archivo, SERVICIOS, opciones(), bd)
    assert prep.resultados[SERVICIOS]["sede_id"].iloc[0] == "SUR-01"
    # Un catálogo con una sede inexistente se rechaza completo.
    wb["Codigos"].append(["CENTRO_COSTO", "X1", "NO-EXISTE", "", "SI"])
    buf = io.BytesIO()
    wb.save(buf)
    assert any("NO-EXISTE" in e for e in catalogo.leer_excel(buf.getvalue())[1])


def _historia(bd, opciones, ultimo):
    meses = [(100, 100000, 100000), (100, 102000, 102000), (100, 98000, 98000), ultimo]
    datos = {"1001": meses, "1002": [(10, 11000, 11000)] * 4, "2001": [(50, 60000, 60000)] * 3 + [(0, 0, 0)]}
    cargues.confirmar(cargues.preparar("energia 2026.xlsx", matriz_energia(datos), SERVICIOS, opciones(), bd), "ana", ruta=bd)
    df = analisis.datos(bd)
    params = db.parametros(bd) | {"alerta_variacion_min_valor": 1000}
    return df, analisis.alertas(df, analisis.desplazamientos(bd), analisis.vehiculos(bd), params, "2026-04")


def test_alertas_de_variacion_mora_y_sin_reporte(bd, opciones):
    df, al = _historia(bd, opciones, (180, 190000, 900000))
    assert analisis.periodo_de_control(df, HOY) == "2026-04"
    tipos = set(al["tipo"])
    assert {"Variación frente al mes anterior", "Saldo en mora", "Sin reporte en el periodo"} <= tipos
    var = al[al["tipo"] == "Variación frente al mes anterior"].iloc[0]
    assert var["severidad"] == "Alta" and var["sede"] == "Hospital del Norte" and "94%" in var["mensaje"]
    assert al[al["tipo"] == "Sin reporte en el periodo"]["hospital"].iloc[0] == "Hospital del Sur"
    assert (al["recomendacion"].str.len() > 20).all()
    texto = analisis.analisis_texto(df, analisis.desplazamientos(bd), analisis.vehiculos(bd), al, "2026-04")
    assert [t for t, _ in texto][0].startswith("Panorama") and "Prioridades de gestión" in [t for t, _ in texto]


def test_sin_cambios_no_hay_alerta_de_variacion(bd, opciones):
    _, al = _historia(bd, opciones, (100, 101000, 101000))
    assert "Variación frente al mes anterior" not in set(al["tipo"])


def test_informe_excel_tiene_hoja_por_item_y_por_hospital(bd, opciones):
    _historia(bd, opciones, (180, 190000, 900000))
    wb = openpyxl.load_workbook(io.BytesIO(informe.generar(bd, hoy=HOY)))
    for hoja in ["Resumen", "Servicios públicos", "Combustible", "Mantenimientos", "del Norte", "del Sur", "Alertas",
                 "Análisis", "Detalle", "Trazabilidad"]:
        assert hoja in wb.sheetnames
    assert wb["Trazabilidad"]["C5"].value == "energia 2026.xlsx"
    solo_norte = openpyxl.load_workbook(io.BytesIO(informe.generar(bd, hospital="Hospital del Norte", hoy=HOY)))
    assert "del Sur" not in solo_norte.sheetnames


def test_usuarios_y_permisos(bd):
    auth.crear("admin", "Central", "clave-segura-1", "central", ruta=bd)
    auth.crear("norte", "Auxiliar", "clave-segura-2", "sede", "Hospital del Norte", ruta=bd)
    assert auth.autenticar("admin", "mala", bd) is None
    u = auth.autenticar("NORTE", "clave-segura-2", bd)
    assert u.ambito == "Hospital del Norte" and not u.es_central
    assert auth.autenticar("admin", "clave-segura-1", bd).ambito is None
    with pytest.raises(ValueError):
        auth.crear("x", "X", "corta", "sede", "Hospital del Norte", ruta=bd)
    with pytest.raises(ValueError):
        auth.activar("admin", False, "admin", bd)                              # no se puede dejar la ESE sin administrador
    assert "clave-segura-1" not in db.consulta("SELECT * FROM usuarios", ruta=bd).to_string()

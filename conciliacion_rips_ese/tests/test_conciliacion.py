"""Emparejamiento, cruce de códigos, diferencias monetarias/cantidades y exportación."""

import io
import zipfile
from decimal import Decimal

import pandas as pd
import pytest

from rips_conciliacion import ConfiguracionConciliacion, conciliar
from rips_conciliacion.reportes import exportar_csv, exportar_excel, exportar_zip, texto_reporte_general
from utilidades import FACTURACION_CSV, archivos_3374, crear_rar, crear_zip, json_2275


@pytest.fixture(scope="module")
def resultado():
    paquete = crear_zip({f"2024-01/{n}": c for n, c in archivos_3374().items()})
    rar = crear_rar({"FEBRERO_2024/FE2001.json": json_2275()})
    return conciliar([("RIPS_SEDE_TAME.zip", paquete), ("RIPS_SEDE_TAME.rar", rar)],
                     [("facturacion.csv", FACTURACION_CSV.encode())])


def fila(df, factura_clave, codigo):
    sel = df[(df["factura_clave"] == factura_clave) & (df["codigo_clave"] == codigo)]
    assert len(sel) == 1, sel
    return sel.iloc[0]


def test_estados_del_detalle(resultado):
    d = resultado.detalle
    assert fila(d, "FE1001", "890201")["estado"] == "COINCIDE"      # FE-1001 vs FE1001 normalizado
    assert fila(d, "FE1001", "903841")["estado"] == "COINCIDE"
    assert fila(d, "FE1001", "AT001")["estado"] == "COINCIDE"
    assert fila(d, "FE1002", "890201")["estado"] == "DIFERENCIA_VALOR"
    assert fila(d, "FE1001", "19912345-1")["estado"] == "DIFERENCIA_VALOR_Y_CANTIDAD"
    assert fila(d, "FE1001", "906913")["estado"] == "CODIGO_NO_REPORTADO_EN_RIPS"
    assert fila(d, "FE9999", "890701")["estado"] == "FACTURA_NO_REPORTADA_EN_RIPS"
    assert fila(d, "FE2001", "890201")["estado"] == "FACTURA_NO_ESTA_EN_FACTURACION"


def test_diferencias_monetarias_y_de_cantidad_exactas(resultado):
    d = resultado.detalle
    med = fila(d, "FE1001", "19912345-1")
    assert (med["cantidad_fact"], med["cantidad_rips"], med["dif_cantidad"]) == (Decimal(11), Decimal(10), Decimal(1))
    assert (med["valor_fact"], med["valor_rips"], med["dif_valor"]) == (Decimal(11000), Decimal(10000), Decimal(1000))
    consulta = fila(d, "FE1002", "890201")
    assert consulta["dif_valor"] == Decimal("2000") and consulta["dif_cantidad"] == 0
    glucosa = fila(d, "FE1001", "903841")
    assert glucosa["valor_fact"] == Decimal("15000.50") == glucosa["valor_rips"]
    amox = fila(d, "FE2001", "20012345-2")
    assert amox["valor_rips"] == Decimal("7500.25") and amox["dif_valor"] == Decimal("-7500.25")
    for c in ("valor_fact", "valor_rips", "dif_valor", "cantidad_fact", "cantidad_rips", "dif_cantidad"):
        assert all(isinstance(v, Decimal) for v in d[c]), c


def test_detalle_indica_factura_mes_sede_hospital_servicio(resultado):
    med = fila(resultado.detalle, "FE1001", "19912345-1")
    assert med["factura"] == "FE-1001"
    assert med["mes"] == "2024-01" and med["sede"] == "TAME" and med["hospital"] == "HOSPITAL SAN ANTONIO"
    assert med["categoria"] == "MEDICAMENTO" and med["tipo_codigo"] == "CUMS"
    assert med["campo_codigo_rips"] == "Código del medicamento" and med["campo_codigo_fact"] == "Codigo"
    assert med["ubicacion_rips"] == "línea 1"
    amox = fila(resultado.detalle, "FE2001", "20012345-2")
    assert amox["mes"] == "2024-02" and amox["campo_codigo_rips"] == "codTecnologiaSalud"


def test_cruce_de_codigos(resultado):
    cc = resultado.cruce_codigos.set_index("codigo_clave")["resultado"].to_dict()
    assert cc == {
        "906913": "SOLO_EN_FACTURACION",
        "890701": "SOLO_EN_FACTURACION",
        "20012345-2": "SOLO_EN_RIPS",
        "890201": "EN_AMBOS", "903841": "EN_AMBOS", "AT001": "EN_AMBOS", "19912345-1": "EN_AMBOS",
    }
    por_sede = resultado.cruce_codigos_sede
    assert set(por_sede[por_sede["resultado"] == "SOLO_EN_RIPS"]["codigo_clave"]) == {"20012345-2"}


def test_comparativo_por_sede_hospital_mes(resultado):
    t = resultado.comparativos["Sede-Hospital-Mes"].set_index("mes")
    ene = t.loc["2024-01"]
    assert ene["valor_fact"] == Decimal("151000.50")
    assert ene["valor_rips"] == Decimal("91000.50")
    assert ene["dif_valor"] == Decimal("60000.00")
    assert ene["cantidad_fact"] == 18 and ene["cantidad_rips"] == 15
    feb = t.loc["2024-02"]
    assert feb["valor_fact"] == 0 and feb["valor_rips"] == Decimal("37500.25")


def test_facturas_y_consistencia_af(resultado):
    f = resultado.facturas.set_index("factura_clave")
    assert f.loc["FE1001", "AF_valor_neto"] == Decimal("61000.50")
    assert f.loc["FE1001", "dif_AF_neto_vs_detalle"] == 0
    assert f.loc["FE1001", "dif_valor"] == Decimal("13000.00")   # 12000 hemograma + 1000 medicamento
    assert f.loc["FE9999", "estado"] == "FACTURA_NO_REPORTADA_EN_RIPS"
    assert f.loc["FE2001", "estado"] == "FACTURA_NO_ESTA_EN_FACTURACION"


def test_consumo_por_farmacia_y_ranking(resultado):
    farm = resultado.consumo["Consumo farmacias"]
    assert list(farm["codigo_clave"]) == ["19912345-1", "20012345-2"]
    assert list(farm["ranking"]) == [1, 2]
    hosp = resultado.consumo["Consumo por hospital"]
    assert set(hosp["hospital"]) == {"HOSPITAL SAN ANTONIO"}


def test_suma_exacta_sin_errores_de_punto_flotante():
    fact = "Factura;Codigo;Valor\n" + "".join(f"F1;X1;0.1\n" for _ in range(3))
    rips = "".join(f"F1,P,CC,1,,1,X1,INSUMO,1,0.1,0.1\n" for _ in range(3))
    r = conciliar([("AT000001.txt", rips.encode())], [("f.csv", fact.encode())],
                  ConfiguracionConciliacion(separador_decimal="."))
    (unica,) = r.detalle.to_dict("records")
    assert unica["valor_fact"] == Decimal("0.3") and unica["estado"] == "COINCIDE"


def test_tolerancia_cero_detecta_un_centavo():
    r = conciliar([("AT000001.txt", b"F1,P,CC,1,,1,X1,INSUMO,1,100.01,100.01\n")],
                  [("f.csv", b"Factura;Codigo;Valor\nF1;X1;100.00\n")], ConfiguracionConciliacion(separador_decimal="."))
    assert r.detalle.iloc[0]["dif_valor"] == Decimal("-0.01")
    assert r.detalle.iloc[0]["estado"] == "DIFERENCIA_VALOR"


def test_equivalencias_ceros_y_posible_homologacion():
    rips = b"FE0001,P,CC,1,,19912345-1,1,ACETAMINOFEN,TAB,500MG,MG,10,100,1000\n"
    fact = b"Factura;Codigo;Cantidad;Valor\nFE1;19912345-01;10;1000\n"
    sin = conciliar([("AM000001.txt", rips)], [("f.csv", fact)])
    assert set(sin.detalle["estado"]) == {"FACTURA_NO_REPORTADA_EN_RIPS", "FACTURA_NO_ESTA_EN_FACTURACION"}
    cfg = ConfiguracionConciliacion(ignorar_ceros_factura=True)
    con_ceros = conciliar([("AM000001.txt", rips)], [("f.csv", fact)], cfg)
    assert set(con_ceros.detalle["posible_homologacion"]) == {"19912345-1", "19912345-01"}
    equivalencias = ("eq.csv", b"codigo_facturacion;codigo_rips\n19912345-01;19912345-1\n")
    ok = conciliar([("AM000001.txt", rips)], [("f.csv", fact)], cfg, equivalencias=equivalencias)
    assert list(ok.detalle["estado"]) == ["COINCIDE"]


def test_catalogo_de_sedes_asigna_sede_y_hospital_a_rips():
    catalogo = ("sedes.csv", b"codigo_prestador;sede;hospital\n810010000101;Tame;Hospital San Antonio\n")
    r = conciliar([("AC000001.txt", archivos_3374()["AC000001.txt"])],
                  [("f.csv", b"Factura;Codigo;Valor;Sede\nFE1001;890201;30000;TAME\n")], catalogo_sedes=catalogo)
    assert set(r.lineas_rips["sede"]) == {"TAME"} and set(r.lineas_rips["hospital"]) == {"HOSPITAL SAN ANTONIO"}


def test_exportacion_excel_csv_y_ambos(resultado, tmp_path):
    destino = tmp_path / "r.xlsx"
    exportar_excel(resultado, destino)
    hojas = pd.ExcelFile(destino).sheet_names
    for h in ("Reporte general", "Diferencias", "Detalle factura-código", "Facturas", "Cruce de códigos",
              "Sede-Hospital-Mes", "Consumo farmacias", "Incidencias", "Criterio emparejamiento"):
        assert h in hojas
    rutas = exportar_csv(resultado, tmp_path / "csv")
    detalle = next(p for p in rutas if "detalle_factura_codigo" in p.name).read_text(encoding="utf-8-sig")
    assert "15000.50" in detalle and "7500.25" in detalle          # valores exactos en CSV
    with zipfile.ZipFile(io.BytesIO(exportar_zip(resultado, "ambos"))) as zf:
        nombres = zf.namelist()
    assert "conciliacion_rips_facturacion.xlsx" in nombres and any(n.startswith("csv/") for n in nombres)
    assert "REPORTE GENERAL" in texto_reporte_general(resultado)


def test_cli(tmp_path):
    from rips_conciliacion.__main__ import main

    rips = tmp_path / "RIPS_SEDE_TAME.zip"
    rips.write_bytes(crear_zip(archivos_3374()))
    fact = tmp_path / "fact.csv"
    fact.write_bytes(FACTURACION_CSV.encode())
    assert main(["--rips", f"{rips}@TAME", "--facturacion", str(fact), "--salida", str(tmp_path / "out"),
                 "--formato", "csv"]) == 0
    assert (tmp_path / "out" / "csv").is_dir()


def test_avance_detalla_archivos_internos_de_los_paquetes(monkeypatch):
    from rips_conciliacion import progreso

    monkeypatch.setattr(progreso, "INTERVALO", 0)
    mensajes = []
    paquete = crear_zip({f"2024-01/{n}": c for n, c in archivos_3374().items()})
    conciliar([("RIPS_SEDE_TAME.zip", paquete)], [("facturacion.csv", FACTURACION_CSV.encode())],
              avance=lambda m, f: mensajes.append((m, f)))
    internos = [m for m, f in mensajes if f is None]
    assert any("RIPS_SEDE_TAME.zip → 2024-01/AC000001.txt" in m for m in internos)
    assert mensajes[-1][1] is not None


def test_excel_recorta_hojas_grandes_y_conserva_las_mayores_diferencias(resultado, monkeypatch):
    from rips_conciliacion import reportes

    monkeypatch.setattr(reportes, "LIMITE_HOJA_EXCEL", 2)
    buffer = io.BytesIO()
    avisos = reportes.exportar_excel(resultado, buffer)
    libro = pd.read_excel(buffer, sheet_name=None)
    assert "Solo en CSV" in libro and "Detalle factura-código" not in libro
    difs = libro["Diferencias"]
    assert len(difs) == 2
    assert set(difs["dif_valor"].abs()) == {45000, 30000}      # las dos mayores en valor absoluto
    assert any("Diferencias" in a for a in avisos)

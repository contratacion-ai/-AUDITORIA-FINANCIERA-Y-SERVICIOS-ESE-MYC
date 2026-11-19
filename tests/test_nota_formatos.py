"""Notas técnicas en otros formatos: una hoja por municipio (Sanitas) y servicios + matriz de PyM (Coosalud)."""

from __future__ import annotations

import openpyxl
import pandas as pd
import pytest

from rips_auditoria import nota_tecnica as nt
from rips_auditoria import nota_tecnica_excel as nx
from rips_auditoria.nota_lectura import codigos_de_celda, leer_nota


def _hoja_municipio(ws, municipio, contrato, poblacion, filas):
    ws.append(["FORMATO NOTA TÉCNICA CAPITA"])
    ws.append(["CONTRATISTA:", None, "ESE MORENO Y CLAVIJO"])
    ws.append(["CONSECUTIVO DEL CONTRATO:", None, contrato])
    ws.append(["MUNICIPIO DE COBERTURA:", None, municipio])
    ws.append(["POBLACIÓN PROYECTADA:", None, poblacion])
    ws.append(["REGIMEN:", None, "CONTRIBUTIVO - SUBSIDIADO"])
    ws.append([])
    ws.append(["Agrupador", "CUPS", "CODIGO NT", "DESCRIPCIÓN", "Frecuencia", "Actividades mes"])
    for f in filas:
        ws.append(f)


@pytest.fixture
def nota_sanitas(tmp_path):
    wb = openpyxl.Workbook()
    _hoja_municipio(wb.active, "TAME", "IBUGCU 1529", 11300, [
        ["CONSULTA MEDICINA GENERAL", 890201, "NT1", "CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL", 0.3, 300],
        ["PROMOCIÓN Y DETECCIÓN", 890201, "NT2", "CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL (PYM)", 0.1, 100],
        ["LABORATORIO CLINICO", 903841, "NT3", "GLUCOSA EN SUERO", 0.05, 50],
    ])
    wb.active.title = "FORMATO CAPITA TAME"
    _hoja_municipio(wb.create_sheet("FORMATO CAPITA FORTUL"), "FORTUL", "IBUGCU1527", 4318, [
        ["CONSULTA MEDICINA GENERAL", 890201, "NT1", "CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL", 0.3, 120],
        ["ODONTOLOGÍA", 997301, "NT4", "DETARTRAJE SUPRAGINGIVAL", 0.1, 40],
    ])
    wb.create_sheet("Hoja1").append(["CUPS", "DESCRIPCION"])  # auxiliar: se omite
    wb["Hoja1"].append([111111, "NO ES DE LA NOTA"])
    ruta = tmp_path / "NOTAS_TECNICAS_POR_MUNICIPIO_SANITAS.xlsx"
    wb.save(ruta)
    return ruta


@pytest.fixture
def nota_coosalud(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "NT_SERVICIOS SUB"
    ws.append(["MUNICIPIO", "REGIMEN", "CodigoCUPS", "DescripcionCUPS", "Agrupador"])
    ws.append(["ARAUQUITA", "SUBSIDIADO", 890201, "CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL", "CONSULTA"])
    ws.append(["ARAUQUITA", "SUBSIDIADO", 903841, "GLUCOSA EN SUERO", "LABORATORIO CLINICO"])
    p = wb.create_sheet("NT 2025 PYM")
    p.append(["NOMBRE DE LA IPS:", "ESE MORENO Y CLAVIJO"])
    p.append(["GRUPO / ACTIVIDAD", "Código CUPS o REPS", "Pobación", "TOTAL POBLACION"])
    p.append(["Valoración integral primera infancia", "890201\n890301", "Primera infancia", 100])
    p.append(["Vacunación", "993101 al 993103", "Todos", 50])
    p.append(["Tamizaje", "5DSB01 - 997310", "Adultez", 20])
    ruta = tmp_path / "NOTA_TECNICA_COOSALUD.xlsx"
    wb.save(ruta)
    return ruta


def test_codigos_de_celda():
    assert codigos_de_celda("890201\n890301") == ["890201", "890301"]
    assert codigos_de_celda("993101 al 993103") == ["993101", "993102", "993103"]
    assert codigos_de_celda("5DSB01 - 997310, 1201") == ["5DSB01", "997310", "001201"]
    assert codigos_de_celda("No aplica") == [] and codigos_de_celda(None) == []


def test_nota_por_municipio_sanitas(nota_sanitas):
    df = leer_nota(nota_sanitas)
    assert set(df["hoja"]) == {"FORMATO CAPITA TAME", "FORMATO CAPITA FORTUL"}  # Hoja1 omitida
    cat = nt.cargar_catalogo(nota_sanitas)
    amb = {a["ambito"]: a for a in cat.attrs["ambitos"]}
    assert set(amb) == {"TAME", "FORTUL"}
    assert amb["TAME"]["contrato"] == "IBUGCU1529" and amb["TAME"]["poblacion"] == "11300"
    tame = nt.cargar_catalogo(nota_sanitas, "Tame").set_index("codigo")
    assert list(tame.index) == ["890201", "903841"] and tame.loc["890201", "meta_mes"] == 400  # 300 + 100
    assert nt.cargar_catalogo(nota_sanitas, "FORTUL").attrs["repetidos"] == 0
    with pytest.raises(ValueError):
        nt.cargar_catalogo(nota_sanitas, "SARAVENA")


def test_nota_coosalud_servicios_y_pym(nota_coosalud):
    cat = nt.cargar_catalogo(nota_coosalud)
    assert [a["ambito"] for a in cat.attrs["ambitos"]] == ["ARAUQUITA"]
    c = cat.set_index("codigo")
    assert {"890201", "903841", "890301", "993102", "5DSB01", "997310"} <= set(c.index)
    assert c.loc["890201", "descripcion"] == "CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL"  # la lista va antes que PyM
    assert "Primera infancia" in c.loc["890301", "descripcion"]


def _at(filas):
    df = pd.DataFrame(filas, columns=["sede", "num_id", "codigo_rips", "fecha", "nombre_entidad"])
    return df.assign(fuente="RIPS TXT", tipo_archivo="AC", archivo="AC1.txt", linea=1, factura="F1",
                     cod_entidad=df["nombre_entidad"].map({"SANITAS": "EPS005"}).fillna("EPS037"),
                     tipo_id="CC", edad_anios=30, con_error=False, error="", nota=None, fecha_de_factura=False)


def test_una_nota_por_municipio_segun_la_sede(nota_sanitas, tmp_path):
    at = _at([
        ("HOSPITAL SAN ANTONIO", "1", "890201", "2025-01-10", "SANITAS"),
        ("HOSPITAL SAN ANTONIO", "2", "890201", "2025-01-12", "SANITAS"),
        ("HOSPITAL SAN ANTONIO", "1", "903841", "2025-02-03", "SANITAS"),
        ("HOSPITAL SAN FRANCISCO", "3", "890201", "2025-02-05", "SANITAS"),
        ("HOSPITAL SAN FRANCISCO", "4", "997301", "2025-02-06", "SANITAS"),
        ("CENTRO DE SALUD JESUS CORONEL", "5", "890201", "2025-01-15", "SANITAS"),
        ("HOSPITAL SAN ANTONIO", "6", "890201", "2025-01-15", "NUEVA EPS"),
    ])
    cfg = {"nota_tecnica": {**nt.perfil_eps("SANITAS"), "json_sin_eps": "excluir"}}
    calcs = {c["resumen"]["ambito"]: c for c in nt.calcular_nota(at, nota_sanitas, cfg=cfg)}
    assert set(calcs) == {"TAME", "FORTUL"}
    tame, fortul = calcs["TAME"], calcs["FORTUL"]
    assert tame["resumen"]["desde"] == fortul["resumen"]["desde"] == "2025-01"  # mismo período
    assert tame["matriz"].loc["890201"].tolist() == [2, 0] and tame["matriz"].loc["903841"].tolist() == [0, 1]
    assert fortul["matriz"].loc["890201"].tolist() == [0, 1] and fortul["matriz"].loc["997301"].tolist() == [0, 1]
    assert tame["resumen"]["sedes_sin_ambito"] == ["CENTRO DE SALUD JESUS CORONEL"]
    assert tame["resumen"]["contrato"] == "IBUGCU1529"
    # sede configurable -> municipio
    cfg["nota_tecnica"]["sedes_municipio"] = {"JESUS CORONEL": "TAME"}
    tame2 = {c["resumen"]["ambito"]: c for c in nt.calcular_nota(at, nota_sanitas, cfg=cfg)}["TAME"]
    assert tame2["matriz"].loc["890201"].tolist() == [3, 0] and tame2["resumen"]["sedes_sin_ambito"] == []

    nota, _hoja = nx.generar(tame, tmp_path)
    assert nota.name == "NOTA_TECNICA_SANITAS_TAME_2025-01_2025-02.xlsx"
    wb = openpyxl.load_workbook(nota)
    texto = "\n".join(str(c.value) for c in wb["Nota técnica"]["A"] if c.value)
    assert "Contrato N.º: IBUGCU1529" in texto and "Población asignada: 11300 · Municipio(s): TAME" in texto
    enc = [c.value for c in wb["Tabla resumen"][1]]
    assert "META MES (nota técnica)" in enc and "% CUMPLIMIENTO" in enc
    fila = next(r for r in wb["Tabla resumen"].iter_rows(min_row=2, values_only=True) if r[0] == "890201")
    assert fila[enc.index("META MES (nota técnica)")] == 400


def test_municipio_de_sede():
    amb = ["TAME", "FORTUL", "ARAUQUITA", "CRAVO NORTE", "PUERTO RONDON"]
    assert nt.municipio_de_sede("Hospital San Lorenzo", amb) == "ARAUQUITA"
    assert nt.municipio_de_sede("PUESTO DE SALUD PUERTO RONDÓN", amb) == "PUERTO RONDON"
    assert nt.municipio_de_sede("HOSPITAL SAN JUAN DE DIOS", amb) == "PUERTO RONDON"
    assert nt.municipio_de_sede("HOSPITAL SAN JOSE", amb) == "CRAVO NORTE"
    assert nt.municipio_de_sede("HOSPITAL SAN JOSE", ["TAME"]) == ""  # el municipio no está en la nota

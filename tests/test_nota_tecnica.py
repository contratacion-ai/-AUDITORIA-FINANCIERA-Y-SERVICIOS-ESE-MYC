"""Nota técnica de capitación: pacientes únicos por código CUPS y mes."""

from __future__ import annotations

import json
import zipfile

import openpyxl
import pandas as pd
import pytest

from rips_auditoria import nota_tecnica as nt
from rips_auditoria import nota_tecnica_excel as nx

PRESTADOR = "817940020603"


def _remision(carpeta, remision, factura, eps_cod, eps_nom, mes, ac, ap=(), am=(), us=None):
    """Escribe una remisión RIPS Res. 3374 (CT, AF, US, AC, AP, AM) en ``carpeta``."""
    carpeta.mkdir(parents=True, exist_ok=True)
    af = [f"{PRESTADOR},ESE MORENO Y CLAVIJO,NI,900034131,{factura},28/{mes}/2023,01/{mes}/2023,28/{mes}/2023,"
          f"{eps_cod},{eps_nom},CAP-2023-01,POS,,0,0,0,100000.00"]
    pacientes = us or sorted({(t, n) for t, n, *_ in [*ac, *ap, *am]})
    lus = [f"{t},{n},{eps_cod},2,PEREZ,GOMEZ,ANA,,{35 if n != '9' else 3},1,F,81,794,U" for t, n in pacientes]
    lac = [f"{factura},{PRESTADOR},{t},{n},{d}/{mes}/2023,,{c},10,13,J069,,,,1,20000.00,0,20000.00" for t, n, d, c in ac]
    lap = [f"{factura},{PRESTADOR},{t},{n},{d}/{mes}/2023,,{c},1,1,,,,,,30000.00" for t, n, d, c in ap]
    lam = [f"{factura},{PRESTADOR},{t},{n},,{c},1,MEDICAMENTO,TABLETA,1MG,MG,1,100.00,100.00" for t, n, c in am]
    archivos = {"AF": af, "US": lus, "AC": lac, "AP": lap, "AM": lam}
    ct = []
    for tipo, lineas in archivos.items():
        if lineas:
            (carpeta / f"{tipo}{remision}.txt").write_text("\r\n".join(lineas) + "\r\n", encoding="utf-8")
            ct.append(f"{PRESTADOR},28/{mes}/2023,{tipo}{remision},{len(lineas)}")
    (carpeta / f"CT{remision}.txt").write_text("\r\n".join(ct) + "\r\n", encoding="utf-8")


@pytest.fixture
def catalogo(tmp_path):
    ruta = tmp_path / "NUEVA_EPS.xlsx"
    pd.DataFrame({
        "CodigoCUP": [890201, 890301, 903841, "MD000012", "I10204", 997301],
        "Homologo": [890201, 890301, 903841, "19973908-1", "I10204", 997301],
        "ESTADO CUPS": ["ACTIVO", "ACTIVO", "ACTIVO", "ACTIVO", "INACTIVO", "ACTIVO"],
        "DescripcionCUP": ["CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL", "CONSULTA DE CONTROL MEDICINA GENERAL",
                           "GLUCOSA EN SUERO", "HIOSCINA N-BUTIL BROMURO 10MG (TABLETA)", "INFORMACION ANTICONCEPCION",
                           "DETARTRAJE SUPRAGINGIVAL"],
    }).to_excel(ruta, index=False)
    return ruta


@pytest.fixture
def entradas(tmp_path):
    base = tmp_path / "rips" / "Hospital San Antonio"
    # Enero: paciente 1 dos veces 890201 (cuenta 1), paciente 2; línea duplicada exacta del paciente 2
    _remision(base / "01. Enero" / "10 NUEVA EPS CAPITA", "000001", "FE100", "EPSS41", "NUEVA EPS S.A.", "01",
              ac=[("CC", "1", "03", "890201"), ("CC", "1", "20", "890201"), ("CC", "2", "05", "890201"),
                  ("CC", "2", "05", "890201")],
              ap=[("CC", "1", "04", "903841")],
              am=[("CC", "2", "019973908-01")])  # MD000012 por su CUM homólogo (con ceros)
    # Febrero: paciente 1 vuelve (cuenta en febrero), paciente 3 en control, menor de edad (9)
    _remision(base / "02. Febrero" / "11 NUEVA EPS CAPITA", "000002", "FE200", "EPSS41", "NUEVA EPS S.A.", "02",
              ac=[("CC", "1", "02", "890201"), ("CC", "3", "10", "890301"), ("TI", "9", "11", "890201")])
    # Otra EPS: no entra
    _remision(base / "02. Febrero" / "12 SANITAS EVENTO", "000003", "FE300", "EPS005", "SANITAS EPS", "02",
              ac=[("CC", "5", "02", "890201")])
    # JSON Res. 2275 de marzo: repetido, nota crédito, sin identificación, fuera de período y código fuera de la nota
    js = base / "03. Marzo" / "json"
    js.mkdir(parents=True)

    def factura(num, servicios, nota=None, doc="7"):
        return {"numDocumentoIdObligado": "900034131", "numFactura": num, "tipoNota": nota, "numNota": None,
                "usuarios": [{"tipoDocumentoIdentificacion": "CC", "numDocumentoIdentificacion": doc,
                              "fechaNacimiento": "1950-03-01", "servicios": servicios}]}

    cons = {"codConsulta": "890201", "fechaInicioAtencion": "2023-03-08 08:00", "vrServicio": 20000}
    (js / "FJ1.json").write_text(json.dumps(factura("FJ1", {"consultas": [cons, cons],
                                                            "procedimientos": [{"codProcedimiento": "999999",
                                                                                "fechaInicioAtencion": "2023-03-08 09:00"}]})))
    (js / "FJ2.json").write_text(json.dumps(factura("FJ2", {"consultas": [cons]}, nota="NC", doc="8")))
    (js / "FJ3.json").write_text(json.dumps(factura("FJ3", {"consultas": [cons]}, doc="")))
    (js / "FJ4.json").write_text(json.dumps(factura("FJ4", {"consultas": [{**cons, "fechaInicioAtencion": "2022-12-30 08:00"}]})))
    return [tmp_path / "rips" / "Hospital San Antonio"]


def test_catalogo_de_la_nota(catalogo):
    c = nt.cargar_catalogo(catalogo).set_index("codigo")
    assert len(c) == 6
    assert c.loc["MD000012", "clave"] == "19973908-1" and c.loc["MD000012", "tipo"] == "Medicamento (CUM)"
    assert c.loc["I10204", "estado"] == "INACTIVO"


def test_conteo_de_pacientes_unicos_por_cups_y_mes(catalogo, entradas):
    calc = nt.procesar_nota(entradas, catalogo, desde="2023-01", hasta="2023-03")
    m = calc["matriz"]
    assert list(m.columns) == ["2023-01", "2023-02", "2023-03"]
    assert m.loc["890201", "2023-01"] == 2  # paciente 1 dos veces + paciente 2 (y su línea duplicada)
    assert m.loc["890201", "2023-02"] == 2  # paciente 1 otra vez (cuenta en febrero) + paciente 9; Sanitas excluida
    assert m.loc["890201", "2023-03"] == 1  # JSON: paciente 7 (repetido) ; NC, sin id y diciembre fuera
    assert m.loc["890301", "2023-02"] == 1 and m.loc["903841", "2023-01"] == 1
    assert m.loc["MD000012", "2023-01"] == 1  # por CUM homólogo con ceros
    assert m.loc["997301"].sum() == 0 and m.loc["I10204"].sum() == 0  # en 0, trazables
    cal = calc["calidad"]
    assert "SIN REGISTROS" in cal.loc["997301", "observaciones"]
    assert "INACTIVO" in cal.loc["I10204", "observaciones"]
    o = cal.loc["890201"]
    assert o["duplicados"] >= 2 and o["excl_anulado"] == 1 and o["excl_sin_id"] == 1 and o["excl_fuera_periodo"] == 1
    assert o["registros_otra_eps"] == 1 and o["json_eps_asumida"] >= 1
    assert "duplicado" in o["observaciones"] and "nota crédito" in o["observaciones"]
    assert cal.loc["MD000012", "fecha_de_factura"] == 1  # AM sin fecha de prestación
    assert cal.loc["890201", "pacientes_periodo"] == 4  # 1, 2, 9 y 7
    assert "999999" in calc["fuera_nota"].index
    cv = calc["curso_vida"]
    assert cv.loc["890201", "Infancia (6 a 11 años)"] == 0 and cv.loc["890201", "Primera infancia (0 a 5 años)"] == 1
    assert cv.loc["890201", "Vejez (60 años y más)"] == 1  # JSON fechaNacimiento 1950
    r = calc["resumen"]
    assert r["meses_sin_informacion"] == [] and r["codigos_sin_registros"] == 2


def test_mes_sin_informacion_y_rango(catalogo, entradas):
    calc = nt.procesar_nota(entradas, catalogo, desde="2023-01", hasta="2023-04")
    assert calc["resumen"]["meses_sin_informacion"] == ["2023-04"]
    assert calc["matriz"]["2023-04"].sum() == 0


def test_excel_nota_y_hoja_con_formulas_y_tabla_dinamica(catalogo, entradas, tmp_path):
    calc = nt.procesar_nota(entradas, catalogo, desde="2023-01", hasta="2023-03")
    nota, hoja = nx.generar(calc, tmp_path / "salida")
    wb = openpyxl.load_workbook(nota)
    assert {"Nota técnica", "Tabla resumen", "Calidad", "Curso de vida", "Códigos fuera de la nota"} <= set(wb.sheetnames)
    texto = "\n".join(str(c.value) for c in wb["Nota técnica"]["A"] if c.value)
    for seccion in ("OBJETIVO", "FUENTE DE DATOS", "CRITERIOS DE INCLUSIÓN", "METODOLOGÍA", "TABLA RESUMEN",
                    "OBSERVACIONES DE CALIDAD", "CONCLUSIONES"):
        assert seccion in texto
    assert "[NÚMERO DEL CONTRATO]" in texto  # campos del contrato entre corchetes
    h = openpyxl.load_workbook(hoja)
    assert h.sheetnames[:2] == ["Matriz", "Tabla dinámica"] and "Datos" in h.sheetnames and "Validación" in h.sheetnames
    m = h["Matriz"]
    assert m["D3"].value == "2023-01" and m["D5"].value.startswith("=COUNTIFS(Datos!$A:$A,$A5")
    filas = {m.cell(row=r, column=1).value: r for r in range(5, 11)}
    r = filas["997301"]
    assert m[f"G{r}"].value == f"=SUM(D{r}:F{r})" and "SIN REGISTROS" in m[f"I{r}"].value
    assert m["D11"].value == "=SUM(D5:D10)" and m["D12"].value == '=IF(D11=0,"SIN INFORMACION","OK")'
    with zipfile.ZipFile(hoja) as z:
        nombres = z.namelist()
        assert any(n.startswith("xl/pivotTables/pivotTable") for n in nombres)
        assert any(n.startswith("xl/pivotCache/pivotCacheDefinition") for n in nombres)
    datos = pd.read_excel(hoja, sheet_name="Datos")
    assert len(datos) == len(calc["pacientes"])
    assert datos.groupby(["CUPS", "MES"])["PACIENTE"].nunique().max() == 2


def test_plantilla_en_blanco(tmp_path):
    ruta = nx.exportar_nota(tmp_path / "PLANTILLA.xlsx")
    wb = openpyxl.load_workbook(ruta)
    texto = "\n".join(str(c.value) for c in wb["Nota técnica"]["A"] if c.value)
    assert "[NOMBRE DE LA EPS]" in texto and "[MES INICIAL]" in texto and "[N.º]" in texto
    assert wb["Tabla resumen"]["A2"].value == "[CÓDIGO]"


def test_solo_txt_sin_json_y_comando(catalogo, tmp_path, capsys):
    """Regresión: sin archivos JSON la fecha conserva su tipo (antes fallaba el .dt)."""
    from rips_auditoria.cli import main

    base = tmp_path / "solo_txt" / "Hospital San Jose"
    _remision(base / "05. Mayo" / "10 NUEVA EPS", "000010", "FX1", "EPSS41", "NUEVA EPS S.A.", "05",
              ac=[("CC", "1", "03", "890201")])
    calc = nt.procesar_nota([base], catalogo)
    assert calc["resumen"]["desde"] == calc["resumen"]["hasta"] == "2023-05"
    assert calc["matriz"].loc["890201", "2023-05"] == 1
    assert main(["nota-tecnica", str(base), "--nota", str(catalogo), "-s", str(tmp_path / "out")]) == 0
    assert "1 pacientes únicos" in capsys.readouterr().out
    assert len(list((tmp_path / "out").glob("*.xlsx"))) == 2
    assert main(["nota-tecnica", "--plantilla", "-s", str(tmp_path / "pl")]) == 0
    assert (tmp_path / "pl" / "PLANTILLA_NOTA_TECNICA_CAPITACION.xlsx").exists()


def test_ventana_de_cargue_genera_la_nota(catalogo, entradas, monkeypatch):
    pytest.importorskip("httpx")
    import time

    from fastapi.testclient import TestClient

    from rips_auditoria.web import servidor

    monkeypatch.setenv("AUDITORIA_ESE_DIR", "/no/existe")
    c = TestClient(servidor.app, client=("127.0.0.1", 50000))
    assert c.get("/api/nota-tecnica/estado").json() == {"archivo": None}
    # sin nota guardada ni archivo: error claro
    r = c.post("/api/cargues", data={"ruta_local": str(entradas[0]), "nota_tecnica": "true"})
    assert r.status_code == 400 and "nota técnica" in r.json()["detail"]
    r = c.post("/api/cargues", data={"ruta_local": str(entradas[0]), "nota_tecnica": "true", "nt_desde": "2023-01",
                                     "nt_hasta": "2023-03", "nt_eps": "NUEVA EPS"},
               files={"nota_archivo": ("NUEVA_EPS.xlsx", catalogo.read_bytes())})
    assert r.status_code == 200, r.text
    for _ in range(300):
        t = c.get(f"/api/cargues/{r.json()['id']}").json()
        if t["estado"] in ("terminado", "error"):
            break
        time.sleep(0.2)
    assert t["estado"] == "terminado", t["error"]
    nt_ = t["resultado"]["nota_tecnica"]
    assert nt_["codigos_nota"] == 6 and nt_["pacientes_unicos_periodo"] == 5  # pacientes 1, 2, 3, 9 y 7 and nt_["desde"] == "2023-01"
    assert sum(a.startswith("NOTA_TECNICA/") for a in t["resultado"]["archivos"]) == 2
    assert c.get("/api/nota-tecnica/estado").json()["codigos"] == 6  # queda guardada
    assert c.get("/api/nota-tecnica/plantilla").status_code == 200

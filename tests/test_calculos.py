import io

import openpyxl
import pandas as pd
import pytest

from rips_auditoria import maestros
from rips_auditoria.analisis import Resultado, analizar
from rips_auditoria.calculos import capita, cruces
from rips_auditoria.clasificacion import clasificar
from rips_auditoria.constantes import CONTROL
from rips_auditoria.ingesta.lote import procesar
from rips_auditoria.referencias.dashboards import cargar_final
from rips_auditoria.servicios import construir


def _servicios_control() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Servicios sintéticos con las frecuencias, pacientes y asistencias del dashboard FINAL y una NT
    cuyo costo unitario × frecuencia da el costo mensual publicado, repartido con GPCT por grupo."""
    ref = cargar_final()
    filas, nt = [], []
    for m, r in enumerate(ref.detalle_mensual, start=1):
        per = f"2026-{m:02d}"
        P, A, Q = r["pacientes"], r["asistencias"], r["cups"]
        base, resto = divmod(Q, A)
        cant_grupo = [0] * 10
        for j in range(A):
            g = j % 10
            q = base + (1 if j < resto else 0)
            cant_grupo[g] += q
            filas.append({"tipo_archivo": "AP", "num_factura": f"F{m}", "paciente": f"P{m}-{j % P}",
                          "fecha": f"{1 + j // P:02d}/{m:02d}/2026", "mes": per, "periodo": per,
                          "municipio": "TAME", "hospital": "H", "codigo": f"T{m:02d}{g}", "tipo_codigo": "CUPS",
                          "descripcion": None, "cantidad": float(q), "valor_unitario": 0.0, "valor_total": 0.0,
                          "finalidad": "1", "tipo_servicio_at": None, "es_episodio": False,
                          "modalidad_declarada": "CAPITA", "eps_objetivo": True})
        for g, (grupo, pct) in enumerate(zip(ref.grupos, ref.grupos_pct)):
            nt.append({"codigo": f"T{m:02d}{g}", "descripcion": grupo, "grupo": grupo, "tipo_codigo": "CUPS",
                       "municipio": "TODOS", "mes": None, "frecuencia_prevista": None,
                       "costo_unitario": r["costo"] * pct / 100 / cant_grupo[g]})
    return pd.DataFrame(filas), pd.DataFrame(nt)


def test_motor_capita_reproduce_totales_de_control():
    s, nt = _servicios_control()
    s = clasificar(s, None, nt)
    assert (s["modalidad"] == "CAPITA").all()
    c = capita.costear(s, nt)
    meses = sorted(c["mes"].unique())
    grupos = capita.costos_grupo_mes(c, meses)
    det = capita.detalle_mensual(c, grupos[meses].sum(axis=0), meses)
    assert abs(det["costo"].sum() - CONTROL["capita_total"]) <= 1
    assert det["cups"].sum() == CONTROL["cups"]
    assert det["pacientes"].sum() == CONTROL["pacientes_unicos"]
    assert det["asistencias"].sum() == CONTROL["asistencias"]
    k = capita.kpis(det["costo"].tolist(), det["pacientes"].tolist(), det["asistencias"].tolist())
    assert round(k.promedio_mes) == CONTROL["capita_promedio_mes"]
    assert round(k.costo_usuario_prom) == 69_781
    # participación por grupo igual a la del dashboard
    pct = grupos.set_index("grupo_nombre")["participacion_pct"].round(1)
    assert pct.iloc[0] == 43.3 and pct.sum() == pytest.approx(100, abs=0.2)


def test_clasificacion_precedencia_y_trazabilidad():
    s = pd.DataFrame([
        {"codigo": "A1", "modalidad_declarada": "EVENTO"},     # OFERTADO en catálogo -> contractual CAPITA
        {"codigo": "B1", "modalidad_declarada": "CAPITA"},     # NO OFERTADO -> contractual EVENTO
        {"codigo": "C1", "modalidad_declarada": None},         # solo en NT -> CAPITA
        {"codigo": "D1", "modalidad_declarada": "PGP"},        # sin marca -> declarada
        {"codigo": "E1", "modalidad_declarada": None},         # sin nada -> EVENTO por defecto
    ]).assign(tipo_archivo="AP", descripcion=None, finalidad="1", tipo_servicio_at=None, es_episodio=False)
    cat = pd.DataFrame([{"codigo": "A1", "grupo": "LABORATORIO", "estado": "OFERTADO"},
                        {"codigo": "B1", "grupo": "PROCEDIMIENTOS", "estado": "NO OFERTADO"}])
    nt = pd.DataFrame([{"codigo": "C1", "grupo": "IMAGENOLOGIA", "costo_unitario": 10.0}])
    r = clasificar(s, cat, nt).set_index("codigo")
    assert r.loc["A1", "modalidad_catalogo"] == "CAPITA" and r.loc["A1", "regla_modalidad"] == "CATALOGO_OFERTADO"
    assert r.loc["A1", "modalidad"] == "EVENTO" and r.loc["A1", "discrepancia_modalidad"]
    assert r.loc["B1", "modalidad_catalogo"] == "EVENTO" and r.loc["B1", "discrepancia_modalidad"]
    assert r.loc["C1", "regla_modalidad"] == "NOTA_TECNICA" and r.loc["C1", "modalidad"] == "CAPITA"
    assert r.loc["C1", "grupo"] == "IMAGENOLOGIA"
    assert r.loc["D1", "regla_modalidad"] == "MODALIDAD_DECLARADA" and r.loc["D1", "modalidad"] == "PGP"
    assert r.loc["E1", "regla_modalidad"] == "POR_DEFECTO" and r.loc["E1", "modalidad"] == "EVENTO"
    assert r.loc["E1", "regla_grupo"] in ("HEURISTICA", "REGLA_DASHBOARD")


def test_cruces_nt_tarifario_y_facturacion(carpeta_rips):
    l = procesar([carpeta_rips])
    s = construir(l)
    s["modalidad_declarada"] = None          # sin modalidad en la carpeta: decide catálogo/NT
    nt = pd.DataFrame([{"codigo": "902207", "grupo": "LABORATORIO", "municipio": "TAME", "mes": None,
                        "frecuencia_prevista": 0.5, "costo_unitario": 9000.0, "descripcion": "HEMOGRAMA"}])
    s = clasificar(s, None, nt)
    x = cruces.rips_vs_nota_tecnica(s, nt, 10)
    fila = x[x.codigo == "902207"].iloc[0]
    assert fila.estado == "SOBREPASO" and fila.desviacion == 0.5 and fila.desviacion_pct == 100
    tar = pd.DataFrame([{"codigo": "906249", "tarifa": 60000, "vigencia_desde": "01/01/2026", "vigencia_hasta": None},
                        {"codigo": "890201", "tarifa": 30000, "vigencia_desde": "01/01/2026", "vigencia_hasta": None}])
    t = cruces.evento_vs_tarifario(s, tar, 1).set_index("codigo")
    assert t.loc["906249", "estado"] == "SOBRE_TARIFA" and t.loc["906249", "dif_valor_total"] == 10000
    assert t.loc["890201", "estado"] == "CONFORME"
    assert t.loc["S31307", "estado"] == "SIN_TARIFA"
    fac = pd.DataFrame([{"periodo": "2026-08", "municipio": None, "ambito": None, "codigo": "906249", "cantidad": 2,
                         "valor": 130000, "descripcion": "VIH"},
                        {"periodo": "2026-08", "municipio": None, "ambito": None, "codigo": "999999", "cantidad": 1,
                         "valor": 5000, "descripcion": "SIN RIPS"}])
    f = cruces.rips_vs_facturacion(s, fac).set_index("codigo")
    assert f.loc["906249", "estado"] == "CONCILIADO"
    assert f.loc["999999", "estado"] == "FACTURADO_SIN_RIPS" and f.loc["999999", "glosa_potencial"] == 5000
    assert f.loc["890201", "estado"] == "RIPS_SIN_FACTURA"
    cf = cruces.conciliacion_facturas(l.tablas)
    assert set(cf["estado"]) == {"CONCILIADA"}


def test_costo_por_municipio_tiene_prioridad():
    s = pd.DataFrame([{"codigo": "X", "municipio": "TAME", "modalidad": "CAPITA", "es_episodio": False, "cantidad": 2.0},
                      {"codigo": "X", "municipio": "ARAUCA", "modalidad": "CAPITA", "es_episodio": False, "cantidad": 2.0}])
    nt = pd.DataFrame([{"codigo": "X", "municipio": "TAME", "costo_unitario": 100.0},
                       {"codigo": "X", "municipio": "TODOS", "costo_unitario": 50.0}])
    c = capita.costear(s, nt)
    assert c["costo_capita"].tolist() == [200.0, 100.0]


def test_maestro_informe_nt_costos_y_marcacion():
    """Libro mínimo con el diseño que lee el PARSER del dashboard (hojas MARCACION y NT COSTOS)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "MARCACION CUPS CAPITA"
    ws.append(["N", "CODIGO CUPS", "HOMOLOGO", "DESCRIPCION", "GRUPO", "SUBGRUPO", "TARIFA 03/09/2026", "ESTADO", "UVB"])
    ws.append([1, "902207", "902207", "HEMOGRAMA I", "LABORATORIO", "HEMATOLOGIA", 43600, "OFERTADO", 3.1])
    ws.append([2, "S31307", None, "TRASLADO", "TRASLADOS", None, 1400000, "NO OFERTADO", None])
    nt = wb.create_sheet("NT COSTOS")
    nt.append(["titulo"])
    nt.append(["CODIGOCUPS", "DESCRIPCION CUPS", "GRUPO", "ENERO", "FEBRERO", "ENERO", "FEBRERO"])
    nt.append(["902207", "HEMOGRAMA I", "LABORATORIO", 10, 30, 100000, 300000])
    nt.append(["990201", "EDUCACION", "PROMOCION Y MANTENIMIENTO DE LA SALUD", 4, 0, 2000, 0])
    buf = io.BytesIO()
    wb.save(buf)
    res = maestros.cargar("informe", buf.getvalue(), "INFORME.xlsx")
    assert res["catalogo"]["filas"] == 2
    cat = maestros.leer("catalogo")
    assert cat.set_index("codigo").loc["902207", "estado"] == "OFERTADO"
    ntd = maestros.leer("nota_tecnica").set_index("codigo")
    assert ntd.loc["902207", "costo_unitario"] == 10000
    assert ntd.loc["990201", "costo_unitario"] == 500


def test_maestro_tarifario_plantilla_y_pendiente():
    csv = "codigo;descripcion;tarifa;vigencia_desde;vigencia_hasta\n906249;VIH;135.149,09;01/01/2026;\n"
    r = maestros.cargar("tarifario", csv.encode(), "tarifario.csv")
    assert r["tarifario"]["notas"]          # marcado pendiente de confirmación
    t = maestros.leer("tarifario")
    assert t.iloc[0]["tarifa"] == pytest.approx(135149.09)
    est = {e["nombre"]: e for e in maestros.estado()}
    assert est["tarifario"]["pendiente_confirmacion"]


def test_analisis_completo_informes_y_json(carpeta_rips, tmp_path):
    from rips_auditoria.informes import excel, pdf
    l = procesar([carpeta_rips])
    r = analizar([l])
    assert r.indicadores["evento_total"] > 0
    assert not r.tablas["top20_evento"].empty
    assert (r.tablas["riesgo"]["codigo"] == "S31307").any()
    x = excel.escribir(r, tmp_path / "i.xlsx")
    hojas = openpyxl.load_workbook(x).sheetnames
    for h in ("Resumen", "CAPITA Grupos", "Detalle Mensual CAPITA", "TOP 20 EVENTO", "Simulación E→C", "Escenarios",
              "Riesgo", "Hallazgos", "NT vs RIPS", "EVENTO vs Tarifario", "RIPS vs Facturación", "Trazabilidad"):
        assert h in hojas
    # Anonimización: ningún documento ni nombre del paciente en el informe
    contenido = " ".join(str(c.value) for ws in openpyxl.load_workbook(x).worksheets for row in ws.iter_rows()
                         for c in row if c.value is not None)
    for dato in ("1000001", "2000002", "PEREZ", "LUIS"):
        assert dato not in contenido
    p = pdf.escribir(r, tmp_path / "i.pdf")
    assert p.read_bytes()[:4] == b"%PDF"
    r2 = Resultado.desde_json(r.a_json())
    assert r2.indicadores == r.indicadores
    assert r2.tablas["escenarios"].shape == r.tablas["escenarios"].shape


def test_dos_lotes_con_los_mismos_archivos_no_duplican(carpeta_rips):
    l1, l2 = procesar([carpeta_rips]), procesar([carpeta_rips])
    uno, dos = analizar([l1]), analizar([l1, l2])
    assert dos.indicadores["evento_total"] == uno.indicadores["evento_total"]
    assert any("más de un lote" in a for a in dos.advertencias)

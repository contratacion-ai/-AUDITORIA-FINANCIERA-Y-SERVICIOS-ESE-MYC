"""Plan de acción con cierre verificado, Drive «sólo JSON» y notas técnicas con los JSON de Drive."""

from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest

from rips_auditoria import aplicativo, depuracion, drive, json_rips, nota_tecnica, plan_accion
from rips_auditoria import tablero as tb

from .test_drive import _factura_drive


def _alerta(cat, sede, mes, msg, valor=None, nivel="CRÍTICA"):
    return {"nivel": nivel, "categoria": cat, "sede": sede, "mes": mes, "mensaje": msg, "accion": "Hacer algo",
            "valor": valor}


def test_plan_de_accion_responsable_plazo_y_cierre_verificado():
    hoy = dt.date(2026, 11, 25)
    alertas = [_alerta("Sin RIPS", "HOSPITAL SAN ANTONIO", "2026-03", "Sin RIPS de marzo"),
               _alerta("Conciliación AF vs. servicios", "HOSPITAL SAN LORENZO", "2026-04", "3 facturas (diferencia $ 900)",
                       900.0, "ALTA"),
               _alerta("Variación mensual", "HOSPITAL SAN LORENZO", "2026-05", "el valor de servicios subió 80%", 80.0, "MEDIA")]
    p = plan_accion.construir(alertas, hoy=hoy).set_index("categoria")
    assert p.loc["Sin RIPS", "prioridad"] == 1 and p.loc["Sin RIPS", "fecha_limite"] == "2026-11-30"
    assert p.loc["Sin RIPS", "responsable"] == "Facturación de la sede" and p.loc["Sin RIPS", "estado"] == "PENDIENTE"
    assert p.loc["Conciliación AF vs. servicios", "valor_en_riesgo"] == 900.0
    assert pd.isna(p.loc["Variación mensual", "valor_en_riesgo"])  # un porcentaje no es dinero en riesgo
    # el equipo la toma y la reasigna; el texto de la alerta cambia con las cifras pero la tarea es la misma
    i = p.loc["Conciliación AF vs. servicios", "id"]
    plan_accion.actualizar(i, "EN PROCESO", "Yerson · Facturación", "Llamada con la sede")
    alertas[1]["mensaje"] = "2 facturas (diferencia $ 400)"
    p = plan_accion.construir(alertas, hoy=hoy + dt.timedelta(days=10)).set_index("categoria")
    c = p.loc["Conciliación AF vs. servicios"]
    assert c["estado"] == "EN PROCESO" and c["responsable"] == "Yerson · Facturación" and c["dias_restantes"] == -2
    with pytest.raises(ValueError):
        plan_accion.actualizar(i, "HECHA")
    # la sede subió los RIPS de marzo: la alerta ya no aparece -> resuelta y verificada
    p = plan_accion.construir(alertas[1:], hoy=hoy).set_index("categoria")
    assert p.loc["Sin RIPS", "estado"] == plan_accion.VERIFICADA
    r = plan_accion.resumen(p.reset_index())
    assert r["verificadas"] == 1 and r["abiertas"] == 2 and r["valor_en_riesgo_abierto"] == 900.0
    # si vuelve a faltar, se reabre
    p = plan_accion.construir(alertas, hoy=hoy).set_index("categoria")
    assert p.loc["Sin RIPS", "estado"] == "PENDIENTE"


def test_plan_en_el_tablero_y_en_el_excel(tmp_path):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from rips_auditoria.web import servidor

    filas = pd.DataFrame([{c: "" for c in aplicativo.COLUMNAS} | {
        "MES": m, "EPS": "NUEVA EPS", "SEDE": s, "FACTURA_FEV": f"F{m}{s[-1]}", "TIPO_SERVICIO": "Consultas",
        "CODIGO_CUPS_CUMS": "890201", "CANTIDAD": 1, "VALOR_SERVICIO": 1000, "VALOR_FACTURA_FEV": 1000,
        "VALIDACION": "CUV MSPS VALIDADA"} for m, s in (("2026-01", "HOSPITAL A"), ("2026-02", "HOSPITAL A"),
                                                         ("2026-03", "HOSPITAL A"), ("2026-01", "HOSPITAL B"),
                                                         ("2026-03", "HOSPITAL B"))])
    json_rips.integrar(filas)
    c = TestClient(servidor.app, client=("127.0.0.1", 50000))
    r = c.get("/api/tablero/plan").json()
    falta = [f for f in r["filas"] if f["categoria"] == "Sin RIPS"]
    assert [(f["sede"], f["mes"]) for f in falta] == [("HOSPITAL B", "2026-02")]
    assert c.post(f"/api/tablero/plan/{falta[0]['id']}", json={"estado": "EN PROCESO"}).status_code == 200
    assert c.post("/api/tablero/plan/noexiste", json={"estado": "EN PROCESO"}).status_code == 404
    assert c.get("/api/tablero/plan").json()["resumen"]["en_proceso"] == 1
    ruta = tb.exportar_excel(*tb.cargar(tb.carpeta_acumulado()), tmp_path / "t.xlsx")
    assert "Plan de acción" in pd.ExcelFile(ruta).sheet_names


def _remision_txt(carpeta, factura):
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "AF000001.txt").write_text(
        f"817940020603,ESE,NI,900034131,{factura},10/01/2026,01/01/2026,31/01/2026,EPS037,NUEVA EPS,C1,,,0,0,0,5000\r\n")
    (carpeta / "CT000001.txt").write_text("817940020603,31/01/2026,AF000001,1\r\n")


@pytest.fixture
def enviados(monkeypatch):
    lista = []
    monkeypatch.setattr(aplicativo, "enviar_meses", lambda a, cfg=None, aviso=None: lista.extend(a) or [])
    monkeypatch.setattr(aplicativo, "resumen_cruce", lambda cfg=None: [])
    return lista


def test_drive_solo_json_no_lee_ni_envia_los_txt(tmp_path, enviados):
    raiz = tmp_path / "Rips TXT y JSON 2026"
    _factura_drive(raiz, "Hospital San Antonio", "01. Enero", "JSON ENERO", "FHSA1", "2026-01-12 08:00")
    _remision_txt(raiz / "Hospital San Antonio" / "01. Enero" / "TXT ENERO" / "NUEVA EPS", "TXT99")
    # un TXT cargado antes por otro camino tampoco va al aplicativo en modo sólo JSON
    json_rips.integrar(pd.DataFrame([{c: "" for c in aplicativo.COLUMNAS} | {
        "MES": "2026-01", "SEDE": "HOSPITAL SAN ANTONIO", "FACTURA_FEV": "TXT01", "CODIGO_CUPS_CUMS": "890301",
        "TIPO_SERVICIO": "Consultas", "CANTIDAD": 1, "VALOR_SERVICIO": 5, "VALIDACION": "OK"}]))
    drive.guardar_configuracion({"modo": "local", "carpeta_local": str(raiz)})
    assert drive.configuracion()["solo_json"] is True  # por defecto
    r = drive.sincronizar()
    assert r["procesadas"][0]["remisiones_txt"] == 0
    enero = pd.read_excel(enviados[0], sheet_name=aplicativo.HOJA_DATOS, header=3)
    assert set(enero["FACTURA_FEV"]) == {"FHSA1"}  # ni el TXT de Drive ni el TXT anterior
    # quitar del tablero lo que vino de TXT (con vista previa y ficha)
    v = depuracion.vista_txt()
    assert v["total_filas"] == 1 and v["filas"][0]["FACTURA_FEV"] == "TXT01"
    assert depuracion.aplicar_txt(v["ficha"]) == {"eliminadas": 1}
    assert set(tb.cargar(tb.carpeta_acumulado())[0]["FACTURA_FEV"]) == {"FHSA1"}


def test_notas_tecnicas_con_los_json_de_drive(tmp_path, monkeypatch):
    raiz = tmp_path / "Rips TXT y JSON 2026"
    _factura_drive(raiz, "Hospital San Antonio", "01. Enero", "JSON ENERO", "FHSA1", "2026-01-12 08:00")
    _factura_drive(raiz, "Centro de Salud Jesus Coronel", "02. Febrero", "JSON FEBRERO", "FCSJ2", "2026-02-03 08:00")
    drive.guardar_configuracion({"modo": "local", "carpeta_local": str(raiz)})
    nota = tmp_path / "NUEVA_EPS.xlsx"
    pd.DataFrame({"CodigoCUP": ["890201", "890301"], "DescripcionCUP": ["CONSULTA 1A VEZ", "CONTROL"]}).to_excel(nota, index=False)
    nota_tecnica.guardar_nota(nota, "NUEVA EPS")
    with pytest.raises(drive.ErrorDrive):
        drive.generar_notas(["sanitas"])
    r = drive.generar_notas(desde="2026-01", hasta="2026-02")
    (n,) = r["notas_tecnicas"]
    assert n["eps"] == "NUEVA EPS" and n["pacientes_unicos_periodo"] == 1 and n["codigos_con_pacientes"] == 1
    assert sorted(p.split("/")[-1][:12] for p in r["archivos"]) == ["HOJA_PACIENT", "NOTA_TECNICA"]
    assert json.dumps(r)  # serializable para la ventana

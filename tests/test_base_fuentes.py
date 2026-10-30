"""Base consolidada (actualización mes a mes), limpieza, sincronización Drive y frecuencias NT."""
import os
import time

import pandas as pd

from rips_auditoria import base, fuentes
from rips_auditoria.analisis import analizar
from rips_auditoria.calculos import cruces, frecuencias
from rips_auditoria.clasificacion import clasificar
from rips_auditoria.ingesta.lote import procesar
from rips_auditoria.servicios import construir
from tests.conftest import TXT


def _hospital(raiz, nombre="Hospital Prueba", meses=("08. Agosto",), af_valor="180000.00"):
    for mes in meses:
        d = raiz / nombre / mes / "NUEVA EPS EVENTO"
        d.mkdir(parents=True, exist_ok=True)
        mm = mes[:2]
        for t, v in TXT.items():
            if t == "AF":
                v = v.replace("180000.00", af_valor)
            v = v.replace("FT000", f"FT{mm}0").replace("/08/2026", f"/{mm}/2026")   # facturas propias del mes
            (d / f"{t}000123.txt").write_text(v, encoding="utf-8")
    return raiz / nombre


def test_recarga_reemplaza_el_mes_y_no_suma(tmp_path):
    h = _hospital(tmp_path, meses=("07. Julio", "08. Agosto"))
    r1 = base.actualizar(procesar([h]))
    assert len(r1["nuevas"]) == 2
    total1 = base.listar()["facturas"].sum()
    # el hospital corrige agosto en Drive y se vuelve a cargar solo agosto
    r2 = base.actualizar(procesar([h / "08. Agosto"], manual_global={"hospital": "Hospital Prueba"}))
    assert len(r2["reemplazadas"]) == 1 and not r2["nuevas"]
    assert base.listar()["facturas"].sum() == total1
    assert set(base.listar()["periodo"]) == {"2026-07", "2026-08"}


def test_limpiar_por_mes_y_vaciar(tmp_path):
    base.actualizar(procesar([_hospital(tmp_path, meses=("07. Julio", "08. Agosto"))]))
    assert base.eliminar() == 0                                    # sin filtros no borra
    assert base.eliminar(periodo="2026-07") == 1
    assert list(base.listar()["periodo"]) == ["2026-08"]
    assert base.vaciar() == 1 and base.listar().empty


def test_modo_carpeta_conserva_otras_eps(tmp_path):
    h = _hospital(tmp_path)
    otra = h / "08. Agosto" / "NUEVA EPS CAPITA"
    otra.mkdir()
    for t, v in TXT.items():
        (otra / f"{t}000124.txt").write_text(v.replace("FT000", "FC000").replace("000123", "000124"), encoding="utf-8")
    base.actualizar(procesar([h]))
    assert base.listar().iloc[0]["remisiones"] == 2
    sub = procesar([("Hospital Prueba/08. Agosto/NUEVA EPS EVENTO/" + f"{t}000123.txt",
                     v.replace("FT000", "FT080").encode()) for t, v in TXT.items()])
    base.actualizar(sub, modo="carpeta")
    assert base.listar().iloc[0]["remisiones"] == 2               # la carpeta CAPITA sigue
    base.actualizar(sub, modo="mes")
    assert base.listar().iloc[0]["remisiones"] == 1               # el mes queda solo con lo cargado


def test_sincronizacion_drive_solo_procesa_cambios(tmp_path):
    drive = tmp_path / "Drive"
    h = _hospital(drive, meses=("07. Julio", "08. Agosto"))
    fuentes.agregar(str(h))
    r = fuentes.sincronizar()
    assert len(r["procesadas"]) == 2 and r["sin_cambios"] == 0
    assert set(base.listar()["hospital"]) == {"Hospital Prueba"}
    r = fuentes.sincronizar()
    assert not r["procesadas"] and r["sin_cambios"] == 2
    time.sleep(0.01)
    af = h / "08. Agosto" / "NUEVA EPS EVENTO" / "AF000123.txt"
    af.write_text(af.read_text(encoding="utf-8").replace("180000.00", "190000.00"), encoding="utf-8")
    os.utime(af, None)
    r = fuentes.sincronizar()
    assert len(r["procesadas"]) == 1 and r["sin_cambios"] == 1
    fila = base.listar().set_index("periodo").loc["2026-08"]
    assert fila["valor_af"] == 190000 + 1450000


def test_analisis_desde_la_base(tmp_path):
    base.actualizar(procesar([_hospital(tmp_path, meses=("07. Julio", "08. Agosto"))]))
    r = analizar([base.lote()])
    assert r.alcance["meses"] == ["2026-07", "2026-08"]
    assert "escenarios" not in r.tablas and "pgp_mensual" not in r.tablas
    assert r.indicadores["evento_total"] == 2 * (30000 * 2 + 40000 + 80000 + 50000 + 1400000)
    r2 = analizar([base.lote(periodos=["2026-08"])])
    assert r2.alcance["meses"] == ["2026-08"]


def test_frecuencias_nt_vs_rips_por_municipio_mes(carpeta_rips):
    s = construir(procesar([carpeta_rips]))
    s["modalidad_declarada"] = None
    nt = pd.DataFrame([
        {"codigo": "902207", "grupo": "LABORATORIO", "municipio": "TAME", "mes": None, "frecuencia_prevista": 2.0,
         "costo_unitario": 9000.0, "descripcion": "HEMOGRAMA"},
        {"codigo": "906249", "grupo": "LABORATORIO", "municipio": "TAME", "mes": None, "frecuencia_prevista": 1.0,
         "costo_unitario": 20000.0, "descripcion": "VIH"},
        {"codigo": "906249", "grupo": "LABORATORIO", "municipio": "FORTUL", "mes": None, "frecuencia_prevista": 5.0,
         "costo_unitario": 20000.0, "descripcion": "VIH"}])
    s = clasificar(s, None, nt)
    x = cruces.rips_vs_nota_tecnica(s, nt, 10)
    assert "FORTUL" not in set(x["municipio"])                       # municipio sin RIPS cargados no cuenta
    fr = frecuencias.resumenes(x, 10)
    mm = fr["por_municipio_mes"].iloc[0]
    assert (mm.municipio, mm.mes, mm.frecuencia_prevista, mm.frecuencia_rips) == ("TAME", "2026-08", 3.0, 3.0)
    assert mm.ejecucion_pct == 100 and mm.estado == "DENTRO"
    cod = fr["por_codigo"].set_index("codigo")
    assert cod.loc["906249", "estado"] == "SOBREEJECUCION" and cod.loc["906249", "valor_exceso"] == 20000
    assert cod.loc["902207", "estado"] == "SUBEJECUCION"

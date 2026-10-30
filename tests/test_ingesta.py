import json

import pytest

from rips_auditoria.ingesta import contexto
from rips_auditoria.ingesta.lote import corregir, procesar
from tests.conftest import ARCHIVO_REAL_JSON, ARCHIVO_REAL_XLSX, ARCHIVO_REAL_ZIP, PREST, TXT, zip_bytes


def test_carpeta_txt_detecta_hospital_municipio_mes(carpeta_rips):
    l = procesar([carpeta_rips])
    r = l.remisiones.iloc[0]
    assert r.hospital == "Centro de Salud Prueba" and r.fuente_hospital == "carpeta"
    assert r.municipio == "TAME" and r.fuente_municipio == "contenido"      # por código de habilitación 81794
    assert r.periodo == "2026-08" and r.fuente_periodo == "carpeta"
    assert r.modalidad_declarada == "EVENTO"
    assert len(l.tablas["AF"]) == 2 and len(l.tablas["AP"]) == 3 and len(l.tablas["AT"]) == 1
    assert l.incidencias.query("severidad == 'ERROR'").empty


def test_zip_y_rutas_web():
    datos = zip_bytes({f"Hospital Prueba/08. Agosto/NUEVA EPS/{t}000123.txt": v for t, v in TXT.items()})
    l = procesar([("carga.zip", datos)])
    assert l.remisiones.iloc[0].hospital == "Hospital Prueba"
    assert len(l.tablas["AC"]) == 2


def test_txt_y_json_mismo_envio_se_deduplican(carpeta_rips, json_2275_equivalente):
    """El mismo contenido en TXT y JSON 2275 no se suma dos veces."""
    solo_txt = procesar([carpeta_rips])
    ambos = procesar([carpeta_rips, ("Centro de Salud Prueba/08. Agosto/NUEVA EPS EVENTO SUBSIDIADO/rips.json",
                                     json_2275_equivalente)])
    for t in ("AF", "AC", "AP", "AT"):
        assert len(ambos.tablas[t]) == len(solo_txt.tablas[t]), t
    assert not ambos.duplicados.empty
    assert set(ambos.duplicados["formato"]) == {"JSON_2275"}       # se conserva el TXT (prioridad)
    assert (ambos.tablas["AP"]["formato"] == "TXT").all()


def test_duplicado_dentro_de_un_archivo_se_conserva_y_marca(tmp_path):
    d = tmp_path / "Hospital X" / "08. Agosto" / "NUEVA EPS"
    d.mkdir(parents=True)
    for t, v in TXT.items():
        (d / f"{t}000123.txt").write_text(v + (TXT["AP"].splitlines()[0] + "\n" if t == "AP" else ""), encoding="utf-8")
    l = procesar([tmp_path / "Hospital X"])
    assert len(l.tablas["AP"]) == 4                         # no se elimina: puede ser legítimo
    assert (l.incidencias.regla == "REGISTRO_DUPLICADO").any()
    assert (l.incidencias.regla == "CT_TOTAL_REGISTROS").any()


def test_remision_copiada_en_dos_carpetas(tmp_path):
    for mes in ("07. Julio", "08. Agosto"):
        d = tmp_path / "Hospital X" / mes / "NUEVA EPS"
        d.mkdir(parents=True)
        for t, v in TXT.items():
            (d / f"{t}000123.txt").write_text(v, encoding="utf-8")
    l = procesar([tmp_path / "Hospital X"])
    assert len(l.tablas["AF"]) == 2
    assert (l.incidencias.regla == "REMISION_DUPLICADA").any()


def test_validaciones_por_archivo_no_detienen_proceso(tmp_path):
    d = tmp_path / "Hospital X" / "08. Agosto" / "NUEVA EPS"
    d.mkdir(parents=True)
    malos = dict(TXT)
    malos["AC"] = f"FT0009,{PREST},CC,1000001,32/13/2026,,890201,10,13,J00X,,,,1,30000.00,0,30000.00\n" \
                  f"FT0001,{PREST},CC,,03/08/2026,,890201,10\n"
    malos["AF"] = TXT["AF"] + TXT["AF"].splitlines()[0] + "\n"
    for t, v in malos.items():
        (d / f"{t}000123.txt").write_text(v, encoding="utf-8")
    (d / "XX000123.txt").write_text("basura", encoding="utf-8")
    (d / "soporte.pdf").write_bytes(b"%PDF")
    (tmp_path / "Hospital X" / "danado.zip").write_bytes(b"no es zip")
    l = procesar([tmp_path / "Hospital X"])
    reglas = set(l.incidencias.regla)
    assert {"FORMATO_FECHA", "CANTIDAD_CAMPOS", "FACTURA_DUPLICADA", "FACTURA_NO_EXISTE_EN_AF",
            "ARCHIVO_NO_RECONOCIDO", "ZIP_CORRUPTO"} <= reglas
    assert len(l.tablas["AP"]) == 3                           # el resto se procesó


def test_eps_fuera_de_alcance_y_nit():
    af = TXT["AF"].replace("EPS037", "EPS005").replace("900034131", "800000000")
    datos = zip_bytes({f"H/08. Agosto/SANITAS/{t}000123.txt": (af if t == "AF" else v) for t, v in TXT.items()})
    l = procesar([("c.zip", datos)])
    reglas = set(l.incidencias.regla)
    assert "EPS_FUERA_DE_ALCANCE" in reglas and "NIT_PRESTADOR_DIFERENTE" in reglas


def test_correccion_manual(carpeta_rips):
    l = procesar([carpeta_rips])
    env = l.remisiones.iloc[0].envio_id
    n = corregir(l, env, periodo="2026-09", municipio="ARAUCA")
    assert n > 0
    assert set(l.tablas["AP"]["periodo"]) == {"2026-09"}
    assert set(l.tablas["AP"]["fuente_municipio"]) == {"manual"}


def test_manual_global_tiene_prioridad(carpeta_rips):
    l = procesar([carpeta_rips], manual_global={"periodo": "2026-07"})
    assert l.remisiones.iloc[0].periodo == "2026-07"
    assert l.remisiones.iloc[0].fuente_periodo == "manual"


def test_contexto_desde_ruta_drive():
    c = contexto.desde_ruta("Hospital San Jose de Cravo Norte-20261014T130856Z-1-001/Hospital San Jose de Cravo "
                            "Norte/02. Febrero/NUEVA SUBS/AF009236.txt", 2026)
    assert c.hospital == "Hospital San José de Cravo Norte"
    assert c.periodo == "2026-02"
    assert c.municipio == "CRAVO NORTE"
    assert contexto.municipio_por_codigo("812200020604") == "CRAVO NORTE"


def test_formato_no_define_sede(carpeta_rips, json_2275_equivalente):
    """TXT y JSON del mismo envío quedan en el mismo hospital/municipio: el formato no es sede."""
    l = procesar([carpeta_rips, ("Centro de Salud Prueba/08. Agosto/NUEVA EPS EVENTO SUBSIDIADO/rips.json",
                                 json_2275_equivalente)])
    assert set(l.remisiones["hospital"]) == {"Centro de Salud Prueba"}
    assert set(l.remisiones["municipio"]) == {"TAME"}


@pytest.mark.skipif(not (ARCHIVO_REAL_JSON and ARCHIVO_REAL_XLSX), reason="Defina RIPS_CONSOLIDADO_JSON/XLSX")
def test_consolidado_real_reproduce_indice():
    from pathlib import Path
    idx = json.loads(Path(ARCHIVO_REAL_JSON).read_text(encoding="utf-8"))["indice"]
    l = procesar([ARCHIVO_REAL_JSON, ARCHIVO_REAL_XLSX])
    r = l.resumen().set_index("periodo")
    for i in idx:
        for k in ("facturas", "usuarios_unicos", "valor_neto_facturado_af", "reg_AC", "reg_AP", "reg_AM", "reg_AT"):
            assert float(r.loc[i["periodo"], k]) == pytest.approx(float(i[k])), (i["periodo"], k)
    # JSON y XLSX traen los mismos datos: todo el XLSX queda como repetido
    assert set(l.duplicados["formato"]) == {"CONSOLIDADO_XLSX"}


@pytest.mark.skipif(not ARCHIVO_REAL_ZIP, reason="Defina RIPS_MUESTRA_ZIP")
def test_zip_real_procesa_sin_errores_fatales():
    l = procesar([ARCHIVO_REAL_ZIP])
    assert len(l.tablas["AF"]) > 0
    assert set(l.remisiones["municipio"].dropna()) == {"TAME"}

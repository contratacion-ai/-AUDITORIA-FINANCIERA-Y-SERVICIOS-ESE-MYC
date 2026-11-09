"""Detección automática de la versión de RIPS."""

import json

from rips_conciliacion.deteccion import detectar
from rips_conciliacion.lectores import leer_rips
from rips_conciliacion.modelos import VERSION_2275, VERSION_3374, VERSION_NO_DETERMINADA
from utilidades import AC_TXT, AF_TXT, AM_TXT, AU_LINEA, US_TXT, crear_zip, json_2275


def test_3374_por_nombre_de_archivo():
    det = detectar("AF000001.txt", AF_TXT.encode())
    assert det.version == VERSION_3374
    assert det.tipo == "AF"


def test_3374_por_contenido_cuando_el_nombre_no_es_estandar():
    assert detectar("facturas_enero.txt", AF_TXT.encode()).tipo == "AF"
    assert detectar("consultas.txt", AC_TXT.encode()).tipo == "AC"
    assert detectar("usuarios.txt", US_TXT.encode()).tipo == "US"
    assert detectar("medicamentos.txt", AM_TXT.encode()).tipo == "AM"
    assert detectar("urgencias.txt", AU_LINEA.encode()).tipo == "AU"
    assert detectar("x.txt", AC_TXT.encode()).version == VERSION_3374


def test_2275_json_incluso_con_extension_txt():
    assert detectar("FE2001.json", json_2275()).version == VERSION_2275
    assert detectar("FE2001.txt", json_2275()).version == VERSION_2275


def test_2275_lista_de_facturas():
    contenido = json.dumps([json.loads(json_2275("A1")), json.loads(json_2275("A2"))]).encode()
    assert detectar("lote.json", contenido).version == VERSION_2275


def test_no_determinada_texto_desconocido():
    det = detectar("notas.txt", b"hola,mundo,esto,no,es,rips,1,2,3\n")
    assert det.version == VERSION_NO_DETERMINADA
    assert "no corresponden a ninguna estructura" in det.motivo


def test_no_determinada_json_sin_llaves_2275():
    det = detectar("otro.json", b'{"factura": "X", "items": []}')
    assert det.version == VERSION_NO_DETERMINADA
    assert "numFactura" in det.motivo and "factura" in det.motivo


def test_no_determinada_json_invalido_indica_linea_y_columna():
    det = detectar("malo.json", b'{\n  "numFactura": "X",\n  "usuarios": [\n}')
    assert det.version == VERSION_NO_DETERMINADA
    assert "línea 4" in det.motivo


def test_no_asume_version_si_nombre_af_pero_campos_no_coinciden():
    det = detectar("AF000001.txt", b"a,b,c\n")
    assert det.version == VERSION_NO_DETERMINADA
    assert "17 campos" in det.motivo


def test_paquete_con_versiones_mixtas_se_reporta_y_archivo_no_determinado_es_error():
    paquete = crear_zip({"AF000001.txt": AF_TXT.encode(), "FE2001.json": json_2275(), "raro.txt": b"x;y\n"})
    res = leer_rips(("sede.zip", paquete))
    assert VERSION_3374 in res.version and VERSION_2275 in res.version
    errores = [i for i in res.incidencias if i.severidad == "ERROR"]
    assert any("NO DETERMINADA" in i.mensaje and i.archivo.endswith("raro.txt") for i in errores)

"""Fábrica de RIPS sintéticos para las pruebas (sin datos reales de pacientes)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

PRESTADOR = "817940020603"


def envio_valido(remision: str = "000123", factura: str = "FV1001", mes: str = "05", anio: str = "2026") -> dict[str, str]:
    """Remisión completa y válida: todas las referencias cruzadas cuadran.

    Valor neto AF = AC neto 30000 + AP 50000 + AM 3000 + AT 20000 = 103000.
    """
    f = lambda d: f"{d}/{mes}/{anio}"  # noqa: E731
    archivos = {
        "AF": [f"{PRESTADOR},ESE MORENO Y CLAVIJO,NI,900034131,{factura},{f('30')},{f('01')},{f('28')},"
               f"EPS005,SANITAS EPS,CT-2026-01,POS,,0,0,0,103000.00"],
        "US": ["CC,1001,EPS005,2,PEREZ,GOMEZ,ANA,MARIA,35,1,F,81,794,U",
               "RC,2002,EPS005,2,PEREZ,,LUIS,,1,3,M,81,794,R"],
        "AC": [f"{factura},{PRESTADOR},CC,1001,{f('05')},,890201,10,13,J069,,,,1,35000.00,5000.00,30000.00"],
        "AP": [f"{factura},{PRESTADOR},CC,1001,{f('06')},,903895,1,1,,,,,,50000.00"],
        "AM": [f"{factura},{PRESTADOR},CC,1001,,19942561-6,1,ACETAMINOFEN 500MG,TABLETA,500MG,MG,2,1500.00,3000.00"],
        "AT": [f"{factura},{PRESTADOR},CC,1001,,1,INS001,JERINGA 5ML,1,20000.00,20000.00"],
        "AU": [f"{factura},{PRESTADOR},CC,1001,{f('07')},10:00,,13,J069,,,,1,1,,{f('07')},14:30"],
        "AH": [f"{factura},{PRESTADOR},CC,1001,1,{f('08')},09:00,,13,O800,O800,,,,,1,,{f('10')},11:00"],
        "AN": [f"{factura},{PRESTADOR},CC,1001,{f('09')},03:15,39,1,M,3200,P070,,,"],
    }
    ct = [f"{PRESTADOR},{f('28')},{t}{remision},{len(lineas)}" for t, lineas in archivos.items()]
    archivos["CT"] = ct
    return {f"{t}{remision}.txt": "\r\n".join(lineas) + "\r\n" for t, lineas in archivos.items()}


def envio_con_errores(remision: str = "000124") -> dict[str, bytes]:
    """Remisión con errores de estructura y de contenido conocidos."""
    p = PRESTADOR
    af = f"{p},ESE MORENO Y CLAVIJO,NI,900034131,FV2001,30/05/2026,01/05/2026,31/05/2026,EPS005,SANITAS,C1,POS,,0,0,0,999999.00"
    us = ["CC,1001,EPS005,2,CASTAÃ‘EDA,RUIZ,ANA,,35,1,F,81,794,U",  # doble codificación
          "CC,1002,EPS005,9,LOPEZ,,JUAN,,40,1,X,81,794,U"]  # tipo usuario y sexo inválidos
    ac = [
        f"FV2001,{p},CC,1001,05/05/2026,,890201,10,13,J069,,,,1,35000.00,0,35000.00",  # válido
        f"FV2001,{p},CC,1001,05/05/2026,,890201,10,13,J069,,,,1,35000.00,0",  # 16 campos
        f"FV2001,{p},CC,1001,31/02/2026,,890201,10,13,J069,,,,1,35000.00,0,35000.00",  # fecha imposible
        f"FV2001,{p},CC,1001,05/05/2026,,890201,99,13,J069,,,,1,35000.00,0,35000.00",  # finalidad inválida
        f"FV2001,{p},CC,1001,05/05/2026,,890201,44,38,J069,,,,1,35000.00,0,35000.00",  # catálogo 2275
        f"FV9999,{p},CC,1001,05/05/2026,,890201,10,13,J069,,,,1,35000.00,0,35000.00",  # factura inexistente
        f"FV2001,{p},CC,9999,05/05/2026,,890201,10,13,J069,,,,1,35000.00,0,35000.00",  # usuario inexistente
        f"FV2001,{p},CC,1001,05/05/2026,,890201,10,13,,,,,1,35000.00,0,35000.00",  # dx obligatorio vacío
        f"FV2001,{p},CC,1001,05/05/2026,,890201,10,13,J069,,,,1,35000.5.2,0,35000.00",  # número inválido
        f"\"FV2001\",{p},CC,1001,05/05/2026,,890201,10,13,J069,,,,1,35000.00,0,35000.00",  # comillas
        f"FV2001,{p},CC,1001,05/05/2026,,890201,10,13,J069,,,,1,35000.00,0,35000.00,",  # coma final
        "",  # línea en blanco
        f"FV2001,{p},CC,1001,05/05/2026,,890201,10,13,J069,,,,1,35000.00,5000,35000.00",  # neto incoherente
    ]
    am = [f"FV2001,{p},CC,1001,,123,1,{'X' * 45},TAB,1MG,MG,2,10.00,20.00"]  # nombre > 30
    ct = [f"{p},31/05/2026,AF{remision},1", f"{p},31/05/2026,US{remision},2",
          f"{p},31/05/2026,AC{remision},99",  # total no coincide
          f"{p},31/05/2026,AM{remision},1",
          f"{p},31/05/2026,AP{remision},5"]  # archivo no existe
    at_cp1252 = f"FV2001,{p},CC,1001,,3,129B02,INTERNACIÓN ADULTOS,1,1000.00,1000.00\r\n".encode("cp1252")
    out = {f"CT{remision}.txt": "\r\n".join(ct).encode(), f"AF{remision}.txt": af.encode(),
           f"US{remision}.txt": "\r\n".join(us).encode("utf-8"), f"AC{remision}.txt": "\r\n".join(ac).encode(),
           f"AM{remision}.txt": "\r\n".join(am).encode(), f"AT{remision}.txt": at_cp1252}
    return out


def escribir(carpeta: Path, archivos: dict) -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    for nombre, contenido in archivos.items():
        datos = contenido.encode("utf-8") if isinstance(contenido, str) else contenido
        (carpeta / nombre).write_bytes(datos)
    return carpeta


def zip_bytes(archivos: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for ruta, contenido in archivos.items():
            zf.writestr(ruta, contenido)
    return buf.getvalue()


def zip_varios_hospitales(ruta: Path) -> Path:
    """ZIP con dos hospitales, varios meses, un ZIP anidado, un ZIP corrupto
    y archivos que no son RIPS TXT (JSON de la Res. 2275)."""
    contenido: dict[str, bytes | str] = {}
    for n, t in envio_valido("000123", "FV1001", "05").items():
        contenido[f"Hospital San Antonio/05. Mayo/123__SANITAS/Rips/{n}"] = t
    for n, t in envio_valido("000130", "FV1101", "06").items():
        contenido[f"Hospital San Antonio/06. Junio/130__NUEVA_EPS/Rips/{n}"] = t
    for n, t in envio_valido("000200", "FB5001", "05").items():
        contenido[f"Hospital San Jose/05. Mayo/200__ADRES/Rips/{n}"] = t
    anidado = zip_bytes({n: t for n, t in envio_valido("000210", "FB5101", "06").items()})
    contenido["Hospital San Jose/06. Junio/210__SOAT/Rips/210.zip"] = anidado
    contenido["Hospital San Jose/06. Junio/211__ROTO/Rips/211.zip"] = b"PK\x03\x04esto no es un zip"
    contenido["Hospital San Antonio/05. Mayo/123__SANITAS/FV1001/FV1001.json"] = '{"numFactura": "FV1001"}'
    ruta.write_bytes(zip_bytes(contenido))
    return ruta

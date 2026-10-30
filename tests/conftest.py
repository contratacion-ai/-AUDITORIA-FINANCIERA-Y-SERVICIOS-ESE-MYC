"""Fixtures: RIPS sintéticos (datos ficticios, sin pacientes reales) y entorno de datos aislado."""
from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))


@pytest.fixture(autouse=True)
def datos_aislados(tmp_path, monkeypatch):
    """Cada prueba usa su propia carpeta datos/ (maestros, lotes, sal)."""
    monkeypatch.setenv("RIPS_SAL", "sal-de-pruebas")
    from rips_auditoria import config, maestros
    monkeypatch.setattr(config, "DIR_DATOS", tmp_path / "datos")
    monkeypatch.setattr(maestros, "DIR_MAESTROS", tmp_path / "datos" / "maestros")
    yield


# Remisión ficticia de Nueva EPS en Tame (cod. habilitación 81794...), agosto 2026.
PREST = "817940099901"
TXT = {
    "CT": f"{PREST},20/09/2026,AF000123,2\n{PREST},20/09/2026,US000123,2\n{PREST},20/09/2026,AC000123,2\n"
          f"{PREST},20/09/2026,AP000123,3\n{PREST},20/09/2026,AT000123,1\n",
    "AF": f"{PREST},ESE DEPARTAMENTAL DE PRIMER NIVEL MORENO Y CLAVIJO,NI,900034131,FT0001,25/08/2026,01/08/2026,"
          f"31/08/2026,EPS037,NUEVA EPS,CTO-EVENTO-1,,,0.00,0,0,180000.00\n"
          f"{PREST},ESE DEPARTAMENTAL DE PRIMER NIVEL MORENO Y CLAVIJO,NI,900034131,FT0002,25/08/2026,05/08/2026,"
          f"05/08/2026,EPS037,NUEVA EPS,CTO-EVENTO-1,,,0.00,0,0,1450000.00\n",
    "US": "CC,1000001,EPS037,2,PEREZ,GOMEZ,ANA,MARIA,40,1,F,81,794,U\n"
          "TI,2000002,EPS037,2,RUIZ,,LUIS,,12,1,M,81,794,R\n",
    "AC": f"FT0001,{PREST},CC,1000001,03/08/2026,,890201,10,13,J00X,,,,1,30000.00,0,30000.00\n"
          f"FT0001,{PREST},CC,1000001,10/08/2026,,890301,10,13,I10X,,,,3,30000.00,0,30000.00\n",
    "AP": f"FT0001,{PREST},CC,1000001,03/08/2026,,902207,1,1,,,,,,40000.00\n"
          f"FT0001,{PREST},CC,1000001,03/08/2026,,906249,1,1,,,,,,80000.00\n"
          f"FT0002,{PREST},TI,2000002,05/08/2026,,906249,1,1,,,,,,50000.00\n",
    "AT": f"FT0002,{PREST},TI,2000002,,2,S31307,TRASLADO INTERMUNICIPAL >25KM,1,1400000.00,1400000.00\n",
}


def zip_bytes(archivos: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for ruta, txt in archivos.items():
            z.writestr(ruta, txt)
    return buf.getvalue()


@pytest.fixture
def carpeta_rips(tmp_path) -> Path:
    base = tmp_path / "Centro de Salud Prueba" / "08. Agosto" / "NUEVA EPS EVENTO SUBSIDIADO"
    base.mkdir(parents=True)
    for t, txt in TXT.items():
        (base / f"{t}000123.txt").write_text(txt, encoding="utf-8")
    return tmp_path / "Centro de Salud Prueba"


@pytest.fixture
def json_2275_equivalente() -> bytes:
    """Mismas facturas FT0001/FT0002 en formato JSON Res. 2275/2023."""
    def ap(cod, v, fecha, tid, nid, cons):
        return {"codPrestador": PREST, "fechaInicioAtencion": f"{fecha} 08:00", "numAutorizacion": None,
                "codProcedimiento": cod, "grupoServicios": "02", "finalidadTecnologiaSalud": "1",
                "tipoDocumentoIdentificacion": tid, "numDocumentoIdentificacion": nid, "vrServicio": v,
                "valorPagoModerador": 0, "consecutivo": cons}
    f1 = {"numDocumentoIdObligado": "900034131", "numFactura": "FT0001", "usuarios": [{
        "tipoDocumentoIdentificacion": "CC", "numDocumentoIdentificacion": "1000001", "tipoUsuario": "04",
        "fechaNacimiento": "1986-01-01", "codSexo": "F", "codMunicipioResidencia": "81794",
        "codZonaTerritorialResidencia": "01", "consecutivo": 1, "servicios": {
            "consultas": [
                {"codPrestador": PREST, "fechaInicioAtencion": "2026-08-03 08:00", "codConsulta": "890201",
                 "finalidadTecnologiaSalud": "10", "causaMotivoAtencion": "13", "codDiagnosticoPrincipal": "J00X",
                 "tipoDiagnosticoPrincipal": "1", "tipoDocumentoIdentificacion": "CC",
                 "numDocumentoIdentificacion": "1000001", "vrServicio": 30000, "valorPagoModerador": 0, "consecutivo": 1},
                {"codPrestador": PREST, "fechaInicioAtencion": "2026-08-10 08:00", "codConsulta": "890301",
                 "finalidadTecnologiaSalud": "10", "causaMotivoAtencion": "13", "codDiagnosticoPrincipal": "I10X",
                 "tipoDiagnosticoPrincipal": "3", "tipoDocumentoIdentificacion": "CC",
                 "numDocumentoIdentificacion": "1000001", "vrServicio": 30000, "valorPagoModerador": 0, "consecutivo": 2}],
            "procedimientos": [ap("902207", 40000, "2026-08-03", "CC", "1000001", 1),
                               ap("906249", 80000, "2026-08-03", "CC", "1000001", 2)]}}]}
    f2 = {"numDocumentoIdObligado": "900034131", "numFactura": "FT0002", "usuarios": [{
        "tipoDocumentoIdentificacion": "TI", "numDocumentoIdentificacion": "2000002", "tipoUsuario": "04",
        "fechaNacimiento": "2014-01-01", "codSexo": "M", "codMunicipioResidencia": "81794",
        "codZonaTerritorialResidencia": "02", "consecutivo": 1, "servicios": {
            "procedimientos": [ap("906249", 50000, "2026-08-05", "TI", "2000002", 1)],
            "otrosServicios": [{"codPrestador": PREST, "tipoOS": "2", "codTecnologiaSalud": "S31307",
                                "nomTecnologiaSalud": "TRASLADO INTERMUNICIPAL >25KM", "cantidadOS": 1,
                                "tipoDocumentoIdentificacion": "TI", "numDocumentoIdentificacion": "2000002",
                                "vrUnitOS": 1400000, "vrServicio": 1400000, "valorPagoModerador": 0,
                                "fechaSuministroTecnologia": "2026-08-05 09:00", "consecutivo": 1}]}}]}
    return json.dumps([f1, f2]).encode()


ARCHIVO_REAL_ZIP = os.environ.get("RIPS_MUESTRA_ZIP")
ARCHIVO_REAL_JSON = os.environ.get("RIPS_CONSOLIDADO_JSON")
ARCHIVO_REAL_XLSX = os.environ.get("RIPS_CONSOLIDADO_XLSX")

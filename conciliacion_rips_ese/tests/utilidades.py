"""Generadores de archivos de prueba (TXT 3374, JSON 2275, ZIP, RAR, facturación)."""

from __future__ import annotations

import io
import json
import struct
import zipfile
import zlib

# --- RIPS 3374 -------------------------------------------------------------

PRESTADOR = "810010000101"

AF_TXT = (
    f"{PRESTADOR},ESE MORENO Y CLAVIJO,NI,900000001,FE1001,15/01/2024,01/01/2024,31/01/2024,EPS001,EPS PRUEBA,C-01,S,,0,0,0,61000.50\n"
    f"{PRESTADOR},ESE MORENO Y CLAVIJO,NI,900000001,FE1002,20/01/2024,01/01/2024,31/01/2024,EPS001,EPS PRUEBA,C-01,S,,0,0,0,30000\n"
)
US_TXT = "CC,1001,EPS001,1,PEREZ,GOMEZ,JUAN,,40,1,M,81,001,U\n"
AC_TXT = (
    f"FE1001,{PRESTADOR},CC,1001,10/01/2024,,890201,10,13,I10X,,,,1,30000,0,30000\n"
    f"FE1002,{PRESTADOR},CC,1001,12/01/2024,,890201,10,13,I10X,,,,1,30000,0,30000\n"
)
AP_TXT = f"FE1001,{PRESTADOR},CC,1001,10/01/2024,,903841,1,2,1,I10X,,,,15000.50\n"
AM_TXT = f"FE1001,{PRESTADOR},CC,1001,,19912345-1,1,ACETAMINOFEN,TABLETA,500MG,MG,10,1000,10000\n"
AU_LINEA = f"FE1001,{PRESTADOR},CC,1001,10/01/2024,08:00,,13,I10X,,,,1,1,,10/01/2024,12:00\n"
AT_TXT = f"FE1001,{PRESTADOR},CC,1001,,1,AT001,JERINGA,2,3000,6000\n"
CT_TXT = f"{PRESTADOR},31/01/2024,AF000001,2\n" f"{PRESTADOR},31/01/2024,AC000001,2\n"


def archivos_3374() -> dict[str, bytes]:
    return {
        "CT000001.txt": CT_TXT.encode(),
        "AF000001.txt": AF_TXT.encode(),
        "US000001.txt": US_TXT.encode(),
        "AC000001.txt": AC_TXT.encode(),
        "AP000001.txt": AP_TXT.encode(),
        "AM000001.txt": AM_TXT.encode(),
        "AT000001.txt": AT_TXT.encode(),
    }


# --- RIPS 2275 -------------------------------------------------------------


def json_2275(
    factura="FE2001", valor_consulta=30000, cantidad_med=5, valor_med="7500.25"
) -> bytes:
    doc = {
        "numDocumentoIdObligado": "900000001",
        "numFactura": factura,
        "tipoNota": None,
        "numNota": None,
        "usuarios": [
            {
                "tipoDocumentoIdentificacion": "CC",
                "numDocumentoIdentificacion": "2002",
                "tipoUsuario": "01",
                "fechaNacimiento": "1980-05-01",
                "codSexo": "F",
                "codPaisResidencia": "170",
                "codMunicipioResidencia": "81001",
                "codZonaTerritorialResidencia": "01",
                "incapacidad": "NO",
                "consecutivo": 1,
                "codPaisOrigen": "170",
                "servicios": {
                    "consultas": [
                        {
                            "codPrestador": PRESTADOR,
                            "fechaInicioAtencion": "2024-02-03 08:00",
                            "numAutorizacion": None,
                            "codConsulta": "890201",
                            "modalidadGrupoServicioTecSal": "01",
                            "grupoServicios": "01",
                            "codServicio": 325,
                            "finalidadTecnologiaSalud": "15",
                            "causaMotivoAtencion": "38",
                            "codDiagnosticoPrincipal": "J00X",
                            "tipoDiagnosticoPrincipal": "01",
                            "tipoDocumentoIdentificacion": "CC",
                            "numDocumentoIdentificacion": "555",
                            "vrServicio": valor_consulta,
                            "conceptoRecaudo": "05",
                            "valorPagoModerador": 0,
                            "numFEVPagoModerador": None,
                            "consecutivo": 1,
                        }
                    ],
                    "medicamentos": [
                        {
                            "codPrestador": PRESTADOR,
                            "numAutorizacion": None,
                            "idMIPRES": None,
                            "fechaDispensAdmon": "2024-02-03 09:00",
                            "codDiagnosticoPrincipal": "J00X",
                            "tipoMedicamento": "01",
                            "codTecnologiaSalud": "20012345-2",
                            "nomTecnologiaSalud": "AMOXICILINA",
                            "concentracionMedicamento": 500,
                            "unidadMedida": 168,
                            "formaFarmaceutica": "COLFAR",
                            "unidadMinDispensa": 1,
                            "cantidadMedicamento": cantidad_med,
                            "diasTratamiento": 5,
                            "tipoDocumentoIdentificacion": "CC",
                            "numDocumentoIdentificacion": "555",
                            "vrUnitMedicamento": 1500.05,
                            "vrServicio": (
                                float(valor_med) if "." in str(valor_med) else valor_med
                            ),
                            "conceptoRecaudo": "05",
                            "valorPagoModerador": 0,
                            "numFEVPagoModerador": None,
                            "consecutivo": 1,
                        }
                    ],
                    "urgencias": [
                        {
                            "codPrestador": PRESTADOR,
                            "fechaInicioAtencion": "2024-02-03 07:00",
                            "causaMotivoAtencion": "38",
                            "codDiagnosticoPrincipal": "J00X",
                            "condicionDestinoUsuarioEgreso": "01",
                            "fechaEgreso": "2024-02-03 10:00",
                            "consecutivo": 1,
                        }
                    ],
                },
            }
        ],
    }
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


# --- Paquetes --------------------------------------------------------------


def crear_zip(archivos: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for nombre, contenido in archivos.items():
            zf.writestr(nombre, contenido)
    return buffer.getvalue()


def _crc16(datos: bytes) -> bytes:
    return struct.pack("<H", zlib.crc32(datos) & 0xFFFF)


def crear_rar(archivos: dict[str, bytes]) -> bytes:
    """Crea un RAR 4.x con archivos ALMACENADOS (sin compresión).

    Es un RAR válido que ``rarfile`` lee sin herramientas externas; así la
    prueba no depende de tener ``rar``/``unrar`` instalados.
    """
    salida = bytearray(b"Rar!\x1a\x07\x00")
    cuerpo = struct.pack("<BHHHI", 0x73, 0x0000, 13, 0, 0)
    salida += _crc16(cuerpo) + cuerpo
    for nombre, contenido in archivos.items():
        nombre_b = nombre.replace("/", "\\").encode("ascii")
        dos_time = (2024 - 1980) << 25 | 1 << 21 | 1 << 16
        cuerpo = (
            struct.pack(
                "<BHHIIBIIBBHI",
                0x74,  # HEAD_TYPE: archivo
                0x8000,  # HEAD_FLAGS: LONG_BLOCK
                32 + len(nombre_b),  # HEAD_SIZE
                len(contenido),  # PACK_SIZE
                len(contenido),  # UNP_SIZE
                2,  # HOST_OS: Win32
                zlib.crc32(contenido) & 0xFFFFFFFF,
                dos_time,
                20,  # UNP_VER
                0x30,  # METHOD: almacenado
                len(nombre_b),
                0x20,  # ATTR
            )
            + nombre_b
        )
        salida += _crc16(cuerpo) + cuerpo + contenido
    cuerpo = struct.pack("<BHH", 0x7B, 0x4000, 7)
    salida += _crc16(cuerpo) + cuerpo
    return bytes(salida)


# --- Facturación ------------------------------------------------------------

FACTURACION_CSV = (
    "Sede;Hospital;Ambito;Numero Factura;Fecha Factura;Codigo;Descripcion;Cantidad;Valor Unitario;Valor Total\n"
    # FE1001: coincide consulta, procedimiento; medicamento con 1 unidad de más; insumo coincide
    "TAME;HOSPITAL SAN ANTONIO;AMBULATORIO;FE-1001;15/01/2024;890201;CONSULTA MEDICINA GENERAL;1;30.000;30.000\n"
    "TAME;HOSPITAL SAN ANTONIO;AMBULATORIO;FE-1001;15/01/2024;903841;GLUCOSA;1;15.000,50;15.000,50\n"
    "TAME;HOSPITAL SAN ANTONIO;FARMACIA;FE-1001;15/01/2024;19912345-1;ACETAMINOFEN;11;1.000;11.000\n"
    "TAME;HOSPITAL SAN ANTONIO;AMBULATORIO;FE-1001;15/01/2024;AT001;JERINGA;2;3.000;6.000\n"
    # FE1001: código facturado y no reportado en RIPS
    "TAME;HOSPITAL SAN ANTONIO;AMBULATORIO;FE-1001;15/01/2024;906913;HEMOGRAMA;1;12.000;12.000\n"
    # FE1002: consulta con valor distinto (RIPS 30000)
    "TAME;HOSPITAL SAN ANTONIO;AMBULATORIO;FE1002;20/01/2024;890201;CONSULTA MEDICINA GENERAL;1;32.000;32.000\n"
    # FE9999: factura que no está en RIPS
    "TAME;HOSPITAL SAN ANTONIO;URGENCIAS;FE9999;25/01/2024;890701;CONSULTA URGENCIAS;1;45.000;45.000\n"
)

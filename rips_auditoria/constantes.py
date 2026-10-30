"""Constantes compartidas: meses, grupos de servicio, municipios y totales de control.

Los nombres de grupo replican exactamente los usados en los artefactos de referencia
(`DASHBOARD_CAPITA_EVENTO_PGP_ESE_MYC_2026.html`, objeto ``GN``, y la hoja
"MARCACION CUPS CAPITA" del informe de servicios). No se deben renombrar: el cruce con
la Nota Técnica depende de que coincidan.
"""
from __future__ import annotations

import unicodedata

MESES = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO",
         "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
MESES_AB = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

# Clave normalizada (mayúsculas sin tildes) -> nombre para mostrar (igual al dashboard).
GRUPOS = {
    "PROMOCION Y MANTENIMIENTO DE LA SALUD": "Promoción y Mant. de la Salud",
    "URGENCIAS": "Urgencias",
    "MEDICINA GENERAL": "Medicina General",
    "LABORATORIO": "Laboratorio",
    "ODONTOLOGIA": "Odontología",
    "MEDICAMENTOS": "Medicamentos",
    "APOYO TERAPEUTICO": "Apoyo Terapéutico",
    "IMAGENOLOGIA": "Imagenología",
    "INTERNACION BASICA": "Internación Básica",
    "PROCEDIMIENTOS": "Procedimientos",
    "TRASLADOS": "Traslados",
    "CONSULTA MEDICA ESPECIALIZADA": "Consulta médica especializada",
    "ATENCION PARTO Y RN": "Atención parto y RN",
    "MEDICAMENTOS E INSUMOS": "Medicamentos e insumos",
    "OTROS": "Otros / por clasificar",
    "COMPLEMENTARIOS": "Complementarios",
    "ATENCION DOMICILIARIA": "Atención domiciliaria",
    "ALBERGUE": "Albergue",
}
# Alias que la hoja NT COSTOS usa y el dashboard unifica.
ALIAS_GRUPO = {"URGENCIAS CON OBSERVACION": "URGENCIAS"}

# Los 10 grupos CAPITA del dashboard FINAL (mismo orden).
GRUPOS_CAPITA_FINAL = [
    "PROMOCION Y MANTENIMIENTO DE LA SALUD", "URGENCIAS", "MEDICINA GENERAL", "LABORATORIO",
    "ODONTOLOGIA", "APOYO TERAPEUTICO", "IMAGENOLOGIA", "INTERNACION BASICA",
    "PROCEDIMIENTOS", "MEDICAMENTOS",
]

# Tipos de archivo RIPS (Res. 3374/2000, Anexo Técnico No. 1).
TIPOS_RIPS = ["CT", "AF", "US", "AD", "AC", "AP", "AU", "AH", "AN", "AM", "AT"]
TIPOS_SERVICIO = ["AC", "AP", "AU", "AH", "AN", "AM", "AT", "AD"]

# Totales de control (dashboard FINAL, enero–agosto 2026).
CONTROL = {
    "capita_total": 8_053_016_408,
    "cups": 748_058,
    "pacientes_unicos": 114_923,   # suma de pacientes únicos mensuales
    "asistencias": 213_814,
    "capita_promedio_mes": 1_006_627_051,
    "capita_ampliada_mes": 1_112_074_196,
    "capita_ajustada_mes": 1_253_045_148,
    "pgp2_mes": 3_045_623_529,
    "migrables_mes": 105_750_000,
}

# Totales de control de la Nota Técnica (hoja "NOTA TECNICA CAPITA NEPS", frecuencias mensuales).
# En el libro el segundo municipio es ARAUQUITA (no Arauca).
CONTROL_NT_MUNICIPIO = {
    "TAME": 94_139.59, "ARAUQUITA": 107_359.72, "FORTUL": 55_767.15,
    "CRAVO NORTE": 2_111.63, "PUERTO RONDON": 3_388.34,
}

NIT_ESE = "900034131"


def norm(s) -> str:
    """Normaliza como el PARSER del dashboard: mayúsculas, sin tildes, sin espacios extremos."""
    if s is None:
        return ""
    s = str(s).strip().upper()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def nombre_grupo(g: str) -> str:
    g = ALIAS_GRUPO.get(norm(g), norm(g))
    return GRUPOS.get(g, g.capitalize())


def mes_desde_texto(s) -> int | None:
    """'08. Agosto' / 'AGOSTO' / 'Ago' -> 8."""
    t = norm(s)
    for i, m in enumerate(MESES):
        if m in t:
            return i + 1
    for i, m in enumerate(MESES_AB):
        if t == norm(m):
            return i + 1
    return None

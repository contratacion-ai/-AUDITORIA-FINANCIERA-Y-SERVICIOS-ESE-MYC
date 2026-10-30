"""Lectura de los dashboards HTML de referencia (no se modifican ni se reemplazan).

* ``DASHBOARD_CAPITA_PGP_ESE_MYC_2026_FINAL.html``: los datos están codificados como constantes
  JavaScript (``GN``, ``GRIPS``, ``GMON``, ``GPCT``, ``DM``, ``PGP_*``, ``T20``, ``SIM``, ``RIE``) y
  algunos totales están escritos en el HTML. Es la fuente de los **totales de control**.
* ``DASHBOARD_CAPITA_EVENTO_PGP_ESE_MYC_2026.html``: trae el objeto ``DEFAULT`` (JSON) con la Nota
  Técnica por grupo/mes, pacientes, facturación EVENTO, consolidado por CUPS, giro y medicamentos,
  más los catálogos ``OBS`` (aplicabilidad CAPITA), ``MIG`` (migrables), ``RIE`` (riesgo) y ``TRAT``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..config import DIR_REFERENCIAS
from ..constantes import GRUPOS_CAPITA_FINAL, norm

ARCHIVO_FINAL = "DASHBOARD_CAPITA_PGP_ESE_MYC_2026_FINAL.html"
ARCHIVO_EVENTO = "DASHBOARD_CAPITA_EVENTO_PGP_ESE_MYC_2026.html"


def _js_a_json(expr: str):
    """Convierte un literal JS simple (arreglos/objetos con comillas simples) a Python."""
    s = re.sub(r"//[^\n]*", "", expr)
    s = re.sub(r"'((?:[^'\\]|\\.)*)'", lambda m: json.dumps(m.group(1), ensure_ascii=False), s)
    s = re.sub(r",\s*([\]}])", r"\1", s)
    return json.loads(s)


def _const(js: str, nombre: str):
    m = re.search(rf"const\s+{re.escape(nombre)}\s*=\s*", js)
    if not m:
        raise KeyError(nombre)
    i = m.end()
    apertura = js[i]
    cierre = {"[": "]", "{": "}"}[apertura]
    nivel, j, en_str = 0, i, None
    while j < len(js):
        ch = js[j]
        if en_str:
            if ch == "\\":
                j += 1
            elif ch == en_str:
                en_str = None
        elif ch in "'\"":
            en_str = ch
        elif ch == apertura:
            nivel += 1
        elif ch == cierre:
            nivel -= 1
            if nivel == 0:
                return _js_a_json(js[i:j + 1])
        j += 1
    raise ValueError(f"No se pudo leer {nombre}")


def _script(html: str) -> str:
    return max(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S), key=len)


@dataclass
class ReferenciaFinal:
    """Datos del dashboard FINAL. Montos en pesos, meses Ene–Ago 2026."""
    meses: list[str]
    grupos: list[str]                 # claves normalizadas en el orden del dashboard
    grupos_rips: list[float]
    grupos_mensual: list[list[float]]
    grupos_pct: list[float]
    detalle_mensual: list[dict]       # mes, cups, cums, total, pacientes, asistencias, costo, costo_usuario, costo_atencion
    pgp: dict[str, list[float]]
    top20: list[dict]
    simulacion: list[dict]
    riesgo: list[dict]
    escenarios_mes: dict[str, float]
    textos: dict[str, float] = field(default_factory=dict)

    @property
    def costo_capita_total(self) -> float:
        return sum(r["costo"] for r in self.detalle_mensual)

    def totales(self) -> dict[str, float]:
        d = self.detalle_mensual
        return {"capita_total": self.costo_capita_total, "cups": sum(r["cups"] for r in d),
                "cums": sum(r["cums"] for r in d), "pacientes_unicos": sum(r["pacientes"] for r in d),
                "asistencias": sum(r["asistencias"] for r in d)}


@dataclass
class ReferenciaEvento:
    """Datos del dashboard CAPITA·EVENTO·PGP (objeto DEFAULT y catálogos)."""
    datos: dict
    aplicabilidad: dict[str, tuple[str, str]]
    migrables: dict[str, str]
    riesgo: list[tuple[str, str, str]]
    tratamiento: dict[str, tuple[str, str]]
    grupos_nombre: dict[str, str]


def _leer(nombre: str, carpeta: Path | None) -> str:
    return ((carpeta or DIR_REFERENCIAS) / nombre).read_text(encoding="utf-8")


@lru_cache(maxsize=4)
def cargar_final(carpeta: Path | None = None) -> ReferenciaFinal:
    html = _leer(ARCHIVO_FINAL, carpeta)
    js = _script(html)
    gn = _const(js, "GN")
    claves = []
    for nombre in gn:
        k = norm(nombre).replace("MANT.SALUD", "MANTENIMIENTO DE LA SALUD").replace("PROMOCION Y MANT.", "PROMOCION Y MANTENIMIENTO")
        claves.append(next((g for g in GRUPOS_CAPITA_FINAL if g.startswith(k[:10])), k))
    dm = _const(js, "DM")
    meses = _const(js, "MESES")
    det = [{"mes": meses[i], "cups": r[0], "cums": r[1], "total": r[2], "pacientes": r[3], "asistencias": r[4],
            "costo": r[5], "costo_usuario": r[6], "costo_atencion": r[7]} for i, r in enumerate(dm)]
    pgp = {k: _const(js, f"PGP_{k}") for k in ("RIPS_CAP", "RIPS_EV", "COSTO_FIJ", "FACT_EV", "CAP2025", "UPC",
                                                "P1", "P2", "P3", "VAT")}
    t20 = [{"n": r[0], "codigo": r[1], "servicio": r[2], "valor": r[3], "frecuencia": r[4], "valor_unitario": r[5],
            "aplica_capita": r[6], "observacion": r[7]} for r in _const(js, "T20")]
    sim = [{"codigo": r[0], "servicio": r[1], "frec_c1": r[2], "frec_c2": r[3], "frec_total": r[4], "prom_mes": r[5],
            "valor_evento": r[6], "costo_estimado": r[7], "costo_mes_capita": r[8], "costo_anual": r[9],
            "usuarios": r[10], "costo_usuario": r[11], "impacto_pct": r[12]} for r in _const(js, "SIM")]
    rie = [{"codigo": r[0], "servicio": r[1], "valor": r[2], "frecuencia": r[3], "nivel": r[4], "observacion": r[5]}
           for r in _const(js, "RIE")]
    m = re.search(r"mk\('cEsc'.*?data:\s*(\[[\d,\s]+\])", js, re.S)
    esc_vals = json.loads(m.group(1)) if m else []
    nombres_esc = ["esc1_actual", "esc2_ampliada", "esc3_demanda_real", "giro_sin_upc", "giro_upc", "actas_bogota",
                   "pgp1_cap_evento", "pgp2_upc_evento"]
    texto = re.sub(r"<[^>]+>", " ", html)

    def monto(patron):
        mm = re.search(patron, texto)
        return float(mm.group(1).replace(".", "")) if mm else None
    mt = re.search(r"<td>TOTAL</td><td>[\d.]+</td>.*?<td>\$([\d.]+)</td>", js, re.S)
    textos = {"total_capita_html": float(mt.group(1).replace(".", "")) if mt else None,
              "migrables_mes_texto": monto(r"adicional total es\s*\$([\d.]+)/mes"),
              "migrables_esc2": monto(r"13 servicios migrables\s*\$([\d.]+)"),
              "base_cuatrimestre2": monto(r"Base 2do cuatrimestre\s*\$([\d.]+)"),
              "ajuste_poblacional": monto(r"Ajuste crecimiento pob\.\(3%\)\s*\$([\d.]+)"),
              "nuevos_servicios": monto(r"Nuevos servicios\s*\$([\d.]+)"),
              "usuarios_contratados": monto(r"Usuarios Contratados\s*([\d.]+)"),
              "valor_usuario_giro": monto(r"usuarios × \$([\d.]+)\)"),
              "valor_usuario_upc": monto(r"Con UPC 2026 \(\+16,49%\):.*?× \$([\d.]+)\)")}
    return ReferenciaFinal(meses=meses, grupos=claves, grupos_rips=_const(js, "GRIPS"),
                           grupos_mensual=_const(js, "GMON"), grupos_pct=_const(js, "GPCT"), detalle_mensual=det,
                           pgp=pgp, top20=t20, simulacion=sim, riesgo=rie,
                           escenarios_mes=dict(zip(nombres_esc, esc_vals)), textos=textos)


@lru_cache(maxsize=4)
def cargar_evento(carpeta: Path | None = None) -> ReferenciaEvento:
    js = _script(_leer(ARCHIVO_EVENTO, carpeta))
    linea = next(l for l in js.split("\n") if l.startswith("const DEFAULT="))
    datos = json.loads(linea[len("const DEFAULT="):].rstrip().rstrip(";"))
    obs = {k: tuple(v) for k, v in _const(js, "OBS").items()}
    trat = {k: tuple(v) for k, v in _const(js, "TRAT").items()}
    return ReferenciaEvento(datos=datos, aplicabilidad=obs, migrables=_const(js, "MIG"),
                            riesgo=[tuple(r) for r in _const(js, "RIE")], tratamiento=trat,
                            grupos_nombre=_const(js, "GN"))

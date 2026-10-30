"""Deducción de hospital, municipio, periodo y modalidad de una remisión.

Orden de precedencia (cada valor guarda su ``fuente_*`` para trazabilidad):

* **manual**   – corrección del usuario (interfaz o parámetro del script).
* **carpeta**  – estructura de carpetas: ``<Hospital>/<NN. Mes>/<EPS y modalidad>/``.
* **contenido** – datos del RIPS: código de habilitación del prestador (municipio DANE),
  fechas del AF (periodo), número de contrato/carpeta (modalidad).

El formato del archivo (TXT/JSON) nunca se usa para inferir sede ni hospital.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath

from ..config import contratos_modalidad, red_prestadores, sedes
from ..constantes import MESES, mes_desde_texto, norm

RE_DRIVE = re.compile(r"-\d{8}T\d{6}Z-\d+-\d+$")          # sufijo que agrega Google Drive al ZIP
RE_SEDE = re.compile(r"\b(HOSPITAL|CENTRO DE SALUD|PUESTO DE SALUD|E\.?S\.?E|IPS|UNIDAD|SEDE)\b")
RE_MES_CARPETA = re.compile(r"^\s*(\d{1,2})\s*[.\-_ ]\s*([A-Za-zÁÉÍÓÚáéíóú]+)")
RE_ANIO = re.compile(r"\b(20\d{2})\b")


@dataclass
class Contexto:
    hospital: str | None = None
    municipio: str | None = None
    periodo: str | None = None          # AAAA-MM
    modalidad_declarada: str | None = None
    carpeta_envio: str | None = None
    fuente_hospital: str | None = None
    fuente_municipio: str | None = None
    fuente_periodo: str | None = None
    notas: list[str] = field(default_factory=list)


def limpiar_nombre(parte: str) -> str:
    return RE_DRIVE.sub("", parte).strip()


def municipio_por_codigo(cod_prestador: str | None) -> str | None:
    if not cod_prestador:
        return None
    return red_prestadores().get("municipios", {}).get(str(cod_prestador).strip()[:5])


def sede_por_codigo(cod_prestador: str | None) -> dict | None:
    if not cod_prestador:
        return None
    c = str(cod_prestador).strip()
    return next((s for s in sedes() if c in s["cod_habilitacion"]), None)


def sede_por_texto(texto: str) -> dict | None:
    """Sede cuyo alias aparece en la ruta (se prefiere el alias más largo, es decir el más específico)."""
    t = norm(texto)
    mejor, largo = None, 0
    for s in sedes():
        for a in s["alias"]:
            if a and len(a) > largo and re.search(rf"(^|[^A-Z0-9]){re.escape(a)}([^A-Z0-9]|$)", t):
                mejor, largo = s, len(a)
    return mejor


def municipio_por_texto(texto: str) -> str | None:
    s = sede_por_texto(texto)
    if s:
        return s["municipio"]
    t = norm(texto)
    for mun in red_prestadores().get("municipios", {}).values():
        if re.search(rf"\b{re.escape(mun)}\b", t):
            return mun
    return None


def modalidad_por_texto(*textos: str | None) -> str | None:
    pal = red_prestadores().get("modalidad_por_palabra", {})
    for t in textos:
        tn = norm(t)
        for k, v in pal.items():
            if re.search(rf"\b{k}\b", tn):
                return v
    return None


def desde_ruta(ruta: str, anio_defecto: int | None = None) -> Contexto:
    """Analiza ``.../<Hospital>/<08. Agosto>/<NUEVA EPS EVENTO>/AF0001.txt``."""
    partes = [limpiar_nombre(p) for p in PurePosixPath(ruta).parts[:-1]]
    ctx = Contexto()
    idx_mes = None
    for i, p in enumerate(partes):
        m = RE_MES_CARPETA.match(p)
        mes = None
        if m and 1 <= int(m.group(1)) <= 12 and mes_desde_texto(m.group(2)):
            mes = int(m.group(1))
        elif norm(p) in MESES:
            mes = MESES.index(norm(p)) + 1
        if mes:
            idx_mes = i
            anio = next((int(a) for a in RE_ANIO.findall(" ".join(partes))), anio_defecto)
            if anio:
                ctx.periodo = f"{anio:04d}-{mes:02d}"
                ctx.fuente_periodo = "carpeta"
            else:
                ctx.notas.append(f"Mes {mes:02d} desde carpeta sin año; se toma del contenido")
                ctx.periodo = f"????-{mes:02d}"
    # hospital: la carpeta que parezca sede, o la anterior a la del mes
    previas = partes[:idx_mes] if idx_mes is not None else partes[:-1]
    cand = [p for p in previas if RE_SEDE.search(norm(p))]
    if cand:
        ctx.hospital = cand[-1]
    elif idx_mes and idx_mes > 0:
        ctx.hospital = partes[idx_mes - 1]
    if ctx.hospital:
        ctx.fuente_hospital = "carpeta"
    sede = sede_por_texto(" / ".join(partes[:idx_mes] if idx_mes is not None else partes))
    if sede:
        ctx.hospital, ctx.fuente_hospital = sede["nombre"], "carpeta (sede)"
    # carpeta de la remisión = la carpeta que contiene el archivo (si no es la del mes)
    if partes and (idx_mes is None or idx_mes < len(partes) - 1):
        ctx.carpeta_envio = partes[-1]
    ctx.modalidad_declarada = modalidad_por_texto(ctx.carpeta_envio)
    mun = municipio_por_texto(" / ".join(partes))
    if mun:
        ctx.municipio, ctx.fuente_municipio = mun, "carpeta"
    return ctx


def _fecha(s) -> datetime | None:
    if isinstance(s, datetime):
        return s
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(s).strip()[:16], fmt)
        except (ValueError, TypeError):
            continue
    return None


def periodo_desde_af(registros_af: list[dict]) -> str | None:
    """Mes más frecuente del inicio del periodo de facturación (fecha_inicio) del AF."""
    c = Counter()
    for r in registros_af:
        f = _fecha(r.get("fecha_inicio")) or _fecha(r.get("fecha_expedicion"))
        if f:
            c[f"{f.year:04d}-{f.month:02d}"] += 1
    return c.most_common(1)[0][0] if c else None


def completar(ctx: Contexto, registros_af: list[dict], cod_prestador: str | None,
              num_contrato: str | None = None, manual: dict | None = None) -> Contexto:
    """Completa con el contenido y aplica correcciones manuales (máxima prioridad)."""
    per_af = periodo_desde_af(registros_af)
    if ctx.periodo and ctx.periodo.startswith("????") and per_af:
        ctx.periodo = per_af[:4] + ctx.periodo[4:]
        ctx.fuente_periodo = "carpeta+contenido"
    elif not ctx.periodo and per_af:
        ctx.periodo, ctx.fuente_periodo = per_af, "contenido"
    mun = municipio_por_codigo(cod_prestador)
    if mun:
        if ctx.municipio and ctx.municipio != mun:
            ctx.notas.append(f"Municipio de carpeta ({ctx.municipio}) difiere del código de habilitación "
                             f"{cod_prestador} ({mun}); se usa el código")
        ctx.municipio, ctx.fuente_municipio = mun, "contenido"
    sede = sede_por_codigo(cod_prestador)
    if sede and ctx.fuente_hospital != "carpeta (sede)":
        # el código de habilitación identifica la sede aunque la carpeta tenga otro nombre
        ctx.hospital, ctx.fuente_hospital = sede["nombre"], "contenido (sede)"
    if not ctx.hospital and cod_prestador:
        ctx.hospital, ctx.fuente_hospital = f"Prestador {cod_prestador}", "contenido"
    mod_contrato = contratos_modalidad().get(str(num_contrato or "").strip().upper())
    if mod_contrato:
        ctx.modalidad_declarada = mod_contrato
    if not ctx.modalidad_declarada:
        ctx.modalidad_declarada = modalidad_por_texto(num_contrato)
    for k, v in (manual or {}).items():
        if v and hasattr(ctx, k):
            setattr(ctx, k, v)
            if k in ("hospital", "municipio", "periodo"):
                setattr(ctx, f"fuente_{k}", "manual")
    return ctx

"""Carga de configuración (YAML/CSV de la carpeta ``config/``) y rutas de trabajo locales."""
from __future__ import annotations

import csv
import os
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
DIR_CONFIG = Path(os.environ.get("RIPS_CONFIG_DIR", RAIZ / "config"))
DIR_DATOS = Path(os.environ.get("RIPS_DATA_DIR", RAIZ / "datos"))
DIR_REFERENCIAS = RAIZ / "referencias"


@dataclass
class CampoRips:
    archivo: str
    orden: int
    nombre: str
    dato: str
    longitud: int
    tipo: str
    obligatorio: str
    permitidos: dict[str, str] = field(default_factory=dict)
    extendidos: set[str] = field(default_factory=set)


@lru_cache(maxsize=1)
def red_prestadores() -> dict:
    with open(DIR_CONFIG / "red_prestadores.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def sedes() -> list[dict]:
    """Sedes de la red normalizadas (acepta también el formato antiguo ``{alias: municipio}``)."""
    from .constantes import norm
    crudo = red_prestadores().get("sedes") or []
    if isinstance(crudo, dict):
        crudo = [{"nombre": k.title(), "municipio": v, "alias": [k]} for k, v in crudo.items()]
    out = []
    for s in crudo:
        alias = [norm(a) for a in (s.get("alias") or [])] + [norm(s["nombre"])]
        out.append({"nombre": s["nombre"], "municipio": norm(s.get("municipio")),
                    "alias": sorted(set(alias), key=len, reverse=True),
                    "cod_habilitacion": [str(c).strip() for c in (s.get("cod_habilitacion") or [])],
                    "verificado": bool(s.get("verificado"))})
    return out


def guardar_sedes(lista: list[dict]) -> None:
    """Escribe las sedes en ``red_prestadores.yaml`` (conserva el resto de la configuración)."""
    datos = dict(red_prestadores())
    datos["sedes"] = [{"nombre": s["nombre"].strip(), "municipio": str(s.get("municipio") or "").strip().upper(),
                       "alias": [a.strip() for a in s.get("alias") or [] if a and a.strip()],
                       "cod_habilitacion": [str(c).strip() for c in s.get("cod_habilitacion") or [] if str(c).strip()],
                       "verificado": bool(s.get("verificado"))} for s in lista if s.get("nombre", "").strip()]
    with open(DIR_CONFIG / "red_prestadores.yaml", "w", encoding="utf-8") as fh:
        fh.write("# Red de prestadores de la E.S.E. Moreno y Clavijo (editado desde la aplicación).\n")
        yaml.safe_dump(datos, fh, allow_unicode=True, sort_keys=False, width=200)
    red_prestadores.cache_clear()


def contratos_modalidad() -> dict[str, str]:
    """Número de contrato (AF) -> modalidad CAPITA / EVENTO, definido por el usuario."""
    return {str(k).strip().upper(): str(v).strip().upper()
            for k, v in (red_prestadores().get("contratos_modalidad") or {}).items() if v}


def guardar_contratos(mapa: dict[str, str]) -> None:
    datos = dict(red_prestadores())
    datos["contratos_modalidad"] = {str(k).strip(): str(v).strip().upper() for k, v in mapa.items()
                                    if str(k).strip() and str(v).strip().upper() in ("CAPITA", "EVENTO")}
    with open(DIR_CONFIG / "red_prestadores.yaml", "w", encoding="utf-8") as fh:
        fh.write("# Red de prestadores de la E.S.E. Moreno y Clavijo (editado desde la aplicación).\n")
        yaml.safe_dump(datos, fh, allow_unicode=True, sort_keys=False, width=200)
    red_prestadores.cache_clear()


@lru_cache(maxsize=1)
def diccionario_rips() -> dict[str, list[CampoRips]]:
    """Estructura de cada archivo RIPS según el Anexo Técnico No. 1 (orden de campos)."""
    out: dict[str, list[CampoRips]] = {}
    with open(DIR_CONFIG / "diccionario_rips_3374.csv", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            perm = {}
            for par in filter(None, (p.strip() for p in (r["valores_permitidos"] or "").split(";"))):
                k, _, v = par.partition("=")
                perm[k.strip()] = v.strip()
            ext = {x.strip() for x in (r["valores_extendidos_advertencia"] or "").split(",") if x.strip()}
            out.setdefault(r["archivo"], []).append(CampoRips(
                archivo=r["archivo"], orden=int(r["orden"]), nombre=r["nombre_tecnico"],
                dato=r["dato_anexo"], longitud=int(r["longitud"] or 0), tipo=r["tipo"],
                obligatorio=r["obligatorio"], permitidos=perm, extendidos=ext))
    for v in out.values():
        v.sort(key=lambda c: c.orden)
    return out


def columnas(tipo: str) -> list[str]:
    return [c.nombre for c in diccionario_rips()[tipo]]


def etiquetas_a_tecnico() -> dict[str, str]:
    """'Número de la factura' -> 'num_factura' (para leer el XLSX consolidado)."""
    m = {}
    for campos in diccionario_rips().values():
        for c in campos:
            m[c.dato] = c.nombre
    m.update({"Hospital": "hospital", "Período (AAAA-MM)": "periodo", "Id remisión (interno)": "envio_id",
              "Número de remisión": "remision", "Carpeta de la remisión": "carpeta_envio",
              "Archivo origen": "archivo", "Línea en archivo": "linea"})
    return m


def sal_anonimizacion() -> str:
    """Sal local para seudonimizar identificadores de paciente. Se crea una vez y no se versiona."""
    env = os.environ.get("RIPS_SAL")
    if env:
        return env
    DIR_DATOS.mkdir(parents=True, exist_ok=True)
    p = DIR_DATOS / ".sal_anonimizacion"
    if not p.exists():
        p.write_text(secrets.token_hex(16), encoding="utf-8")
    return p.read_text(encoding="utf-8").strip()

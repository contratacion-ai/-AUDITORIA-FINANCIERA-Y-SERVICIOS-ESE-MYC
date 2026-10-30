"""Base consolidada de RIPS: un repositorio local que se actualiza mes a mes.

Los RIPS llegan por hospital desde Google Drive y se corrigen/reenvían con frecuencia. Por eso la base
se organiza en **particiones (hospital, mes)**: cada vez que se carga una carpeta, las particiones que
trae esa carga **reemplazan** a las anteriores (no se suman). Así, volver a cargar la carpeta
"Hospital X/08. Agosto" actualiza agosto de ese hospital sin tocar los demás meses ni hospitales.

Modo ``carpeta``: reemplaza solo las carpetas de remisión (EPS) incluidas en la carga, útil cuando se
sube una sola subcarpeta corregida.

Almacenamiento: ``datos/base/<particion>.pkl`` + ``datos/base/indice.json``.
"""
from __future__ import annotations

import hashlib
import json
import pickle
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import config
from .constantes import TIPOS_RIPS, TIPOS_SERVICIO
from .ingesta.lote import Lote, combinar


def _dir() -> Path:
    d = config.DIR_DATOS / "base"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clave(hospital, periodo) -> str:
    return hashlib.sha1(f"{hospital}|{periodo}".encode()).hexdigest()[:16]


def _indice() -> dict:
    p = _dir() / "indice.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _guardar_indice(ind: dict) -> None:
    (_dir() / "indice.json").write_text(json.dumps(ind, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def _sublote(lote: Lote, envios: set[str], hospital, periodo, clave: str) -> Lote:
    tablas = {t: df[df["envio_id"].isin(envios)].reset_index(drop=True) for t, df in lote.tablas.items()}
    inc = lote.incidencias
    inc = inc[inc["envio_id"].isin(envios) | ((inc["hospital"] == hospital) & (inc["periodo"] == periodo))]
    arch = lote.archivos
    if "envio_id" in arch:
        arch = arch[arch["envio_id"].isin(envios)]
    dup = lote.duplicados
    if not dup.empty and "envio_id" in dup:
        dup = dup[dup["envio_id"].isin(envios)]
    rem = lote.remisiones[lote.remisiones["envio_id"].isin(envios)]
    return Lote(id=clave, creado=lote.creado, tablas=tablas, incidencias=inc.reset_index(drop=True),
                archivos=arch.reset_index(drop=True), remisiones=rem.reset_index(drop=True),
                duplicados=dup.reset_index(drop=True), notas=[f"lote origen {lote.id}"])


def _abrir(clave: str) -> Lote:
    with open(_dir() / f"{clave}.pkl", "rb") as fh:
        return pickle.load(fh)


def _escribir(clave: str, l: Lote) -> None:
    with open(_dir() / f"{clave}.pkl", "wb") as fh:
        pickle.dump(l, fh)


def actualizar(lote: Lote, origen: str = "", modo: str = "mes") -> dict:
    """Incorpora un lote a la base. ``modo='mes'`` reemplaza cada (hospital, mes) de la carga;
    ``modo='carpeta'`` reemplaza solo las carpetas de remisión cargadas dentro de ese mes."""
    ind = _indice()
    rem = lote.remisiones
    res = {"nuevas": [], "reemplazadas": [], "omitidas": []}
    if rem.empty:
        return res
    for (hosp, per), g in rem.groupby(["hospital", "periodo"], dropna=False):
        hosp = None if pd.isna(hosp) else hosp
        per = None if pd.isna(per) else per
        clave = _clave(hosp, per)
        nuevo = _sublote(lote, set(g["envio_id"]), hosp, per, clave)
        if not any(len(nuevo.tablas[t]) for t in ("AF", *TIPOS_SERVICIO)):
            res["omitidas"].append({"hospital": hosp, "periodo": per, "motivo": "sin facturas ni servicios"})
            continue
        existe = clave in ind
        if existe and modo == "carpeta":
            viejo = _abrir(clave)
            carpetas = set(g["carpeta_envio"].dropna())
            quedan = set(viejo.remisiones.loc[~viejo.remisiones["carpeta_envio"].isin(carpetas), "envio_id"])
            if quedan:
                viejo = _sublote(viejo, quedan, hosp, per, clave)
                nuevo = combinar([viejo, nuevo])
                nuevo.id = clave
        _escribir(clave, nuevo)
        af = nuevo.tablas["AF"]
        eps = af["cod_entidad"].astype(str).str.upper().value_counts().to_dict() if not af.empty else {}
        ind[clave] = {"hospital": hosp, "municipio": g["municipio"].dropna().iloc[0] if g["municipio"].notna().any() else None,
                      "periodo": per, "carpetas": sorted(map(str, nuevo.remisiones["carpeta_envio"].dropna().unique())),
                      "remisiones": int(len(nuevo.remisiones)), "facturas": int(len(af)),
                      "facturas_nueva_eps": int(sum(v for k, v in eps.items() if k in ("EPS037", "EPSS37"))),
                      "servicios": int(sum(len(nuevo.tablas[t]) for t in TIPOS_SERVICIO)),
                      "valor_af": float(pd.to_numeric(af["valor_neto"], errors="coerce").sum()) if not af.empty else 0.0,
                      "errores": int((nuevo.incidencias["severidad"] == "ERROR").sum()),
                      "cargado": datetime.now().isoformat(timespec="seconds"), "origen": origen, "lote": lote.id,
                      "modo": modo}
        (res["reemplazadas"] if existe else res["nuevas"]).append({"hospital": hosp, "periodo": per})
    _guardar_indice(ind)
    return res


def listar() -> pd.DataFrame:
    ind = _indice()
    if not ind:
        return pd.DataFrame(columns=["clave", "hospital", "municipio", "periodo", "remisiones", "facturas",
                                     "facturas_nueva_eps", "servicios", "valor_af", "errores", "cargado", "origen"])
    df = pd.DataFrame([{"clave": k, **v} for k, v in ind.items()])
    df["carpetas"] = df["carpetas"].map(lambda x: ", ".join(x) if isinstance(x, list) else x)
    return df.sort_values(["hospital", "periodo"], na_position="last").reset_index(drop=True)


def eliminar(hospital: str | None = None, periodo: str | None = None, municipio: str | None = None,
             clave: str | None = None) -> int:
    """Limpia datos de la base. Sin filtros no borra nada (use ``vaciar``)."""
    if not any((hospital, periodo, municipio, clave)):
        return 0
    ind = _indice()
    borrar = [k for k, v in ind.items()
              if (clave is None or k == clave) and (hospital is None or v["hospital"] == hospital)
              and (periodo is None or v["periodo"] == periodo) and (municipio is None or v["municipio"] == municipio)]
    for k in borrar:
        (_dir() / f"{k}.pkl").unlink(missing_ok=True)
        ind.pop(k)
    _guardar_indice(ind)
    return len(borrar)


def eliminar_lote(lote_id: str) -> int:
    """Quita las particiones cargadas por un lote (antes de reincorporarlo corregido)."""
    ind = _indice()
    borrar = [k for k, v in ind.items() if v.get("lote") == lote_id]
    for k in borrar:
        (_dir() / f"{k}.pkl").unlink(missing_ok=True)
        ind.pop(k)
    _guardar_indice(ind)
    return len(borrar)


def vaciar() -> int:
    ind = _indice()
    for k in ind:
        (_dir() / f"{k}.pkl").unlink(missing_ok=True)
    _guardar_indice({})
    return len(ind)


def lote(hospitales: list[str] | None = None, municipios: list[str] | None = None,
         periodos: list[str] | None = None) -> Lote:
    """Lote unificado (deduplicado entre particiones) para el análisis, con filtros opcionales."""
    ind = _indice()
    claves = [k for k, v in ind.items()
              if (not hospitales or v["hospital"] in hospitales) and (not municipios or v["municipio"] in municipios)
              and (not periodos or v["periodo"] in periodos)]
    if not claves:
        raise ValueError("La base no tiene datos para los filtros seleccionados; cargue o sincronice RIPS")
    lotes = [_abrir(k) for k in sorted(claves, key=lambda k: (str(ind[k]["hospital"]), str(ind[k]["periodo"])))]
    u = combinar(lotes)
    u.id = f"base:{len(claves)}particiones"
    return u


def resumen_tablas() -> dict[str, int]:
    ind = _indice()
    return {"particiones": len(ind), "hospitales": len({v["hospital"] for v in ind.values()}),
            "meses": len({v["periodo"] for v in ind.values()}),
            "facturas": sum(v["facturas"] for v in ind.values()),
            "servicios": sum(v["servicios"] for v in ind.values())}


__all__ = ["actualizar", "listar", "eliminar", "vaciar", "lote", "resumen_tablas", "TIPOS_RIPS"]

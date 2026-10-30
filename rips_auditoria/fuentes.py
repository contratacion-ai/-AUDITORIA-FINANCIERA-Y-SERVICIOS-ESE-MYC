"""Carpetas vigiladas (Google Drive para escritorio u otra carpeta sincronizada).

Cada hospital comparte sus RIPS en Drive y los actualiza constantemente. Se registran las rutas locales
de esas carpetas (por ejemplo ``G:\\Mi unidad\\RIPS\\Hospital San Jose de Cravo Norte``) y
``sincronizar()`` vuelve a procesar **solo** las carpetas de mes que cambiaron (archivos nuevos,
modificados o borrados) y actualiza la base consolidada por (hospital, mes).

Unidad de actualización = carpeta de mes (``NN. Mes``). Los ZIP/RAR sueltos fuera de una carpeta de
mes se tratan como unidad propia. La huella de cada unidad es un SHA-1 de (ruta, tamaño, fecha de
modificación) de sus archivos.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from . import base, config
from .ingesta.contexto import RE_MES_CARPETA, limpiar_nombre
from .ingesta.lote import procesar

EXT = {".txt", ".json", ".xlsx", ".zip", ".rar"}


def _archivo() -> Path:
    config.DIR_DATOS.mkdir(parents=True, exist_ok=True)
    return config.DIR_DATOS / "fuentes.json"


def leer() -> dict:
    p = _archivo()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"carpetas": [], "huellas": {}}


def _guardar(d: dict) -> None:
    _archivo().write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def agregar(ruta: str) -> dict:
    p = Path(ruta).expanduser()
    if not p.is_dir():
        raise ValueError(f"La carpeta no existe o no es accesible: {ruta}")
    d = leer()
    if str(p) not in d["carpetas"]:
        d["carpetas"].append(str(p))
    _guardar(d)
    return d


def quitar(ruta: str) -> dict:
    d = leer()
    d["carpetas"] = [c for c in d["carpetas"] if c != ruta]
    d["huellas"] = {k: v for k, v in d["huellas"].items() if not k.startswith(ruta)}
    _guardar(d)
    return d


def _es_mes(nombre: str) -> bool:
    m = RE_MES_CARPETA.match(limpiar_nombre(nombre))
    return bool(m) and 1 <= int(m.group(1)) <= 12


def unidades(raiz: Path) -> dict[Path, list[Path]]:
    """Agrupa los archivos RIPS de la raíz por carpeta de mes (o por archivo comprimido suelto)."""
    out: dict[Path, list[Path]] = {}
    for f in sorted(raiz.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in EXT:
            continue
        mes = next((p for p in f.relative_to(raiz).parents if p.name and _es_mes(p.name)), None)
        u = raiz / mes if mes is not None else f if f.suffix.lower() in (".zip", ".rar") else f.parent
        out.setdefault(u, []).append(f)
    return out


def _huella(archivos: list[Path]) -> str:
    h = hashlib.sha1()
    for f in sorted(archivos):
        st = f.stat()
        h.update(f"{f}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def sincronizar(forzar: bool = False, anio_defecto: int = 2026, progreso=None) -> dict:
    """Procesa las carpetas de mes nuevas o modificadas y actualiza la base. Devuelve el reporte."""
    d = leer()
    rep = {"inicio": datetime.now().isoformat(timespec="seconds"), "procesadas": [], "sin_cambios": 0,
           "errores": [], "ausentes": []}
    vistas = set()
    for raiz_s in d["carpetas"]:
        raiz = Path(raiz_s)
        if not raiz.is_dir():
            rep["errores"].append({"carpeta": raiz_s, "error": "No accesible (¿Drive sin sincronizar?)"})
            continue
        for u, archivos in unidades(raiz).items():
            k = str(u)
            vistas.add(k)
            hu = _huella(archivos)
            if not forzar and d["huellas"].get(k) == hu:
                rep["sin_cambios"] += 1
                continue
            if progreso:
                progreso(k)
            try:
                base_rel = raiz.parent
                entradas = [(f.relative_to(base_rel).as_posix(), f.read_bytes()) for f in archivos]
                l = procesar(entradas, anio_defecto=anio_defecto)
                r = base.actualizar(l, origen=k, modo="mes")
                d["huellas"][k] = hu
                rep["procesadas"].append({"unidad": k, "archivos": len(archivos), **{x: len(r[x]) for x in r},
                                          "errores_validacion": int((l.incidencias["severidad"] == "ERROR").sum())})
            except Exception as e:  # una carpeta dañada no detiene la sincronización
                rep["errores"].append({"carpeta": k, "error": str(e)[:300]})
    rep["ausentes"] = [k for k in d["huellas"] if k not in vistas and any(k.startswith(c) for c in d["carpetas"])]
    d["ultima_sincronizacion"] = rep["inicio"]
    _guardar(d)
    return rep

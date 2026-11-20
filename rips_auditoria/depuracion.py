"""Limpieza de los datos cargados y depuración de duplicados.

Trabaja sobre el acumulado homologado (``<datos>/acumulado_aplicativo``):
un CSV por mes (``AAAA-MM.csv``) con las filas que alimentan el tablero y los
envíos al aplicativo de auditoría, más ``indicadores.csv`` por sede-mes.

Todo borrado se hace en dos pasos:

1. **Vista previa** (``vista_limpieza`` / ``vista_duplicados``): devuelve las
   filas que se eliminarían, un resumen por mes y sede, y una **ficha**: una
   huella de los archivos y del criterio en ese momento.
2. **Confirmación** (``aplicar_limpieza`` / ``aplicar_duplicados``): exige la
   ficha de la vista previa. Si los datos cambiaron desde entonces (otro
   cargue), la ficha no coincide y no se borra nada: hay que volver a revisar.

Las filas eliminadas se copian antes en ``<acumulado>/papelera/`` (CSV con
fecha) para poder recuperarlas a mano.

Duplicados reales (sobre las columnas de la plantilla del aplicativo):

* **Exactos**: filas idénticas en todas las columnas. Se conserva la primera.
* **Factura repetida en otro mes**: misma sede, factura, EPS, contrato,
  modalidad, servicio y código, con la misma cantidad y valor, en meses
  distintos (típico al cargar en un mes RIPS que ya se habían cargado en
  otro). Se conserva la del mes más antiguo.

No se consideran duplicadas filas con la misma factura y código pero
cantidad o valor distintos: pueden ser correcciones y se dejan para revisión.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import pandas as pd

from .aplicativo import COLUMNAS, _norm
from .tablero import ARCHIVO_INDICADORES, _archivos_mes, carpeta_acumulado

LLAVE_SIN_MES = ["EPS", "NIT_EPS", "CONTRATO", "CUCON", "MODALIDAD", "SEDE", "FACTURA_FEV", "FACTURA_RIPS",
                 "TIPO_SERVICIO", "CODIGO_CUPS_CUMS", "CANTIDAD", "VALOR_SERVICIO"]
MUESTRA = 500  # filas de la vista previa que se devuelven a la pantalla (el resumen cuenta todas)


class FichaVencida(ValueError):
    """Los datos cambiaron desde la vista previa: hay que volver a revisarla."""


def _carpeta(carpeta) -> Path:
    return Path(carpeta) if carpeta else carpeta_acumulado()


def _leer(carpeta: Path) -> pd.DataFrame:
    partes = []
    for ruta in _archivos_mes(carpeta):
        d = pd.read_csv(ruta, dtype=str, keep_default_na=False, encoding="utf-8").reindex(columns=COLUMNAS, fill_value="")
        d["_archivo"] = ruta.name
        d["_fila"] = range(len(d))
        partes.append(d)
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=[*COLUMNAS, "_archivo", "_fila"])


def _ficha(carpeta: Path, criterio: str) -> str:
    archivos = _archivos_mes(carpeta) + [p for p in [carpeta / ARCHIVO_INDICADORES] if p.exists()]
    firma = "|".join(f"{p.name}:{p.stat().st_size}:{p.stat().st_mtime_ns}" for p in archivos)
    return hashlib.sha256(f"{criterio}#{firma}".encode()).hexdigest()[:24]


def _resumen(filas: pd.DataFrame, extra: list[str] | None = None) -> list[dict]:
    if filas.empty:
        return []
    cols = ["MES", "SEDE", *(extra or [])]
    v = pd.to_numeric(filas["VALOR_SERVICIO"], errors="coerce").fillna(0)
    r = (filas.assign(_v=v).groupby(cols).agg(filas=("_v", "size"), facturas=("FACTURA_FEV", "nunique"), valor=("_v", "sum"))
         .reset_index().sort_values(cols))
    return r.to_dict("records")


def _vista(filas: pd.DataFrame, ficha: str, extra: list[str] | None = None) -> dict:
    v = pd.to_numeric(filas["VALOR_SERVICIO"], errors="coerce").fillna(0)
    cols = [*COLUMNAS, *(extra or [])]
    return {"total_filas": len(filas), "valor": float(v.sum()), "facturas": int(filas["FACTURA_FEV"].nunique()) if len(filas) else 0,
            "resumen": _resumen(filas, extra), "filas": filas[cols].head(MUESTRA).to_dict("records"),
            "ficha": ficha}


def _criterio(mes, sede) -> tuple[str | None, str | None]:
    mes = (mes or "").strip() or None
    sede = _norm(sede) if (sede or "").strip() else None
    if not mes and not sede:
        raise ValueError("Indique un mes, una sede o ambos para limpiar.")
    return mes, sede


def _seleccion(df: pd.DataFrame, mes, sede) -> pd.Series:
    m = pd.Series(True, index=df.index)
    if mes:
        m &= df["MES"] == mes
    if sede:
        m &= df["SEDE"].map(_norm) == sede
    return m


# ---------------------------------------------------------------------------
# Limpieza por mes y/o sede
# ---------------------------------------------------------------------------

def vista_limpieza(mes: str | None = None, sede: str | None = None, carpeta=None) -> dict:
    """Filas que se eliminarían al limpiar ``mes``, ``sede`` o ambos (no borra nada)."""
    carpeta = _carpeta(carpeta)
    mes, sede = _criterio(mes, sede)
    df = _leer(carpeta)
    filas = df[_seleccion(df, mes, sede)]
    out = _vista(filas, _ficha(carpeta, f"limpiar|{mes}|{sede}"))
    ind = _indicadores(carpeta)
    out["indicadores"] = int(_seleccion(ind, mes, sede).sum()) if len(ind) else 0
    out["criterio"] = {"mes": mes, "sede": sede}
    return out


def aplicar_limpieza(ficha: str, mes: str | None = None, sede: str | None = None, carpeta=None) -> dict:
    """Elimina lo que mostró ``vista_limpieza`` con la misma ``ficha``."""
    carpeta = _carpeta(carpeta)
    mes, sede = _criterio(mes, sede)
    if ficha != _ficha(carpeta, f"limpiar|{mes}|{sede}"):
        raise FichaVencida("Los datos cambiaron desde la vista previa. Vuelva a revisar antes de confirmar.")
    df = _leer(carpeta)
    sel = _seleccion(df, mes, sede)
    eliminadas = _eliminar(carpeta, df, sel, f"limpieza_{mes or 'todos'}_{sede or 'todas'}")
    ind = _indicadores(carpeta)
    n_ind = 0
    if len(ind):
        s_ind = _seleccion(ind, mes, sede)
        n_ind = int(s_ind.sum())
        if n_ind:
            _escribir(ind[~s_ind], carpeta / ARCHIVO_INDICADORES)
    return {"eliminadas": eliminadas, "indicadores_eliminados": n_ind}


# ---------------------------------------------------------------------------
# Duplicados reales
# ---------------------------------------------------------------------------

def detectar_duplicados(df: pd.DataFrame) -> pd.DataFrame:
    """Filas sobrantes (las que se eliminarían), con su ``tipo_duplicado``.
    ``df`` lleva las columnas de la plantilla; el orden define cuál se conserva."""
    if df.empty:
        return df.assign(tipo_duplicado=pd.Series(dtype=str))
    base = df.copy()
    for c in ("CANTIDAD", "VALOR_SERVICIO", "VALOR_FACTURA_FEV"):  # 1 == 1.0 == "1.0"
        base[c] = pd.to_numeric(base[c], errors="coerce").round(2).astype(str)
    base["_orden_mes"] = base["MES"]
    base = base.sort_values(["_orden_mes"], kind="stable")
    exactos = base.duplicated(COLUMNAS, keep="first")
    otro_mes = base.duplicated(LLAVE_SIN_MES, keep="first") & ~exactos
    # en «otro mes» sólo cuenta si la primera aparición está en un mes distinto
    primero_mes = base.groupby(LLAVE_SIN_MES, sort=False)["MES"].transform("first")
    otro_mes &= base["MES"] != primero_mes
    tipo = pd.Series("", index=base.index)
    tipo[exactos] = "exacto"
    tipo[otro_mes] = "factura repetida en otro mes"
    sobra = tipo != ""
    return df.loc[base.index[sobra]].assign(tipo_duplicado=tipo[sobra].to_numpy()).sort_index()


def vista_duplicados(carpeta=None) -> dict:
    carpeta = _carpeta(carpeta)
    df = _leer(carpeta)
    dup = detectar_duplicados(df)
    out = _vista(dup, _ficha(carpeta, "duplicados"), ["tipo_duplicado"])
    out["por_tipo"] = dup["tipo_duplicado"].value_counts().to_dict() if len(dup) else {}
    return out


def aplicar_duplicados(ficha: str, carpeta=None) -> dict:
    carpeta = _carpeta(carpeta)
    if ficha != _ficha(carpeta, "duplicados"):
        raise FichaVencida("Los datos cambiaron desde la vista previa. Vuelva a revisar antes de confirmar.")
    df = _leer(carpeta)
    dup = detectar_duplicados(df)
    sel = df.index.isin(dup.index)
    return {"eliminadas": _eliminar(carpeta, df, pd.Series(sel, index=df.index), "duplicados"),
            "por_tipo": dup["tipo_duplicado"].value_counts().to_dict() if len(dup) else {}}


# ---------------------------------------------------------------------------

def _indicadores(carpeta: Path) -> pd.DataFrame:
    ruta = carpeta / ARCHIVO_INDICADORES
    if not ruta.exists():
        return pd.DataFrame(columns=["MES", "SEDE"])
    return pd.read_csv(ruta, dtype=str, keep_default_na=False, encoding="utf-8")


def _escribir(df: pd.DataFrame, ruta: Path) -> None:
    tmp = ruta.with_suffix(".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8")
    tmp.replace(ruta)


def _eliminar(carpeta: Path, df: pd.DataFrame, sel: pd.Series, motivo: str) -> int:
    """Copia a la papelera las filas seleccionadas y reescribe los meses afectados."""
    if not sel.any():
        return 0
    papelera = carpeta / "papelera"
    papelera.mkdir(exist_ok=True)
    fuera = df[sel]
    nombre = f"{dt.datetime.now():%Y%m%d_%H%M%S}_{_norm(motivo).replace(' ', '_')}.csv"
    fuera.drop(columns=["_fila"]).to_csv(papelera / nombre, index=False, encoding="utf-8")
    for archivo in fuera["_archivo"].unique():
        quedan = df[(df["_archivo"] == archivo) & ~sel][COLUMNAS]
        ruta = carpeta / archivo
        if quedan.empty:
            ruta.unlink()
        else:
            _escribir(quedan, ruta)
    return int(sel.sum())


__all__ = ["FichaVencida", "aplicar_duplicados", "aplicar_limpieza", "detectar_duplicados", "vista_duplicados",
           "vista_limpieza"]

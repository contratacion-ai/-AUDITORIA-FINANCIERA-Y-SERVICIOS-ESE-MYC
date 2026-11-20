"""Orquestación: carga -> parseo -> organización hospital/mes -> validación.

Uso desde Python::

    from rips_auditoria import procesar
    res = procesar(["Hospital_San_Antonio.zip"])
    res.unidades()                       # [("Hospital San Antonio", "2026-05"), ...]
    u = res.unidad("Hospital San Antonio", "2026-05")
    u.datos["AC"]                        # DataFrame de consultas de ese hospital-mes
    u.incidencias                        # incidencias de ese hospital-mes
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .config import cargar_configuracion
from .especificacion import ORDEN_ARCHIVOS, especificacion
from .incidencias import RegistroIncidencias, resumen_incidencias
from .lectura import Envio, agrupar_envios, contexto_desde_ruta, descubrir_archivos
from .parser import parsear_archivo
from .validacion import (
    a_fecha,
    a_numero,
    conciliar_facturas,
    incidencias_conciliacion,
    validar_campos,
    validar_envios,
    validar_periodo_facturas,
)

log = logging.getLogger(__name__)

SIN_PERIODO = "SIN_PERIODO"
COLUMNAS_TRAZA = ["hospital", "periodo", "envio_id", "remision", "carpeta_envio", "archivo", "linea"]


@dataclass
class UnidadAnalisis:
    """Información de un hospital en un mes: la unidad consultable/exportable."""

    hospital: str
    periodo: str
    datos: dict[str, pd.DataFrame]
    incidencias: pd.DataFrame
    envios: pd.DataFrame
    conciliacion: pd.DataFrame

    def resumen(self) -> dict:
        reg = {t: int(len(self.datos.get(t, []))) for t in ORDEN_ARCHIVOS}
        sev = self.incidencias["severidad"].value_counts().to_dict() if not self.incidencias.empty else {}
        conc = self.conciliacion
        return {
            "hospital": self.hospital,
            "periodo": self.periodo,
            "remisiones": int(len(self.envios)),
            "facturas": int(len(conc)),
            "usuarios_unicos": int(self.datos["US"][["tipo_id_usuario", "num_id_usuario"]].drop_duplicates().shape[0])
            if "US" in self.datos and not self.datos["US"].empty else 0,
            "registros_por_archivo": reg,
            "valor_neto_facturado_af": float(conc["valor_neto_af"].fillna(0).sum()) if not conc.empty else 0.0,
            "valor_servicios_rips": float(conc["total_servicios"].sum()) if not conc.empty else 0.0,
            "facturas_con_diferencia": int((conc["estado"] == "DIFERENCIA").sum()) if not conc.empty else 0,
            "incidencias": {k: int(v) for k, v in sev.items()},
        }


@dataclass
class ResultadoProcesamiento:
    datos: dict[str, pd.DataFrame]
    incidencias: pd.DataFrame
    envios: pd.DataFrame
    archivos: pd.DataFrame
    conciliacion: pd.DataFrame
    config: dict = field(default_factory=dict)

    def unidades(self) -> list[tuple[str, str]]:
        if self.envios.empty:
            return []
        pares = self.envios[["hospital", "periodo"]].drop_duplicates()
        return sorted(map(tuple, pares.to_numpy().tolist()))

    def hospitales(self) -> list[str]:
        return sorted({h for h, _ in self.unidades()})

    def periodos(self, hospital: str) -> list[str]:
        return [p for h, p in self.unidades() if h == hospital]

    def unidad(self, hospital: str, periodo: str) -> UnidadAnalisis:
        if (hospital, periodo) not in self.unidades():
            raise KeyError(f"No hay información para {hospital!r} en {periodo!r}. Disponibles: {self.unidades()}")

        def filtrar(df: pd.DataFrame) -> pd.DataFrame:
            if df.empty or "hospital" not in df:
                return df.iloc[0:0]
            return df[(df["hospital"] == hospital) & (df["periodo"] == periodo)].reset_index(drop=True)

        return UnidadAnalisis(
            hospital=hospital,
            periodo=periodo,
            datos={t: filtrar(df) for t, df in self.datos.items()},
            incidencias=filtrar(self.incidencias),
            envios=filtrar(self.envios),
            conciliacion=filtrar(self.conciliacion),
        )

    def resumen_unidades(self) -> pd.DataFrame:
        filas = []
        for h, p in self.unidades():
            r = self.unidad(h, p).resumen()
            fila = {k: v for k, v in r.items() if not isinstance(v, dict)}
            fila.update({f"reg_{k}": v for k, v in r["registros_por_archivo"].items()})
            fila.update({f"inc_{k.lower()}": v for k, v in r["incidencias"].items()})
            filas.append(fila)
        return pd.DataFrame(filas)

    def resumen_incidencias(self) -> pd.DataFrame:
        return resumen_incidencias(self.incidencias)


# ---------------------------------------------------------------------------

def procesar(entradas: list[str | Path] | str | Path, config: dict | None = None,
             hospital: str | None = None) -> ResultadoProcesamiento:
    """Procesa una o varias entradas (TXT, carpetas, ZIP) y devuelve el resultado
    organizado por hospital y mes. ``hospital`` fuerza el nombre del hospital
    para todo lo cargado (útil al cargar TXT sueltos de un único hospital)."""
    if isinstance(entradas, (str, Path)):
        entradas = [entradas]
    cfg = config or cargar_configuracion()
    c_lect, c_org, c_val = cfg["lectura"], cfg["organizacion"], cfg["validacion"]
    inc = RegistroIncidencias(c_val.get("severidades"))

    fuentes = descubrir_archivos(list(entradas), inc)
    envios, inventario = agrupar_envios(fuentes, inc, c_lect["extensiones_ignoradas"], c_lect["preferir_corregidos"])
    log.info("Remisiones encontradas: %d", len(envios))

    partes: dict[str, list[pd.DataFrame]] = defaultdict(list)
    por_envio: dict[str, dict[str, dict]] = {}
    pendientes_inc: list[dict] = []
    for envio in envios:
        por_envio[envio.envio_id] = {}
        for tipo, arch in envio.archivos.items():
            try:
                r = parsear_archivo(arch, envio.envio_id, c_lect["codificaciones"], c_lect["reparar_mojibake"])
            except Exception as exc:  # un archivo dañado no detiene el lote
                log.exception("Error inesperado leyendo %s", arch.ruta)
                inc.agregar("ARCHIVO_ILEGIBLE", f"Error inesperado: {exc}", archivo=arch.ruta,
                            envio_id=envio.envio_id, tipo_archivo=tipo)
                continue
            partes[tipo].append(r.datos)
            pendientes_inc.extend(r.incidencias)
            por_envio[envio.envio_id][tipo] = {"archivo": arch.ruta, "lineas": r.lineas_totales,
                                               "remision": arch.remision}
            inventario.append({
                "archivo": arch.ruta, "tipo_archivo": tipo, "estado": "procesado", "motivo": "",
                "bytes": len(arch.contenido), "envio_id": envio.envio_id, "codificacion": r.codificacion,
                "registros": r.lineas_totales, "registros_validos": r.registros_validos,
                "registros_rechazados": r.registros_rechazados,
            })
    for i in pendientes_inc:
        inc.agregar(**i)

    datos = {t: (pd.concat(partes[t], ignore_index=True) if partes.get(t) else _vacio(t)) for t in ORDEN_ARCHIVOS}

    _asignar_hospital(envios, datos, c_org, hospital, inc)
    _asignar_periodo(envios, datos, c_org["prioridad_periodo"], inc)

    for tipo, df in datos.items():
        validar_campos(tipo, df, inc)
    validar_envios(datos, envios, por_envio, inc, float(c_val["tolerancia_valores"]))

    df_envios = _tabla_envios(envios, por_envio)
    meta = df_envios.set_index("envio_id")[["hospital", "periodo", "remision", "carpeta_envio"]]
    for tipo in datos:
        datos[tipo] = _agregar_traza(datos[tipo], meta)
    validar_periodo_facturas(datos["AF"], inc)

    conc = conciliar_facturas(datos, float(c_val["tolerancia_valores"]))
    incidencias_conciliacion(conc, datos["AF"], inc)
    conc = _con_hospital_periodo(conc, meta)

    df_inc = _ubicar_incidencias(inc.como_dataframe(), meta, df_envios)
    df_inc = _limitar(df_inc, int(c_val.get("max_incidencias_por_regla_archivo") or 0))
    df_arch = pd.DataFrame(inventario)
    if not df_arch.empty:
        df_arch = _con_hospital_periodo(df_arch.reindex(columns=list(df_arch.columns) + (
            [] if "envio_id" in df_arch else ["envio_id"])), meta)
    return ResultadoProcesamiento(datos, df_inc, df_envios, df_arch, conc, cfg)


def _con_hospital_periodo(df: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    m = meta.reindex(df["envio_id"].astype(object).to_numpy())
    out = df.copy()
    out["hospital"] = m["hospital"].to_numpy()
    out["periodo"] = m["periodo"].to_numpy()
    return out


def _vacio(tipo: str) -> pd.DataFrame:
    cols = ["envio_id", "archivo", "linea"] + especificacion(tipo).columnas
    return pd.DataFrame({c: pd.Series(dtype="string") for c in cols})


def _primero(df: pd.DataFrame, envio_id: str, campo: str) -> str | None:
    if df.empty:
        return None
    v = df.loc[df["envio_id"] == envio_id, campo]
    v = v[v.fillna("") != ""]
    return str(v.iloc[0]) if len(v) else None


def _asignar_hospital(envios: list[Envio], datos, c_org, forzado, inc) -> None:
    mapa = {str(k): v for k, v in (c_org.get("hospitales_por_prestador") or {}).items()}
    defecto = c_org.get("hospital_por_defecto")
    for e in envios:
        if forzado:
            e.hospital, e.fuente_hospital = forzado, "parametro"
            continue
        if e.hospital and e.fuente_hospital == "carpeta":
            continue
        cod = _primero(datos["AF"], e.envio_id, "cod_prestador") or _primero(datos["CT"], e.envio_id, "cod_prestador")
        if cod and cod in mapa:
            e.hospital, e.fuente_hospital = mapa[cod], "configuracion"
        elif defecto:
            e.hospital, e.fuente_hospital = defecto, "configuracion"
        elif e.hospital:  # nombre del ZIP
            continue
        else:
            e.hospital, e.fuente_hospital = f"PRESTADOR_{cod or 'DESCONOCIDO'}", "codigo_prestador"
            inc.agregar("HOSPITAL_NO_DETERMINADO", envio_id=e.envio_id, archivo=e.directorio, valor=cod)


def _moda(serie: pd.Series):
    serie = serie.dropna()
    return serie.mode().iloc[0] if not serie.empty else None


def _asignar_periodo(envios: list[Envio], datos, prioridad: list[str], inc) -> None:
    af, ct = datos["AF"], datos["CT"]
    f_af = pd.DataFrame({"envio_id": af["envio_id"], "f": a_fecha(af["fecha_expedicion"])}) if not af.empty else None
    f_ct = pd.DataFrame({"envio_id": ct["envio_id"], "f": a_fecha(ct["fecha_remision"])}) if not ct.empty else None
    grupos_af = dict(tuple(f_af.groupby("envio_id")["f"])) if f_af is not None else {}
    grupos_ct = dict(tuple(f_ct.groupby("envio_id")["f"])) if f_ct is not None else {}
    for e in envios:
        fechas_af = grupos_af.get(e.envio_id, pd.Series(dtype="datetime64[ns]")).dropna()
        fechas_ct = grupos_ct.get(e.envio_id, pd.Series(dtype="datetime64[ns]")).dropna()
        for fuente in prioridad:
            periodo = None
            if fuente == "carpeta" and e.mes_carpeta:
                anio = e.anio_carpeta
                if anio is None and len(fechas_af):
                    mismas = fechas_af[fechas_af.dt.month == e.mes_carpeta]
                    anio = int(_moda((mismas if len(mismas) else fechas_af).dt.year))
                if anio is None and len(fechas_ct):
                    f = fechas_ct.iloc[0]
                    anio = f.year - 1 if f.month < e.mes_carpeta else f.year
                if anio:
                    periodo = f"{anio:04d}-{e.mes_carpeta:02d}"
            elif fuente == "factura" and len(fechas_af):
                periodo = _moda(fechas_af.dt.strftime("%Y-%m"))
            elif fuente == "remision" and len(fechas_ct):
                periodo = fechas_ct.iloc[0].strftime("%Y-%m")
            if periodo:
                e.periodo, e.fuente_periodo = periodo, fuente
                break
    # Remisiones con carpeta de mes pero sin AF ni CT legibles: se toma el año
    # de las demás remisiones del mismo hospital y carpeta de mes.
    anios: dict[tuple, list[str]] = defaultdict(list)
    for e in envios:
        if e.periodo and e.fuente_periodo == "carpeta":
            anios[(e.hospital, e.mes_carpeta)].append(e.periodo)
    for e in envios:
        if not e.periodo and e.mes_carpeta and anios.get((e.hospital, e.mes_carpeta)):
            e.periodo = pd.Series(anios[(e.hospital, e.mes_carpeta)]).mode().iloc[0]
            e.fuente_periodo = "carpeta (año de remisiones vecinas)"
        if not e.periodo:
            e.periodo, e.fuente_periodo = SIN_PERIODO, None
            inc.agregar("PERIODO_NO_DETERMINADO", envio_id=e.envio_id, archivo=e.directorio)


def _tabla_envios(envios: list[Envio], por_envio) -> pd.DataFrame:
    filas = []
    for e in envios:
        info = por_envio.get(e.envio_id, {})
        fila = {
            "hospital": e.hospital, "periodo": e.periodo, "envio_id": e.envio_id, "remision": e.remision,
            "carpeta_envio": e.carpeta_envio, "directorio": e.directorio,
            "fuente_hospital": e.fuente_hospital, "fuente_periodo": e.fuente_periodo,
            "archivos": ",".join(t for t in ORDEN_ARCHIVOS if t in e.archivos),
        }
        fila.update({f"reg_{t}": info[t]["lineas"] for t in ORDEN_ARCHIVOS if t in info})
        filas.append(fila)
    cols = ["hospital", "periodo", "envio_id", "remision", "carpeta_envio", "directorio", "fuente_hospital",
            "fuente_periodo", "archivos"] + [f"reg_{t}" for t in ORDEN_ARCHIVOS]
    df = pd.DataFrame(filas).reindex(columns=cols)
    for t in ORDEN_ARCHIVOS:
        df[f"reg_{t}"] = pd.to_numeric(df[f"reg_{t}"], errors="coerce").fillna(0).astype(int)
    return df


def _agregar_traza(df: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    m = meta.reindex(df["envio_id"].to_numpy())
    out = df.copy()
    for c in ["hospital", "periodo", "remision", "carpeta_envio"]:
        out[c] = m[c].to_numpy()
    campos = [c for c in df.columns if c not in ("envio_id", "archivo", "linea")]
    return out[COLUMNAS_TRAZA + campos]


def _ubicar_incidencias(df: pd.DataFrame, meta: pd.DataFrame, envios: pd.DataFrame) -> pd.DataFrame:
    """Completa hospital y período de cada incidencia. Las que no pertenecen a
    una remisión (p. ej. ZIP corrupto) se ubican por la ruta del archivo."""
    if df.empty:
        return df
    con_envio = df["envio_id"].notna() & df["envio_id"].isin(meta.index)
    m = meta.reindex(df.loc[con_envio, "envio_id"].to_numpy())
    df.loc[con_envio, "hospital"] = m["hospital"].to_numpy()
    df.loc[con_envio, "periodo"] = m["periodo"].to_numpy()
    sin = ~con_envio
    if sin.any():
        # período más frecuente de las remisiones del mismo hospital y carpeta de mes
        for idx in df.index[sin]:
            ruta = str(df.at[idx, "archivo"] or "")
            ctx = contexto_desde_ruta(ruta)
            h = ctx["hospital"]
            cand = envios
            if h:
                cand = cand[cand["hospital"] == h]
            if ctx["mes"]:
                cand = cand[cand["periodo"].astype(str).str.endswith(f"-{ctx['mes']:02d}")]
            df.at[idx, "hospital"] = h or (envios["hospital"].iloc[0] if len(envios) else None)
            df.at[idx, "periodo"] = _moda(cand["periodo"]) if len(cand) else SIN_PERIODO
    return df


def _limitar(df: pd.DataFrame, maximo: int) -> pd.DataFrame:
    if maximo <= 0 or df.empty:
        return df
    orden = df.groupby(["regla", "archivo"], dropna=False).cumcount()
    return df[orden < maximo].reset_index(drop=True)


__all__ = ["procesar", "ResultadoProcesamiento", "UnidadAnalisis", "a_numero"]

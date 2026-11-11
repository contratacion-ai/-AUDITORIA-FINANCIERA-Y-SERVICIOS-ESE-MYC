"""Gestión de cargues: preparar (leer + validar), confirmar, reemplazar y eliminar.

El flujo siempre es en dos pasos para que nadie cargue a ciegas:

1. ``preparar`` lee el archivo, lo valida y calcula qué registros vigentes sustituiría.
   No escribe nada.
2. ``confirmar`` guarda el cargue, marca como *reemplazados* los registros vigentes
   con la misma clave (ítem, categoría, periodo, sede) y deja todo en la bitácora.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from dataclasses import dataclass, field

import pandas as pd

from . import config, db
from .catalogo import Catalogo
from .config import DESPLAZAMIENTOS, ITEMS
from .extraccion import extraer_archivo
from .extraccion.comunes import ADVERTENCIA, ERROR, INFO, Incidencia, Tabla
from .validacion import Opciones, Resultado, validar

MAX_INCIDENCIAS_GUARDADAS = 5000


@dataclass
class Preparacion:
    archivo: str
    contenido: bytes
    sha256: str
    opciones: Opciones
    tablas: list = field(default_factory=list)
    resultados: dict = field(default_factory=dict)     # item -> DataFrame de registros válidos
    incidencias: list = field(default_factory=list)
    inc_extraccion: list = field(default_factory=list)   # las de lectura, para poder revalidar
    filas_leidas: dict = field(default_factory=dict)
    rechazadas: dict = field(default_factory=dict)
    valor_rechazado: float = 0.0
    reemplazos: pd.DataFrame = field(default_factory=pd.DataFrame)   # lo que quedaría sustituido
    cargas_previas: pd.DataFrame = field(default_factory=pd.DataFrame)  # mismo archivo ya cargado

    @property
    def total_validas(self) -> int:
        return sum(len(d) for d in self.resultados.values())

    def incidencias_df(self) -> pd.DataFrame:
        cols = ["severidad", "hoja", "fila", "columna", "motivo", "valor", "archivo"]
        return pd.DataFrame([i.como_dict() for i in self.incidencias], columns=["archivo", "hoja", "fila", "columna",
                                                                               "severidad", "motivo", "valor"])[cols]

    def conteo(self, severidad: str) -> int:
        return sum(1 for i in self.incidencias if i.severidad == severidad)


def preparar(nombre: str, contenido: bytes, item: str | None, opciones: Opciones, ruta=None) -> Preparacion:
    prep = Preparacion(nombre, contenido, hashlib.sha256(contenido).hexdigest(), opciones)
    tablas, inc = extraer_archivo(nombre, contenido, item)
    prep.tablas = tablas
    prep.incidencias = list(inc)
    prep.inc_extraccion = list(inc)
    validar_tablas(prep, tablas, ruta)
    return prep


def validar_tablas(prep: Preparacion, tablas: list[Tabla], ruta=None, conservar: list | None = None) -> None:
    """(Re)valida las tablas de una preparación; se usa también tras corregir datos en pantalla."""
    cat = Catalogo(ruta)
    params = db.parametros(ruta)
    prep.resultados, prep.filas_leidas, prep.rechazadas, prep.valor_rechazado = {}, {}, {}, 0.0
    if conservar is not None:
        prep.incidencias = list(conservar)
    for t in tablas:
        r: Resultado = validar(t, cat, params, prep.opciones)
        for i in r.incidencias:
            i.archivo = prep.archivo
        prep.incidencias += r.incidencias
        prep.filas_leidas[t.item] = prep.filas_leidas.get(t.item, 0) + r.filas_leidas
        prep.rechazadas[t.item] = prep.rechazadas.get(t.item, 0) + r.rechazadas
        prep.valor_rechazado += r.valor_rechazado
        if len(r.registros):
            r.registros["_formato"] = t.formato
            previo = prep.resultados.get(t.item)
            prep.resultados[t.item] = r.registros if previo is None else pd.concat([previo, r.registros],
                                                                                   ignore_index=True)
    prep.reemplazos = _calcular_reemplazos(prep, ruta)
    prep.cargas_previas = db.consulta(
        "SELECT cargue_id, fecha_carga, usuario, periodos FROM cargues WHERE archivo_sha256=? ORDER BY cargue_id",
        (prep.sha256,), ruta)


def _claves(item: str, df: pd.DataFrame) -> pd.DataFrame:
    if item == DESPLAZAMIENTOS:
        return df[["periodo", "sede_id"]].drop_duplicates().assign(categoria="")
    return df[["categoria", "periodo", "sede_id"]].drop_duplicates()


def _calcular_reemplazos(prep: Preparacion, ruta=None) -> pd.DataFrame:
    partes = []
    for item, df in prep.resultados.items():
        claves = _claves(item, df)
        if item == DESPLAZAMIENTOS:
            vig = db.consulta("SELECT cargue_id, '' AS categoria, periodo, sede_id, COUNT(*) AS registros, 0.0 AS valor "
                              "FROM desplazamientos WHERE estado='vigente' GROUP BY 1,2,3,4", ruta=ruta)
        else:
            vig = db.consulta("SELECT cargue_id, categoria, periodo, sede_id, COUNT(*) AS registros, SUM(valor) AS valor "
                              "FROM registros WHERE estado='vigente' AND item=? GROUP BY 1,2,3,4", (item,), ruta)
        m = vig.merge(claves, on=["categoria", "periodo", "sede_id"])
        if len(m):
            partes.append(m.assign(item=item))
    if not partes:
        return pd.DataFrame(columns=["item", "cargue_id", "categoria", "periodo", "sede_id", "registros", "valor"])
    return pd.concat(partes, ignore_index=True)


def confirmar(prep: Preparacion, usuario: str, modo: str = "reemplazar", observacion: str = "", ruta=None) -> list[int]:
    """Guarda el cargue. Devuelve los identificadores creados (uno por ítem del archivo)."""
    if modo not in ("reemplazar", "complementar"):
        raise ValueError("modo debe ser 'reemplazar' o 'complementar'")
    if prep.total_validas == 0:
        raise ValueError("No hay filas válidas para cargar.")
    op = prep.opciones
    dir_archivos = (Path(ruta).parent / "archivos_cargados") if ruta else config.DIR_ARCHIVOS
    dir_archivos.mkdir(parents=True, exist_ok=True)
    seguro = re.sub(r"[^A-Za-z0-9._-]+", "_", prep.archivo)[-120:]
    destino = dir_archivos / f"{prep.sha256[:12]}_{seguro}"
    if not destino.exists():
        destino.write_bytes(prep.contenido)   # copia del archivo original, para auditoría
    ruta_archivo = str(destino)

    ids = []
    with db.transaccion(ruta) as con:
        for item, df in prep.resultados.items():
            periodos = sorted(df["periodo"].unique())
            formatos = ", ".join(sorted(df["_formato"].unique()))
            n_adv = sum(1 for i in prep.incidencias if i.severidad == ADVERTENCIA)
            valor_total = float(df["valor"].sum()) if item != DESPLAZAMIENTOS else 0.0
            cur = con.execute(
                """INSERT INTO cargues(item, formato, archivo_nombre, archivo_sha256, archivo_ruta, fecha_carga, usuario,
                       ambito, periodo_declarado, periodos, modo, filas_leidas, filas_cargadas, filas_rechazadas,
                       advertencias, valor_total, observacion)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item, formatos, prep.archivo, prep.sha256, ruta_archivo, db.ahora(), usuario,
                 op.hospital or "Central", op.periodo if op.alcance == "periodo" else None, ", ".join(periodos), modo,
                 prep.filas_leidas.get(item, 0), len(df), prep.rechazadas.get(item, 0), n_adv, valor_total,
                 observacion or None))
            cid = cur.lastrowid
            ids.append(cid)

            reemplazados = []
            if modo == "reemplazar":
                reemplazados = _reemplazar(con, item, df, cid)
            _insertar(con, item, df, cid)
            con.execute("UPDATE cargues SET reemplazo_de=? WHERE cargue_id=?",
                        (", ".join(f"#{c}" for c in reemplazados) or None, cid))
            for i in prep.incidencias[:MAX_INCIDENCIAS_GUARDADAS]:
                con.execute("INSERT INTO incidencias(cargue_id, hoja, fila, columna, severidad, motivo, valor) "
                            "VALUES (?,?,?,?,?,?,?)", (cid, i.hoja, i.fila, i.columna, i.severidad, i.motivo, i.valor))
            detalle = (f"{ITEMS.get(item, 'Desplazamientos')}: archivo «{prep.archivo}», periodos {', '.join(periodos)}, "
                       f"{len(df)} registros cargados, {prep.rechazadas.get(item, 0)} filas rechazadas.")
            if reemplazados:
                detalle += " Reemplazó registros de los cargues " + ", ".join(f"#{c}" for c in reemplazados) + "."
            db.registrar_evento(con, usuario, "cargue", detalle, cid)
    return ids


def _reemplazar(con, item: str, df: pd.DataFrame, cid: int) -> list[int]:
    tabla = "desplazamientos" if item == DESPLAZAMIENTOS else "registros"
    claves = _claves(item, df)
    con.execute("CREATE TEMP TABLE IF NOT EXISTS _claves(categoria TEXT, periodo TEXT, sede_id TEXT)")
    con.execute("DELETE FROM _claves")
    con.executemany("INSERT INTO _claves VALUES (?,?,?)",
                    claves[["categoria", "periodo", "sede_id"]].itertuples(index=False, name=None))
    if item == DESPLAZAMIENTOS:
        cond = ("estado='vigente' AND EXISTS (SELECT 1 FROM _claves k WHERE k.periodo=desplazamientos.periodo "
                "AND k.sede_id=desplazamientos.sede_id)")
        params: tuple = ()
    else:
        cond = ("estado='vigente' AND item=? AND EXISTS (SELECT 1 FROM _claves k WHERE k.periodo=registros.periodo "
                "AND k.sede_id=registros.sede_id AND k.categoria=registros.categoria)")
        params = (item,)
    previos = [r[0] for r in con.execute(f"SELECT DISTINCT cargue_id FROM {tabla} WHERE {cond}", params)]
    con.execute(f"UPDATE {tabla} SET estado='reemplazado', sustituido_por=?, fecha_estado=? WHERE {cond}",
                (cid, db.ahora(), *params))
    return sorted(previos)


def _insertar(con, item: str, df: pd.DataFrame, cid: int) -> None:
    df = df.astype(object).where(df.notna(), None)
    if item == DESPLAZAMIENTOS:
        cols = ["periodo", "fecha", "placa", "sede_id", "km_inicial", "km_final", "km", "motivo", "origen", "destino",
                "hoja", "fila"]
        tabla = "desplazamientos"
    else:
        cols = ["item", "categoria", "concepto", "periodo", "fecha", "sede_id", "referencia", "descripcion", "cantidad",
                "unidad", "valor", "total_a_pagar", "proveedor", "documento", "odometro", "observacion", "hoja", "fila"]
        tabla = "registros"
    sql = f"INSERT INTO {tabla}(cargue_id, {', '.join(cols)}) VALUES ({', '.join('?' * (len(cols) + 1))})"
    con.executemany(sql, [(cid, *fila) for fila in df[cols].itertuples(index=False, name=None)])


# --------------------------------------------------------------------------- #
# Consulta y eliminación
# --------------------------------------------------------------------------- #
def listar(ruta=None, hospital: str | None = None) -> pd.DataFrame:
    df = db.consulta("""
        SELECT c.*,
               COALESCE(r.vigentes,0)+COALESCE(d.vigentes,0) AS vigentes,
               COALESCE(r.reemplazados,0)+COALESCE(d.reemplazados,0) AS reemplazados,
               COALESCE(r.eliminados,0)+COALESCE(d.eliminados,0) AS eliminados
        FROM cargues c
        LEFT JOIN (SELECT cargue_id, SUM(estado='vigente') vigentes, SUM(estado='reemplazado') reemplazados,
                          SUM(estado='eliminado') eliminados FROM registros GROUP BY 1) r USING(cargue_id)
        LEFT JOIN (SELECT cargue_id, SUM(estado='vigente') vigentes, SUM(estado='reemplazado') reemplazados,
                          SUM(estado='eliminado') eliminados FROM desplazamientos GROUP BY 1) d USING(cargue_id)
        ORDER BY c.cargue_id DESC""", ruta=ruta)
    if hospital:
        df = df[df["ambito"] == hospital]

    def estado(f):
        if f["vigentes"] and not (f["reemplazados"] or f["eliminados"]):
            return "Vigente"
        if f["vigentes"]:
            return "Vigente en parte"
        if f["eliminados"] and not f["reemplazados"]:
            return "Eliminado"
        return "Reemplazado"

    df["estado"] = df.apply(estado, axis=1) if len(df) else []
    df["item_nombre"] = df["item"].map(lambda i: ITEMS.get(i, "Desplazamientos"))
    return df.reset_index(drop=True)


def eliminar(cargue_id: int, usuario: str, motivo: str, restaurar: bool = False, hospital: str | None = None,
             ruta=None) -> str:
    """Retira del historial vigente los registros de un cargue (no los borra: quedan como 'eliminado').

    ``restaurar``: vuelve a dejar vigentes los registros que este cargue había reemplazado,
    siempre que nadie más haya cargado después datos para la misma sede y periodo.
    """
    if not motivo or not motivo.strip():
        raise ValueError("Indique el motivo de la eliminación; queda en la bitácora.")
    with db.transaccion(ruta) as con:
        c = con.execute("SELECT * FROM cargues WHERE cargue_id=?", (cargue_id,)).fetchone()
        if c is None:
            raise ValueError(f"No existe el cargue #{cargue_id}.")
        if hospital and c["ambito"] != hospital:
            raise PermissionError("Solo puede eliminar cargues de su propia sede.")
        ahora = db.ahora()
        n = 0
        restaurados = 0
        for tabla, clave in (("registros", "item, categoria, periodo, sede_id"), ("desplazamientos", "periodo, sede_id")):
            n += con.execute(f"UPDATE {tabla} SET estado='eliminado', fecha_estado=? WHERE cargue_id=? AND estado='vigente'",
                             (ahora, cargue_id)).rowcount
            if restaurar:
                iguales = " AND ".join(f"v.{k.strip()}=t.{k.strip()}" for k in clave.split(","))
                pk = "registro_id" if tabla == "registros" else "desplazamiento_id"
                ids = [r[0] for r in con.execute(
                    f"""SELECT t.{pk} FROM {tabla} t WHERE t.sustituido_por=? AND t.estado='reemplazado'
                        AND NOT EXISTS (SELECT 1 FROM {tabla} v WHERE v.estado='vigente' AND {iguales})""",
                    (cargue_id,))]
                con.executemany(f"UPDATE {tabla} SET estado='vigente', sustituido_por=NULL, fecha_estado=? WHERE {pk}=?",
                                [(ahora, i) for i in ids])
                restaurados += len(ids)
        detalle = f"Eliminado el cargue #{cargue_id} («{c['archivo_nombre']}»): {n} registros retirados. Motivo: {motivo.strip()}"
        if restaurar:
            detalle += f" Se restauraron {restaurados} registros que este cargue había reemplazado."
        db.registrar_evento(con, usuario, "eliminacion", detalle, cargue_id)
    return detalle


def incidencias_de(cargue_id: int, ruta=None) -> pd.DataFrame:
    return db.consulta("SELECT severidad, hoja, fila, columna, motivo, valor FROM incidencias WHERE cargue_id=? "
                       "ORDER BY incidencia_id", (cargue_id,), ruta)


def bitacora(ruta=None, limite: int = 500) -> pd.DataFrame:
    return db.consulta("SELECT fecha, usuario, accion, cargue_id, detalle FROM eventos ORDER BY evento_id DESC LIMIT ?",
                       (limite,), ruta)

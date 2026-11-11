"""Base de datos SQLite: historial mensual, catálogo, cargues y bitácora.

Reglas de diseño:

* Nada se borra físicamente. Un registro reemplazado o eliminado cambia de ``estado``
  y conserva el cargue que lo sustituyó: el historial siempre se puede reconstruir.
* ``cargues`` guarda qué archivo entró (nombre y huella SHA-256), cuándo, quién lo
  cargó y a qué periodos corresponde, sin importar la fecha en que se recibió.
* ``eventos`` es la bitácora: cada carga, reemplazo, eliminación o cambio de catálogo.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from . import config

ESQUEMA_SQL = """
CREATE TABLE IF NOT EXISTS sedes (
    sede_id      TEXT PRIMARY KEY,
    hospital     TEXT NOT NULL,          -- hospital o centro del que depende la sede
    sede         TEXT NOT NULL,
    municipio    TEXT,
    tipo         TEXT,
    es_principal INTEGER NOT NULL DEFAULT 0,
    activo       INTEGER NOT NULL DEFAULT 1,
    validado     INTEGER NOT NULL DEFAULT 1,
    observacion  TEXT
);
CREATE TABLE IF NOT EXISTS codigos (
    tipo        TEXT NOT NULL,           -- CENTRO_COSTO | CUENTA_ENERGIA | CUENTA_ACUEDUCTO | ALIAS | ...
    codigo      TEXT NOT NULL,           -- normalizado
    codigo_original TEXT,
    sede_id     TEXT NOT NULL REFERENCES sedes(sede_id),
    descripcion TEXT,
    validado    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (tipo, codigo)
);
CREATE TABLE IF NOT EXISTS vehiculos (
    placa       TEXT PRIMARY KEY,
    sede_id     TEXT NOT NULL REFERENCES sedes(sede_id),
    descripcion TEXT,
    clase       TEXT,
    combustible TEXT,
    rendimiento_esperado REAL,           -- km por galón
    validado    INTEGER NOT NULL DEFAULT 1,
    observacion TEXT
);
CREATE TABLE IF NOT EXISTS cargues (
    cargue_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    item             TEXT NOT NULL,
    formato          TEXT,
    archivo_nombre   TEXT NOT NULL,
    archivo_sha256   TEXT NOT NULL,
    archivo_ruta     TEXT,
    fecha_carga      TEXT NOT NULL,
    usuario          TEXT NOT NULL,
    ambito           TEXT,                -- hospital del usuario que cargó, o 'Central'
    periodo_declarado TEXT,
    periodos         TEXT,                -- periodos con registros cargados
    modo             TEXT NOT NULL,       -- reemplazar | complementar
    filas_leidas     INTEGER DEFAULT 0,
    filas_cargadas   INTEGER DEFAULT 0,
    filas_rechazadas INTEGER DEFAULT 0,
    advertencias     INTEGER DEFAULT 0,
    valor_total      REAL DEFAULT 0,
    reemplazo_de     TEXT,                -- cargues cuyos registros sustituyó
    observacion      TEXT
);
CREATE TABLE IF NOT EXISTS registros (
    registro_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    cargue_id     INTEGER NOT NULL REFERENCES cargues(cargue_id),
    item          TEXT NOT NULL,
    categoria     TEXT NOT NULL,
    concepto      TEXT,
    periodo       TEXT NOT NULL,          -- AAAA-MM
    fecha         TEXT,
    sede_id       TEXT NOT NULL REFERENCES sedes(sede_id),
    referencia    TEXT,                   -- cuenta, placa o equipo
    descripcion   TEXT,
    cantidad      REAL,
    unidad        TEXT,
    valor         REAL NOT NULL,
    total_a_pagar REAL,
    proveedor     TEXT,
    documento     TEXT,
    odometro      REAL,
    observacion   TEXT,
    hoja          TEXT,
    fila          INTEGER,
    estado        TEXT NOT NULL DEFAULT 'vigente',   -- vigente | reemplazado | eliminado
    sustituido_por INTEGER,               -- cargue que lo reemplazó
    fecha_estado  TEXT
);
CREATE INDEX IF NOT EXISTS ix_registros_clave ON registros(item, categoria, periodo, sede_id, estado);
CREATE INDEX IF NOT EXISTS ix_registros_cargue ON registros(cargue_id);
CREATE TABLE IF NOT EXISTS desplazamientos (
    desplazamiento_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cargue_id   INTEGER NOT NULL REFERENCES cargues(cargue_id),
    periodo     TEXT NOT NULL,
    fecha       TEXT,
    placa       TEXT NOT NULL,
    sede_id     TEXT NOT NULL REFERENCES sedes(sede_id),
    km_inicial  REAL,
    km_final    REAL,
    km          REAL,
    motivo      TEXT,
    origen      TEXT,
    destino     TEXT,
    hoja        TEXT,
    fila        INTEGER,
    estado      TEXT NOT NULL DEFAULT 'vigente',
    sustituido_por INTEGER,
    fecha_estado TEXT
);
CREATE INDEX IF NOT EXISTS ix_despl_clave ON desplazamientos(periodo, sede_id, estado);
CREATE TABLE IF NOT EXISTS incidencias (
    incidencia_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cargue_id  INTEGER NOT NULL REFERENCES cargues(cargue_id),
    hoja TEXT, fila INTEGER, columna TEXT, severidad TEXT, motivo TEXT, valor TEXT
);
CREATE INDEX IF NOT EXISTS ix_incidencias_cargue ON incidencias(cargue_id);
CREATE TABLE IF NOT EXISTS eventos (
    evento_id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha     TEXT NOT NULL,
    usuario   TEXT NOT NULL,
    accion    TEXT NOT NULL,
    cargue_id INTEGER,
    detalle   TEXT
);
CREATE TABLE IF NOT EXISTS usuarios (
    usuario  TEXT PRIMARY KEY,
    nombre   TEXT,
    rol      TEXT NOT NULL,               -- central | sede
    hospital TEXT,                        -- ámbito del rol 'sede'
    sal      TEXT NOT NULL,
    clave    TEXT NOT NULL,
    activo   INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS parametros (
    clave TEXT PRIMARY KEY, valor REAL NOT NULL, descripcion TEXT
);
"""


def ahora() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def conectar(ruta: Path | str | None = None) -> sqlite3.Connection:
    ruta = Path(ruta) if ruta else config.RUTA_BD
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta, timeout=30)
    con.row_factory = sqlite3.Row
    # WAL: varios equipos pueden consultar mientras uno carga.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def inicializar(ruta: Path | str | None = None, con_catalogo_inicial: bool = True) -> None:
    """Crea las tablas si no existen y carga parámetros y catálogo iniciales."""
    with transaccion(ruta) as con:
        con.executescript(ESQUEMA_SQL)
        for clave, valor, desc in config.PARAMETROS_DEFECTO:
            con.execute("INSERT OR IGNORE INTO parametros(clave, valor, descripcion) VALUES (?,?,?)",
                        (clave, valor, desc))
        vacio = con.execute("SELECT COUNT(*) FROM sedes").fetchone()[0] == 0
    if vacio and con_catalogo_inicial and config.DIR_CATALOGO_INICIAL.exists():
        from . import catalogo
        catalogo.cargar_inicial(ruta)


@contextmanager
def transaccion(ruta: Path | str | None = None):
    con = conectar(ruta)
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def consulta(sql: str, params: tuple | list = (), ruta: Path | str | None = None) -> pd.DataFrame:
    con = conectar(ruta)
    try:
        return pd.read_sql_query(sql, con, params=list(params))
    finally:
        con.close()


def parametros(ruta: Path | str | None = None) -> dict[str, float]:
    p = {c: v for c, v, _ in config.PARAMETROS_DEFECTO}
    try:
        df = consulta("SELECT clave, valor FROM parametros", ruta=ruta)
        p.update(dict(zip(df["clave"], df["valor"])))
    except Exception:
        pass
    return p


def registrar_evento(con: sqlite3.Connection, usuario: str, accion: str, detalle: str = "",
                     cargue_id: int | None = None) -> None:
    con.execute("INSERT INTO eventos(fecha, usuario, accion, cargue_id, detalle) VALUES (?,?,?,?,?)",
                (ahora(), usuario, accion, cargue_id, detalle))

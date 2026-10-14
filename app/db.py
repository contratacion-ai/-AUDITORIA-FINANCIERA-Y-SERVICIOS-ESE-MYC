"""Persistencia (SQLite), parámetros de auditoría, identidad del documento y consulta a DataFrame.

Versión 4.0 — Preparado por Winston Gerlein Gutiérrez Pastrana, Profesional de Costos.
Las bases creadas con versiones anteriores se migran automáticamente (``_migrar``) sin perder datos.
"""
import os, sqlite3
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("AUDIT_DATA_DIR", os.path.join(BASE, "data"))
UPLOADS = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOADS, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "auditoria_v2.db")

# ── Identidad profesional (se usa en portada, encabezados y pie de cada hoja del libro de salida, plantillas y dashboard) ──
AUTOR = "Winston Gerlein Gutiérrez Pastrana"
CARGO = "Profesional de Costos"
IDENTIDAD = f"{AUTOR} – {CARGO}"
INSTITUCION = "ESE Departamental de Primer Nivel Moreno y Clavijo"
NIT = "900034131"
VERSION = "4.0"
# Paleta sobria y coherente (hex sin #) para Excel y dashboard
AZUL, AZUL_MED, AZUL_CLARO, GRIS, GRIS_CLARO = "1F4E79", "2E75B6", "DDEBF7", "595959", "F2F2F2"
FUENTE = "Calibri"

UMBRAL_OK, UMBRAL_ALERTA = 0.02, 0.05      # |diferencia| / base: <=2% OK, <=5% ALERTA, >5% CRÍTICO
NT_EXCESO, NT_BAJA = 0.20, -0.50           # cápita: eventos RIPS vs eventos de la nota técnica
SEDES = ["CENTRO DE SALUD JESUS CORONEL", "CENTRO DE SALUD PANAMA DE ARAUCA", "HOSPITAL SAN ANTONIO", "HOSPITAL SAN FRANCISCO",
         "HOSPITAL SAN JOSE", "HOSPITAL SAN JUAN DE DIOS", "HOSPITAL SAN LORENZO", "HOSPITAL SAN RICARDO PAMPURI", "SEDE ADMINISTRATIVA"]
HOM_EPS = [("NUEVA", "NUEVA EMPRESA PROMOTORA DE SALUD"), ("SANITAS", "ENTIDAD PROMOTORA DE SALUD SANITAS S A S"),
           ("COOSALUD", "COOSALUD ENTIDAD PROMOTORA DE SALUD S A"), ("FOMAG", "FIDEICOMISOS PATRIMONIOS AUTONOMOS FIDUCIARIA LA PREVISORA SA"),
           ("PREVISORA", "FIDEICOMISOS PATRIMONIOS AUTONOMOS FIDUCIARIA LA PREVISORA SA"), ("FAMISANAR", "ENTIDAD PROMOTORA DE SALUD FAMISANAR S A S"),
           ("CAPRESOCA", "CAPRESOCA EPS")]   # alias → nombre EPS tal como sale de Software 360 (editable en /api/homologacion/eps)

TOL_PRODUCCION = 0.10                      # costos: diferencia aceptable entre producción (costos) y cantidades facturadas/RIPS
TOL_COSTO_UNIT = 0.02                      # costos: consistencia costo_unitario x cantidad = costo total

REGLAS = {  # reglas de auditoría de líneas de facturación
    "R01": "Línea sin número de factura (posible no radicada)",
    "R02": "Valor total en cero o negativo",
    "R03": "Valor total distinto de valor actividad x cantidad",
    "R04": "Cantidad en cero o negativa",
    "R05": "Sin código CUPS ni de medicamento",
    "R06": "Fecha de facturación fuera del mes de la hoja",
    "R07": "Factura asociada a más de un contrato",
    "R08": "Contrato no parametrizado en PARAM CONTRATO",
    "R09": "Servicio no parametrizado en PARAM SERVICIO",
}

DDL = """
CREATE TABLE IF NOT EXISTS carga(id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT, mes TEXT, archivo TEXT,
  fecha TEXT, estado TEXT, filas INTEGER DEFAULT 0, mensaje TEXT);
CREATE TABLE IF NOT EXISTS fact(carga_id INT, mes TEXT, eps TEXT, eps_nombre TEXT, contrato TEXT, modalidad TEXT, sede TEXT,
  servicio TEXT, ambito TEXT, regimen TEXT, cups TEXT, cantidad REAL, valor REAL, lineas INT, origen_cups TEXT);
CREATE TABLE IF NOT EXISTS linea(carga_id INT, mes TEXT, fecha TEXT, servicio TEXT, agenda TEXT, regulado TEXT, cod_proc TEXT, cod_med TEXT,
  actividad TEXT, diagnostico TEXT, factura TEXT, valor_actividad REAL, tipo_paciente TEXT, regimen TEXT, modalidad TEXT, cantidad REAL, valor REAL);
CREATE TABLE IF NOT EXISTS dim(carga_id INT, mes TEXT, dim TEXT, item TEXT, sede TEXT, modalidad TEXT, cantidad REAL, valor REAL);
CREATE TABLE IF NOT EXISTS factura(carga_id INT, mes TEXT, factura TEXT, eps TEXT, eps_nombre TEXT, contrato TEXT, modalidad TEXT, sede TEXT,
  n_contratos INT, valor REAL, lineas INT);
CREATE TABLE IF NOT EXISTS hallazgo(carga_id INT, mes TEXT, contrato TEXT, regla TEXT, lineas INT, valor REAL);
CREATE TABLE IF NOT EXISTS rips(carga_id INT, mes TEXT, eps TEXT, nit_eps TEXT, contrato TEXT, cucon TEXT, modalidad TEXT, sede TEXT,
  servicio TEXT, cups TEXT, factura TEXT, cantidad REAL, valor REAL, descripcion TEXT);
CREATE TABLE IF NOT EXISTS rips_factura(carga_id INT, mes TEXT, factura TEXT, factura_rips TEXT, eps TEXT, contrato TEXT, cucon TEXT,
  modalidad TEXT, sede TEXT, valor_fev REAL, servicios REAL, validacion TEXT);
CREATE TABLE IF NOT EXISTS costo(carga_id INT, mes TEXT, sede TEXT, servicio TEXT, cups TEXT, concepto TEXT, cantidad REAL, valor REAL, tipo_costo TEXT);
CREATE TABLE IF NOT EXISTS costo_unitario(carga_id INT, cups TEXT, actividad TEXT, contrato TEXT, modalidad TEXT, eps TEXT, costo_unitario REAL, mes TEXT);
CREATE TABLE IF NOT EXISTS carga_validacion(carga_id INT, fila INT, campo TEXT, severidad TEXT, mensaje TEXT, valor TEXT);
CREATE TABLE IF NOT EXISTS regla_actividad(mes TEXT, actividad_norm TEXT, actividad TEXT, cups TEXT, lineas INT, PRIMARY KEY(mes, actividad_norm, cups));
CREATE TABLE IF NOT EXISTS nota(carga_id INT, eps TEXT, modalidad TEXT, contrato TEXT, cups TEXT, descripcion TEXT, categoria TEXT,
  tarifa REAL, eventos_mes REAL, tope_valor REAL, vigencia TEXT);
CREATE TABLE IF NOT EXISTS param_contrato(contrato TEXT PRIMARY KEY, modalidad TEXT, sede TEXT, servicio TEXT);
CREATE TABLE IF NOT EXISTS param_servicio(servicio TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS hom_eps(alias TEXT PRIMARY KEY, eps TEXT);
CREATE TABLE IF NOT EXISTS hom_codigo(codigo_rips TEXT PRIMARY KEY, codigo_360 TEXT, descripcion TEXT);
CREATE INDEX IF NOT EXISTS i_fact ON fact(mes); CREATE INDEX IF NOT EXISTS i_fac ON factura(mes, factura);
CREATE INDEX IF NOT EXISTS i_rips ON rips(mes); CREATE INDEX IF NOT EXISTS i_ripsf ON rips_factura(mes, factura);
CREATE INDEX IF NOT EXISTS i_val ON carga_validacion(carga_id); CREATE INDEX IF NOT EXISTS i_cu ON costo_unitario(cups);
CREATE INDEX IF NOT EXISTS i_dim ON dim(mes, dim); CREATE INDEX IF NOT EXISTS i_lin ON linea(mes);
"""

def conn():
    c = sqlite3.connect(DB_PATH, timeout=120)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

# Columnas agregadas después de la v2/v3: se añaden con ALTER TABLE si la base ya existía
COLUMNAS_NUEVAS = {"fact": [("origen_cups", "TEXT")], "rips": [("descripcion", "TEXT")], "costo": [("tipo_costo", "TEXT")]}

def _migrar(c):
    """Agrega las columnas nuevas a bases creadas con versiones anteriores (idempotente)."""
    for tabla, cols in COLUMNAS_NUEVAS.items():
        existentes = {r[1] for r in c.execute(f"PRAGMA table_info({tabla})")}
        for nombre, tipo in cols:
            if nombre not in existentes: c.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo}")

def init():
    with conn() as c:
        c.executescript(DDL)
        _migrar(c)
        c.executemany("INSERT OR IGNORE INTO hom_eps VALUES(?,?)", HOM_EPS)

def df(sql, params=()):
    with conn() as c:
        return pd.read_sql_query(sql, c, params=params)

"""Persistencia (SQLite), parámetros de auditoría, identidad del documento y consulta a DataFrame.

Versión 5.0 — Preparado por Winston Gerlein Gutiérrez Pastrana, Profesional de Costos.
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
VERSION = "5.0"
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

# ── v5: facturas generadas (encabezado de factura de Software 360) y medicamentos/insumos ──
PLAZO_RADICACION = 22                      # días hábiles entre expedición y radicación (plazo interno parametrizable: ajústelo a la norma/contrato vigente)
DIAS_FACTURACION_TARDIA = 30               # días calendario entre egreso (o ingreso) y expedición de la factura
TOL_PRECIO = 0.10                          # medicamentos/insumos: variación máxima aceptable del valor unitario de un mismo código
TOL_DISPENSACION = 0.05                    # farmacia: diferencia aceptable entre cantidad dispensada y facturada
ABC_A, ABC_B = 0.80, 0.95
META_GLOSA, META_RADICACION = 0.03, 0.95    # metas internas sugeridas para el informe gerencial (ajustables)                  # cortes de la clasificación ABC (Pareto) por valor acumulado

REGLAS_FG = {  # reglas de auditoría de facturas generadas (encabezado)
    "F01": "Factura glosada por la EPS",
    "F02": f"Pendiente por radicar con más de {PLAZO_RADICACION} días hábiles desde su expedición",
    "F03": f"Radicada después de {PLAZO_RADICACION} días hábiles",
    "F04": "Fecha de radicado anterior a la fecha de la factura",
    "F05": "DIAN: enviada y no recibida o sin CUFE",
    "F06": "Total distinto de subtotal − cuota − copago − bono − descuento + IVA",
    "F07": f"Facturación tardía: más de {DIAS_FACTURACION_TARDIA} días entre egreso/ingreso y factura",
    "F08": "Fecha de egreso anterior a la fecha de ingreso",
    "F09": "Factura anulada",
    "F10": "Factura con total en cero o negativo",
    "F11": "Cuota moderadora o copago sin recibo de caja",
    "F12": "Factura con valor excedente",
}

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
CREATE TABLE IF NOT EXISTS medins(carga_id INT, mes TEXT, clase TEXT, sede TEXT, eps_nombre TEXT, contrato TEXT, modalidad TEXT, servicio TEXT,
  ambito TEXT, codigo TEXT, descripcion TEXT, cantidad REAL, valor REAL, lineas INT, vu_min REAL, vu_max REAL, cant_max REAL, lineas_cero INT);
CREATE TABLE IF NOT EXISTS farmacia(carga_id INT, mes TEXT, sede TEXT, codigo TEXT, descripcion TEXT, clase TEXT, concepto TEXT,
  cantidad REAL, costo_unitario REAL, valor_costo REAL);
CREATE TABLE IF NOT EXISTS fac_gen(carga_id INT, mes TEXT, factura TEXT, ingreso TEXT, ambito TEXT, estado_factura TEXT, estado_auditoria TEXT,
  fecha_factura TEXT, subtotal REAL, cuota REAL, copago REAL, bono REAL, iva REAL, total REAL, descuento REAL, particular TEXT, registrada_por TEXT,
  fecha_anulacion TEXT, motivo_anulacion TEXT, tipo_ide TEXT, paciente_id TEXT, fecha_ingreso TEXT, fecha_egreso TEXT, recibo_caja TEXT, valor_recibo REAL,
  tipo_contrato TEXT, tercero TEXT, nit TEXT, contrato TEXT, regimen TEXT, fecha_radicado TEXT, radicado_eps TEXT, radicado_actual TEXT,
  valor_excedente REAL, sede TEXT, estado_dian TEXT, cufe TEXT, clase_contrato TEXT, clase_atencion TEXT);
CREATE TABLE IF NOT EXISTS param_contrato(contrato TEXT PRIMARY KEY, modalidad TEXT, sede TEXT, servicio TEXT);
CREATE TABLE IF NOT EXISTS param_servicio(servicio TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS hom_eps(alias TEXT PRIMARY KEY, eps TEXT);
CREATE TABLE IF NOT EXISTS hom_codigo(codigo_rips TEXT PRIMARY KEY, codigo_360 TEXT, descripcion TEXT);
CREATE INDEX IF NOT EXISTS i_fact ON fact(mes); CREATE INDEX IF NOT EXISTS i_fac ON factura(mes, factura);
CREATE INDEX IF NOT EXISTS i_rips ON rips(mes); CREATE INDEX IF NOT EXISTS i_ripsf ON rips_factura(mes, factura);
CREATE INDEX IF NOT EXISTS i_val ON carga_validacion(carga_id); CREATE INDEX IF NOT EXISTS i_cu ON costo_unitario(cups);
CREATE INDEX IF NOT EXISTS i_dim ON dim(mes, dim); CREATE INDEX IF NOT EXISTS i_med ON medins(mes, sede);
CREATE INDEX IF NOT EXISTS i_far ON farmacia(mes, sede); CREATE INDEX IF NOT EXISTS i_fg ON fac_gen(mes, factura); CREATE INDEX IF NOT EXISTS i_lin ON linea(mes);
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

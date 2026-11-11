"""Catálogo configurable: sedes, hospitales, códigos internos y vehículos.

El catálogo vive en la base de datos y se actualiza subiendo un Excel con tres hojas
(``Sedes``, ``Codigos``, ``Vehiculos``) desde la página "Catálogo". No hay nombres de
sedes ni códigos escritos en el código fuente: cuando un centro de costo cambia, se
edita el Excel y se vuelve a subir.

Resolución de la sede de una fila, en este orden:
  1. código (centro de costo, cuenta del servicio o el propio código de sede);
  2. placa del vehículo;
  3. nombre de la sede, del hospital o un alias registrado;
  4. nombre de la hoja (libros con una hoja por sede).
Lo que no se reconoce se rechaza y se reporta con una sugerencia; nunca se adivina.
"""
from __future__ import annotations

import difflib
import io
from pathlib import Path

import pandas as pd

from . import config, db
from .extraccion.comunes import normalizar, normalizar_codigo, vacio

TIPOS_CODIGO = ["CENTRO_COSTO", "CUENTA_ENERGIA", "CUENTA_ACUEDUCTO", "CUENTA_OTRO", "ALIAS"]

HOJAS = {
    "Sedes": ["sede_id", "hospital", "sede", "municipio", "tipo", "es_principal", "activo", "validado", "observacion"],
    "Codigos": ["tipo", "codigo", "sede_id", "descripcion", "validado"],
    "Vehiculos": ["placa", "sede_id", "descripcion", "clase", "combustible", "rendimiento_esperado_km_gal",
                  "validado", "observacion"],
}
AYUDA_HOJAS = {
    "Sedes": "Una fila por sede. 'hospital' es el hospital o centro del que depende. Marque es_principal=SI en la "
             "sede principal de cada hospital (allí se asignan los vehículos y los datos reportados a nombre del hospital).",
    "Codigos": "Códigos con los que las fuentes identifican una sede. tipo: " + ", ".join(TIPOS_CODIGO) +
               ". ALIAS = otro nombre con el que aparece la sede en los archivos.",
    "Vehiculos": "Placa (sin espacios ni guiones) y sede a la que está asignado el vehículo. El rendimiento esperado "
                 "(km/galón) alimenta la alerta de consumo.",
}


def _si(valor, defecto=True) -> int:
    if vacio(valor):
        return int(defecto)
    return int(normalizar(valor) in ("si", "s", "1", "x", "true", "verdadero", "1 0"))


# --------------------------------------------------------------------------- #
# Lectura, validación y aplicación del catálogo
# --------------------------------------------------------------------------- #
def leer_excel(contenido: bytes) -> tuple[dict[str, pd.DataFrame], list[str]]:
    errores: list[str] = []
    try:
        libro = pd.read_excel(io.BytesIO(contenido), sheet_name=None, dtype=object)
    except Exception as e:
        return {}, [f"No se pudo abrir el Excel del catálogo: {e}"]
    hojas = {normalizar(n): df for n, df in libro.items()}
    tablas = {}
    for nombre, columnas in HOJAS.items():
        df = hojas.get(normalizar(nombre))
        if df is None:
            errores.append(f"Falta la hoja «{nombre}».")
            continue
        df = df.rename(columns={c: normalizar(c).replace(" ", "_") for c in df.columns})
        df = df.dropna(how="all")
        obligatorias = {"Sedes": ["sede_id", "hospital", "sede"], "Codigos": ["tipo", "codigo", "sede_id"],
                        "Vehiculos": ["placa", "sede_id"]}[nombre]
        faltan = [c for c in obligatorias if c not in df.columns]
        if faltan:
            errores.append(f"Hoja «{nombre}»: faltan las columnas {', '.join(faltan)}.")
            continue
        for c in columnas:
            if c not in df.columns:
                df[c] = None
        tablas[nombre] = df[columnas].reset_index(drop=True)
    if errores:
        return tablas, errores
    return tablas, validar_tablas(tablas)


def validar_tablas(t: dict[str, pd.DataFrame]) -> list[str]:
    errores = []
    sedes = t["Sedes"]
    ids = sedes["sede_id"].map(lambda v: "" if vacio(v) else str(v).strip())
    for i, fila in sedes.iterrows():
        for c in ("sede_id", "hospital", "sede"):
            if vacio(fila[c]):
                errores.append(f"Sedes, fila {i + 2}: falta «{c}».")
    dup = ids[ids.duplicated() & (ids != "")].unique()
    if len(dup):
        errores.append("Sedes: sede_id repetido: " + ", ".join(dup[:10]))
    validos = set(ids)
    for i, fila in t["Codigos"].iterrows():
        if vacio(fila["codigo"]) or vacio(fila["sede_id"]) or vacio(fila["tipo"]):
            errores.append(f"Codigos, fila {i + 2}: tipo, codigo y sede_id son obligatorios.")
            continue
        if str(fila["tipo"]).strip().upper() not in TIPOS_CODIGO:
            errores.append(f"Codigos, fila {i + 2}: tipo «{fila['tipo']}» no válido. Use: {', '.join(TIPOS_CODIGO)}.")
        if str(fila["sede_id"]).strip() not in validos:
            errores.append(f"Codigos, fila {i + 2}: la sede «{fila['sede_id']}» no existe en la hoja Sedes.")
    claves = t["Codigos"].assign(k=lambda d: d["tipo"].astype(str).str.upper().str.strip() + "|" +
                                 d["codigo"].map(normalizar_codigo))
    dup = claves["k"][claves["k"].duplicated()].unique()
    if len(dup):
        errores.append("Codigos: (tipo, código) repetido: " + ", ".join(dup[:10]))
    for i, fila in t["Vehiculos"].iterrows():
        if vacio(fila["placa"]) or vacio(fila["sede_id"]):
            errores.append(f"Vehiculos, fila {i + 2}: placa y sede_id son obligatorios.")
        elif str(fila["sede_id"]).strip() not in validos:
            errores.append(f"Vehiculos, fila {i + 2}: la sede «{fila['sede_id']}» no existe en la hoja Sedes.")
    placas = t["Vehiculos"]["placa"].map(normalizar_codigo)
    dup = placas[placas.duplicated() & (placas != "")].unique()
    if len(dup):
        errores.append("Vehiculos: placa repetida: " + ", ".join(dup[:10]))
    return errores[:60]


def aplicar(tablas: dict[str, pd.DataFrame], usuario: str, origen: str = "", ruta=None) -> str:
    """Reemplaza el catálogo por el entregado. Las sedes que ya no vienen se inactivan
    (no se borran, porque el historial las referencia)."""
    with db.transaccion(ruta) as con:
        antes = con.execute("SELECT COUNT(*) FROM sedes WHERE activo=1").fetchone()[0]
        nuevos_ids = []
        for _, f in tablas["Sedes"].iterrows():
            sid = str(f["sede_id"]).strip()
            nuevos_ids.append(sid)
            con.execute(
                """INSERT INTO sedes(sede_id, hospital, sede, municipio, tipo, es_principal, activo, validado, observacion)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(sede_id) DO UPDATE SET hospital=excluded.hospital, sede=excluded.sede,
                     municipio=excluded.municipio, tipo=excluded.tipo, es_principal=excluded.es_principal,
                     activo=excluded.activo, validado=excluded.validado, observacion=excluded.observacion""",
                (sid, str(f["hospital"]).strip(), str(f["sede"]).strip(), _txt(f["municipio"]), _txt(f["tipo"]),
                 _si(f["es_principal"], False), _si(f["activo"]), _si(f["validado"]), _txt(f["observacion"])))
        marcas = ",".join("?" * len(nuevos_ids))
        con.execute(f"UPDATE sedes SET activo=0 WHERE sede_id NOT IN ({marcas})", nuevos_ids)
        con.execute("DELETE FROM codigos")
        for _, f in tablas["Codigos"].iterrows():
            con.execute("INSERT INTO codigos(tipo, codigo, codigo_original, sede_id, descripcion, validado) "
                        "VALUES (?,?,?,?,?,?)",
                        (str(f["tipo"]).strip().upper(), normalizar_codigo(f["codigo"]), _txt(f["codigo"]),
                         str(f["sede_id"]).strip(), _txt(f["descripcion"]), _si(f["validado"])))
        con.execute("DELETE FROM vehiculos")
        for _, f in tablas["Vehiculos"].iterrows():
            rend = pd.to_numeric(f["rendimiento_esperado_km_gal"], errors="coerce")
            con.execute("INSERT INTO vehiculos(placa, sede_id, descripcion, clase, combustible, rendimiento_esperado, "
                        "validado, observacion) VALUES (?,?,?,?,?,?,?,?)",
                        (normalizar_codigo(f["placa"]), str(f["sede_id"]).strip(), _txt(f["descripcion"]),
                         _txt(f["clase"]), _txt(f["combustible"]), None if pd.isna(rend) else float(rend),
                         _si(f["validado"]), _txt(f["observacion"])))
        resumen = (f"Catálogo actualizado: {len(tablas['Sedes'])} sedes (antes {antes} activas), "
                   f"{len(tablas['Codigos'])} códigos, {len(tablas['Vehiculos'])} vehículos."
                   + (f" Origen: {origen}." if origen else ""))
        db.registrar_evento(con, usuario, "catalogo_actualizado", resumen)
    return resumen


def _txt(v):
    if vacio(v):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def cargar_inicial(ruta=None, carpeta: Path | None = None) -> None:
    carpeta = carpeta or config.DIR_CATALOGO_INICIAL
    tablas = {}
    for nombre, columnas in HOJAS.items():
        archivo = carpeta / f"{nombre.lower()}.csv"
        df = pd.read_csv(archivo, dtype=object) if archivo.exists() else pd.DataFrame(columns=columnas)
        for c in columnas:
            if c not in df.columns:
                df[c] = None
        tablas[nombre] = df[columnas]
    errores = validar_tablas(tablas)
    if errores:
        raise ValueError("Catálogo inicial inválido: " + "; ".join(errores[:5]))
    aplicar(tablas, "sistema", "catálogo inicial", ruta)


def tablas_actuales(ruta=None) -> dict[str, pd.DataFrame]:
    sn = lambda s: s.map({1: "SI", 0: "NO"})
    sedes = db.consulta("SELECT * FROM sedes ORDER BY hospital, es_principal DESC, sede", ruta=ruta)
    for c in ("es_principal", "activo", "validado"):
        sedes[c] = sn(sedes[c])
    cod = db.consulta("SELECT tipo, COALESCE(codigo_original, codigo) AS codigo, sede_id, descripcion, validado "
                      "FROM codigos ORDER BY tipo, sede_id, codigo", ruta=ruta)
    cod["validado"] = sn(cod["validado"])
    veh = db.consulta("SELECT placa, sede_id, descripcion, clase, combustible, rendimiento_esperado AS "
                      "rendimiento_esperado_km_gal, validado, observacion FROM vehiculos ORDER BY sede_id, placa",
                      ruta=ruta)
    veh["validado"] = sn(veh["validado"])
    return {"Sedes": sedes[HOJAS["Sedes"]], "Codigos": cod, "Vehiculos": veh}


def exportar_excel(ruta=None) -> bytes:
    """Catálogo vigente en Excel, listo para editar y volver a subir."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    tablas = tablas_actuales(ruta)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        for nombre, df in tablas.items():
            df.to_excel(xl, sheet_name=nombre, index=False)
            ws = xl.sheets[nombre]
            for j, col in enumerate(df.columns, start=1):
                c = ws.cell(row=1, column=j)
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", fgColor="1C5CAB")
                c.alignment = Alignment(vertical="center")
                ancho = max([len(str(col))] + [len(str(v)) for v in df[col].head(300) if v is not None])
                ws.column_dimensions[get_column_letter(j)].width = min(max(ancho + 2, 10), 60)
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
        ayuda = pd.DataFrame({"Hoja": list(AYUDA_HOJAS), "Cómo diligenciarla": list(AYUDA_HOJAS.values())})
        ayuda.to_excel(xl, sheet_name="Instrucciones", index=False)
        xl.sheets["Instrucciones"].column_dimensions["A"].width = 14
        xl.sheets["Instrucciones"].column_dimensions["B"].width = 150
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Resolución de sedes
# --------------------------------------------------------------------------- #
class Catalogo:
    """Catálogo en memoria para resolver la sede de cada fila."""

    def __init__(self, ruta=None):
        self.sedes = db.consulta("SELECT * FROM sedes", ruta=ruta)
        cod = db.consulta("SELECT * FROM codigos", ruta=ruta)
        veh = db.consulta("SELECT * FROM vehiculos", ruta=ruta)
        self.activas = set(self.sedes.loc[self.sedes["activo"] == 1, "sede_id"])
        self.hospital_de = dict(zip(self.sedes["sede_id"], self.sedes["hospital"]))
        self.nombre_de = dict(zip(self.sedes["sede_id"], self.sedes["sede"]))
        self.vehiculos = {f["placa"]: f for _, f in veh.iterrows()}
        self._por_tipo: dict[tuple[str, str], str] = {}
        self._por_codigo: dict[str, set] = {}
        self._por_nombre: dict[str, set] = {}
        for _, f in self.sedes.iterrows():
            self._por_codigo.setdefault(normalizar_codigo(f["sede_id"]), set()).add(f["sede_id"])
            self._por_nombre.setdefault(normalizar_codigo(f["sede"]), set()).add(f["sede_id"])
            if f["es_principal"]:
                self._por_nombre.setdefault(normalizar_codigo(f["hospital"]), set()).add(f["sede_id"])
        for _, f in cod.iterrows():
            if f["tipo"] == "ALIAS":
                self._por_nombre.setdefault(f["codigo"], set()).add(f["sede_id"])
            else:
                self._por_tipo[(f["tipo"], f["codigo"])] = f["sede_id"]
                self._por_codigo.setdefault(f["codigo"], set()).add(f["sede_id"])

    def sedes_de_hospital(self, hospital: str) -> set:
        return set(self.sedes.loc[self.sedes["hospital"] == hospital, "sede_id"])

    def resolver(self, codigo=None, nombre=None, placa=None, hoja=None, tipos: tuple = ()
                 ) -> tuple[str | None, str]:
        """Devuelve (sede_id, motivo). Si no se reconoce, sede_id es None y el motivo lo explica."""
        intentos = []
        c = normalizar_codigo(codigo)
        if c:
            for t in tipos:
                if (t, c) in self._por_tipo:
                    return self._activa(self._por_tipo[(t, c)], f"código {codigo}")
            candidatas = self._por_codigo.get(c, set())
            if len(candidatas) == 1:
                return self._activa(next(iter(candidatas)), f"código {codigo}")
            if len(candidatas) > 1:
                return None, (f"el código «{codigo}» corresponde a varias sedes ({', '.join(sorted(candidatas))}); "
                              "indique el tipo de código correcto en el catálogo")
            intentos.append(f"código «{codigo}»")
        p = normalizar_codigo(placa)
        if p:
            if p in self.vehiculos:
                return self._activa(self.vehiculos[p]["sede_id"], f"placa {p}")
            intentos.append(f"placa «{p}»")
        for texto, etiqueta in ((nombre, "nombre"), (hoja, "hoja")):
            n = normalizar_codigo(texto)
            if not n:
                continue
            candidatas = self._por_nombre.get(n, set())
            if len(candidatas) == 1:
                return self._activa(next(iter(candidatas)), f"{etiqueta} {texto}")
            if len(candidatas) > 1:
                return None, f"el nombre «{texto}» coincide con varias sedes; use el código de sede"
            if etiqueta == "nombre":
                intentos.append(f"nombre «{str(texto).strip()}»")
        if not intentos:
            return None, "la fila no trae código, placa ni nombre de sede"
        sugerencia = self._sugerir(nombre) if not vacio(nombre) else ""
        return None, ("no está en el catálogo: " + ", ".join(intentos) + sugerencia)

    def _activa(self, sede_id: str, como: str) -> tuple[str | None, str]:
        if sede_id not in self.activas:
            return None, f"la sede {sede_id} ({como}) está inactiva en el catálogo"
        return sede_id, como

    def _sugerir(self, nombre) -> str:
        cerca = difflib.get_close_matches(normalizar_codigo(nombre), list(self._por_nombre), n=1, cutoff=0.75)
        if not cerca:
            return ""
        sid = next(iter(self._por_nombre[cerca[0]]))
        return f". ¿Será {sid} – {self.nombre_de.get(sid, '')}? Si es así, agréguelo como ALIAS en el catálogo"

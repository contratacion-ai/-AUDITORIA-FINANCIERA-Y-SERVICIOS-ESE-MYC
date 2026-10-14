"""Integración con el aplicativo de Auditoría de Facturación ESE MyC (v5).

Ese aplicativo (FastAPI en http://localhost:8000) recibe RIPS por la pestaña
«Cargas» o por ``POST /api/cargas/rips`` en su **plantilla homologada**
(columnas MES, EPS, NIT_EPS, CONTRATO, CUCON, MODALIDAD, SEDE, FACTURA_FEV,
FACTURA_RIPS, TIPO_SERVICIO, CODIGO_CUPS_CUMS, DESCRIPCION_SERVICIO,
CANTIDAD, VALOR_SERVICIO, VALOR_FACTURA_FEV, VALIDACION).

Este módulo:

1. Convierte los RIPS de la Res. 3374 ya validados a esas columnas, un
   archivo por mes (``RIPS_AUDITORIA_<AAAA-MM>.xlsx``).
2. **Acumula las sedes** de un mismo mes entre cargues sucesivos. Motivo: el
   aplicativo, al recibir RIPS de un mes, borra todo lo que tenía de ese mes
   (``_reemplazar`` en su ``processing.py``). Si se subiera un hospital a la
   vez, cada carga borraría las anteriores. El acumulado (carpeta
   ``acumulado_aplicativo``) guarda lo último procesado de cada sede y el
   archivo del mes siempre lleva todas.
3. Opcionalmente envía cada mes al aplicativo abierto y espera a que la carga
   quede en estado «ok».

Decisiones (configurables en ``aplicativo_auditoria`` de configuracion.yaml):

* EPS = nombre de la entidad del AF (el aplicativo lo homologa con sus
  alias); sin entidad = «PARTICULAR».
* CONTRATO = nombre de la carpeta de la remisión sin número ni «_TXT»
  (las carpetas exportadas de Software 360 llevan el nombre del contrato);
  CUCON = número de contrato del AF.
* MODALIDAD = por palabras del contrato (EVENTO, CAPITA/GLOBAL, PGP); si no
  hay, se infiere: CAPITA cuando la mayoría de facturas de la remisión no
  concilian con sus servicios (factura global), EVENTO en caso contrario.
* AU, AH y AN no tienen código ni valor en la Res. 3374 (se facturan por AP,
  AT y AM), por eso no generan filas.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pandas as pd
import xlsxwriter

from .validacion import a_numero

log = logging.getLogger(__name__)

COLUMNAS = [
    "MES", "EPS", "NIT_EPS", "CONTRATO", "CUCON", "MODALIDAD", "SEDE", "FACTURA_FEV", "FACTURA_RIPS",
    "TIPO_SERVICIO", "CODIGO_CUPS_CUMS", "DESCRIPCION_SERVICIO", "CANTIDAD", "VALOR_SERVICIO",
    "VALOR_FACTURA_FEV", "VALIDACION",
]
LLAVES = ["MES", "EPS", "NIT_EPS", "CONTRATO", "CUCON", "MODALIDAD", "SEDE", "FACTURA_FEV", "FACTURA_RIPS",
          "TIPO_SERVICIO", "CODIGO_CUPS_CUMS"]
HOJA_DATOS = "CONSOLIDADO RIPS"  # el aplicativo lee sólo hojas que empiezan por "CONSOLIDADO"

# tipo de archivo -> (TIPO_SERVICIO, código, descripción, cantidad, valor)
SERVICIOS = {
    "AC": ("Consultas", "cod_consulta", None, None, "valor_consulta"),
    "AP": ("Procedimientos", "cod_procedimiento", None, None, "valor"),
    "AM": ("Medicamentos", "cod_medicamento", "nombre_generico", "num_unidades", "valor_total"),
    "AT": ("Otros servicios", "cod_servicio", "nombre_servicio", "cantidad", "valor_total"),
}

DEFAULTS = {
    "habilitado": True,
    "carpeta_acumulado": None,  # None = <carpeta de salida base>/acumulado_aplicativo
    "enviar_automaticamente": False,
    "url": None,  # None = detectar en http://127.0.0.1:8000..8010
    "abrir_aplicativo": True,  # abrirlo si está instalado y cerrado al momento de enviar
    "puertos": [8000, 8010],
    "espera_maxima_segundos": 900,
    "modalidad_por_contrato": [["EVENTO", "EVENTO"], ["CAPITA|CÁPITA|GLOBAL", "CAPITA"], ["PGP", "PGP"]],
    "inferir_modalidad": True,
    "usar_parametros_aplicativo": True,  # leer PARAM CONTRATO del aplicativo si está abierto
    "contratos": {},  # texto de carpeta (normalizado) -> nombre exacto del contrato en Software 360
    "eps_por_codigo": {},  # código entidad AF -> nombre EPS de Software 360
    "nit_por_codigo": {},  # código entidad AF -> NIT EPS
}


def _norm(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", " ", t).strip()


def limpiar_contrato(carpeta: str) -> str:
    """'10204   SANITAS SUBSIDIADO ASITENCIAL _TXT' -> 'SANITAS SUBSIDIADO ASITENCIAL'."""
    t = re.sub(r"^(rips)?\d+[\s_\-]*", "", str(carpeta or "").strip(), flags=re.IGNORECASE)
    t = re.sub(r"[\s_]*TXT$", "", t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t.replace("_", " ")).strip().upper()


def _modalidad_nombre(contrato: str, reglas) -> str:
    """Primera regla cuyo alguna palabra ("A|B|C") aparece completa en el contrato."""
    n = f" {_norm(contrato)} "
    for patron, modalidad in reglas:
        if any(f" {_norm(p)} " in n for p in str(patron).split("|") if _norm(p)):
            return modalidad
    return ""


def emparejar_contrato(nombre: str, catalogo: list[tuple[str, str]], umbral: float = 0.6):
    """Busca el contrato de PARAM CONTRATO (Software 360) que corresponde al nombre
    de la carpeta: igualdad normalizada o, si no, mayor coincidencia de palabras
    (índice de Jaccard >= ``umbral``). Devuelve (contrato, modalidad) o None."""
    n = _norm(nombre)
    palabras = set(n.split())
    mejor, puntaje = None, 0.0
    for contrato, modalidad in catalogo:
        cn = _norm(contrato)
        if cn == n:
            return contrato, modalidad
        otras = set(cn.split())
        j = len(palabras & otras) / len(palabras | otras) if palabras | otras else 0.0
        if j > puntaje:
            mejor, puntaje = (contrato, modalidad), j
    return mejor if puntaje >= umbral else None


def construir_filas(resultado, cfg_app: dict | None = None,
                    contratos_app: list[tuple[str, str]] | None = None) -> pd.DataFrame:
    """Filas en la plantilla homologada del aplicativo, agregadas por llave.

    ``contratos_app``: PARAM CONTRATO del aplicativo [(contrato, modalidad)]; si se
    entrega, CONTRATO y MODALIDAD se toman de allí cuando el nombre coincide."""
    c = {**DEFAULTS, **(cfg_app or {})}
    af = resultado.datos.get("AF")
    if af is None or af.empty:
        return pd.DataFrame(columns=COLUMNAS)
    fac = af.drop_duplicates(["envio_id", "num_factura"])[
        ["envio_id", "num_factura", "hospital", "periodo", "carpeta_envio", "cod_entidad", "nombre_entidad",
         "num_contrato", "valor_neto"]].copy()
    fac["valor_fev"] = a_numero(fac["valor_neto"]).fillna(0)
    conc = resultado.conciliacion[["envio_id", "num_factura", "estado"]] if not resultado.conciliacion.empty else \
        pd.DataFrame(columns=["envio_id", "num_factura", "estado"])
    fac = fac.merge(conc, on=["envio_id", "num_factura"], how="left")

    partes = []
    for tipo, (nombre, col_cod, col_desc, col_cant, col_valor) in SERVICIOS.items():
        df = resultado.datos.get(tipo)
        if df is None or df.empty:
            continue
        partes.append(pd.DataFrame({
            "envio_id": df["envio_id"].to_numpy(), "num_factura": df["num_factura"].to_numpy(),
            "hospital": df["hospital"].to_numpy(), "periodo": df["periodo"].to_numpy(),
            "TIPO_SERVICIO": nombre,
            "CODIGO_CUPS_CUMS": df[col_cod].fillna("").astype(str).str.strip().to_numpy(),
            "DESCRIPCION_SERVICIO": (df[col_desc].fillna("").astype(str).to_numpy() if col_desc else ""),
            "CANTIDAD": (a_numero(df[col_cant]).fillna(1).to_numpy() if col_cant else 1.0),
            "VALOR_SERVICIO": a_numero(df[col_valor]).fillna(0).to_numpy(),
        }))
    srv = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(
        columns=["envio_id", "num_factura", "hospital", "periodo", "TIPO_SERVICIO", "CODIGO_CUPS_CUMS",
                 "DESCRIPCION_SERVICIO", "CANTIDAD", "VALOR_SERVICIO"])
    # Facturas del AF sin servicios: una fila sin código para que el valor FEV no se pierda.
    con_srv = set(zip(srv["envio_id"], srv["num_factura"]))
    solas = fac[[(e, f) not in con_srv for e, f in zip(fac["envio_id"], fac["num_factura"])]]
    if len(solas):
        srv = pd.concat([srv, pd.DataFrame({
            "envio_id": solas["envio_id"], "num_factura": solas["num_factura"], "hospital": solas["hospital"],
            "periodo": solas["periodo"], "TIPO_SERVICIO": "", "CODIGO_CUPS_CUMS": "", "DESCRIPCION_SERVICIO": "",
            "CANTIDAD": 0.0, "VALOR_SERVICIO": 0.0})], ignore_index=True)
    if srv.empty:
        return pd.DataFrame(columns=COLUMNAS)

    d = srv.merge(fac.drop(columns=["hospital", "periodo"]), on=["envio_id", "num_factura"], how="left")
    # Servicios cuya factura no está en el AF: se completan con los datos de su remisión.
    por_envio = fac.groupby("envio_id").agg(cod_entidad_e=("cod_entidad", "first"),
                                            nombre_entidad_e=("nombre_entidad", "first"),
                                            carpeta_e=("carpeta_envio", "first"))
    d = d.join(por_envio, on="envio_id")
    for col, alt in (("cod_entidad", "cod_entidad_e"), ("nombre_entidad", "nombre_entidad_e"),
                     ("carpeta_envio", "carpeta_e")):
        d[col] = d[col].where(d[col].notna(), d[alt])
    d["estado"] = d["estado"].fillna("FACTURA NO ESTÁ EN AF")

    eps_cod = {str(k): v for k, v in (c["eps_por_codigo"] or {}).items()}
    nit_cod = {str(k): v for k, v in (c["nit_por_codigo"] or {}).items()}
    cod = d["cod_entidad"].fillna("").astype(str).str.strip()
    nombre = d["nombre_entidad"].fillna("").astype(str).str.strip()
    eps = cod.map(eps_cod).fillna(nombre)
    d["EPS"] = eps.where(eps != "", "PARTICULAR")
    d["NIT_EPS"] = cod.map(nit_cod).fillna("")
    contratos = {_norm(k): v for k, v in (c["contratos"] or {}).items()}
    base = d["carpeta_envio"].fillna("").map(limpiar_contrato)
    d["CONTRATO"] = [contratos.get(_norm(x), x) or "SIN CONTRATO" for x in base]
    d["CUCON"] = d["num_contrato"].fillna("").astype(str)
    d["MODALIDAD"] = [_modalidad_nombre(x, c["modalidad_por_contrato"]) for x in d["CONTRATO"]]
    if contratos_app:
        pares = {x: emparejar_contrato(x, contratos_app) for x in d["CONTRATO"].unique()}
        hallado = d["CONTRATO"].map(lambda x: pares.get(x))
        con = hallado.notna()
        d.loc[con, "MODALIDAD"] = [p[1] or m for p, m in zip(hallado[con], d.loc[con, "MODALIDAD"])]
        d.loc[con, "CONTRATO"] = [p[0] for p in hallado[con]]
        d.loc[con, "estado"] = d.loc[con, "estado"] + " · CONTRATO DE PARAM CONTRATO"
    if c["inferir_modalidad"]:
        dif = fac.assign(dif=fac["estado"].eq("DIFERENCIA")).groupby("envio_id")["dif"].mean()
        inferida = d["envio_id"].map(dif).fillna(0).gt(0.5).map({True: "CAPITA", False: "EVENTO"})
        sin = d["MODALIDAD"].fillna("") == ""
        d.loc[sin, "MODALIDAD"] = inferida[sin]
        d.loc[sin, "estado"] = d.loc[sin, "estado"] + " · MODALIDAD INFERIDA"
    d["MES"] = d["periodo"]
    d["SEDE"] = d["hospital"].fillna("").map(lambda h: _norm(h) or "SIN SEDE")
    d["FACTURA_FEV"] = d["num_factura"].fillna("").astype(str)
    d["FACTURA_RIPS"] = ""
    d["VALOR_FACTURA_FEV"] = d["valor_fev"].fillna(0)
    d["VALIDACION"] = d["estado"]
    for col in LLAVES:
        d[col] = d[col].fillna("").astype(str)
    out = (d.groupby(LLAVES, as_index=False, sort=False)
             .agg(DESCRIPCION_SERVICIO=("DESCRIPCION_SERVICIO", "first"), CANTIDAD=("CANTIDAD", "sum"),
                  VALOR_SERVICIO=("VALOR_SERVICIO", "sum"), VALOR_FACTURA_FEV=("VALOR_FACTURA_FEV", "max"),
                  VALIDACION=("VALIDACION", "first")))
    out = out[out["MES"].str.match(r"^\d{4}-\d{2}$")]
    return out[COLUMNAS].sort_values(["MES", "SEDE", "EPS", "FACTURA_FEV", "TIPO_SERVICIO"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Acumulado por mes (todas las sedes)
# ---------------------------------------------------------------------------

def actualizar_acumulado(filas: pd.DataFrame, carpeta: str | Path) -> dict[str, pd.DataFrame]:
    """Reemplaza en el acumulado de cada mes las sedes que trae ``filas`` y
    devuelve el contenido completo de los meses tocados."""
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    meses: dict[str, pd.DataFrame] = {}
    for mes, nuevo in filas.groupby("MES", sort=True):
        ruta = carpeta / f"{mes}.csv"
        if ruta.exists():
            previo = pd.read_csv(ruta, dtype=str, keep_default_na=False, encoding="utf-8")
            previo = previo[~previo["SEDE"].isin(set(nuevo["SEDE"]))]
            for col in ("CANTIDAD", "VALOR_SERVICIO", "VALOR_FACTURA_FEV"):
                previo[col] = pd.to_numeric(previo[col], errors="coerce").fillna(0)
            total = pd.concat([previo, nuevo], ignore_index=True)
        else:
            total = nuevo.copy()
        total = total[COLUMNAS].sort_values(["SEDE", "EPS", "FACTURA_FEV", "TIPO_SERVICIO"]).reset_index(drop=True)
        tmp = ruta.with_suffix(".tmp")
        total.to_csv(tmp, index=False, encoding="utf-8")
        tmp.replace(ruta)
        meses[str(mes)] = total
    return meses


def resumen_sedes(df: pd.DataFrame) -> pd.DataFrame:
    fev = df.drop_duplicates(["SEDE", "FACTURA_FEV"]).groupby("SEDE")["VALOR_FACTURA_FEV"].sum()
    g = df.groupby("SEDE").agg(facturas=("FACTURA_FEV", "nunique"), filas=("FACTURA_FEV", "size"),
                               valor_servicios=("VALOR_SERVICIO", "sum"))
    g["valor_fev"] = fev
    return g.reset_index()


SUPUESTOS = [
    "Archivo generado por el Cargue RIPS (Res. 3374/2000) para la pestaña «Cargas» > RIPS del aplicativo de auditoría.",
    "Contiene TODAS las sedes procesadas para el mes: el aplicativo reemplaza el mes completo al cargar.",
    "EPS = nombre de la entidad del AF (el aplicativo lo homologa con sus alias). Sin entidad = PARTICULAR.",
    "CONTRATO = carpeta de la remisión sin número; CUCON = número de contrato del AF.",
    "MODALIDAD por el nombre del contrato (EVENTO / CAPITA-GLOBAL / PGP); si no lo dice, se infiere "
    "(CAPITA si la mayoría de facturas no concilian con sus servicios) y VALIDACION lo indica.",
    "VALIDACION = resultado de la conciliación AF vs. servicios de la herramienta de cargue.",
    "AU, AH y AN no generan filas: en la Res. 3374 no traen código ni valor (se facturan en AP, AT y AM).",
]


def escribir_mes(df: pd.DataFrame, mes: str, ruta: str | Path, institucion: str = "") -> Path:
    """Libro del mes en el formato homologado del aplicativo (encabezado en la fila 4)."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(ruta), {"strings_to_numbers": False, "strings_to_formulas": False,
                                        "strings_to_urls": False})
    titulo = wb.add_format({"bold": True, "font_size": 14, "font_color": "#1B7F2A", "font_name": "Calibri"})
    sub = wb.add_format({"italic": True, "font_color": "#595959", "font_name": "Calibri"})
    enc = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#1B9E2B", "border": 1,
                         "text_wrap": True, "valign": "vcenter", "font_name": "Calibri"})
    num = wb.add_format({"num_format": "#,##0.00", "font_name": "Calibri"})
    txt = wb.add_format({"num_format": "@", "font_name": "Calibri"})
    ws = wb.add_worksheet(HOJA_DATOS)
    ws.write_string(0, 0, f"{institucion or 'ESE Moreno y Clavijo'} · Consolidado RIPS homologado {mes}", titulo)
    ws.write_string(1, 0, f"Generado {dt.datetime.now():%Y-%m-%d %H:%M} · {len(df):,} filas · "
                          f"{df['SEDE'].nunique()} sede(s) · Res. 3374/2000 → plantilla RIPS del aplicativo de auditoría", sub)
    anchos = [9, 34, 12, 34, 16, 11, 26, 14, 12, 16, 16, 34, 10, 16, 18, 30]
    for j, (c, w) in enumerate(zip(COLUMNAS, anchos)):
        ws.write_string(3, j, c, enc)
        ws.set_column(j, j, w)
    numericas = {"CANTIDAD", "VALOR_SERVICIO", "VALOR_FACTURA_FEV"}
    for i, fila in enumerate(df[COLUMNAS].itertuples(index=False, name=None), start=4):
        for j, (c, v) in enumerate(zip(COLUMNAS, fila)):
            if c in numericas:
                ws.write_number(i, j, float(v or 0), num)
            elif v not in ("", None):
                ws.write_string(i, j, str(v), txt)
    ws.freeze_panes(4, 0)
    ws.autofilter(3, 0, max(4, 3 + len(df)), len(COLUMNAS) - 1)

    rs = resumen_sedes(df)
    w2 = wb.add_worksheet("Resumen_Sedes")
    for j, c in enumerate(rs.columns):
        w2.write_string(0, j, c, enc)
        w2.set_column(j, j, 30 if j == 0 else 18)
    for i, fila in enumerate(rs.itertuples(index=False, name=None), start=1):
        w2.write_string(i, 0, str(fila[0]))
        for j, v in enumerate(fila[1:], start=1):
            w2.write_number(i, j, float(v or 0), num)
    w3 = wb.add_worksheet("Supuestos")
    w3.set_column(0, 0, 130)
    for i, s in enumerate(SUPUESTOS):
        w3.write_string(i, 0, s)
    wb.close()
    return ruta


def exportar_para_aplicativo(resultado, salida: str | Path, cfg_app: dict | None = None,
                             carpeta_acumulado: str | Path | None = None, hospital: str | None = None,
                             periodo: str | None = None) -> list[Path]:
    """Genera ``<salida>/APLICATIVO_AUDITORIA/RIPS_AUDITORIA_<mes>.xlsx`` por cada mes
    del resultado (o sólo del hospital/período filtrado), con todas las sedes acumuladas."""
    c = {**DEFAULTS, **(cfg_app or {})}
    contratos_app = None
    if c.get("usar_parametros_aplicativo", True):
        cliente = ClienteAplicativo(c.get("url"), c.get("puertos") or (8000, 8010), timeout=2)
        if cliente.detectar():
            contratos_app = cliente.contratos() or None
            if contratos_app:
                log.info("PARAM CONTRATO del aplicativo: %d contratos para homologar nombres y modalidad",
                         len(contratos_app))
    filas = construir_filas(resultado, c, contratos_app)
    if hospital:
        filas = filas[filas["SEDE"] == _norm(hospital)]
    if periodo:
        filas = filas[filas["MES"] == periodo]
    if filas.empty:
        return []
    acumulado = Path(carpeta_acumulado or c.get("carpeta_acumulado") or Path(salida).parent / "acumulado_aplicativo")
    meses = actualizar_acumulado(filas, acumulado)
    destino = Path(salida) / "APLICATIVO_AUDITORIA"
    return [escribir_mes(df, mes, destino / f"RIPS_AUDITORIA_{mes}.xlsx") for mes, df in meses.items()]


# ---------------------------------------------------------------------------
# Envío directo al aplicativo
# ---------------------------------------------------------------------------

class ErrorAplicativo(RuntimeError):
    pass


class ClienteAplicativo:
    """Cliente mínimo (sólo librería estándar) de la API del aplicativo de auditoría."""

    def __init__(self, url: str | None = None, puertos=(8000, 8010), timeout: float = 5.0):
        self.url = url.rstrip("/") if url else None
        self.puertos = range(int(puertos[0]), int(puertos[-1]) + 1)
        self.timeout = timeout

    def _get(self, url: str):
        with urllib.request.urlopen(url, timeout=self.timeout) as r:
            return json.load(r)

    def detectar(self) -> str | None:
        candidatos = [self.url] if self.url else [f"http://127.0.0.1:{p}" for p in self.puertos]
        for base in candidatos:
            try:
                if self._get(f"{base}/api/health").get("ok") is True:
                    self.url = base
                    return base
            except (OSError, ValueError, urllib.error.URLError):
                continue
        return None

    def enviar_rips(self, ruta: str | Path, mes: str | None = None) -> int:
        if not self.url and not self.detectar():
            raise ErrorAplicativo("El aplicativo de auditoría no está abierto (no responde en localhost:8000-8010).")
        ruta = Path(ruta)
        limite = uuid.uuid4().hex
        partes = []
        if mes:
            partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="mes"\r\n\r\n{mes}\r\n'.encode())
        partes.append((f'--{limite}\r\nContent-Disposition: form-data; name="file"; filename="{ruta.name}"\r\n'
                       "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n").encode())
        partes.append(ruta.read_bytes())
        partes.append(f"\r\n--{limite}--\r\n".encode())
        req = urllib.request.Request(f"{self.url}/api/cargas/rips", data=b"".join(partes), method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={limite}"})
        try:
            with urllib.request.urlopen(req, timeout=max(self.timeout, 120)) as r:
                datos = json.load(r)
        except urllib.error.HTTPError as exc:
            raise ErrorAplicativo(f"El aplicativo rechazó el archivo ({exc.code}): {exc.read()[:300].decode('utf-8', 'replace')}") from exc
        return int(datos["cargas"][0]["id"])

    def contratos(self) -> list[tuple[str, str]]:
        """PARAM CONTRATO vigente en el aplicativo: [(contrato, modalidad)]."""
        try:
            filas = self._get(f"{self.url}/api/parametros")["contratos"]["filas"]
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
            return []
        vistos = {}
        for f in filas:
            if f.get("contrato"):
                vistos.setdefault(f["contrato"], (f.get("modalidad") or "").upper())
        return list(vistos.items())

    def validacion_cruce(self) -> list[dict]:
        """Cobertura de llaves comunes (EPS, contrato, sede, factura, CUPS) que calcula el aplicativo."""
        try:
            return self._get(f"{self.url}/api/conciliacion/validacion-cruce").get("filas", [])
        except (OSError, ValueError, urllib.error.URLError):
            return []

    def iniciar_instalado(self, espera: float = 60) -> str | None:
        """Abre el aplicativo de auditoría si está instalado en este equipo (Windows) y
        espera a que responda. Instalación estándar del v5:
        %LOCALAPPDATA%\\Programs\\AuditoriaESE_MyC\\{python\\pythonw.exe, iniciar.py}."""
        import os
        import subprocess

        base = Path(os.environ.get("AUDITORIA_ESE_DIR") or
                    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "AuditoriaESE_MyC")
        lanzador = base / "iniciar.py"
        py = base / "python" / ("pythonw.exe" if os.name == "nt" else "python")
        if not (lanzador.exists() and py.exists()):
            return None
        banderas = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([str(py), str(lanzador)], cwd=str(base), creationflags=banderas)
        fin = time.monotonic() + espera
        while time.monotonic() < fin:
            if self.detectar():
                return self.url
            time.sleep(1.5)
        return None

    def esperar(self, carga_id: int, maximo: float = 900, intervalo: float = 2.0) -> dict:
        fin = time.monotonic() + maximo
        while True:
            d = self._get(f"{self.url}/api/cargas/{carga_id}")
            if d.get("estado") in ("ok", "error", "reemplazada"):
                return d
            if time.monotonic() > fin:
                return {**d, "estado": "tiempo agotado"}
            time.sleep(intervalo)


def enviar_meses(archivos: list[Path], cfg_app: dict | None = None, aviso=log.info) -> list[dict]:
    """Envía cada ``RIPS_AUDITORIA_<mes>.xlsx`` al aplicativo y espera su resultado."""
    c = {**DEFAULTS, **(cfg_app or {})}
    cliente = ClienteAplicativo(c.get("url"), c.get("puertos") or (8000, 8010))
    if not cliente.detectar() and c.get("abrir_aplicativo", True) and archivos:
        aviso("El aplicativo de auditoría está cerrado; intentando abrirlo …")
        cliente.iniciar_instalado()
    if not cliente.url or not cliente.detectar():
        raise ErrorAplicativo("El aplicativo de auditoría no está abierto. Ábralo (acceso «Auditoría ESE MyC») "
                              "y vuelva a intentar, o cargue los archivos manualmente en la pestaña Cargas > RIPS.")
    resultados = []
    for ruta in archivos:
        mes = re.search(r"(\d{4}-\d{2})", Path(ruta).name)
        mes = mes.group(1) if mes else None
        aviso(f"Enviando {Path(ruta).name} al aplicativo ({cliente.url}) …")
        cid = cliente.enviar_rips(ruta, mes)
        r = cliente.esperar(cid, float(c.get("espera_maxima_segundos") or 900))
        aviso(f"  {mes}: {r.get('estado')} · {r.get('mensaje') or ''}")
        resultados.append({"mes": mes, "archivo": str(ruta), "carga_id": cid, "estado": r.get("estado"),
                           "mensaje": r.get("mensaje"), "filas": r.get("filas")})
    return resultados


def resumen_cruce(cfg_app: dict | None = None) -> list[dict]:
    """Validación del cruce del aplicativo (después de enviar), para mostrarla al usuario."""
    c = {**DEFAULTS, **(cfg_app or {})}
    cliente = ClienteAplicativo(c.get("url"), c.get("puertos") or (8000, 8010), timeout=10)
    if not cliente.detectar():
        return []
    return [{k: f.get(k) for k in ("llave", "origen", "destino", "pct_cruce", "estado", "ejemplos_sin_cruce")}
            for f in cliente.validacion_cruce()]

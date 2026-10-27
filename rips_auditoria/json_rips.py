"""Lectura de RIPS en JSON y su integración al acumulado sin duplicar.

Formatos aceptados (cada archivo ``.json``, suelto, en carpetas o en ZIP):

1. **RIPS JSON de la Res. 2275 de 2023** (FEV-RIPS): un objeto por factura,
   o una lista de ellos, con ``numFactura`` y ``usuarios[].servicios``
   (``consultas``, ``procedimientos``, ``medicamentos``, ``otrosServicios``;
   ``urgencias``, ``hospitalizacion`` y ``recienNacidos`` no traen código ni
   valor y no generan filas, igual que AU/AH/AN en la Res. 3374).
   Campos usados: ``codConsulta`` / ``codProcedimiento`` /
   ``codTecnologiaSalud`` (código), ``nomTecnologiaSalud`` (descripción),
   ``cantidadMedicamento`` / ``cantidadOS`` (cantidad; consultas y
   procedimientos cuentan 1), ``vrServicio`` (valor), ``fechaInicioAtencion``
   / ``fechaDispensAdmon`` / ``fechaSuministroTecnologia`` (mes) y
   ``codPrestador`` (sede por ``organizacion.hospitales_por_prestador``).
   El JSON no trae la EPS ni el valor neto de la FEV: EPS = «SIN EPS (RIPS
   JSON)» y VALOR_FACTURA_FEV = suma de ``vrServicio`` de la factura.
2. **Filas homologadas**: una lista de objetos (o ``{"filas": [...]}``) con
   las columnas de la plantilla del aplicativo (MES, EPS, SEDE, FACTURA_FEV,
   TIPO_SERVICIO, CODIGO_CUPS_CUMS, CANTIDAD, VALOR_SERVICIO…).

Sede y mes, como en los TXT: el parámetro ``hospital``/``mes`` si se da;
si no, la carpeta (``Hospital/05. Mayo/...``; si la carpeta no dice el año,
se toma el de las fechas de la factura); si no, el código de prestador y la
fecha de atención más frecuente de la factura.

Depuración (misma lógica que los datos reales):

* Se normalizan sede (``_norm``), códigos (mayúsculas, sin espacios) y valores.
* Registros de servicio repetidos dentro del JSON (mismo contenido, p. ej. el
  mismo archivo cargado dos veces o una factura duplicada) se cuentan una vez.
* Se agregan por la llave de la plantilla, como ``aplicativo.construir_filas``.
* Al integrar, se descartan las filas cuya factura y código (con su sede,
  servicio, cantidad y valor) ya estén en el acumulado en cualquier mes; las
  nuevas se **agregan** al mes que corresponde (no reemplazan la sede).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path

import pandas as pd

from .aplicativo import COLUMNAS, LLAVES, _modalidad_nombre, _norm, limpiar_contrato
from .aplicativo import DEFAULTS as DEF_APP
from .catalogo_cups import normalizar_codigo
from .depuracion import LLAVE_SIN_MES
from .incidencias import RegistroIncidencias
from .lectura import contexto_desde_ruta, descubrir_archivos
from .tablero import _archivos_mes, carpeta_acumulado

log = logging.getLogger(__name__)

# lista JSON -> (TIPO_SERVICIO, campo código, campo descripción, campo cantidad, campo fecha)
SERVICIOS_2275 = {
    "consultas": ("Consultas", "codConsulta", None, None, "fechaInicioAtencion"),
    "procedimientos": ("Procedimientos", "codProcedimiento", None, None, "fechaInicioAtencion"),
    "medicamentos": ("Medicamentos", "codTecnologiaSalud", "nomTecnologiaSalud", "cantidadMedicamento", "fechaDispensAdmon"),
    "otrosServicios": ("Otros servicios", "codTecnologiaSalud", "nomTecnologiaSalud", "cantidadOS",
                       "fechaSuministroTecnologia"),
}
EPS_JSON = "SIN EPS (RIPS JSON)"
_RE_FECHA = re.compile(r"(20\d{2})-(\d{2})")


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _servicios_2275(doc: dict, ruta: str, ctx: dict) -> list[dict]:
    """Un registro por servicio de una factura RIPS JSON (Res. 2275)."""
    factura = str(doc.get("numFactura") or "").strip()
    salida = []
    for usuario in doc.get("usuarios") or []:
        servicios = (usuario or {}).get("servicios") or {}
        for lista, (tipo, c_cod, c_desc, c_cant, c_fecha) in SERVICIOS_2275.items():
            for s in servicios.get(lista) or []:
                if not isinstance(s, dict):
                    continue
                salida.append({
                    "factura": factura, "tipo": tipo, "codigo": normalizar_codigo(s.get(c_cod)),
                    "descripcion": str(s.get(c_desc) or "").strip() if c_desc else "",
                    "cantidad": _num(s.get(c_cant)) if c_cant else 1.0, "valor": _num(s.get("vrServicio")),
                    "fecha": str(s.get(c_fecha) or ""), "prestador": str(s.get("codPrestador") or "").strip(),
                    # identifica el registro para descartar repetidos exactos dentro del JSON
                    "huella": json.dumps({"f": factura, "u": usuario.get("numDocumentoIdentificacion"), "l": lista, **s},
                                         sort_keys=True, default=str),
                    "ruta": ruta, **ctx,
                })
    return salida


def _contexto(ruta: str, hospital: str | None, mes: str | None) -> dict:
    c = contexto_desde_ruta(ruta)
    mes_carpeta = f"{c['anio']}-{c['mes']:02d}" if c.get("mes") and c.get("anio") else None
    return {"sede_ctx": hospital or c.get("hospital") or "", "mes_ctx": mes or mes_carpeta,
            # carpeta con mes sin año ("05. Mayo"): el año sale de las fechas de la factura
            "mes_sin_anio": None if (mes or mes_carpeta) else c.get("mes"),
            "carpeta": c.get("carpeta_envio") or ""}


def leer_json(entradas, hospital: str | None = None, mes: str | None = None, cfg: dict | None = None) -> dict:
    """Lee los JSON de ``entradas`` y devuelve ``{"filas": DataFrame (COLUMNAS),
    "resumen": {...}, "incidencias": [...]}`` ya depurado y agregado."""
    from .config import cargar_configuracion

    cfg = cfg or cargar_configuracion()
    por_prestador = (cfg.get("organizacion") or {}).get("hospitales_por_prestador") or {}
    reglas = ((cfg.get("aplicativo_auditoria") or {}).get("modalidad_por_contrato") or DEF_APP["modalidad_por_contrato"])
    inc = RegistroIncidencias()
    registros: list[dict] = []
    homologadas: list[pd.DataFrame] = []
    avisos: list[str] = []
    archivos = 0
    for fuente in descubrir_archivos(list(entradas), inc):
        if not fuente.ruta.lower().endswith(".json"):
            continue
        archivos += 1
        try:
            texto = fuente.contenido.decode("utf-8-sig")
        except UnicodeDecodeError:
            texto = fuente.contenido.decode("latin-1")
        try:
            doc = json.loads(texto)
        except json.JSONDecodeError as exc:
            avisos.append(f"{fuente.ruta}: JSON inválido ({exc.msg}, línea {exc.lineno})")
            continue
        ctx = _contexto(fuente.ruta, hospital, mes)
        lista = doc.get("filas") if isinstance(doc, dict) and isinstance(doc.get("filas"), list) else doc
        docs = lista if isinstance(lista, list) else [lista]
        if docs and all(isinstance(x, dict) and "usuarios" in x for x in docs):
            for d in docs:
                registros += _servicios_2275(d, fuente.ruta, ctx)
        elif docs and all(isinstance(x, dict) for x in docs) and {"CODIGO_CUPS_CUMS", "FACTURA_FEV"} <= set(docs[0]):
            h = pd.DataFrame(docs).reindex(columns=COLUMNAS, fill_value="")
            if ctx["sede_ctx"]:
                h["SEDE"] = h["SEDE"].where(h["SEDE"].astype(str).str.strip() != "", ctx["sede_ctx"])
            if ctx["mes_ctx"]:
                h["MES"] = h["MES"].where(h["MES"].astype(str).str.strip() != "", ctx["mes_ctx"])
            homologadas.append(h)
        else:
            avisos.append(f"{fuente.ruta}: formato no reconocido (ni RIPS JSON Res. 2275 ni filas homologadas)")
    leidos = len(registros) + sum(len(h) for h in homologadas)
    partes, repetidos = [], 0

    if registros:
        r = pd.DataFrame(registros)
        repetidos += int(r.duplicated("huella").sum())
        r = r.drop_duplicates("huella")
        # mes: el de la carpeta/parámetro; si no, el más frecuente en las fechas de la factura
        partes_fecha = r["fecha"].str.extract(_RE_FECHA)
        r["_mes_fecha"] = (partes_fecha[0] + "-" + partes_fecha[1]).where(partes_fecha[0].notna(), None)
        moda = r.groupby("factura")["_mes_fecha"].agg(lambda s: s.dropna().mode().iloc[0] if s.notna().any() else None)
        mes_fechas = r["factura"].map(moda)
        anio = mes_fechas.str[:4].fillna(str(dt.date.today().year))
        por_carpeta = r["mes_sin_anio"].notna()
        mes_fechas = mes_fechas.where(~por_carpeta, anio + "-" + r["mes_sin_anio"].map(
            lambda m: f"{int(m):02d}" if pd.notna(m) else ""))
        r["MES"] = r["mes_ctx"].where(r["mes_ctx"].notna(), mes_fechas)
        r["SEDE"] = r["sede_ctx"].where(r["sede_ctx"] != "", r["prestador"].map(lambda p: por_prestador.get(p, "")))
        contrato = r["carpeta"].map(limpiar_contrato)
        fev = r.groupby("factura")["valor"].sum()
        partes.append(pd.DataFrame({
            "MES": r["MES"], "EPS": EPS_JSON, "NIT_EPS": "", "CONTRATO": contrato.where(contrato != "", "SIN CONTRATO"),
            "CUCON": "", "MODALIDAD": [_modalidad_nombre(c, reglas) for c in contrato], "SEDE": r["SEDE"],
            "FACTURA_FEV": r["factura"], "FACTURA_RIPS": "", "TIPO_SERVICIO": r["tipo"],
            "CODIGO_CUPS_CUMS": r["codigo"], "DESCRIPCION_SERVICIO": r["descripcion"], "CANTIDAD": r["cantidad"],
            "VALOR_SERVICIO": r["valor"], "VALOR_FACTURA_FEV": r["factura"].map(fev),
            "VALIDACION": "RIPS JSON (Res. 2275)",
        }))
    if homologadas:
        h = pd.concat(homologadas, ignore_index=True)
        antes = len(h)
        h = h.drop_duplicates()
        repetidos += antes - len(h)
        partes.append(h)
    filas = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLUMNAS)
    filas, descartadas = _normalizar(filas)
    resumen = {"archivos_json": archivos, "registros_leidos": leidos, "repetidos_en_json": repetidos,
               "filas": len(filas), "sin_mes_o_sede": descartadas}
    if descartadas:
        avisos.append(f"{descartadas} registro(s) sin mes (AAAA-MM) o sin sede: indique el hospital y el mes del JSON.")
    return {"filas": filas, "resumen": resumen, "avisos": avisos}


def _normalizar(filas: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Misma forma que ``aplicativo.construir_filas``: textos limpios, sede
    normalizada, valores numéricos y agregación por la llave de la plantilla."""
    if filas.empty:
        return pd.DataFrame(columns=COLUMNAS), 0
    f = filas.copy()
    for c in COLUMNAS:
        if c not in ("CANTIDAD", "VALOR_SERVICIO", "VALOR_FACTURA_FEV"):
            f[c] = f[c].fillna("").astype(str).str.strip()
    for c in ("CANTIDAD", "VALOR_SERVICIO", "VALOR_FACTURA_FEV"):
        f[c] = pd.to_numeric(f[c], errors="coerce").fillna(0.0)
    f["SEDE"] = f["SEDE"].map(lambda s: _norm(s))
    f["CODIGO_CUPS_CUMS"] = f["CODIGO_CUPS_CUMS"].map(normalizar_codigo)
    validos = f["MES"].str.fullmatch(r"\d{4}-\d{2}") & (f["SEDE"] != "")
    descartadas = int((~validos).sum())
    f = f[validos]
    out = (f.groupby(LLAVES, as_index=False, sort=False)
             .agg(DESCRIPCION_SERVICIO=("DESCRIPCION_SERVICIO", "first"), CANTIDAD=("CANTIDAD", "sum"),
                  VALOR_SERVICIO=("VALOR_SERVICIO", "sum"), VALOR_FACTURA_FEV=("VALOR_FACTURA_FEV", "max"),
                  VALIDACION=("VALIDACION", "first")))
    return out[COLUMNAS].reset_index(drop=True), descartadas


def _llave(df: pd.DataFrame) -> pd.Series:
    d = df.copy()
    for c in ("CANTIDAD", "VALOR_SERVICIO"):
        d[c] = pd.to_numeric(d[c], errors="coerce").round(2).astype(str)
    d["SEDE"] = d["SEDE"].map(_norm)
    d["CODIGO_CUPS_CUMS"] = d["CODIGO_CUPS_CUMS"].map(normalizar_codigo)
    # La EPS del JSON es genérica: se compara sin EPS/contrato/modalidad para reconocer
    # la misma factura ya cargada desde los TXT.
    cols = [c for c in LLAVE_SIN_MES if c not in ("EPS", "NIT_EPS", "CONTRATO", "CUCON", "MODALIDAD", "FACTURA_RIPS")]
    return d[cols].astype(str).agg("|".join, axis=1)


def integrar(filas: pd.DataFrame, carpeta=None) -> dict:
    """Agrega al acumulado las filas que no estén ya cargadas (en ningún mes)."""
    carpeta = Path(carpeta) if carpeta else carpeta_acumulado()
    carpeta.mkdir(parents=True, exist_ok=True)
    if filas.empty:
        return {"agregadas": 0, "ya_cargadas": 0, "meses": []}
    existentes = [pd.read_csv(p, dtype=str, keep_default_na=False, encoding="utf-8") for p in _archivos_mes(carpeta)]
    previas = set(_llave(pd.concat(existentes, ignore_index=True))) if existentes else set()
    nuevas = filas[~_llave(filas).isin(previas)]
    meses = []
    for mes, g in nuevas.groupby("MES", sort=True):
        ruta = carpeta / f"{mes}.csv"
        total = g
        if ruta.exists():
            total = pd.concat([pd.read_csv(ruta, dtype=str, keep_default_na=False, encoding="utf-8"), g], ignore_index=True)
        total = total.reindex(columns=COLUMNAS, fill_value="")
        tmp = ruta.with_suffix(".tmp")
        total.to_csv(tmp, index=False, encoding="utf-8")
        tmp.replace(ruta)
        meses.append(str(mes))
    return {"agregadas": len(nuevas), "ya_cargadas": len(filas) - len(nuevas), "meses": meses,
            "actualizado": dt.datetime.now().isoformat(timespec="seconds")}


def cargar_json(entradas, hospital: str | None = None, mes: str | None = None, cfg: dict | None = None,
                carpeta=None) -> dict:
    """Lee, depura e integra; devuelve el resumen para la pantalla o la consola."""
    r = leer_json(entradas, hospital, mes, cfg)
    res = integrar(r["filas"], carpeta)
    return {**r["resumen"], **res, "avisos": r["avisos"]}


def hay_json(entradas) -> bool:
    for e in entradas:
        p = Path(e)
        if p.is_file() and p.suffix.lower() == ".json":
            return True
        if p.is_dir() and next((x for x in p.rglob("*") if x.suffix.lower() == ".json"), None) is not None:
            return True
        if p.suffix.lower() == ".zip":
            import zipfile

            try:
                with zipfile.ZipFile(p) as z:
                    if any(n.lower().endswith(".json") for n in z.namelist()):
                        return True
            except (zipfile.BadZipFile, OSError):
                pass
    return False


__all__ = ["cargar_json", "hay_json", "integrar", "leer_json"]

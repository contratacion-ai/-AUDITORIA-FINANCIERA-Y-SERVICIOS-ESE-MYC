"""Ventana de cargue (página local) con la imagen de la ESE Moreno y Clavijo.

Se sirve en 127.0.0.1 (puerto 8100-8110, para no chocar con el aplicativo
de auditoría en 8000-8010) o, en modo «en red», a los demás equipos con
clave de acceso (ver ``red.py``). Permite:

* cargar un ZIP, TXT sueltos o una carpeta (arrastrando o eligiendo), o
  indicar la ruta de una carpeta/ZIP de este equipo sin copiarla;
* ver el avance y el resumen por hospital y mes;
* generar y **enviar cada mes al aplicativo de auditoría** (abriéndolo si
  está instalado y cerrado) y ver su «validación del cruce»;
* abrir la carpeta de resultados o el aplicativo de auditoría;
* consultar el **tablero de análisis** (``/tablero``) sobre el acumulado
  homologado que se envía al aplicativo: por sede, mes, empresa, servicio y
  CUPS, comparativos, frecuencias de uso, alertas y análisis, con descarga a
  Excel/CSV y reenvío de meses al aplicativo.

Los cargues se ejecutan uno a la vez en un hilo; la página consulta el estado.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import traceback
import urllib.parse
import uuid
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from .. import tablero as tb
from ..aplicativo import ClienteAplicativo, ErrorAplicativo, enviar_meses, escribir_mes, resumen_cruce
from ..config import cargar_configuracion, carpeta_datos
from ..exportacion import exportar_todo
from ..lectura import detectar_mes
from ..procesamiento import procesar
from . import red

ESTATICOS = Path(__file__).parent / "static"
app = FastAPI(title="Cargue RIPS · ESE Moreno y Clavijo", version=__version__, docs_url="/docs")
app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")
app.add_middleware(red.ProteccionRed)  # equipos remotos: clave de acceso; este equipo: libre

_trabajos: dict[str, dict] = {}
_turno = threading.Lock()  # un cargue a la vez: el proceso usa bastante memoria


class _CapturaLog(logging.Handler):
    def __init__(self, trabajo: dict):
        super().__init__(logging.INFO)
        self.trabajo = trabajo

    def emit(self, record):
        self.trabajo["log"].append(f"{dt.datetime.now():%H:%M:%S}  {record.getMessage()}")


def _cfg_app(cfg: dict) -> dict:
    return cfg.get("aplicativo_auditoria") or {}


def _abrir(ruta: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(ruta)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(ruta)])
    else:
        subprocess.Popen(["xdg-open", str(ruta)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ruta_segura(base: Path, relativa: str) -> Path:
    """Ruta de un archivo subido conservando sus carpetas, sin salirse de ``base``."""
    partes = [p for p in PurePosixPath(relativa.replace("\\", "/")).parts if p not in ("", ".", "..", "/")]
    partes = [p.replace(":", "_") for p in partes] or ["archivo"]
    destino = (base.joinpath(*partes)).resolve()
    if base.resolve() not in destino.parents:
        raise HTTPException(400, f"Nombre de archivo no permitido: {relativa}")
    return destino


# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def inicio():
    return (ESTATICOS / "index.html").read_text(encoding="utf-8")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(ESTATICOS / "icono_ese.ico")


def _es_local(request: Request) -> bool:
    return (request.client.host if request.client else "") in red.LOCALES


def _url_para(request: Request, url: str | None) -> str | None:
    """URL del aplicativo de auditoría vista desde el navegador que pregunta: en
    otro equipo, ``127.0.0.1`` se cambia por la dirección del servidor (puente)."""
    if not url or _es_local(request):
        return url
    puerto = urllib.parse.urlsplit(url).port or 8000
    return f"http://{request.url.hostname}:{puerto}"


@app.get("/api/estado")
def estado(request: Request):
    cfg = cargar_configuracion()
    c = _cfg_app(cfg)
    cliente = ClienteAplicativo(c.get("url"), c.get("puertos") or (8000, 8010), timeout=1.5)
    base = Path(os.environ.get("AUDITORIA_ESE_DIR") or
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "AuditoriaESE_MyC")
    local = _es_local(request)
    en_red = red.red_habilitada(cfg)
    puerto = request.url.port or 8100
    return {"version": __version__, "carpeta_datos": str(carpeta_datos()),
            "aplicativo": _url_para(request, cliente.detectar()),
            "aplicativo_instalado": (base / "iniciar.py").exists(),
            "ocupado": _turno.locked(), "equipo_local": local,
            "red": {"habilitada": en_red, "urls": [f"http://{ip}:{puerto}" for ip in red.ips_locales()] if en_red else [],
                    "clave": red.clave_acceso(cfg) if (en_red and local) else None}}


@app.post("/api/aplicativo/abrir")
def abrir_aplicativo(request: Request):
    cfg = _cfg_app(cargar_configuracion())
    cliente = ClienteAplicativo(cfg.get("url"), cfg.get("puertos") or (8000, 8010), timeout=1.5)
    url = _url_para(request, cliente.detectar() or cliente.iniciar_instalado())
    if not url:
        raise HTTPException(404, "No se encontró el aplicativo de Auditoría ESE MyC instalado en este equipo. "
                                 "Instálelo con «Instalador_Auditoria_ESE_MyC» o ábralo manualmente.")
    return {"url": url}


# Starlette limita por defecto un formulario a 1000 archivos y 1000 campos
# (Request.form(max_files=1000)); una carpeta anual de RIPS trae miles de TXT,
# por eso el formulario se lee a mano con límites amplios. No hay límite de
# líneas por archivo: cada TXT se procesa completo.
MAX_ARCHIVOS_FORMULARIO = 500_000


def _texto(form, campo: str) -> str:
    v = form.get(campo)
    return v if isinstance(v, str) else ""


@app.post("/api/cargues")
async def crear_cargue(request: Request):
    form = await request.form(max_files=MAX_ARCHIVOS_FORMULARIO, max_fields=MAX_ARCHIVOS_FORMULARIO)
    archivos = [a for a in form.getlist("archivos") if not isinstance(a, str)]
    ruta_local, hospital = _texto(form, "ruta_local"), _texto(form, "hospital")
    enviar = _texto(form, "enviar").lower() in ("true", "1", "on", "si", "sí")
    consolidado = _texto(form, "consolidado").lower() in ("true", "1", "on", "si", "sí")
    entradas: list[Path] = []
    tid = uuid.uuid4().hex[:10]
    ruta_local = ruta_local.strip().strip('"').strip("'")
    if ruta_local:
        p = Path(ruta_local)
        if not p.exists():
            raise HTTPException(400, f"No existe la ruta: {ruta_local}")
        entradas.append(p)
    temporal = carpeta_datos() / "temporal" / tid
    if archivos:
        for a in archivos:
            destino = _ruta_segura(temporal, a.filename or "archivo")
            destino.parent.mkdir(parents=True, exist_ok=True)
            with destino.open("wb") as fh:
                shutil.copyfileobj(a.file, fh, 1 << 20)
        hijos = sorted(temporal.iterdir())
        if not hospital.strip() and any(h.is_dir() and detectar_mes(h.name)[0] for h in hijos):
            shutil.rmtree(temporal, ignore_errors=True)
            raise HTTPException(400, "Subió la carpeta de un mes: escriba el nombre del hospital "
                                     "o suba la carpeta del hospital completa.")
        entradas += hijos
    if not entradas:
        raise HTTPException(400, "Seleccione un ZIP, archivos TXT o una carpeta, o escriba la ruta en este equipo.")
    trabajo = {"id": tid, "estado": "en cola", "fase": "En cola", "inicio": dt.datetime.now().isoformat(timespec="seconds"),
               "entradas": [str(e) for e in entradas] if ruta_local else [a.filename for a in archivos],
               "log": [], "resultado": None, "error": None, "salida": None}
    _trabajos[tid] = trabajo
    threading.Thread(target=_ejecutar, args=(trabajo, entradas, hospital.strip() or None, enviar, consolidado,
                                            temporal if archivos else None), daemon=True).start()
    return {"id": tid}


def _ejecutar(t: dict, entradas: list[Path], hospital, enviar: bool, consolidado: bool, temporal: Path | None):
    raiz = logging.getLogger("rips_auditoria")
    manejador = _CapturaLog(t)
    raiz.addHandler(manejador)
    nivel = raiz.level
    raiz.setLevel(logging.INFO)
    try:
        with _turno:
            t["estado"] = "procesando"
            cfg = cargar_configuracion()
            cfg["exportacion"]["consolidado"] = bool(consolidado)
            salida = carpeta_datos() / f"cargue_{dt.datetime.now():%Y%m%d_%H%M%S}"
            t["salida"] = str(salida)
            t["fase"] = "Leyendo y validando RIPS (Res. 3374)"
            res = procesar(entradas, cfg, hospital=hospital)
            if res.envios.empty:
                raise ValueError("No se encontraron archivos RIPS (CT, AF, US, AC, AP, AU, AH, AN, AM, AT) en lo cargado.")
            t["fase"] = "Generando Excel, JSON y archivos para el aplicativo de auditoría"
            archivos = exportar_todo(res, salida, cfg)
            tabla = res.resumen_unidades()
            inc = res.resumen_incidencias().head(15)
            resultado = {
                # to_json convierte NaN en null (JSON válido)
                "unidades": json.loads(tabla.to_json(orient="records", force_ascii=False)),
                "incidencias": json.loads(inc.to_json(orient="records", force_ascii=False)),
                "archivos": [str(Path(a).relative_to(salida)).replace("\\", "/") for a in archivos],
                "envio": None, "cruce": None, "aviso_envio": None,
            }
            mensuales = [a for a in archivos if Path(a).parent.name == "APLICATIVO_AUDITORIA"]
            if enviar and mensuales:
                t["fase"] = "Enviando cada mes al aplicativo de auditoría"
                try:
                    resultado["envio"] = enviar_meses(mensuales, _cfg_app(cfg), aviso=lambda m: t["log"].append(m))
                    resultado["cruce"] = resumen_cruce(_cfg_app(cfg))
                except ErrorAplicativo as exc:
                    resultado["aviso_envio"] = str(exc)
            t["resultado"] = resultado
            t["estado"] = "terminado"
            t["fase"] = "Cargue terminado"
    except Exception as exc:  # se muestra en la página; el servidor sigue funcionando
        t["estado"] = "error"
        t["fase"] = "El cargue no se completó"
        t["error"] = f"{type(exc).__name__}: {exc}"
        t["log"].append(traceback.format_exc(limit=3))
    finally:
        raiz.removeHandler(manejador)
        raiz.setLevel(nivel)
        if temporal:
            shutil.rmtree(temporal, ignore_errors=True)


@app.get("/api/cargues/{tid}")
def ver_cargue(tid: str, desde: int = 0):
    t = _trabajos.get(tid)
    if not t:
        raise HTTPException(404, "Cargue no encontrado")
    return {**t, "log": t["log"][desde:], "lineas_log": len(t["log"])}


@app.post("/api/cargues/{tid}/abrir-carpeta")
def abrir_carpeta(tid: str, request: Request):
    if not _es_local(request):
        raise HTTPException(400, "La carpeta de resultados está en el equipo servidor: descargue los archivos de la lista.")
    t = _trabajos.get(tid)
    if not t or not t.get("salida") or not Path(t["salida"]).exists():
        raise HTTPException(404, "La carpeta de resultados no existe todavía")
    _abrir(Path(t["salida"]))
    return {"ok": True, "carpeta": t["salida"]}


@app.get("/api/cargues/{tid}/archivo")
def descargar(tid: str, ruta: str):
    t = _trabajos.get(tid)
    if not t or not t.get("salida"):
        raise HTTPException(404, "Cargue no encontrado")
    base = Path(t["salida"]).resolve()
    destino = (base / ruta).resolve()
    if base not in destino.parents or not destino.is_file():
        raise HTTPException(404, "Archivo no encontrado")
    return FileResponse(destino, filename=destino.name)


@app.get("/api/plantilla")
def plantilla():
    from ..excel import generar_plantilla

    ruta = carpeta_datos() / "Plantilla_RIPS_Res3374_AnexoTecnico1.xlsx"
    generar_plantilla(ruta)
    return FileResponse(ruta, filename=ruta.name)


# ---------------------------------------------------------------------------
# Tablero de análisis
# ---------------------------------------------------------------------------

FILTROS = ("desde", "hasta", "sede", "eps", "servicio", "contrato", "modalidad", "validacion", "cups")


def _filtros(request: Request) -> dict:
    q = request.query_params
    f = {k: q.get(k, "") for k in ("desde", "hasta", "cups")}
    f.update({k: q.getlist(k) for k in FILTROS if k not in f})
    return f


def _datos_tablero():
    cfg = cargar_configuracion()
    return (*tb.cargar(tb.carpeta_acumulado(cfg)), cfg.get("tablero") or {})


@app.get("/tablero", response_class=HTMLResponse)
def pagina_tablero():
    return (ESTATICOS / "tablero.html").read_text(encoding="utf-8")


@app.get("/api/tablero/opciones")
def tablero_opciones():
    df, _ind, _p = _datos_tablero()
    return {**tb.opciones(df), "tablas": tb.TABLAS, "carpeta": str(tb.carpeta_acumulado(cargar_configuracion()))}


@app.get("/api/tablero")
def tablero_datos(request: Request):
    df, ind, params = _datos_tablero()
    return tb.tablero(df, ind, _filtros(request), params)


@app.get("/api/tablero/comparativo")
def tablero_comparativo(request: Request, filas: str = "sede", columnas: str = "mes", medida: str = "valor"):
    df, _ind, _p = _datos_tablero()
    try:
        return tb.comparativo(tb.filtrar(df, _filtros(request)), filas, columnas, medida, limite=2000)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/tablero/tabla/{nombre}")
def tablero_tabla(request: Request, nombre: str, pagina: int = 1, tam: int = 100, buscar: str = "",
                  orden: str = "", desc: bool = True, formato: str = "json"):
    if nombre not in tb.TABLAS:
        raise HTTPException(404, f"Tabla no encontrada. Disponibles: {', '.join(tb.TABLAS)}")
    df, ind, params = _datos_tablero()
    t = tb.buscar_y_ordenar(tb.tabla(df, ind, nombre, _filtros(request), params), buscar, orden or None, desc)
    if formato == "csv":
        # CSV completo (todas las filas), separador ; y BOM para abrirlo directo en Excel
        contenido = t.to_csv(index=False, sep=";", decimal=",", encoding="utf-8")
        return Response("\ufeff" + contenido, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="RIPS_{nombre}.csv"'})
    tam = max(1, min(tam, 5000))
    pagina = max(1, pagina)
    parte = t.iloc[(pagina - 1) * tam: pagina * tam]
    return {"nombre": nombre, "titulo": tb.TABLAS[nombre], "total": len(t), "pagina": pagina, "tam": tam,
            "columnas": [str(c) for c in t.columns], "filas": tb._registros(parte)}


@app.get("/api/tablero/excel")
def tablero_excel(request: Request):
    df, ind, params = _datos_tablero()
    if df.empty:
        raise HTTPException(404, "Todavía no hay RIPS cargados para el tablero.")
    ruta = carpeta_datos() / "informes" / f"TABLERO_RIPS_{dt.datetime.now():%Y%m%d_%H%M%S}.xlsx"
    tb.exportar_excel(df, ind, ruta, _filtros(request), params)
    return FileResponse(ruta, filename=ruta.name)


@app.get("/api/tablero/cruce")
def tablero_cruce():
    """Validación del cruce que muestra el aplicativo de auditoría (si está abierto)."""
    c = _cfg_app(cargar_configuracion())
    cliente = ClienteAplicativo(c.get("url"), c.get("puertos") or (8000, 8010), timeout=2)
    if not cliente.detectar():
        return {"conectado": False, "cruce": []}
    try:
        return {"conectado": True, "cruce": resumen_cruce(c)}
    except ErrorAplicativo as exc:
        return {"conectado": True, "cruce": [], "aviso": str(exc)}


@app.post("/api/tablero/enviar")
async def tablero_enviar(request: Request):
    """Envía al aplicativo de auditoría los meses elegidos tal como están en el
    acumulado (todas las sedes), sin volver a procesar los RIPS."""
    cuerpo = await request.json()
    meses = [m for m in (cuerpo.get("meses") or []) if isinstance(m, str)]
    df, _ind, _p = _datos_tablero()
    disponibles = set(df["MES"])
    meses = sorted(m for m in meses if m in disponibles)
    if not meses:
        raise HTTPException(400, "Elija al menos un mes cargado en el tablero.")
    tid = uuid.uuid4().hex[:10]
    trabajo = {"id": tid, "estado": "en cola", "fase": "En cola", "inicio": dt.datetime.now().isoformat(timespec="seconds"),
               "entradas": meses, "log": [], "resultado": None, "error": None, "salida": None}
    _trabajos[tid] = trabajo
    threading.Thread(target=_enviar_desde_tablero, args=(trabajo, df, meses), daemon=True).start()
    return {"id": tid}


def _enviar_desde_tablero(t: dict, df, meses: list[str]):
    try:
        with _turno:
            t["estado"] = "procesando"
            salida = carpeta_datos() / f"envio_tablero_{dt.datetime.now():%Y%m%d_%H%M%S}"
            t["salida"] = str(salida)
            t["fase"] = "Generando los archivos del aplicativo"
            archivos = [escribir_mes(df[df["MES"] == m][list(tb.COLUMNAS)], m, salida / f"RIPS_AUDITORIA_{m}.xlsx")
                        for m in meses]
            t["fase"] = "Enviando cada mes al aplicativo de auditoría"
            cfg = _cfg_app(cargar_configuracion())
            resultado = {"envio": None, "cruce": None, "aviso_envio": None,
                         "archivos": [a.name for a in archivos]}
            try:
                resultado["envio"] = enviar_meses(archivos, cfg, aviso=lambda m: t["log"].append(m))
                resultado["cruce"] = resumen_cruce(cfg)
            except ErrorAplicativo as exc:
                resultado["aviso_envio"] = str(exc)
            t["resultado"] = resultado
            t["estado"] = "terminado"
            t["fase"] = "Envío terminado"
    except Exception as exc:
        t["estado"] = "error"
        t["fase"] = "El envío no se completó"
        t["error"] = f"{type(exc).__name__}: {exc}"
        t["log"].append(traceback.format_exc(limit=3))

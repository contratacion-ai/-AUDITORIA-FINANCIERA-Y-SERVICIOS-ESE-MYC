"""Ventana de cargue (página local) con la imagen de la ESE Moreno y Clavijo.

Se sirve sólo en 127.0.0.1 (puerto 8100-8110, para no chocar con el
aplicativo de auditoría en 8000-8010). Permite:

* cargar un ZIP, TXT sueltos o una carpeta (arrastrando o eligiendo), o
  indicar la ruta de una carpeta/ZIP de este equipo sin copiarla;
* ver el avance y el resumen por hospital y mes;
* generar y **enviar cada mes al aplicativo de auditoría** (abriéndolo si
  está instalado y cerrado) y ver su «validación del cruce»;
* abrir la carpeta de resultados o el aplicativo de auditoría.

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
import uuid
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..aplicativo import ClienteAplicativo, ErrorAplicativo, enviar_meses, resumen_cruce
from ..config import cargar_configuracion, carpeta_datos
from ..exportacion import exportar_todo
from ..lectura import detectar_mes
from ..procesamiento import procesar

ESTATICOS = Path(__file__).parent / "static"
app = FastAPI(title="Cargue RIPS · ESE Moreno y Clavijo", version=__version__, docs_url="/docs")
app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")

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


@app.get("/api/estado")
def estado():
    cfg = cargar_configuracion()
    c = _cfg_app(cfg)
    cliente = ClienteAplicativo(c.get("url"), c.get("puertos") or (8000, 8010), timeout=1.5)
    base = Path(os.environ.get("AUDITORIA_ESE_DIR") or
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "AuditoriaESE_MyC")
    return {"version": __version__, "carpeta_datos": str(carpeta_datos()), "aplicativo": cliente.detectar(),
            "aplicativo_instalado": (base / "iniciar.py").exists(),
            "ocupado": _turno.locked()}


@app.post("/api/aplicativo/abrir")
def abrir_aplicativo():
    cfg = _cfg_app(cargar_configuracion())
    cliente = ClienteAplicativo(cfg.get("url"), cfg.get("puertos") or (8000, 8010), timeout=1.5)
    url = cliente.detectar() or cliente.iniciar_instalado()
    if not url:
        raise HTTPException(404, "No se encontró el aplicativo de Auditoría ESE MyC instalado en este equipo. "
                                 "Instálelo con «Instalador_Auditoria_ESE_MyC» o ábralo manualmente.")
    return {"url": url}


@app.post("/api/cargues")
async def crear_cargue(archivos: list[UploadFile] = File(default=[]), ruta_local: str = Form(""),  # noqa: B008 (patrón de FastAPI)
                       hospital: str = Form(""), enviar: bool = Form(False), consolidado: bool = Form(False)):
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
def abrir_carpeta(tid: str):
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

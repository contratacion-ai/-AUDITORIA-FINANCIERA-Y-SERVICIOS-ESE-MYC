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
import re
import shutil
import subprocess
import sys
import threading
import traceback
import urllib.parse
import uuid
from pathlib import Path, PurePosixPath

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__, catalogo_cups, cruce_medicamentos, depuracion, drive, json_rips, nota_tecnica, nota_tecnica_excel
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


@app.post("/api/apagar")
def apagar(request: Request):
    """Cierra esta ventana de cargue (sólo desde este equipo): lo usa el acceso directo al actualizar de versión."""
    if not _es_local(request):
        raise HTTPException(403, "Sólo desde este equipo.")
    if _turno.locked():
        raise HTTPException(409, "Hay un cargue en curso; espere a que termine.")
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"ok": True}


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
    # el formulario se cierra al salir (libera los archivos temporales de la subida)
    async with request.form(max_files=MAX_ARCHIVOS_FORMULARIO, max_fields=MAX_ARCHIVOS_FORMULARIO) as form:
        archivos = [a for a in form.getlist("archivos") if not isinstance(a, str)]
        ruta_local, hospital = _texto(form, "ruta_local"), _texto(form, "hospital")
        enviar = _texto(form, "enviar").lower() in ("true", "1", "on", "si", "sí")
        consolidado = _texto(form, "consolidado").lower() in ("true", "1", "on", "si", "sí")
        nt = None  # notas técnicas de capitación (pacientes únicos por CUPS y mes), una por EPS
        if _texto(form, "nota_tecnica").lower() in ("true", "1", "on", "si", "sí"):
            subida = form.get("nota_archivo")
            if subida is not None and not isinstance(subida, str) and subida.filename:
                _guardar_nota_subida(subida, _texto(form, "nt_eps") or "NUEVA EPS")
            notas = nota_tecnica.notas_guardadas()
            elegidas = [x for x in _texto(form, "nt_ids").split(",") if x.strip()]
            if elegidas:
                notas = [n for n in notas if n["id"] in elegidas]
            if not notas:
                raise HTTPException(400, "Cargue al menos una nota técnica (códigos CUPS de la EPS) para generarla.")
            nt = {"desde": _texto(form, "nt_desde").strip() or None, "hasta": _texto(form, "nt_hasta").strip() or None,
                  "notas": notas}
        entradas: list[Path] = []
        tid = uuid.uuid4().hex[:10]
        ruta_local = ruta_local.strip().strip('"').strip("'")
        if ruta_local:
            p = Path(ruta_local)
            parece_ruta = any(ch in ruta_local for ch in ("\\", "/", ":"))
            if p.exists():
                entradas.append(p)
            elif archivos and not parece_ruta:
                # Se escribió un nombre (p. ej. del hospital) en el campo de la ruta y ya hay archivos
                # seleccionados: se usa como hospital en lugar de detener el cargue.
                hospital = hospital.strip() or ruta_local
                ruta_local = ""
            else:
                raise HTTPException(400, f"No existe la ruta «{ruta_local}» en el equipo donde corre la aplicación. "
                                         "Ese campo es para la ruta de una carpeta o ZIP (p. ej. E:\\RIPS\\Hospital); "
                                         "el nombre del hospital va en «Hospital (opcional)».")
        temporal = carpeta_datos() / "temporal" / tid
        if archivos:
            for a in archivos:
                destino = _ruta_segura(temporal, a.filename or "archivo")
                destino.parent.mkdir(parents=True, exist_ok=True)
                with destino.open("wb") as fh:
                    shutil.copyfileobj(a.file, fh, 1 << 20)
            sueltos_json = [a for a in archivos if (a.filename or "").lower().endswith((".json", ".xml"))
                            and len(PurePosixPath((a.filename or "").replace("\\", "/")).parts) == 1]
            if sueltos_json and not hospital.strip():
                shutil.rmtree(temporal, ignore_errors=True)
                raise HTTPException(400, "Subió archivos JSON sueltos: escriba el hospital (sede) en las opciones "
                                         "del cargue o suba la carpeta del hospital.")
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
                                            temporal if archivos else None, nt), daemon=True).start()
    return {"id": tid}


def _ejecutar(t: dict, entradas: list[Path], hospital, enviar: bool, consolidado: bool, temporal: Path | None,
              nt: dict | None = None):
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
            con_json = json_rips.hay_json(entradas)
            if res.envios.empty and not con_json:
                raise ValueError("No se encontraron archivos RIPS (CT, AF, US, AC, AP, AU, AH, AN, AM, AT ni JSON) en lo cargado.")
            archivos = []
            if not res.envios.empty:
                t["fase"] = "Generando Excel, JSON y archivos para el aplicativo de auditoría"
                archivos = exportar_todo(res, salida, cfg)
            resumen_json = None
            if con_json:
                # RIPS en JSON (Res. 2275 u homologados): se depuran y se agregan sin duplicar lo ya cargado
                t["fase"] = "Leyendo RIPS en JSON y agregándolos sin duplicar"
                resumen_json = json_rips.cargar_json(entradas, hospital=hospital, cfg=cfg,
                                                     carpeta=tb.carpeta_acumulado(cfg))
                indice_cuv = resumen_json.pop("cuv", {})
                if indice_cuv:  # validación del Ministerio (CUV) por factura
                    from ..cuv import reporte

                    ruta_cuv = salida / "VALIDACION_MSPS.xlsx"
                    ruta_cuv.parent.mkdir(parents=True, exist_ok=True)
                    reporte(indice_cuv).to_excel(ruta_cuv, index=False, sheet_name="CUV por factura")
                    archivos.append(ruta_cuv)
                t["log"].append(f"JSON: {resumen_json['registros_leidos']} registros leídos, "
                                f"{resumen_json['repetidos_en_json']} repetidos en el JSON, "
                                f"{resumen_json['ya_cargadas']} ya cargados, {resumen_json['agregadas']} filas agregadas")
                catalogo_cups.actualizar_descripciones(tb.cargar(tb.carpeta_acumulado(cfg))[0])
            resumenes_nt = []
            if nt:
                # Notas técnicas: pacientes únicos por código y mes, una por EPS, con los mismos RIPS (TXT + JSON)
                partes = [p for p in (nota_tecnica.atenciones_txt(res), nota_tecnica.atenciones_json(entradas, hospital))
                          if len(p)]
                at = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=nota_tecnica.COLS_AT)
                varias = len(nt["notas"]) > 1
                if varias:
                    t["log"].append("Varias EPS: los JSON sin factura electrónica (XML) ni AF no se asignan a ninguna EPS "
                                    "(cargue cada JSON con su XML); quedan reportados en «entidades encontradas».")
                for nota in nt["notas"]:
                    t["fase"] = f"Generando la nota técnica de {nota['eps_nombre']} (pacientes únicos por CUPS y mes)"
                    cfg_n = nota_tecnica.cfg_nota(cfg, nota)
                    if varias:  # un JSON sin EPS no puede sumarse a todas las EPS a la vez
                        cfg_n["nota_tecnica"]["json_sin_eps"] = "excluir"
                    # una nota por municipio si la nota de la EPS trae varios (p. ej. Sanitas)
                    calcs = nota_tecnica.calcular_nota(at, nota["ruta"], nt.get("desde"), nt.get("hasta"), cfg_n)
                    for i_calc, calc in enumerate(calcs):
                        archivos += nota_tecnica_excel.generar(calc, salida / "NOTA_TECNICA")
                        r = calc["resumen"]
                        resumenes_nt.append({k: r.get(k) for k in (
                            "eps", "ambito", "contrato", "poblacion", "sedes_ambito", "sedes_sin_ambito", "desde",
                            "hasta", "codigos_nota", "codigos_con_pacientes", "codigos_sin_registros",
                            "pacientes_unicos_periodo", "registros_incluidos", "duplicados", "excl_anulado",
                            "con_errores", "excl_fuera_periodo", "meses_sin_informacion", "registros_otra_eps",
                            "registros_eps", "entidades_encontradas", "atenciones_periodo", "cumplimiento_meta",
                            "codigos_con_meta", "codigos_bajo_80")})
                        if i_calc:  # las sedes sin municipio se avisan una vez por EPS
                            resumenes_nt[-1]["sedes_sin_ambito"] = []
                        t["log"].append(f"Nota técnica {r['eps']}{' · ' + r['ambito'] if r.get('ambito') else ''}: "
                                        f"{r['codigos_con_pacientes']} de {r['codigos_nota']} códigos con pacientes, "
                                        f"{r['pacientes_unicos_periodo']} pacientes únicos ({r['desde']} a {r['hasta']})")
            tabla = res.resumen_unidades()
            inc = res.resumen_incidencias().head(15)
            resultado = {
                # to_json convierte NaN en null (JSON válido)
                "unidades": json.loads(tabla.to_json(orient="records", force_ascii=False)),
                "incidencias": json.loads(inc.to_json(orient="records", force_ascii=False)),
                "archivos": [str(Path(a).relative_to(salida)).replace("\\", "/") for a in archivos],
                "envio": None, "cruce": None, "aviso_envio": None, "json": resumen_json,
                "notas_tecnicas": resumenes_nt, "nota_tecnica": resumenes_nt[0] if resumenes_nt else None,
            }
            mensuales = [a for a in archivos if Path(a).parent.name == "APLICATIVO_AUDITORIA"]
            if resumen_json and resumen_json.get("meses_json"):
                # los meses con JSON se regeneran desde el acumulado (TXT + JSON, todas las sedes) para el aplicativo
                meses = {m.group(1) for a in mensuales for m in [re.search(r"(\d{4}-\d{2})", Path(a).name)] if m}
                meses |= set(resumen_json["meses_json"])
                nuevos = tb.exportar_meses(meses, salida / "APLICATIVO_AUDITORIA", cfg)
                archivos = [a for a in archivos if Path(a).parent.name != "APLICATIVO_AUDITORIA"] + nuevos
                resultado["archivos"] = [str(Path(a).relative_to(salida)).replace("\\", "/") for a in archivos]
                mensuales = nuevos
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


# ---------------------------------------------------------------------------
# Catálogos de CUPS (informe enriquecido) y depuración de datos cargados
# ---------------------------------------------------------------------------

async def _archivo_subido(request: Request, carpeta: Path) -> Path:
    # el formulario se cierra al salir (libera los archivos temporales de la subida)
    async with request.form() as form:
        a = form.get("archivo")
        if a is None or isinstance(a, str) or not a.filename:
            raise HTTPException(400, "Seleccione el archivo.")
        destino = _ruta_segura(carpeta, Path(a.filename).name)
        destino.parent.mkdir(parents=True, exist_ok=True)
        with destino.open("wb") as fh:
            shutil.copyfileobj(a.file, fh, 1 << 20)
    return destino


def _guardar_nota_subida(subida, eps: str, textos: str = "", codigos: str = "") -> dict:
    tmp = carpeta_datos() / "temporal" / f"nota_{uuid.uuid4().hex[:8]}"
    ruta_nota = _ruta_segura(tmp, Path(subida.filename).name)
    ruta_nota.parent.mkdir(parents=True, exist_ok=True)
    with ruta_nota.open("wb") as fh:
        shutil.copyfileobj(subida.file, fh, 1 << 20)
    try:
        return nota_tecnica.guardar_nota(ruta_nota, eps, textos or None, codigos or None)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, f"El archivo de la nota técnica no es válido: {exc}") from exc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.get("/api/nota-tecnica/estado")
def nota_tecnica_estado():
    notas = [{k: n[k] for k in ("id", "eps_nombre", "archivo", "codigos", "inactivos", "eps_textos", "eps_codigos",
                                "ambitos")}
             for n in nota_tecnica.notas_guardadas()]
    primera = notas[0] if notas else {}
    return {"notas": notas, "archivo": primera.get("archivo"), "codigos": primera.get("codigos"),
            "inactivos": primera.get("inactivos"), "perfiles": nota_tecnica.PERFILES_EPS}


@app.post("/api/nota-tecnica/notas")
async def nota_tecnica_guardar(request: Request):
    """Agrega (o reemplaza) la nota técnica de una EPS."""
    async with request.form() as form:
        subida = form.get("archivo")
        if subida is None or isinstance(subida, str) or not subida.filename:
            raise HTTPException(400, "Seleccione el archivo de la nota técnica.")
        eps = _texto(form, "eps").strip()
        if not eps:
            raise HTTPException(400, "Indique la EPS de la nota técnica.")
        r = _guardar_nota_subida(subida, eps, _texto(form, "textos"), _texto(form, "codigos"))
    return r


@app.delete("/api/nota-tecnica/notas/{id_nota}")
def nota_tecnica_eliminar(id_nota: str):
    if not nota_tecnica.eliminar_nota(id_nota):
        raise HTTPException(404, "Nota técnica no encontrada")
    return {"ok": True}


@app.get("/api/nota-tecnica/plantilla")
def nota_tecnica_plantilla(anio: str = "", id: str = ""):  # noqa: A002
    """Plantilla de la nota técnica con la tabla de todos los servicios de la nota guardada (si la hay)."""
    notas = nota_tecnica.notas_guardadas()
    nota = next((n for n in notas if n["id"] == id), notas[0] if notas else None)
    cat = nota_tecnica.cargar_catalogo(nota["ruta"]) if nota else None
    nombre = f"PLANTILLA_NOTA_TECNICA_{nota['id'].upper() if nota else 'CAPITACION'}.xlsx"
    ruta = nota_tecnica_excel.exportar_nota(carpeta_datos() / nombre, None, cat, anio.strip() or None)
    return FileResponse(ruta, filename=ruta.name)


@app.get("/api/catalogos/estado")
def catalogos_estado():
    return {**catalogo_cups.estado(), "farmacia": cruce_medicamentos.estado()}


@app.get("/api/farmacia/resumen")
def farmacia_resumen(request: Request):
    """Indicadores del cruce de medicamentos facturados vs. portafolio de farmacia (con los filtros del tablero)."""
    df, _ind, _p = _datos_tablero()
    return cruce_medicamentos.resumen(tb.filtrar(df, _filtros(request)))


@app.post("/api/catalogos/{tipo}")
async def catalogos_cargar(tipo: str, request: Request):
    """Carga el archivo complementario (Excel/CSV) o el tarifario UVB y completa las descripciones."""
    if tipo not in ("complemento", "tarifario", "farmacia"):
        raise HTTPException(404, "Catálogo no reconocido")
    temporal = carpeta_datos() / "temporal" / uuid.uuid4().hex[:10]
    ruta = await _archivo_subido(request, temporal)
    try:
        if tipo == "farmacia":  # cruce portafolio de farmacia vs. maestro (CUM)
            r = {**cruce_medicamentos.cargar_cruce(ruta), "descripciones": catalogo_cups.actualizar_descripciones()}
        else:
            r = catalogo_cups.cargar_complemento(ruta) if tipo == "complemento" else catalogo_cups.cargar_tarifario(ruta)
    except (ValueError, KeyError, ImportError) as exc:
        raise HTTPException(400, f"No se pudo leer el archivo: {exc}") from exc
    finally:
        shutil.rmtree(temporal, ignore_errors=True)
    return r  # incluye las descripciones completadas y guardadas


def _carpeta_datos_acumulado() -> Path:
    if _turno.locked():
        raise HTTPException(409, "Hay un cargue en curso. Espere a que termine para limpiar o depurar.")
    return tb.carpeta_acumulado(cargar_configuracion())


@app.post("/api/datos/limpieza/vista")
async def limpieza_vista(request: Request):
    """Muestra (sin borrar) lo que se eliminaría al limpiar un mes, una sede o ambos."""
    c = await request.json()
    try:
        return depuracion.vista_limpieza(c.get("mes"), c.get("sede"), _carpeta_datos_acumulado())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/datos/limpieza/confirmar")
async def limpieza_confirmar(request: Request):
    c = await request.json()
    if c.get("confirmar") is not True:
        raise HTTPException(400, "Falta la confirmación explícita.")
    try:
        return depuracion.aplicar_limpieza(str(c.get("ficha") or ""), c.get("mes"), c.get("sede"),
                                           _carpeta_datos_acumulado())
    except depuracion.FichaVencida as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/datos/duplicados/vista")
def duplicados_vista():
    return depuracion.vista_duplicados(_carpeta_datos_acumulado())


@app.post("/api/datos/duplicados/confirmar")
async def duplicados_confirmar(request: Request):
    c = await request.json()
    if c.get("confirmar") is not True:
        raise HTTPException(400, "Falta la confirmación explícita.")
    try:
        return depuracion.aplicar_duplicados(str(c.get("ficha") or ""), _carpeta_datos_acumulado())
    except depuracion.FichaVencida as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/datos/json")
async def datos_json(request: Request):
    """Lee uno o varios JSON (RIPS Res. 2275 u homologados) y los integra sin duplicar."""
    carpeta = _carpeta_datos_acumulado()
    # el formulario se cierra al salir (libera los archivos temporales de la subida)
    async with request.form(max_files=MAX_ARCHIVOS_FORMULARIO, max_fields=MAX_ARCHIVOS_FORMULARIO) as form:
        archivos = [a for a in form.getlist("archivos") if not isinstance(a, str)]
        if not archivos:
            raise HTTPException(400, "Seleccione uno o varios archivos JSON.")
        hospital = _texto(form, "hospital").strip() or None
        mes = _texto(form, "mes").strip() or None
        temporal = carpeta_datos() / "temporal" / uuid.uuid4().hex[:10]
        for a in archivos:
            destino = _ruta_segura(temporal, a.filename or "archivo.json")
            destino.parent.mkdir(parents=True, exist_ok=True)
            with destino.open("wb") as fh:
                shutil.copyfileobj(a.file, fh, 1 << 20)
    try:
        with _turno:
            r = json_rips.cargar_json(sorted(temporal.iterdir()), hospital=hospital, mes=mes, carpeta=carpeta)
            catalogo_cups.actualizar_descripciones(tb.cargar(carpeta)[0])
        return r
    finally:
        shutil.rmtree(temporal, ignore_errors=True)


# ---------------------------------------------------------------------------
# Google Drive: carpeta de RIPS (JSON + XML + CUV) por hospital y mes
# ---------------------------------------------------------------------------

@app.get("/api/drive/estado")
def drive_estado():
    e = drive.estado()
    tid = _drive_en_curso.get("id")
    e["en_curso"] = tid if tid and _trabajos.get(tid, {}).get("estado") in ("en cola", "procesando") else None
    return e


@app.get("/api/drive/buscar")
def drive_buscar():
    """Carpetas candidatas en las unidades de Google Drive para escritorio de este equipo."""
    candidatas = [str(x) for x in drive.buscar_carpetas_locales()]
    return {"candidatas": candidatas, "unidades": [str(x) for x in drive.unidades_drive()],
            "diagnostico": "" if candidatas else drive.diagnostico_local("")}


@app.post("/api/drive/config")
async def drive_config(request: Request):
    datos = await request.json()
    c = drive.guardar_configuracion(datos if isinstance(datos, dict) else {})
    _programar_drive()
    return c


@app.post("/api/drive/credenciales")
async def drive_credenciales(request: Request):
    async with request.form() as form:
        archivo = form.get("archivo")
        if archivo is None or not hasattr(archivo, "read"):
            raise HTTPException(400, "Seleccione el archivo de credenciales (JSON) descargado de Google Cloud.")
        try:
            datos = json.loads((await archivo.read()).decode("utf-8-sig"))
            drive.guardar_credenciales(datos)
        except (ValueError, drive.ErrorDrive) as exc:
            raise HTTPException(400, str(exc)) from exc
    return drive.estado_api()


@app.get("/api/drive/conectar")
def drive_conectar(request: Request):
    if not _es_local(request):
        raise HTTPException(403, "La conexión con Google se hace desde el equipo donde corre la aplicación.")
    redirect = f"http://127.0.0.1:{request.url.port or 8100}/api/drive/oauth"
    try:
        url, _estado = drive.url_autorizacion(redirect)
    except drive.ErrorDrive as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url)


@app.get("/api/drive/oauth", response_class=HTMLResponse)
def drive_oauth(code: str = "", state: str = "", error: str = ""):
    if error or not code:
        return HTMLResponse(f"<p>Google no autorizó el acceso ({error or 'sin código'}). Cierre esta pestaña.</p>", 400)
    try:
        e = drive.canjear_codigo(code, state)
    except drive.ErrorDrive as exc:
        return HTMLResponse(f"<p>{exc}</p>", 400)
    return HTMLResponse(f"<p>Conectado a Google Drive {e.get('cuenta') or ''}. Ya puede cerrar esta pestaña y "
                        "volver a la ventana de cargue.</p><script>setTimeout(() => location.href = '/', 2500)</script>")


@app.post("/api/drive/desconectar")
def drive_desconectar():
    drive.desconectar()
    return drive.estado_api()


@app.post("/api/drive/reiniciar")
def drive_reiniciar():
    drive.reiniciar_procesado()
    return {"ok": True}


_drive_en_curso: dict = {}


@app.post("/api/drive/sincronizar")
def drive_sincronizar(cuerpo: dict | None = None):
    if _drive_en_curso.get("id") and _trabajos.get(_drive_en_curso["id"], {}).get("estado") in ("en cola", "procesando"):
        return {"id": _drive_en_curso["id"]}
    return {"id": _lanzar_drive(bool((cuerpo or {}).get("solo_revisar")))}


def _lanzar_drive(solo_revisar: bool = False) -> str:
    tid = uuid.uuid4().hex[:10]
    t = {"id": tid, "estado": "en cola", "fase": "En cola", "inicio": dt.datetime.now().isoformat(timespec="seconds"),
         "entradas": ["Google Drive"], "log": [], "resultado": None, "error": None, "salida": None}
    _trabajos[tid] = t
    _drive_en_curso["id"] = tid
    threading.Thread(target=_ejecutar_drive, args=(t, solo_revisar), daemon=True).start()
    return tid


def _ejecutar_drive(t: dict, solo_revisar: bool):
    raiz = logging.getLogger("rips_auditoria")
    manejador = _CapturaLog(t)
    raiz.addHandler(manejador)
    nivel = raiz.level
    raiz.setLevel(logging.INFO)
    try:
        with _turno:
            t["estado"] = "procesando"
            t["fase"] = "Sincronizando con Google Drive"
            r = drive.sincronizar(aviso=lambda m: t["log"].append(f"{dt.datetime.now():%H:%M:%S}  {m}"),
                                  solo_revisar=solo_revisar)
            t["salida"] = r.get("salida")
            base = Path(r["salida"]) if r.get("salida") else None
            r["archivos"] = [str(Path(a).relative_to(base)).replace("\\", "/") if base else a for a in r["archivos"]]
            t["resultado"] = {"drive": r, "envio": r.get("envio"), "cruce": r.get("cruce"), "unidades": [],
                              "incidencias": [], "aviso_envio": r.get("aviso_envio"), "archivos": r["archivos"]}
            t["estado"] = "terminado"
            t["fase"] = "Sincronización terminada"
    except Exception as exc:  # se muestra en la ventana
        t["estado"] = "error"
        t["fase"] = "La sincronización no se completó"
        if isinstance(exc, drive.ErrorDrive):  # mensaje para el usuario, sin detalle técnico
            t["error"] = str(exc)
            t["log"].append(str(exc))
        else:
            t["error"] = f"{type(exc).__name__}: {exc}"
            t["log"].append(traceback.format_exc(limit=3))
    finally:
        raiz.removeHandler(manejador)
        raiz.setLevel(nivel)


_programador: dict = {"hilo": None, "detener": threading.Event()}


def _programar_drive() -> None:
    """Revisión automática de Drive cada ``revisar_cada_min`` minutos mientras la aplicación esté abierta."""
    minutos = int(drive.configuracion(cargar_configuracion()).get("revisar_cada_min") or 0)
    _programador["detener"].set()
    if minutos <= 0:
        return
    detener = threading.Event()
    _programador["detener"] = detener

    def ciclo():
        while not detener.wait(minutos * 60):
            if not _turno.locked():
                try:
                    _lanzar_drive()
                except Exception:  # noqa: BLE001 - la revisión automática no debe tumbar el servidor
                    logging.getLogger(__name__).exception("Revisión automática de Drive")

    _programador["hilo"] = threading.Thread(target=ciclo, daemon=True)
    _programador["hilo"].start()


try:  # al abrir la aplicación
    _programar_drive()
except Exception:  # noqa: BLE001
    logging.getLogger(__name__).exception("No se pudo programar la revisión de Drive")

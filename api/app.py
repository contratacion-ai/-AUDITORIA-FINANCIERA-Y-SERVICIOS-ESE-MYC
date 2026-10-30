"""API FastAPI e interfaz web local.

Ejecutar:  uvicorn api.app:app --host 127.0.0.1 --port 8000
Los datos (RIPS, maestros, resultados) quedan en ``datos/`` del equipo local; no se envían a
servicios externos. Los informes agregados usan seudónimos de paciente.
"""
from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from rips_auditoria import __version__, base, fuentes, maestros
from rips_auditoria.analisis import Resultado, analizar, controles_referencia
from rips_auditoria.config import DIR_DATOS, DIR_REFERENCIAS, guardar_sedes, red_prestadores
from rips_auditoria.informes import excel, pdf
from rips_auditoria.ingesta.lote import Lote, corregir, procesar

AQUI = Path(__file__).resolve().parent
DIR_LOTES = DIR_DATOS / "lotes"
DIR_RES = DIR_DATOS / "resultados"

app = FastAPI(title="Auditoría RIPS – E.S.E. Moreno y Clavijo", version=__version__)
app.mount("/static", StaticFiles(directory=AQUI / "static"), name="static")
app.mount("/referencias", StaticFiles(directory=DIR_REFERENCIAS, html=True), name="referencias")


def _tabla(df: pd.DataFrame, n: int | None = None) -> list[dict]:
    if df is None or df.empty:
        return []
    d = df.head(n) if n else df
    return json.loads(d.to_json(orient="records", date_format="iso", force_ascii=False))


def _lote(lid: str) -> Lote:
    p = DIR_LOTES / f"{lid}.pkl"
    if not p.exists():
        raise HTTPException(404, f"Lote {lid} no existe")
    return Lote.abrir(p)


def _resumen_lote(l: Lote) -> dict:
    inc = l.incidencias
    return {"id": l.id, "creado": l.creado, "notas": l.notas,
            "registros": {t: int(len(df)) for t, df in l.tablas.items()},
            "duplicados_eliminados": int(len(l.duplicados)),
            "indice": _tabla(l.resumen()),
            "remisiones": _tabla(l.remisiones.drop(columns=[c for c in l.remisiones.columns if c.startswith("reg_")],
                                                   errors="ignore")),
            "incidencias": _tabla(inc.groupby(["severidad", "regla"]).size().rename("cantidad").reset_index()
                                  if not inc.empty else inc),
            "archivos_no_procesados": _tabla(l.archivos[l.archivos["estado"] != "procesado"]
                                             if not l.archivos.empty else l.archivos)}


@app.get("/", response_class=HTMLResponse)
def inicio():
    return (AQUI / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/api/salud")
def salud():
    return {"ok": True, "version": __version__, "datos": str(DIR_DATOS)}


@app.post("/api/apagar")
def apagar(request: Request):
    """Cierra la aplicación de escritorio (solo desde el mismo equipo)."""
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(403)
    import os
    import threading
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"cerrando": True}


# ------------------------------------------------------------------------- lotes RIPS
@app.post("/api/lotes")
async def crear_lote(archivos: list[UploadFile] = File(...), rutas: list[str] = Form(default=[]),
                     hospital: str = Form(default=""), municipio: str = Form(default=""),
                     periodo: str = Form(default=""), cod_entidad: str = Form(default=""),
                     anio: int = Form(default=2026), actualizar_base: bool = Form(default=True),
                     modo: str = Form(default="mes")):
    """Carga archivos sueltos, ZIP/RAR o una carpeta (``rutas`` = webkitRelativePath de cada archivo) y
    actualiza la base consolidada: cada (hospital, mes) cargado reemplaza al anterior."""
    entradas = []
    for i, a in enumerate(archivos):
        ruta = rutas[i] if i < len(rutas) and rutas[i] else a.filename
        entradas.append((ruta, await a.read()))
    manual = {k: v for k, v in {"hospital": hospital, "municipio": municipio.upper(), "periodo": periodo,
                                "cod_entidad": cod_entidad}.items() if v}
    lote = procesar(entradas, anio_defecto=anio, manual_global=manual)
    lote.guardar(DIR_LOTES)
    act = base.actualizar(lote, origen="carga web", modo=modo) if actualizar_base else None
    return {**_resumen_lote(lote), "base": act}


@app.get("/api/lotes")
def listar_lotes():
    out = []
    for p in sorted(DIR_LOTES.glob("*.pkl"), reverse=True):
        l = Lote.abrir(p)
        idx = l.resumen()
        out.append({"id": l.id, "creado": l.creado, "facturas": int(len(l.tablas["AF"])),
                    "hospitales": sorted(idx["hospital"].dropna().unique().tolist()) if not idx.empty else [],
                    "periodos": sorted(idx["periodo"].dropna().unique().tolist()) if not idx.empty else []})
    return out


@app.get("/api/lotes/{lid}")
def ver_lote(lid: str):
    return _resumen_lote(_lote(lid))


@app.patch("/api/lotes/{lid}/remisiones")
def corregir_remisiones(lid: str, cambios: list[dict] = Body(...)):
    """Corrección manual: ``[{"envio_id": "E00001", "periodo": "2026-08", "municipio": "TAME"}]``."""
    l = _lote(lid)
    total = sum(corregir(l, c["envio_id"], **{k: v for k, v in c.items() if k != "envio_id"}) for c in cambios)
    l.guardar(DIR_LOTES)
    if base.eliminar_lote(l.id):           # la base se actualiza con la corrección
        base.actualizar(l, origen="corrección manual")
    return {"registros_actualizados": total, **_resumen_lote(l)}


@app.get("/api/lotes/{lid}/incidencias.xlsx")
def incidencias_xlsx(lid: str):
    l = _lote(lid)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as xw:
        l.resumen().to_excel(xw, sheet_name="Indice_Hospital_Mes", index=False)
        l.remisiones.to_excel(xw, sheet_name="Remisiones", index=False)
        l.incidencias.to_excel(xw, sheet_name="Incidencias", index=False)
        l.archivos.to_excel(xw, sheet_name="Archivos", index=False)
        if not l.duplicados.empty:
            l.duplicados[["tipo_archivo", "archivo", "linea", "conservado_en", "motivo"]].to_excel(
                xw, sheet_name="Duplicados_Eliminados", index=False)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="validacion_{lid}.xlsx"'})


@app.delete("/api/lotes/{lid}")
def borrar_lote(lid: str):
    p = DIR_LOTES / f"{lid}.pkl"
    if p.exists():
        p.unlink()
    return {"borrado": lid}


# ------------------------------------------------------------------------- red de sedes
@app.get("/api/sedes")
def ver_sedes():
    crudo = red_prestadores().get("sedes") or []
    return {"sedes": crudo if isinstance(crudo, list) else [
        {"nombre": k, "municipio": v, "alias": [k], "cod_habilitacion": [], "verificado": False} for k, v in crudo.items()],
        "municipios": sorted(set(red_prestadores().get("municipios", {}).values()))}


@app.put("/api/sedes")
def guardar_sedes_api(sedes: list[dict] = Body(..., embed=True)):
    """Reemplaza la lista de sedes. Las cargas siguientes usan los nuevos nombres; para renombrar lo ya
    cargado, sincronice con 'Reprocesar todo' o vuelva a cargar las carpetas."""
    for s in sedes:
        if not str(s.get("nombre", "")).strip() or not str(s.get("municipio", "")).strip():
            raise HTTPException(400, "Cada sede necesita nombre y municipio")
    guardar_sedes(sedes)
    return ver_sedes()


# ------------------------------------------------------------------------- maestros
@app.get("/api/maestros")
def ver_maestros():
    return maestros.estado()


@app.post("/api/maestros/{nombre}")
async def cargar_maestro(nombre: str, archivo: UploadFile = File(...)):
    """``nombre`` = informe | catalogo | nota_tecnica | tarifario | facturacion | despacho_medicamentos."""
    try:
        return maestros.cargar(nombre, await archivo.read(), archivo.filename)
    except (ValueError, KeyError, StopIteration) as e:
        raise HTTPException(400, f"No se pudo leer el maestro: {e}")


@app.get("/api/maestros/plantillas/{nombre}.csv")
def plantilla(nombre: str):
    if nombre not in maestros.PLANTILLAS:
        raise HTTPException(404)
    return StreamingResponse(io.StringIO(",".join(maestros.PLANTILLAS[nombre]) + "\n"), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{nombre}.csv"'})


# ------------------------------------------------------------------------- análisis e informes
def _guardar_resultado(r: Resultado) -> str:
    DIR_RES.mkdir(parents=True, exist_ok=True)
    rid = datetime.now().strftime("%Y%m%d%H%M%S")
    r.guardar(DIR_RES / f"{rid}.json")
    return rid


def _resultado(rid: str) -> Resultado:
    p = DIR_RES / f"{rid}.json"
    if not p.exists():
        raise HTTPException(404, f"Resultado {rid} no existe")
    return Resultado.desde_json(p.read_text(encoding="utf-8"))


def _resumen_resultado(rid: str, r: Resultado) -> dict:
    return {"id": rid, "alcance": r.alcance, "indicadores": r.indicadores, "advertencias": r.advertencias,
            "detalle_mensual": _tabla(r.tablas["detalle_mensual"]), "capita_grupos": _tabla(r.tablas["capita_grupos"]),
            "frecuencias_nt_municipio_mes": _tabla(r.tablas.get("frecuencias_nt_municipio_mes")),
            "frecuencias_nt_grupo": _tabla(r.tablas.get("frecuencias_nt_grupo")),
            "frecuencias_nt_codigo": _tabla(r.tablas.get("frecuencias_nt_codigo"), 60),
            "evento_mensual": _tabla(r.tablas.get("evento_mensual")),
            "top20_evento": _tabla(r.tablas["top20_evento"]), "hallazgos": _tabla(r.tablas["hallazgos"], 50)}


@app.post("/api/analisis")
def ejecutar_analisis(lotes: list[str] = Body(default=[], embed=True), hospitales: list[str] = Body(default=[], embed=True),
                      municipios: list[str] = Body(default=[], embed=True), periodos: list[str] = Body(default=[], embed=True),
                      solo_eps_objetivo: bool = Body(True, embed=True)):
    """Analiza la base consolidada (con filtros) o, si se indican, lotes puntuales."""
    try:
        fuente = [_lote(x) for x in lotes] if lotes else [base.lote(hospitales or None, municipios or None,
                                                                     periodos or None)]
        r = analizar(fuente, solo_eps_objetivo=solo_eps_objetivo)
    except ValueError as e:
        raise HTTPException(400, str(e))
    rid = _guardar_resultado(r)
    return _resumen_resultado(rid, r)


# ------------------------------------------------------------------------- base consolidada y Drive
@app.get("/api/base")
def ver_base():
    df = base.listar()
    return {"resumen": base.resumen_tablas(), "particiones": _tabla(df),
            "filtros": {"hospitales": sorted(df["hospital"].dropna().unique().tolist()) if not df.empty else [],
                        "municipios": sorted(df["municipio"].dropna().unique().tolist()) if not df.empty else [],
                        "periodos": sorted(df["periodo"].dropna().unique().tolist()) if not df.empty else []}}


@app.delete("/api/base")
def limpiar_base(hospital: str | None = None, periodo: str | None = None, municipio: str | None = None,
                 clave: str | None = None):
    return {"particiones_eliminadas": base.eliminar(hospital, periodo, municipio, clave)}


@app.post("/api/base/vaciar")
def vaciar_base(confirmar: bool = Body(False, embed=True)):
    if not confirmar:
        raise HTTPException(400, "Confirme el vaciado de la base")
    return {"particiones_eliminadas": base.vaciar()}


@app.get("/api/fuentes")
def ver_fuentes():
    d = fuentes.leer()
    return {"carpetas": d["carpetas"], "ultima_sincronizacion": d.get("ultima_sincronizacion"),
            "unidades_registradas": len(d.get("huellas", {}))}


@app.post("/api/fuentes")
def agregar_fuente(ruta: str = Body(..., embed=True)):
    try:
        return fuentes.agregar(ruta)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/fuentes")
def quitar_fuente(ruta: str):
    return fuentes.quitar(ruta)


@app.post("/api/fuentes/sincronizar")
def sincronizar(forzar: bool = Body(False, embed=True)):
    return fuentes.sincronizar(forzar=forzar)


@app.get("/api/analisis/{rid}")
def ver_resultado(rid: str):
    return _resumen_resultado(rid, _resultado(rid))


@app.get("/api/analisis/{rid}/informe.xlsx")
def informe_xlsx(rid: str):
    buf = excel.escribir(_resultado(rid), io.BytesIO())
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="informe_RIPS_{rid}.xlsx"'})


@app.get("/api/analisis/{rid}/informe.pdf")
def informe_pdf(rid: str):
    buf = pdf.escribir(_resultado(rid), io.BytesIO())
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="informe_RIPS_{rid}.pdf"'})


@app.get("/api/analisis/{rid}/estado.json")
def estado_json(rid: str):
    return FileResponse(DIR_RES / f"{rid}.json", media_type="application/json", filename=f"estado_{rid}.json")


@app.post("/api/analisis/restaurar")
async def restaurar(archivo: UploadFile = File(...)):
    try:
        r = Resultado.desde_json((await archivo.read()).decode("utf-8"))
    except (ValueError, KeyError) as e:
        raise HTTPException(400, f"JSON no reconocido: {e}")
    rid = _guardar_resultado(r)
    return _resumen_resultado(rid, r)


# ------------------------------------------------------------------------- referencia
@app.get("/api/referencia")
def referencia():
    return JSONResponse(json.loads(json.dumps(controles_referencia(), default=float)))

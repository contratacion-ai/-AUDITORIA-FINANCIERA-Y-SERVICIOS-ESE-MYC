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
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from rips_auditoria import __version__, maestros
from rips_auditoria.analisis import Resultado, analizar, controles_referencia
from rips_auditoria.config import DIR_DATOS, DIR_REFERENCIAS
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


# ------------------------------------------------------------------------- lotes RIPS
@app.post("/api/lotes")
async def crear_lote(archivos: list[UploadFile] = File(...), rutas: list[str] = Form(default=[]),
                     hospital: str = Form(default=""), municipio: str = Form(default=""),
                     periodo: str = Form(default=""), cod_entidad: str = Form(default=""),
                     anio: int = Form(default=2026)):
    """Carga archivos sueltos, ZIP/RAR o una carpeta (``rutas`` = webkitRelativePath de cada archivo)."""
    entradas = []
    for i, a in enumerate(archivos):
        ruta = rutas[i] if i < len(rutas) and rutas[i] else a.filename
        entradas.append((ruta, await a.read()))
    manual = {k: v for k, v in {"hospital": hospital, "municipio": municipio.upper(), "periodo": periodo,
                                "cod_entidad": cod_entidad}.items() if v}
    lote = procesar(entradas, anio_defecto=anio, manual_global=manual)
    lote.guardar(DIR_LOTES)
    return _resumen_lote(lote)


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
            "detalle_mensual": _tabla(r.tablas["detalle_mensual"]), "escenarios": _tabla(r.tablas["escenarios"]),
            "capita_grupos": _tabla(r.tablas["capita_grupos"]), "top20_evento": _tabla(r.tablas["top20_evento"]),
            "hallazgos": _tabla(r.tablas["hallazgos"], 50)}


@app.post("/api/analisis")
def ejecutar_analisis(lotes: list[str] = Body(..., embed=True), solo_eps_objetivo: bool = Body(True, embed=True)):
    try:
        r = analizar([_lote(x) for x in lotes], solo_eps_objetivo=solo_eps_objetivo)
    except ValueError as e:
        raise HTTPException(400, str(e))
    rid = _guardar_resultado(r)
    return _resumen_resultado(rid, r)


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

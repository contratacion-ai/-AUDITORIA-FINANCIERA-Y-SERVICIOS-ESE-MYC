"""API FastAPI. Documentación interactiva de endpoints: /docs (OpenAPI)."""
import io, os, shutil, tempfile, zipfile
from datetime import datetime
from pathlib import Path
import numpy as np, pandas as pd
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook
from . import analysis as A, costos as C, exporter, plantillas as PL, processing as P
from .db import UPLOADS, df, init
from .loaders import ALIAS, _shared, hojas_mensuales, parse_mes

app = FastAPI(title="Auditoría de facturación · ESE Moreno y Clavijo", version="4.0",
              description="Carga mensual de facturación por ámbito (Software 360), RIPS, costos y notas técnicas; conciliación y reportes.")
init()
TIPOS = ("facturacion", "rips", "costos", "notas", "parametros", "codigos")

def _json(d, limit=None):
    total = len(d)
    d = d.head(limit) if limit else d
    return dict(total=total, columnas=list(d.columns), filas=d.astype(object).where(d.notna(), None).to_dict("records"))

@app.get("/api/health", tags=["sistema"])
def health(): return {"ok": True, "hora": datetime.now().isoformat(timespec="seconds")}

@app.get("/api/meses", tags=["datos"], summary="Meses con datos por fuente")
def meses():
    return {t: df(f"SELECT DISTINCT mes FROM {t} ORDER BY mes").mes.tolist() for t in ("fact", "rips", "costo")}

# ── 1. Carga ──
@app.post("/api/cargas/{tipo}", tags=["cargas"], summary="Subir archivo (facturacion | rips | costos | notas)")
def cargar(tipo: str, bg: BackgroundTasks, file: UploadFile = File(...), mes: str | None = Form(None, description="AAAA-MM (opcional si el archivo ya trae el mes)")):
    """facturacion: xlsx de Software 360 con hojas 'ENERO 2026', 'FEBRERO 2026'… (una carga por mes detectado).
    rips/costos: Excel/CSV con columna de mes, hojas nombradas por mes, o parámetro `mes`. notas: tarifas/topes por EPS."""
    if tipo not in TIPOS: raise HTTPException(404, f"Tipo inválido. Use: {TIPOS}")
    ext = Path(file.filename).suffix.lower()
    if ext not in (".xlsx", ".xlsm", ".xls", ".csv") or (tipo in ("facturacion", "parametros") and ext not in (".xlsx", ".xlsm")):
        raise HTTPException(422, "Formato no soportado (xlsx/xlsm/xls/csv; facturación solo xlsx).")
    if mes and not parse_mes(mes): raise HTTPException(422, "El mes debe tener formato AAAA-MM.")
    mes = parse_mes(mes) if mes else None
    path = os.path.join(UPLOADS, f"{datetime.now():%Y%m%d_%H%M%S}_{Path(file.filename).name}")
    with open(path, "wb") as o: shutil.copyfileobj(file.file, o, 1 << 20)
    if tipo == "facturacion":
        try:
            with zipfile.ZipFile(path) as z: hojas = hojas_mensuales(z)
        except Exception as e: raise HTTPException(422, "El archivo no es un Excel (.xlsx) válido o está dañado.")
        sel = [m for m in sorted(hojas) if not mes or m == mes]
        if not sel: raise HTTPException(422, f"No se encontraron hojas mensuales ('ENERO 2026'…). Hojas: {[h for h in hojas] or 'ninguna'}")
        jobs = [(P.registrar(tipo, m, file.filename), m) for m in sel]
        bg.add_task(P.procesar_facturacion, path, jobs)
        return {"cargas": [{"id": i, "mes": m} for i, m in jobs]}
    cid = P.registrar(tipo, mes, file.filename)
    bg.add_task(P.procesar_parametros, cid, path) if tipo == "parametros" else bg.add_task(P.procesar_tabla, cid, tipo, path, mes)
    return {"cargas": [{"id": cid, "mes": mes}]}

@app.get("/api/cargas", tags=["cargas"], summary="Historial de cargas")
def cargas(): return _json(df("SELECT * FROM carga ORDER BY id DESC"))

@app.get("/api/cargas/{cid}", tags=["cargas"])
def carga(cid: int):
    d = df("SELECT * FROM carga WHERE id=?", (cid,))
    if d.empty: raise HTTPException(404, "Carga no encontrada")
    return _json(d)["filas"][0]

@app.get("/api/plantillas/{tipo}", tags=["cargas"], summary="Plantilla homologada (rips|costos|notas|parametros) con hoja de instrucciones y llaves comunes")
def plantilla(tipo: str):
    if tipo not in ("rips", "costos", "notas", "parametros", "codigos"): raise HTTPException(404, "Plantillas: rips, costos, notas, parametros, codigos")
    b = io.BytesIO(); PL.plantilla(tipo).save(b); b.seek(0)
    return StreamingResponse(b, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f"attachment; filename=plantilla_{tipo}.xlsx"})

@app.post("/api/homologar/{tipo}", tags=["cargas"], summary="Convierte un archivo nativo (consolidado RIPS, comparativo de notas técnicas, costos) al formato homologado")
def homologar(tipo: str, file: UploadFile = File(...), mes: str | None = Form(None)):
    if tipo not in ("rips", "costos", "notas"): raise HTTPException(404, "Tipos: rips, costos, notas")
    path = os.path.join(UPLOADS, f"hom_{datetime.now():%Y%m%d_%H%M%S}_{Path(file.filename).name}")
    with open(path, "wb") as o: shutil.copyfileobj(file.file, o, 1 << 20)
    try: ruta, n = PL.homologar(tipo, path, parse_mes(mes) if mes else None, P.eps_alias())
    except zipfile.BadZipFile: raise HTTPException(422, "El archivo no es un Excel (.xlsx) válido o está dañado.")
    except Exception as e: raise HTTPException(422, f"{type(e).__name__}: {e}")
    return FileResponse(ruta, filename=f"HOMOLOGADO_{tipo.upper()}_{Path(file.filename).stem}.xlsx")

@app.get("/api/homologacion/codigos-pendientes", tags=["datos"], summary="Plantilla de códigos RIPS (CUMS/CUPS) que no existen en Software 360, lista para completar y cargar (tipo 'codigos')")
def codigos_pendientes(mes: str | None = None):
    w = " AND mes=?" if mes else ""
    d = df(f"""SELECT cups CODIGO_RIPS, servicio TIPO_SERVICIO, SUM(cantidad) FRECUENCIA FROM rips WHERE cups<>'' AND cups NOT IN (SELECT cups FROM fact)
               AND cups NOT IN (SELECT codigo_rips FROM hom_codigo){w} GROUP BY 1,2 ORDER BY 3 DESC""", (mes,) if mes else ())
    d["CODIGO_SOFTWARE360"] = ""; d["DESCRIPCION"] = ""
    ruta = os.path.join(tempfile.mkdtemp(), "codigos_pendientes.xlsx"); wb = PL.plantilla("codigos"); ws = wb["PLANTILLA"]; ws.delete_rows(2)
    for r in d[["CODIGO_RIPS", "CODIGO_SOFTWARE360", "DESCRIPCION", "TIPO_SERVICIO", "FRECUENCIA"]].itertuples(index=False): ws.append(list(r))
    ws["D1"], ws["E1"] = "TIPO_SERVICIO (referencia)", "FRECUENCIA (referencia)"; wb.save(ruta)
    return FileResponse(ruta, filename="codigos_pendientes_por_homologar.xlsx")

@app.get("/api/parametros", tags=["datos"], summary="PARAM CONTRATO (modalidad) y PARAM SERVICIO vigentes")
def parametros(): return {"contratos": _json(df("SELECT * FROM param_contrato ORDER BY contrato")), "servicios": df("SELECT servicio FROM param_servicio ORDER BY 1").servicio.tolist()}

@app.get("/api/homologacion/eps", tags=["datos"], summary="Alias de EPS usados para homologar nombres (editable)")
def hom_eps(): return _json(df("SELECT * FROM hom_eps ORDER BY eps"))

@app.put("/api/homologacion/eps", tags=["datos"], summary="Agrega/actualiza un alias de EPS: {alias, eps}")
def hom_eps_put(alias: str = Form(...), eps: str = Form(...)):
    from .db import conn
    with conn() as c: c.execute("INSERT OR REPLACE INTO hom_eps VALUES(?,?)", (alias.strip(), eps.strip()))
    return {"ok": True}

# ── 2-3. Informes y análisis ──
@app.get("/api/resumen", tags=["dashboard"], summary="KPIs, serie mensual, ámbito y recomendaciones")
def resumen(mes: str | None = None):
    r = A.resumen(mes); return {k: (v if k != "kpis" else {a: (float(b) if isinstance(b, (np.floating, np.integer)) else b) for a, b in v.items()}) for k, v in r.items()}

@app.get("/api/informes/{nombre}", tags=["informes"], summary="Informes parametrizados 1–8 (ver /api/informes)")
def informes(nombre: str, mes: str | None = None, modalidad: str | None = None, limit: int = 1000):
    if nombre not in A.INFORMES: raise HTTPException(404, f"Informes: {list(A.INFORMES)}")
    return _json(A.informe(nombre, mes, modalidad.upper() if modalidad else None), limit)

@app.get("/api/informes", tags=["informes"])
def lista_informes(): return A.INFORMES

@app.get("/api/conciliacion/fact-rips", tags=["conciliación"], summary="Facturación vs RIPS por eps|servicio|cups|contrato|sede")
def c_fr(mes: str | None = None, nivel: str = "eps", limit: int = 1000):
    if nivel not in ("eps", "servicio", "cups", "contrato", "sede", "modalidad"): raise HTTPException(422, "nivel inválido")
    return _json(A.fact_vs_rips(mes, nivel), limit)

@app.get("/api/conciliacion/tri-eje", tags=["conciliación"], summary="Facturación vs RIPS vs costos por servicio")
def c_te(mes: str | None = None, nivel: str = "servicio", limit: int = 1000):
    if nivel not in ("servicio", "sede", "cups"): raise HTTPException(422, "nivel: servicio|sede|cups")
    return _json(A.tri_eje(mes, nivel), limit)

@app.get("/api/conciliacion/radicacion", tags=["conciliación"], summary="Validación de radicación factura a factura")
def c_ra(mes: str | None = None, estado: str | None = None, limit: int = 1000):
    d = A.radicacion(mes); d = d[d.estado == estado] if estado else d; return _json(d, limit)

@app.get("/api/resumen/empresas", tags=["resumen"], summary="Resumen desagregado por empresa (EPS)")
def r_emp(mes: str | None = None): return dict(empresas=_json(A.resumen_empresa(mes)), mensual=_json(A.resumen_empresa_mensual(mes)))

@app.get("/api/costos/cruce", tags=["costos"], summary="Cruce Costos × RIPS × Facturación × Notas técnicas por CUPS")
def c_cruce(mes: str | None = None, eps: str | None = None, estado: str | None = None, limit: int = 2000):
    d = C.cruce(mes, eps); d = d[d.estado.str.contains(estado, regex=False)] if estado else d; return _json(d, limit)

@app.get("/api/costos/resumen", tags=["costos"], summary="KPIs, estados y servicios del cruce de costos")
def c_res(mes: str | None = None): return C.resumen(mes)

@app.get("/api/costos/validaciones", tags=["costos"], summary="Errores y advertencias de la carga de costos")
def c_val(carga_id: int | None = None, limit: int = 2000): return _json(C.validaciones(carga_id), limit)

@app.get("/api/conciliacion/notas-tecnicas", tags=["conciliación"], summary="Notas técnicas EPS vs facturación y RIPS")
def c_nt(mes: str | None = None, limit: int = 1000): return _json(A.notas_vs_fact(mes), limit)

@app.get("/api/conciliacion/contratos", tags=["conciliación"], summary="Comportamiento de contratos (modalidad, participación, ticket, hallazgos)")
def c_ct(mes: str | None = None, limit: int = 1000): return _json(A.contratos(mes), limit)

@app.get("/api/conciliacion/validacion-cruce", tags=["conciliación"], summary="Cobertura de llaves comunes entre las 4 fuentes (EPS, contrato, sede, factura, CUPS, servicio)")
def c_cv(mes: str | None = None): return _json(A.cobertura(mes))

@app.get("/api/conciliacion/hallazgos", tags=["conciliación"], summary="Inconsistencias de línea (reglas R01–R07)")
def c_h(mes: str | None = None, limit: int = 1000): return _json(A.hallazgos(mes), limit)

# ── 5. Descargas ──
@app.get("/api/comparativos", tags=["dashboard"], summary="Datos para gráficos comparativos (sede, servicio, contratos, modalidad, serie mensual)")
def comparativos(mes: str | None = None): return {k: _json(v)["filas"] for k, v in A.comparativos(mes).items()}

@app.get("/api/comparativo/mensual", tags=["dashboard"], summary="Facturación mes a mes por nivel con variación del último mes")
def comparativo_mensual(nivel: str = "servicio"):
    if nivel not in ("servicio", "sede", "contrato", "eps", "modalidad", "ambito"): raise HTTPException(422, "nivel inválido")
    return _json(A.comparativo_mensual(nivel), 300)

@app.get("/api/estados/{fuente}", tags=["dashboard"], summary="Conteo y valor por estado (radicacion|notas|contratos|tri-eje) para gráficos")
def estados(fuente: str, mes: str | None = None):
    fx = {"radicacion": (A.radicacion, "valor_fact"), "notas": (A.notas_vs_fact, "valor_fact"), "contratos": (A.contratos, "facturado"), "tri-eje": (A.tri_eje, "facturado")}
    if fuente not in fx: raise HTTPException(404, f"Fuentes: {list(fx)}")
    d = fx[fuente][0](mes); col = "estado" if "estado" in d else "alertas"
    if d.empty: return []
    d = d.assign(_e=d[col].astype(str).str.split("; ")).explode("_e")
    g = d.groupby("_e").agg(n=(col, "size"), valor=(fx[fuente][1], "sum")).reset_index().rename(columns={"_e": "estado"}).sort_values("n", ascending=False)
    return _json(g)["filas"]

@app.get("/api/export", tags=["exportación"], summary="Libro Excel consolidado (todas las hojas)")
def export(mes: str | None = None):
    return FileResponse(exporter.exportar(mes), filename=f"auditoria_ESE_MyC_{mes or 'consolidado'}.xlsx")

@app.get("/api/export/{hoja}", tags=["exportación"], summary="Un informe individual (prefijo de nombre de hoja)")
def export_hoja(hoja: str, mes: str | None = None):
    try: return FileResponse(exporter.exportar(mes, hoja), filename=f"{hoja}_{mes or 'consolidado'}.xlsx")
    except KeyError: raise HTTPException(404, "Hoja no encontrada")

app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "static"), html=True), name="static")

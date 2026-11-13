"""API FastAPI. Documentación interactiva de endpoints: /docs (OpenAPI)."""
import io, os, shutil, tempfile, threading, zipfile
from datetime import datetime
from pathlib import Path
import numpy as np, pandas as pd
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook
from . import analysis as A, costos as C, exporter, facturas as F, gerencial as G, medicamentos as MD, plantillas as PL, processing as P
from .db import UPLOADS, VERSION, df, init
from .loaders import ALIAS, _shared, es_facturacion_360, hojas_mensuales, parse_mes, todas_hojas
from .formatos import ACEPTADOS, EXT_COMPRIMIDO, EXT_RIPS, EXT_TABLA, abrir_libro, extension, extraer
from . import rips_fev as RF

app = FastAPI(title="Auditoría de facturación · ESE Moreno y Clavijo", version="5.2",
              description="Carga mensual de facturación por ámbito (Software 360), RIPS, costos y notas técnicas; conciliación y reportes.")
init()
TIPOS = ("facturacion", "actividad", "facturas", "rips", "rips_fev", "costos", "notas", "parametros", "codigos", "farmacia")
PLANTILLAS = ("rips", "costos", "notas", "parametros", "codigos", "farmacia")

def _json(d, limit=None):
    total = len(d)
    d = d.head(limit) if limit else d
    return dict(total=total, columnas=list(d.columns), filas=d.astype(object).where(d.notna(), None).to_dict("records"))

def _limpio(o):
    """Convierte tipos numpy y NaN/inf a tipos JSON (None) de forma recursiva."""
    if isinstance(o, dict): return {k: _limpio(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_limpio(v) for v in o]
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (float, np.floating)): return None if not np.isfinite(o) else float(o)
    return o

@app.get("/api/health", tags=["sistema"])
def health(): return {"ok": True, "version": VERSION, "hora": datetime.now().isoformat(timespec="seconds")}

@app.post("/api/apagar", tags=["sistema"], summary="Cierra el servidor (solo desde este equipo; lo usa el lanzador al actualizar de versión)")
def apagar(request: Request):
    if request.client is None or request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"): raise HTTPException(403, "Solo desde este equipo")
    if request.client.host != "testclient": threading.Timer(0.5, os._exit, args=(0,)).start()
    return {"ok": True}

@app.get("/api/meses", tags=["datos"], summary="Meses con datos por fuente")
def meses():
    return {t: df(f"SELECT DISTINCT mes FROM {t} ORDER BY mes").mes.tolist() for t in ("fact", "rips", "costo", "fac_gen", "farmacia", "rips_det")}

@app.get("/api/filtros", tags=["datos"], summary="Valores disponibles para los filtros del dashboard (sedes, EPS, clases)")
def filtros():
    q = lambda sql: [x for x in df(sql).iloc[:, 0].tolist() if x]
    return dict(sedes=sorted(set(q("SELECT DISTINCT sede FROM fact") + q("SELECT DISTINCT sede FROM fac_gen") + q("SELECT DISTINCT sede FROM medins"))),
                eps=sorted(set(q("SELECT DISTINCT tercero FROM fac_gen") + q("SELECT DISTINCT eps_nombre FROM medins"))),
                clases=list(MD.CLASES))

# ── 1. Carga ──
def _cargar_archivo(tipo, path, nombre, mes, bg):
    """Registra y programa el procesamiento de UNA hoja de cálculo o texto. Devuelve (cargas, nota)."""
    nota = None
    if tipo == "actividad": tipo = "facturacion"          # «facturas generadas por actividad» de 360: mismo proceso que la facturación por ámbito
    if extension(path) in EXT_RIPS: raise ValueError(f"«{nombre}»: los JSON se cargan como «RIPS JSON / TXT».")
    if tipo in ("farmacia", "costos"):
        # el reporte de 360 (facturación por ámbito, p. ej. filtrado a MEDICAMENTOS) cargado en otro tipo se procesa como facturación
        try:
            if es_facturacion_360(abrir_libro(path)):
                tipo, nota = "facturacion", f"«{nombre}» tiene el formato de facturación por ámbito de Software 360: se cargó como Facturación (medicamentos e insumos incluidos)."
        except Exception: pass
    if tipo == "facturacion":
        try:
            z = abrir_libro(path); hojas = hojas_mensuales(z, anio=mes[:4] if mes else None); nombres = list(todas_hojas(z))
        except Exception as e: raise ValueError(f"«{nombre}»: no se pudo leer ({type(e).__name__}: {e}).")
        sel = [m for m in sorted(hojas) if not mes or m == mes]
        if not sel: raise ValueError(f"«{nombre}»: no se encontraron hojas mensuales ('ENERO 2026', 'ENERO' o una hoja con FECHA DE FACTURACION). Hojas: {nombres or 'ninguna'}")
        jobs = [(P.registrar(tipo, m, nombre, ruta=path), m) for m in sel]
        bg.add_task(P.procesar_facturacion, path, jobs)
        return [{"id": i, "mes": m, "archivo": nombre} for i, m in jobs], nota
    cid = P.registrar(tipo, mes, nombre, ruta=path)
    bg.add_task(P.procesar_parametros, cid, path) if tipo == "parametros" else bg.add_task(P.procesar_tabla, cid, tipo, path, mes)
    return [{"id": cid, "mes": mes, "archivo": nombre}], nota

@app.post("/api/cargas/{tipo}", tags=["cargas"], summary="Subir archivo (facturacion | facturas | rips | costos | notas | farmacia | codigos | parametros)")
def cargar(tipo: str, bg: BackgroundTasks, file: UploadFile = File(...), mes: str | None = Form(None, description="AAAA-MM (opcional si el archivo ya trae el mes)")):
    """Acepta Excel (.xlsx .xlsm .xls .xlsb .ods y .xls en HTML), texto (.csv .txt .tsv) y comprimidos (.zip .rar .7z .gz .tar .tgz).
    Un comprimido puede traer varios archivos: cada uno se carga por separado con el tipo elegido.
    facturacion: facturación por ámbito de Software 360 (hojas 'ENERO 2026', 'ENERO' o un CSV; una carga por mes detectado).
    facturas: «Informe de facturas generadas» de 360. farmacia: dispensación/consumo. rips/costos/notas/codigos: formato nativo o plantilla."""
    if tipo not in TIPOS: raise HTTPException(404, f"Tipo inválido. Use: {TIPOS}")
    ext = extension(file.filename)
    if ext not in ACEPTADOS: raise HTTPException(422, f"Formato no soportado ({ext or 'sin extensión'}). Formatos aceptados: {' '.join(ACEPTADOS)}")
    if mes and not parse_mes(mes): raise HTTPException(422, "El mes debe tener formato AAAA-MM.")
    mes = parse_mes(mes) if mes else None
    path = os.path.join(UPLOADS, f"{datetime.now():%Y%m%d_%H%M%S}_{Path(file.filename).name}")
    with open(path, "wb") as o: shutil.copyfileobj(file.file, o, 1 << 20)
    archivos = [(path, Path(file.filename).name)]
    comprimido = ext in EXT_COMPRIMIDO or ext == ".tar.gz"
    if tipo in ("rips", "rips_fev"):
        # RIPS JSON (Res. 2275/2023) o TXT (AF, AC, AP, AM…): todos los archivos del envío van en una sola carga
        try: rutas = extraer(path, path + "_extraido") if comprimido else [path]
        except Exception as e: raise HTTPException(422, f"No se pudo descomprimir «{file.filename}»: {e}")
        rips = [r for r in rutas if RF.es_rips(r)]
        if rips and (tipo == "rips_fev" or len(rips) == len(rutas) or extension(path) in EXT_RIPS):
            cid = P.registrar("rips_fev", mes, Path(file.filename).name + (f" ({len(rips)} archivos)" if len(rips) > 1 else ""), ruta=path)
            bg.add_task(P.procesar_rips_fev, cid, rips, path, mes)
            return {"cargas": [{"id": cid, "mes": mes, "archivo": file.filename}],
                    "nota": None if tipo == "rips_fev" else "Se detectaron RIPS en formato JSON/TXT: se cargaron como «RIPS JSON / TXT»."}
        if tipo == "rips_fev": raise HTTPException(422, "No se encontraron RIPS JSON (numFactura + usuarios) ni TXT (AF, AC, AP, AM, AT…) en el archivo.")
    if comprimido:
        try: tablas = extraer(path, path + "_extraido")
        except Exception as e: raise HTTPException(422, f"No se pudo descomprimir «{file.filename}»: {e}")
        if not tablas: raise HTTPException(422, f"El comprimido no contiene archivos Excel, CSV o texto ({' '.join(sorted(EXT_TABLA))}).")
        archivos = [(t, f"{Path(file.filename).name} › {os.path.relpath(t, path + '_extraido')}") for t in tablas]
    cargas, notas, errores = [], [], []
    for ruta, nombre in archivos:
        try:
            c, n = _cargar_archivo(tipo, ruta, nombre, mes, bg); cargas += c
            if n: notas.append(n)
        except ValueError as e: errores.append(str(e))
    if not cargas: raise HTTPException(422, " | ".join(errores))
    meses = [c["mes"] for c in cargas if c["mes"]]
    if len(meses) != len(set(meses)) and tipo == "facturacion":
        notas.append("Varios archivos traen el mismo mes: cada carga reemplaza la anterior de ese mes (cargue el mes completo en un solo archivo).")
    if len(archivos) > 1: notas.insert(0, f"Comprimido con {len(archivos)} archivo(s): {', '.join(n.split(' › ')[-1] for _, n in archivos)}.")
    return {"cargas": cargas, "nota": " ".join(notas + [f"No se cargaron: {' | '.join(errores)}" if errores else ""]).strip() or None}

@app.get("/api/cargas", tags=["cargas"], summary="Historial de cargas")
def cargas(): return _json(df("SELECT id, tipo, mes, archivo, fecha, estado, filas, mensaje FROM carga ORDER BY id DESC"))

@app.delete("/api/cargas/{cid}", tags=["cargas"], summary="Elimina los datos de una carga y su archivo (el historial la conserva como «eliminada»)")
def borrar_carga(cid: int):
    try: return {"ok": True, "registros": P.eliminar_carga(cid)}
    except KeyError: raise HTTPException(404, "Carga no encontrada")

@app.post("/api/limpiar", tags=["cargas"], summary="Limpia datos: de un mes, de un tipo de carga, o todo (opcionalmente también parámetros y homologaciones)")
def limpiar(mes: str | None = Form(None), tipo: str | None = Form(None), todo: bool = Form(False), incluir_parametros: bool = Form(False)):
    if mes and not parse_mes(mes): raise HTTPException(422, "El mes debe tener formato AAAA-MM.")
    if tipo and tipo not in TIPOS: raise HTTPException(422, f"Tipo inválido. Use: {TIPOS}")
    try: return P.limpiar(parse_mes(mes) if mes else None, tipo, todo, incluir_parametros)
    except ValueError as e: raise HTTPException(422, str(e))

@app.get("/api/cargas/{cid}", tags=["cargas"])
def carga(cid: int):
    d = df("SELECT * FROM carga WHERE id=?", (cid,))
    if d.empty: raise HTTPException(404, "Carga no encontrada")
    return _json(d)["filas"][0]

@app.get("/api/plantillas/{tipo}", tags=["cargas"], summary="Plantilla homologada (rips|costos|notas|parametros) con hoja de instrucciones y llaves comunes")
def plantilla(tipo: str):
    if tipo not in PLANTILLAS: raise HTTPException(404, f"Plantillas: {', '.join(PLANTILLAS)}")
    b = io.BytesIO(); PL.plantilla(tipo).save(b); b.seek(0)
    return StreamingResponse(b, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f"attachment; filename=plantilla_{tipo}.xlsx"})

@app.post("/api/homologar/{tipo}", tags=["cargas"], summary="Convierte un archivo nativo (consolidado RIPS, comparativo de notas técnicas, costos) al formato homologado")
def homologar(tipo: str, file: UploadFile = File(...), mes: str | None = Form(None)):
    if tipo not in ("rips", "costos", "notas"): raise HTTPException(404, "Tipos: rips, costos, notas")
    path = os.path.join(UPLOADS, f"hom_{datetime.now():%Y%m%d_%H%M%S}_{Path(file.filename).name}")
    with open(path, "wb") as o: shutil.copyfileobj(file.file, o, 1 << 20)
    if extension(file.filename) in EXT_COMPRIMIDO or extension(file.filename) == ".tar.gz":
        try: tablas = extraer(path, path + "_extraido")
        except Exception as e: raise HTTPException(422, f"No se pudo descomprimir: {e}")
        if not tablas: raise HTTPException(422, "El comprimido no contiene archivos Excel, CSV o texto.")
        path = tablas[0]
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
    ruta = os.path.join(tempfile.mkdtemp(), "codigos_pendientes.xlsx"); wb = PL.plantilla("codigos"); ws = wb["PLANTILLA"]; ws.delete_rows(5, ws.max_row)   # encabezado en la fila 4; se quita el ejemplo
    for r in d[["CODIGO_RIPS", "CODIGO_SOFTWARE360", "DESCRIPCION", "TIPO_SERVICIO", "FRECUENCIA"]].itertuples(index=False): ws.append(list(r))
    ws["D4"], ws["E4"] = "TIPO_SERVICIO (referencia)", "FRECUENCIA (referencia)"; wb.save(ruta)
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
def c_res(mes: str | None = None): return _limpio(C.resumen(mes))

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

# ── v5: facturas generadas, medicamentos e insumos, informe gerencial ──
@app.get("/api/facturas/resumen", tags=["facturas generadas"], summary="KPIs, estados, sedes, EPS, oportunidad de radicación, antigüedad y reglas F01–F12")
def fg_resumen(mes: str | None = None, sede: str | None = None, eps: str | None = None): return _limpio(F.resumen(mes, sede, eps))

@app.get("/api/facturas/detalle", tags=["facturas generadas"], summary="Facturas con alertas (filtro opcional por regla, p. ej. F01)")
def fg_detalle(mes: str | None = None, sede: str | None = None, eps: str | None = None, alerta: str | None = None, solo_alertas: bool = False, limit: int = 3000):
    return _json(F.detalle(mes, sede, eps, alerta, solo_alertas), limit)

@app.get("/api/facturas/facturadores", tags=["facturas generadas"], summary="Productividad y calidad por usuario que registra la factura")
def fg_fact(mes: str | None = None, sede: str | None = None, eps: str | None = None, limit: int = 500): return _json(F.facturadores(mes, sede, eps), limit)

@app.get("/api/facturas/cruce", tags=["facturas generadas"], summary="Encabezado de factura ↔ detalle por ámbito ↔ RIPS")
def fg_cruce(mes: str | None = None, limit: int = 3000): return _json(F.cruce(mes), limit)

@app.get("/api/medicamentos/resumen", tags=["medicamentos e insumos"], summary="KPIs por sede y clase, top ítems, ABC")
def md_resumen(mes: str | None = None, sede: str | None = None, clase: str | None = None, eps: str | None = None, contrato: str | None = None):
    r = MD.resumen(mes, sede, clase, eps); r["detalle"] = MD.resumen_detalle(mes, sede, clase, eps, contrato); return _limpio(r)

@app.get("/api/medicamentos/items", tags=["medicamentos e insumos"], summary="Ítems con ABC, valor unitario y alertas")
def md_items(mes: str | None = None, sede: str | None = None, clase: str | None = None, eps: str | None = None, limit: int = 3000):
    return _json(MD.items(mes, sede, clase, eps), limit)

@app.get("/api/medicamentos/sede", tags=["medicamentos e insumos"], summary="Ítem × sede (valor o cantidad)")
def md_sede(mes: str | None = None, clase: str | None = None, eps: str | None = None, metrica: str = "valor", limit: int = 3000):
    if metrica not in ("valor", "cantidad"): raise HTTPException(422, "metrica: valor|cantidad")
    return _json(MD.por_sede(mes, clase, eps, metrica), limit)

@app.get("/api/medicamentos/precios", tags=["medicamentos e insumos"], summary="Valor unitario por sede y homogeneidad de precios")
def md_precios(mes: str | None = None, clase: str | None = None, limit: int = 3000): return _json(MD.precios_sede(mes, clase), limit)

@app.get("/api/medicamentos/eps", tags=["medicamentos e insumos"], summary="Medicamentos e insumos por EPS y modalidad")
def md_eps(mes: str | None = None, sede: str | None = None): return _json(MD.por_eps(mes, sede))

@app.get("/api/medicamentos/farmacia", tags=["medicamentos e insumos"], summary="Dispensación de farmacia vs facturación")
def md_farm(mes: str | None = None, sede: str | None = None, limit: int = 3000): return _json(MD.cruce_farmacia(mes, sede), limit)

@app.get("/api/medicamentos/agrupado", tags=["medicamentos e insumos"],
         summary="Medicamentos por contrato | codigo (360/ATC/CUM) | servicio | sede | eps | diagnostico | atc | usuario (anonimizado)")
def md_agr(nivel: str = "contrato", mes: str | None = None, sede: str | None = None, clase: str | None = None, eps: str | None = None,
           contrato: str | None = None, limit: int = 3000):
    if nivel not in MD.NIVELES: raise HTTPException(422, f"nivel: {'|'.join(MD.NIVELES)}")
    return _json(MD.agrupado(nivel, mes, sede, clase, eps, contrato), limit)

@app.get("/api/medicamentos/mes-a-mes", tags=["medicamentos e insumos"], summary="Comparativo mes a mes por contrato, código, servicio, sede, EPS, diagnóstico o grupo ATC")
def md_mm(nivel: str = "contrato", metrica: str = "valor", sede: str | None = None, clase: str | None = None, eps: str | None = None,
          contrato: str | None = None, limit: int = 3000):
    if nivel not in MD.NIVELES or nivel == "usuario": raise HTTPException(422, "nivel: contrato|codigo|servicio|sede|eps|diagnostico|atc")
    if metrica not in ("valor", "cantidad", "usuarios"): raise HTTPException(422, "metrica: valor|cantidad|usuarios")
    return _json(MD.mes_a_mes(nivel, metrica, sede, clase, eps, contrato), limit)

@app.get("/api/medicamentos/rips", tags=["medicamentos e insumos"], summary="Cruce medicamentos facturados (360) ↔ RIPS por factura y código")
def md_rips(mes: str | None = None, sede: str | None = None, eps: str | None = None, contrato: str | None = None, estado: str | None = None, limit: int = 3000):
    d = MD.cruce_rips(mes, sede, eps, contrato)
    if estado and len(d): d = d[d.estado.str.contains(estado, regex=False)]
    return _json(d, limit)

@app.get("/api/medicamentos/homologacion", tags=["medicamentos e insumos"], summary="Códigos que no homologan: 360 sin CUM y CUM del RIPS sin código 360")
def md_hom(mes: str | None = None, limit: int = 3000): return _json(MD.homologacion_cum(mes), limit)

@app.get("/api/medicamentos/homologacion/plantilla", tags=["medicamentos e insumos"], summary="Excel para completar la homologación CUM ↔ código 360 (se carga como tipo «codigos»)")
def md_hom_xlsx(mes: str | None = None):
    h = MD.homologacion_cum(mes); h = h[h.estado != "OK"] if len(h) else h
    if not len(h): raise HTTPException(404, "No hay códigos pendientes de homologar (o no hay medicamentos cargados para el periodo).")
    ruta = os.path.join(tempfile.mkdtemp(), "homologacion_cum.xlsx"); PL.plantilla_homologacion_cum(h, mes).save(ruta)
    return FileResponse(ruta, filename=f"homologacion_cum_{mes or 'todos'}.xlsx")

@app.get("/api/rips-fev/resumen", tags=["RIPS JSON/TXT"], summary="KPIs de los RIPS JSON/TXT, estados del cruce y reglas de estructura")
def rf_res(mes: str | None = None): return _limpio(RF.resumen(mes))

@app.get("/api/rips-fev/facturas", tags=["RIPS JSON/TXT"], summary="Cruce factura a factura: Software 360 vs RIPS JSON/TXT")
def rf_fac(mes: str | None = None, estado: str | None = None, limit: int = 3000):
    d = RF.cruce_facturas(mes)
    if estado and len(d): d = d[d.estado.str.contains(estado, regex=False)]
    return _json(d, limit)

@app.get("/api/rips-fev/servicios", tags=["RIPS JSON/TXT"], summary="Cruce por factura y código (cantidad y valor) 360 vs RIPS")
def rf_srv(mes: str | None = None, tipo: str | None = None, estado: str | None = None, limit: int = 3000):
    d = RF.cruce_servicios(mes, tipo)
    if estado and len(d): d = d[d.estado.str.contains(estado, regex=False)]
    return _json(d, limit)

@app.get("/api/rips-fev/validacion", tags=["RIPS JSON/TXT"], summary="Reglas de estructura RV01–RV08 (resumen o detalle de una regla)")
def rf_val(mes: str | None = None, regla: str | None = None, limit: int = 3000):
    if regla and regla not in RF.REGLAS_RIPS: raise HTTPException(422, f"Reglas: {list(RF.REGLAS_RIPS)}")
    return _json(RF.validacion(mes, regla), limit)

@app.get("/api/gerencial", tags=["informe gerencial"], summary="Informe gerencial: indicadores, riesgos, narrativa y plan de acción")
def gerencial(mes: str | None = None): return _limpio(G.informe(mes))

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

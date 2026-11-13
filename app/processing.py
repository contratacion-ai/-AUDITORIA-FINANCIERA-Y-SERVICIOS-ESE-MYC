"""Procesamiento: transforma archivos cargados en tablas agregadas y registra el historial de cargas."""
import os, shutil, zipfile
from datetime import datetime
from .db import conn, df, TOL_COSTO_UNIT, TABLAS_DATOS, CONSERVAR_ARCHIVOS, UPLOADS
from .formatos import abrir_libro
from . import rips_fev as RF
import pandas as pd
from .loaders import (_shared, hojas_mensuales, param_hojas, procesar_hoja, leer_tabla, leer_rips, leer_facturas_generadas, norm, ALIAS)

TABLAS = {"facturacion": ["fact", "linea", "dim", "factura", "hallazgo", "medins", "med_det"], "rips": ["rips", "rips_factura", "rips_det"], "rips_fev": ["rips", "rips_factura", "rips_det"], "costos": ["costo"],
          "farmacia": ["farmacia"], "facturas": ["fac_gen"]}

def registrar(tipo, mes, archivo, estado="en cola", ruta=None):
    with conn() as c:
        return c.execute("INSERT INTO carga(tipo,mes,archivo,fecha,estado,ruta) VALUES(?,?,?,?,?,?)",
                         (tipo, mes, archivo, datetime.now().isoformat(timespec="seconds"), estado, ruta)).lastrowid

# ───────── limpieza: archivos subidos y datos por carga / mes ─────────
def borrar_archivo(ruta):
    """Borra el archivo subido (y la carpeta «_extraido» de un comprimido) si ninguna carga pendiente lo sigue usando."""
    if not ruta or CONSERVAR_ARCHIVOS: return
    base = os.path.realpath(UPLOADS)
    if not os.path.realpath(ruta).startswith(base + os.sep): return      # nunca se borra nada fuera de la carpeta de cargas
    pend = df("SELECT COUNT(*) n FROM carga WHERE ruta=? AND estado IN ('en cola','procesando')", (ruta,)).n.iloc[0]
    if pend: return
    raiz = ruta.split("_extraido")[0]                     # archivo dentro de un comprimido: se borra el comprimido y lo extraído
    for p in {ruta, raiz}:
        if os.path.isfile(p): os.remove(p)
    if os.path.isdir(raiz + "_extraido"):
        pend = df("SELECT COUNT(*) n FROM carga WHERE ruta LIKE ? AND estado IN ('en cola','procesando')", (raiz + "_extraido%",)).n.iloc[0]
        if not pend: shutil.rmtree(raiz + "_extraido", ignore_errors=True)

def eliminar_carga(cid):
    """Elimina los datos de una carga (todas las tablas) y su archivo; el historial la conserva con estado «eliminada»."""
    with conn() as c:
        r = c.execute("SELECT tipo, mes, ruta, estado FROM carga WHERE id=?", (cid,)).fetchone()
        if r is None: raise KeyError(cid)
        filas = sum(c.execute(f"DELETE FROM {t} WHERE carga_id=?", (cid,)).rowcount for t in TABLAS_DATOS)
        if r["tipo"] == "facturacion" and r["mes"] and r["estado"] == "ok":
            c.execute("DELETE FROM regla_actividad WHERE mes=?", (r["mes"],))
        c.execute("UPDATE carga SET estado='eliminada', mensaje=? WHERE id=?", (f"Eliminada el {datetime.now():%d/%m/%Y %H:%M} ({filas:,} registros borrados)", cid))
    borrar_archivo(r["ruta"])
    return filas

def limpiar(mes=None, tipo=None, todo=False, incluir_parametros=False):
    """Borra datos: de un mes (y opcionalmente de un tipo de carga) o todo. Devuelve {cargas, registros}."""
    cond, p = ["estado NOT IN ('eliminada','en cola','procesando')"], []
    if tipo: cond.append("tipo=?"); p.append(tipo)
    if mes and not todo: cond.append("(',' || mes || ',') LIKE ?"); p.append(f"%,{mes},%")
    if not (mes or tipo or todo): raise ValueError("Indique un mes, un tipo de carga o «todo».")
    ids = df("SELECT id FROM carga WHERE " + " AND ".join(cond), tuple(p)).id.tolist()
    registros = sum(eliminar_carga(i) for i in ids)
    with conn() as c:                                      # filas del mes que hubieran quedado de cargas antiguas
        if mes and not todo:
            tablas = TABLAS.get(tipo, []) if tipo else [t for v in TABLAS.values() for t in v]
            registros += sum(c.execute(f"DELETE FROM {t} WHERE mes=?", (mes,)).rowcount for t in tablas)
            if not tipo or tipo == "facturacion": c.execute("DELETE FROM regla_actividad WHERE mes=?", (mes,))
        if todo:
            registros += sum(c.execute(f"DELETE FROM {t}").rowcount for t in TABLAS_DATOS + ["regla_actividad"])
            if incluir_parametros:
                for t in ("param_contrato", "param_servicio", "hom_codigo"): c.execute(f"DELETE FROM {t}")
    if todo and not CONSERVAR_ARCHIVOS:
        for e in os.scandir(UPLOADS):
            if df("SELECT COUNT(*) n FROM carga WHERE ruta LIKE ? AND estado IN ('en cola','procesando')", (e.path + "%",)).n.iloc[0]: continue
            shutil.rmtree(e.path, ignore_errors=True) if e.is_dir() else os.remove(e.path)
    return dict(cargas=len(ids), registros=int(registros))

def estado(cid, est, mensaje="", filas=0, mes=None):
    with conn() as c:
        c.execute("UPDATE carga SET estado=?, mensaje=?, filas=?, mes=COALESCE(?,mes) WHERE id=?", (est, mensaje[:500], filas, mes, cid))

def eps_alias():
    with conn() as c: return [(r[0], r[1]) for r in c.execute("SELECT alias, eps FROM hom_eps")]

def guardar_param(contratos, servicios):
    with conn() as c:
        if contratos:
            c.execute("DELETE FROM param_contrato")
            c.executemany("INSERT OR REPLACE INTO param_contrato VALUES(?,?,?,?)", contratos)
        if servicios:
            c.execute("DELETE FROM param_servicio")
            c.executemany("INSERT OR REPLACE INTO param_servicio VALUES(?)", [(s,) for s in servicios])

def cargar_param():
    with conn() as c:
        return {"contratos": {norm(r[0]): r[1] for r in c.execute("SELECT contrato, modalidad FROM param_contrato")},
                "servicios": {norm(r[0]) for r in c.execute("SELECT servicio FROM param_servicio")},
                "eps_contrato": {norm(r[0]): r[1] for r in c.execute("SELECT contrato, eps_nombre FROM contrato_eps")}}

def validar_costos(t):
    """Validaciones de carga de COSTOS (fila a fila). Devuelve DataFrame(fila, campo, severidad, mensaje, valor).
    ERROR = la fila no se carga; ADVERTENCIA = se carga pero se reporta."""
    V = []
    def add(mask, campo, sev, msg, col=None):
        for i in t.index[mask]: V.append((int(i) + 2, campo, sev, msg, str(t.at[i, col or campo])[:60]))
    add(t.mes.isna(), "mes", "ERROR", "Mes no reconocido (use AAAA-MM, 01-ENERO o una fecha)", "mes")
    add(t.sede.eq(""), "sede", "ADVERTENCIA", "Sede vacía: el costo no se podrá cruzar por sede", "sede")
    add(t.cups.eq("") & t.servicio.eq(""), "cups", "ERROR", "Sin CUPS ni servicio: no hay llave de cruce", "cups")
    add(t.valor < 0, "valor", "ERROR", "Valor de costo negativo")
    add(t.cantidad < 0, "cantidad", "ERROR", "Cantidad negativa")
    add((t.valor == 0) & (t.cantidad == 0), "valor", "ADVERTENCIA", "Fila sin cantidad ni valor")
    cu = t.costo_unitario if "costo_unitario" in t else pd.Series(0.0, index=t.index)
    calc = cu * t.cantidad
    incons = (cu > 0) & (t.cantidad > 0) & (t.valor > 0) & ((calc - t.valor).abs() > TOL_COSTO_UNIT * t.valor)
    add(incons, "valor", "ADVERTENCIA", f"Costo unitario × cantidad difiere del valor total en más de {TOL_COSTO_UNIT:.0%}")
    dup = t.duplicated(["mes", "sede", "servicio", "cups", "concepto"], keep=False) & ~t.mes.isna()
    add(dup, "cups", "ADVERTENCIA", "Llave repetida (mes+sede+servicio+CUPS+concepto): se sumarán")
    return pd.DataFrame(V, columns=["fila", "campo", "severidad", "mensaje", "valor"])

def mapa_actividad():
    """actividad normalizada → CUPS más frecuente, aprendido de la facturación ya cargada (regla de actividad)."""
    with conn() as c:
        rows = c.execute("SELECT actividad_norm, cups, SUM(lineas) n FROM regla_actividad GROUP BY actividad_norm, cups").fetchall()
    best = {}
    for a, k, n in rows:
        if a not in best or n > best[a][1]: best[a] = (k, n)
    return {a: v[0] for a, v in best.items()}

def _reemplazar(c, tipo, cid, meses):
    """Una recarga del mismo mes reemplaza los datos anteriores; el historial conserva ambas cargas."""
    for t in TABLAS[tipo]:
        c.executemany(f"DELETE FROM {t} WHERE mes=?", [(m,) for m in meses])
    previas = c.execute("SELECT id, mes, ruta FROM carga WHERE tipo=? AND id<>? AND estado='ok'", (tipo, cid)).fetchall()
    for r in previas:
        ms = set((r["mes"] or "").split(",")) - {""}
        if ms and ms <= set(meses):
            c.execute("UPDATE carga SET estado='reemplazada', mensaje=COALESCE(mensaje,'') || ' · reemplazada por la carga ' || ? WHERE id=?", (cid, r["id"]))
            c.execute("DELETE FROM carga_validacion WHERE carga_id=?", (r["id"],))
        elif ms & set(meses):
            c.execute("UPDATE carga SET mensaje=COALESCE(mensaje,'') || ' · meses ' || ? || ' reemplazados por la carga ' || ? WHERE id=?",
                      (",".join(sorted(ms & set(meses))), cid, r["id"]))

def _procesar_facturacion(path, jobs, limit=None):
    """jobs: [(carga_id, 'AAAA-MM')]. Abre el xlsx una sola vez; toma PARAM CONTRATO/SERVICIO del libro y procesa cada mes."""
    try:
        z = abrir_libro(path); ss = _shared(z); hojas = hojas_mensuales(z, ss, jobs[0][1][:4] if jobs else None)
        guardar_param(*param_hojas(z, ss))
    except Exception as e:
        for cid, _ in jobs: estado(cid, "error", f"No se pudo leer el archivo (Excel, CSV o texto) o está dañado: {e}")
        return
    param = cargar_param()
    for cid, mes in jobs:
        param["actividad"] = mapa_actividad()
        estado(cid, "procesando", "Leyendo hoja…")
        try:
            R = procesar_hoja(z, hojas[mes][1], ss, mes, param, limit)
            with conn() as c:
                _reemplazar(c, "facturacion", cid, [mes])
                c.executemany("INSERT INTO fact VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ((cid, mes, *k[:9], *v, k[9]) for k, v in R["fact"].items()))
                c.execute("DELETE FROM regla_actividad WHERE mes=?", (mes,))
                c.executemany("INSERT OR REPLACE INTO regla_actividad VALUES(?,?,?,?,?)", ((mes, a, t, k, n) for a, t, k, n in R["reglas"]))
                c.executemany("INSERT INTO linea VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ((cid, mes, *k, *v) for k, v in R["lin"].items()))
                c.executemany("INSERT INTO dim VALUES(?,?,?,?,?,?,?,?)", ((cid, mes, *k, *v) for k, v in R["dim"].items()))
                c.executemany("INSERT INTO factura(carga_id, mes, factura, eps, eps_nombre, contrato, modalidad, sede, n_contratos, valor, lineas, "
                              "paciente_id, n_pacientes, valor_factura, fecha_radicado, radicado_eps) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              ((cid, mes, fn, x[0], x[1], x[2], x[3], x[4], len(x[7]), x[5], x[6], (next(iter(x[8])) if len(x[8]) == 1 else ""),
                                len(x[8]), x[9], x[10], x[11]) for fn, x in R["fac"].items()))
                if R.get("con_eps"):      # aprende contrato → EPS para los reportes que no traen EPS (facturas generadas por actividad)
                    c.execute("INSERT OR REPLACE INTO contrato_eps SELECT contrato, eps_nombre, 'facturación' FROM (SELECT contrato, eps_nombre, SUM(lineas) n "
                              "FROM fact WHERE carga_id=? AND eps_nombre<>'' GROUP BY 1, 2 ORDER BY n)", (cid,))
                c.executemany("INSERT INTO hallazgo VALUES(?,?,?,?,?,?)", ((cid, mes, k[0], k[1], *v) for k, v in R["hal"].items()))
                c.executemany("INSERT INTO medins VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ((cid, mes, *r) for r in R["medins"]))
                c.executemany("INSERT INTO med_det VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ((cid, mes, *r) for r in R["med_det"]))
            estado(cid, "ok", f"{len(R['fac'])} facturas, {len(R['fact'])} combinaciones agregadas, {len(R['med_det'])} líneas de medicamentos/insumos", R["filas"])
        except Exception as e:
            estado(cid, "error", f"{type(e).__name__}: {e}")

def _procesar_parametros(cid, path):
    estado(cid, "procesando", "Leyendo parámetros…")
    try:
        z = abrir_libro(path); contratos, servicios = param_hojas(z, _shared(z))
        if not contratos and not servicios: raise ValueError("No se encontraron las hojas 'PARAM CONTRATO' / 'PARAM SERVICIO'.")
        guardar_param(contratos, servicios)
        estado(cid, "ok", f"{len(contratos)} contratos, {len(servicios)} servicios parametrizados", len(contratos) + len(servicios))
    except Exception as e:
        estado(cid, "error", f"{type(e).__name__}: {e}")

def _procesar_tabla(cid, tipo, path, mes_param=None):
    """RIPS, costos o notas técnicas (formato homologado o consolidado nativo). Reemplaza por mes (o por EPS+modalidad en notas)."""
    estado(cid, "procesando", "Leyendo archivo…")
    try:
        with conn() as c:
            if tipo == "rips":
                srv, fac = leer_rips(path, mes_param, eps_alias(), mapa_actividad()); meses = sorted(srv["mes"].unique()); n = len(srv)
                _reemplazar(c, "rips", cid, meses)
                srv.assign(carga_id=cid)[["carga_id", "mes", "eps", "nit_eps", "contrato", "cucon", "modalidad", "sede", "servicio", "cups", "factura", "cantidad", "valor", "descripcion"]] \
                    .to_sql("rips", c, if_exists="append", index=False)
                fac.assign(carga_id=cid)[["carga_id", "mes", "factura", "factura_rips", "eps", "contrato", "cucon", "modalidad", "sede", "valor_fev", "servicios", "validacion"]] \
                    .to_sql("rips_factura", c, if_exists="append", index=False)
                msg = f"{len(fac)} facturas y {n} combinaciones de servicio en {', '.join(meses)}"
            elif tipo == "facturas":
                t = leer_facturas_generadas(path, mes_param); meses = sorted(t["mes"].unique())
                _reemplazar(c, "facturas", cid, meses)
                cols = ["mes"] + list(ALIAS["facturas"])
                t.assign(carga_id=cid)[["carga_id"] + cols].to_sql("fac_gen", c, if_exists="append", index=False)
                c.execute("INSERT OR IGNORE INTO contrato_eps SELECT contrato, tercero, 'facturas generadas' FROM (SELECT contrato, tercero, COUNT(*) n "
                          "FROM fac_gen WHERE carga_id=? AND tercero<>'' AND contrato<>'' GROUP BY 1, 2 ORDER BY n DESC)", (cid,))
                msg = f"{len(t):,} facturas por ${t.total.sum():,.0f} en {', '.join(meses)}"
            else:
                t = leer_tabla(path, tipo, mes_param, eps_alias())
                if tipo == "farmacia":
                    meses = sorted(t["mes"].unique())
                    g = t.groupby(["mes", "sede", "codigo", "clase", "concepto"], as_index=False).agg(
                        descripcion=("descripcion", "first"), cantidad=("cantidad", "sum"), valor_costo=("valor_costo", "sum"))
                    g["costo_unitario"] = (g.valor_costo / g.cantidad.where(g.cantidad > 0)).fillna(0.0)
                    _reemplazar(c, "farmacia", cid, meses)
                    g.assign(carga_id=cid)[["carga_id", "mes", "sede", "codigo", "descripcion", "clase", "concepto", "cantidad", "costo_unitario", "valor_costo"]] \
                        .to_sql("farmacia", c, if_exists="append", index=False)
                    msg = f"{len(g):,} registros de dispensación/consumo ({g.codigo.nunique():,} códigos) en {', '.join(meses)}"
                elif tipo == "codigos":
                    c.executemany("INSERT OR REPLACE INTO hom_codigo VALUES(?,?,?)", t[["codigo_rips", "codigo_360", "descripcion"]].values.tolist()); meses = []
                    msg = f"{len(t)} códigos homologados (RIPS → Software 360)"
                elif tipo == "notas":
                    c.executemany("DELETE FROM nota WHERE eps=? AND modalidad=?", t[["eps", "modalidad"]].drop_duplicates().values.tolist()); meses = []
                    t.assign(carga_id=cid)[["carga_id", "eps", "modalidad", "contrato", "cups", "descripcion", "categoria", "tarifa", "eventos_mes", "tope_valor", "vigencia"]] \
                        .to_sql("nota", c, if_exists="append", index=False)
                    cu = t[t.costo_unitario > 0]
                    c.executemany("DELETE FROM costo_unitario WHERE eps=? AND modalidad=?", cu[["eps", "modalidad"]].drop_duplicates().values.tolist())
                    cu.assign(carga_id=cid, actividad=cu.descripcion, mes="")[["carga_id", "cups", "actividad", "contrato", "modalidad", "eps", "costo_unitario", "mes"]] \
                        .to_sql("costo_unitario", c, if_exists="append", index=False)
                    msg = f"{len(t)} tarifas/eventos de {t.eps.nunique()} EPS; {len(cu)} costos unitarios por CUPS"
                else:
                    v = validar_costos(t)
                    if len(v): v.assign(carga_id=cid)[["carga_id", "fila", "campo", "severidad", "mensaje", "valor"]].to_sql("carga_validacion", c, if_exists="append", index=False)
                    malas = set(v.loc[v.severidad == "ERROR", "fila"] - 2) if len(v) else set()
                    n_err = len(malas); t = t[~t.index.isin(malas)]
                    if "tipo_costo" not in t: t["tipo_costo"] = ""
                    if not len(t): raise ValueError(f"Ninguna fila válida: {n_err} con ERROR (ver validaciones de la carga {cid}).")
                    meses = sorted(t["mes"].unique())
                    g = t.groupby(["mes", "sede", "servicio", "cups", "concepto", "tipo_costo"], as_index=False)[["cantidad", "valor"]].sum()
                    _reemplazar(c, "costos", cid, meses)
                    g.assign(carga_id=cid)[["carga_id", "mes", "sede", "servicio", "cups", "concepto", "cantidad", "valor", "tipo_costo"]].to_sql("costo", c, if_exists="append", index=False)
                    msg = f"{len(g)} registros de costo en {', '.join(meses)}; {n_err} filas rechazadas, {int((v.severidad == 'ADVERTENCIA').sum()) if len(v) else 0} advertencias"
        estado(cid, "ok", msg, len(srv) if tipo == "rips" else len(t), ",".join(meses) or None)
    except Exception as e:
        estado(cid, "error", f"{type(e).__name__}: {e}")

def procesar_facturacion(path, jobs, limit=None):
    """Procesa y, al terminar (bien o con error), borra el archivo subido: los datos quedan en la base (AUDIT_CONSERVAR_ARCHIVOS=1 lo conserva)."""
    try: _procesar_facturacion(path, jobs, limit)
    finally: borrar_archivo(path)

def procesar_parametros(cid, path):
    """Procesa y, al terminar (bien o con error), borra el archivo subido: los datos quedan en la base (AUDIT_CONSERVAR_ARCHIVOS=1 lo conserva)."""
    try: _procesar_parametros(cid, path)
    finally: borrar_archivo(path)

def procesar_tabla(cid, tipo, path, mes_param=None):
    """Procesa y, al terminar (bien o con error), borra el archivo subido: los datos quedan en la base (AUDIT_CONSERVAR_ARCHIVOS=1 lo conserva)."""
    try: _procesar_tabla(cid, tipo, path, mes_param)
    finally: borrar_archivo(path)

def _procesar_rips_fev(cid, rutas, mes_param=None):
    """RIPS JSON / TXT (sueltos o de un comprimido) en una sola carga. Reemplaza los RIPS de los meses que traiga."""
    estado(cid, "procesando", f"Leyendo {len(rutas)} archivo(s) RIPS…")
    try:
        det, agg, vf, val = RF.procesar(rutas, mes_param)
        meses = sorted(det["mes"].unique())
        with conn() as c:
            _reemplazar(c, "rips_fev", cid, meses)
            det.assign(carga_id=cid)[["carga_id"] + RF.COLS_DET].to_sql("rips_det", c, if_exists="append", index=False)
            agg.assign(carga_id=cid, nit_eps="", cucon="").rename(columns={"tipo_servicio": "servicio", "codigo": "cups"})[
                ["carga_id", "mes", "eps", "nit_eps", "contrato", "cucon", "modalidad", "sede", "servicio", "cups", "factura", "cantidad", "valor", "descripcion"]] \
                .to_sql("rips", c, if_exists="append", index=False)
            vf.assign(carga_id=cid, factura_rips=vf.factura, cucon="")[["carga_id", "mes", "factura", "factura_rips", "eps", "contrato", "cucon", "modalidad", "sede",
                                                                       "valor_fev", "servicios", "validacion"]].to_sql("rips_factura", c, if_exists="append", index=False)
        orig = det.origen.value_counts().to_dict()
        estado(cid, "ok", f"{vf.factura.nunique():,} facturas, {len(det):,} servicios RIPS ({', '.join(f'{k} {v:,}' for k, v in orig.items())})"
                          + (f"; {len(val)} respuestas de validación (CUV)" if val else "") + f" en {', '.join(meses)}", len(det), ",".join(meses))
    except Exception as e:
        estado(cid, "error", f"{type(e).__name__}: {e}")

def procesar_rips_fev(cid, rutas, ruta_subida, mes_param=None):
    try: _procesar_rips_fev(cid, rutas, mes_param)
    finally: borrar_archivo(ruta_subida)

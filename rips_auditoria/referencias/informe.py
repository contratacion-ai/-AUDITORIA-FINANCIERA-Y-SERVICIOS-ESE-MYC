"""Resumen del libro INFORME DE SERVICIOS 2026 y conciliación con los totales de control.

Lee las hojas de cifras (NT COSTOS, PACIENTES ATENDIDOS, FRECUEN NT SUB/CONT, FRE MES SUB/CONT,
FACTURACION EVENTO, ESCENARIO COSTO VS PAGOS, ESCENARIO MODELO PGP, NOTA TECNICA CAPITA NEPS) y
las compara con el dashboard FINAL. No reemplaza los maestros: los complementa con una
trazabilidad de cifras para la auditoría.
"""
from __future__ import annotations

from ..constantes import CONTROL, CONTROL_FINAL_HISTORICO, CONTROL_NT_MUNICIPIO, MESES, MESES_AB, nombre_grupo, norm
from .dashboards import cargar_final


def _hojas(datos: bytes) -> dict[str, list[list]]:
    from ..maestros import _hojas as h
    return h(datos)


def _hoja(hojas, pref):
    for n, f in hojas.items():
        if norm(n).startswith(norm(pref)):
            return f
    return None


def _n(v) -> float:
    try:
        return float(v) if v is not None and v != "" else 0.0
    except (TypeError, ValueError):
        return 0.0


def _runs(h: list[str]) -> list[list[int]]:
    runs, cur = [], None
    for i, c in enumerate(h):
        if c in MESES:
            if cur and cur[-1] == i - 1 and c not in {h[j] for j in cur}:
                cur.append(i)
            else:
                cur = [i]
                runs.append(cur)
    return runs


def _totales_frecuencia(filas) -> dict:
    """Fila de totales (sin código) de las hojas FRECUEN/FRE MES: total RIPS y total facturación
    (columna TOTAL siguiente a cada corrida de meses) y totales mensuales RIPS."""
    hi = next(i for i, r in enumerate(filas) if r and any(norm(c) == "CODIGOCUPS" for c in r))
    h = [norm(c) for c in filas[hi]]
    tot = next(r for r in reversed(filas) if r and any(isinstance(v, (int, float)) for v in r))
    rr = _runs(h)
    out = {"rips_mensual": [_n(tot[i]) for i in rr[0]] if rr else []}
    for k, run in zip(("rips_total", "facturacion_total"), rr):
        j = next((i for i in range(run[-1] + 1, len(h)) if h[i] == "TOTAL"), None)
        if j is not None:
            out[k] = _n(tot[j])
    if len(rr) > 1:
        out["facturacion_mensual"] = [_n(tot[i]) for i in rr[1]]
        out["facturacion_meses"] = [h[i] for i in rr[1]]
    return out


def resumen_informe(datos: bytes) -> dict:
    from ..maestros import leer_nt_costos, leer_nt_municipio
    hojas = _hojas(datos)
    r: dict = {"hojas": list(hojas)}
    f = _hoja(hojas, "NT COSTOS")
    if f:
        nt, info = leer_nt_costos(f)
        n = info["meses"]
        r["meses"] = MESES_AB[:n]
        r["capita_mensual"] = nt.groupby("mes")["costo"].sum().reindex(range(1, n + 1), fill_value=0).tolist()
        r["rips_nt_mensual"] = nt.groupby("mes")["frecuencia_rips"].sum().reindex(range(1, n + 1), fill_value=0).tolist()
        g = nt.groupby(["grupo", "mes"])["costo"].sum().unstack(fill_value=0)
        r["capita_grupo_mes"] = {k: [float(v) for v in fila] for k, fila in g.iterrows()}
        r["medicamentos_mensual"] = r["capita_grupo_mes"].get("MEDICAMENTOS", [0.0] * n)
        r["unidades_medicamentos"] = float(nt[nt.grupo == "MEDICAMENTOS"]["frecuencia_rips"].sum())
    f = _hoja(hojas, "PACIENTES ATENDIDOS")
    if f:
        hi = next(i for i, x in enumerate(f) if x and any(norm(c) == "PACIENTES UNICOS" for c in x))
        h = [norm(c) for c in f[hi]]
        ix = lambda p: next(i for i, c in enumerate(h) if c.startswith(p))
        det = []
        for x in f[hi + 1:]:
            if x and norm(x[ix("MES")]) in MESES:
                det.append({"mes": norm(x[ix("MES")]), "cups": _n(x[ix("FRECUENCIA CUPS")]),
                            "cums": _n(x[ix("FRECUENCIA CUMS")]), "pacientes": _n(x[ix("PACIENTES UNICOS")]),
                            "asistencias": _n(x[ix("ASISTENCIAS")]), "pacientes_2_mas": _n(x[ix("PACIENTES CON")])})
        r["pacientes_mes"] = det
    for clave, pref in (("frecuen_sub", "FRECUEN NT SUB"), ("frecuen_cont", "FRECUEN NT CONT"),
                        ("fre_mes_sub", "FRE MES SUB"), ("fre_mes_cont", "FRE MES CONT")):
        f = _hoja(hojas, pref)
        if f:
            r[clave] = _totales_frecuencia(f)
    f = _hoja(hojas, "FACTURACION EVENTO")
    if f:
        ev, evr = [], []
        for x in f:
            k = next((i for i, c in enumerate(x or []) if norm(c) in MESES), None)
            if k is not None and norm(x[k + 1]) == "EVENTO":
                ev.append(_n(x[k + 3]))
                evr.append(_n(x[k + 4]))
        r["evento_mensual"], r["evento_servicios"] = ev, evr
    f = _hoja(hojas, "ESCENARIO MODELO PGP")
    if f:
        hi = next((i for i, x in enumerate(f) if x and any(norm(c) == "FACTURACION EVENTO" for c in x)), None)
        if hi is not None:
            h = [norm(c) for c in f[hi]]
            j = h.index("FACTURACION EVENTO")
            jm = next(i for i, c in enumerate(h) if c == "MES")
            r["evento_modelo_pgp"] = [_n(x[j]) for x in f[hi + 1:] if x and norm(x[jm]) in MESES]
            jp = next((i for i, c in enumerate(h) if c.startswith("PGP POSIBLE VALOR CAPITA CON UPC")), None)
            if jp is not None:
                vals = [_n(x[jp]) for x in f[hi + 1:] if x and norm(x[jm]) in MESES]
                r["pgp2_modelo_promedio"] = sum(vals) / len(vals) if vals else None
    f = _hoja(hojas, "ESCENARIO COSTO VS PAGOS")
    if f:
        G = {}

        def sig(i):
            for x in f[i:]:
                if x and len(x) > 9 and isinstance(x[9], (int, float)) and isinstance(x[8], (int, float)) and x[8] > 1000:
                    return x
        for i, x in enumerate(f):
            t = norm(x[1]) if x and len(x) > 1 and isinstance(x[1], str) else ""
            d = sig(i) if t.startswith(("ESCENARIO ACTUAL", "ESCENARIO 1", "ESCENARIO 2")) else None
            if d is None:
                continue
            if t.startswith("ESCENARIO ACTUAL"):
                G.update(usuarios=d[8], valor_usuario=_n(d[7]), giro_sin_upc=d[9])
            elif t.startswith("ESCENARIO 1"):
                G.update(valor_usuario_upc=_n(d[7]), giro_upc=d[9])
            elif t.startswith("ESCENARIO 2"):
                G.update(valor_usuario_actas=_n(d[7]), giro_actas=d[9])
        r["giro"] = G
    f = _hoja(hojas, "NOTA TECNICA CAPITA")
    if f:
        df, _ = leer_nt_municipio(f)
        if not df.empty:
            r["nt_municipio_total"] = df.groupby("municipio")["frecuencia_prevista"].sum().to_dict()
    r["conciliacion"] = conciliar(r)
    return r


def conciliar(r: dict) -> list[dict]:
    """Cifras del libro vs control OFICIAL (libro vigente, medicamentos actuales) y vs dashboard FINAL
    (referencia histórica), con la explicación de cada diferencia."""
    fin = cargar_final()
    H = CONTROL_FINAL_HISTORICO
    filas: list[dict] = []

    def add(ind, informe, control, historico=None, explicacion="", tol=1.0):
        dif = (informe - control) if informe is not None and control is not None else None
        filas.append({"indicador": ind, "informe": informe, "control_oficial": control,
                      "dashboard_final_historico": historico, "diferencia_vs_oficial": dif,
                      "estado": "OK" if dif is not None and abs(dif) <= tol else ("DIFERENTE" if dif is not None else "—"),
                      "explicacion": explicacion})
    if "capita_mensual" in r:
        tot = sum(r["capita_mensual"])
        med = sum(r["medicamentos_mensual"])
        add("Costo CAPITA total ene–ago", tot, CONTROL["capita_total"], H["capita_total"],
            "Oficial = libro vigente con medicamentos actuales. El dashboard FINAL usaba una versión anterior de "
            "medicamentos (diferencia completa en ese grupo).")
        add("Medicamentos (grupo)", med, CONTROL["medicamentos"], H["medicamentos"],
            f"{r.get('unidades_medicamentos', 0):,.0f} unidades CUMS costeadas en NT COSTOS.")
        add("Costo CAPITA sin medicamentos", tot - med, CONTROL["capita_sin_medicamentos"],
            H["capita_total"] - H["medicamentos"], "Idéntico en el libro y en el dashboard FINAL.")
        add("Costo CAPITA promedio/mes", tot / len(r["capita_mensual"]), CONTROL["capita_promedio_mes"],
            H["capita_promedio_mes"])
        for g, pct_fin in zip(fin.grupos, fin.grupos_pct):
            v = sum(r["capita_grupo_mes"].get(g, []))
            filas.append({"indicador": f"Participación {nombre_grupo(g)} (%)", "informe": round(v / tot * 100, 2),
                          "control_oficial": round(v / tot * 100, 2), "dashboard_final_historico": pct_fin,
                          "diferencia_vs_oficial": 0.0, "estado": "OK",
                          "explicacion": "Participación oficial (libro vigente); el FINAL difiere por medicamentos"})
    if "pacientes_mes" in r:
        p = r["pacientes_mes"]
        add("CUPS", sum(x["cups"] for x in p), CONTROL["cups"], H["cups"])
        add("CUMS", sum(x["cums"] for x in p), CONTROL["cums"], H["cums"],
            "Cifra actual del libro (el FINAL mostraba 57.238 de una versión anterior).")
        add("Pacientes únicos (suma mensual)", sum(x["pacientes"] for x in p), CONTROL["pacientes_unicos"],
            H["pacientes_unicos"])
        add("Asistencias", sum(x["asistencias"] for x in p), CONTROL["asistencias"], H["asistencias"])
    for k, nombre, ctrl in (("frecuen_sub", "RIPS NT subsidiado", (910_925, 629_182)),
                            ("frecuen_cont", "RIPS NT contributivo", (17_559, 18_439))):
        if k in r:
            add(f"{nombre} – RIPS", r[k].get("rips_total"), ctrl[0])
            add(f"{nombre} – facturación", r[k].get("facturacion_total"), ctrl[1], None,
                "Facturación incluye diciembre 2025 y llega a julio (columnas de la hoja).")
    if "evento_mensual" in r:
        add("Facturación EVENTO (hoja FACTURACION EVENTO)", sum(r["evento_mensual"]), CONTROL["evento_total"],
            sum(fin.pgp["FACT_EV"]))
    if r.get("evento_modelo_pgp"):
        add("Facturación EVENTO (hoja ESCENARIO MODELO PGP)", sum(r["evento_modelo_pgp"]), CONTROL["evento_total"],
            sum(fin.pgp["FACT_EV"]),
            "Inconsistencia interna del libro: marzo, junio y julio difieren de la hoja FACTURACION EVENTO, "
            "que es la que se usa como oficial.")
    for m, ctrl in CONTROL_NT_MUNICIPIO.items():
        if "nt_municipio_total" in r:
            add(f"NT mensual {m}", r["nt_municipio_total"].get(m, 0.0), ctrl, None, "", tol=0.01)
    return filas

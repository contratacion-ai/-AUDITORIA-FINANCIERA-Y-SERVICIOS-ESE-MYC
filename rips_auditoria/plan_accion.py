"""Plan de acción a partir de las alertas del tablero.

Cada alerta (``tablero.alertas``) se convierte en una tarea con prioridad, responsable sugerido, fecha límite
y estado (PENDIENTE, EN PROCESO, RESUELTA, NO APLICA). Lo que el equipo cambia (estado, responsable,
observaciones) se guarda en ``<datos>/plan_accion.json`` y se conserva entre cargues.

Cierre verificado: si una alerta que estaba en el plan ya no aparece con los datos actuales (la sede
completó los RIPS, se corrigió la factura…), la tarea pasa a «RESUELTA (verificada)» automáticamente.
Así el plan mide el avance real y no sólo lo que se marca a mano.

Prioridad, plazo y responsable por categoría están en ``REGLAS`` (se pueden ajustar en
``configuracion.yaml › plan_accion.reglas``).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

import pandas as pd

from .config import carpeta_datos

ESTADOS = ["PENDIENTE", "EN PROCESO", "RESUELTA", "NO APLICA"]
VERIFICADA = "RESUELTA (verificada)"
# categoría -> (prioridad 1-3, días para resolver, responsable sugerido, el valor es dinero en riesgo)
REGLAS = {
    "Sin RIPS": (1, 5, "Facturación de la sede", False),
    "Conciliación AF vs. servicios": (1, 8, "Facturación + Auditoría de cuentas", True),
    "Servicios sin factura": (1, 5, "Facturación de la sede", True),
    "Facturas sin servicios": (1, 5, "Facturación de la sede", True),
    "Medicamento bajo costo": (1, 8, "Farmacia + Facturación", True),
    "Errores de estructura": (2, 10, "Facturación / soporte del software de facturación", False),
    "Valor unitario atípico": (2, 15, "Auditoría de cuentas + Contratación", False),
    "Medicamento fuera de portafolio": (2, 15, "Farmacia", False),
    "Servicios en cero": (2, 10, "Facturación de la sede", False),
    "Modalidad inferida": (2, 10, "Contratación / Auditoría de cuentas", False),
    "Variación mensual": (3, 20, "Coordinación de sede + Estadística", False),
    "Frecuencia de uso": (3, 30, "Coordinación médica", False),
    "Concentración": (3, 30, "Cartera", False),
}
COLUMNAS = ["id", "prioridad", "estado", "fecha_limite", "dias_restantes", "nivel", "categoria", "sede", "mes",
            "hallazgo", "accion", "valor_en_riesgo", "responsable", "observaciones", "detectada", "actualizada"]


def _ruta() -> Path:
    return carpeta_datos() / "plan_accion.json"


def _leer() -> dict:
    try:
        return json.loads(_ruta().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar(datos: dict) -> None:
    ruta = _ruta()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ruta)


def id_alerta(a: dict) -> str:
    """Identificador estable de la alerta: categoría, sede, mes y el código o la medida que la distingue
    (no el texto completo, que cambia con las cifras)."""
    m = re.search(r"(?:código|CUM)\s+(\S+)", a.get("mensaje", ""))
    extra = m.group(1) if m else ("cantidad" if "cantidad de servicios" in a.get("mensaje", "") else "")
    base = "|".join(str(a.get(k) or "") for k in ("categoria", "sede", "mes")) + "|" + extra
    return hashlib.sha1(base.encode()).hexdigest()[:12]


def construir(lista_alertas: list[dict], reglas: dict | None = None, hoy: dt.date | None = None,
              registrar: bool = True) -> pd.DataFrame:
    """Plan de acción con las alertas actuales y las ya resueltas (verificadas). Con ``registrar`` se
    guardan las alertas nuevas (fecha de detección) para medir el avance en los siguientes cargues."""
    hoy = hoy or dt.date.today()
    reglas = {**REGLAS, **(reglas or {})}
    guardado = _leer()
    filas, vistos, cambios = [], set(), False
    for a in lista_alertas:
        i = id_alerta(a)
        if i in vistos:
            continue
        vistos.add(i)
        prio, dias, resp, dinero = reglas.get(a["categoria"], (3, 30, "Auditoría de cuentas", False))
        g = guardado.get(i) or {}
        if not g or g.get("estado") == VERIFICADA:  # nueva, o reapareció después de resuelta: se reabre
            g = {**g, "estado": "PENDIENTE", "detectada": hoy.isoformat()}
            cambios = True
        g.update(categoria=a["categoria"], sede=a.get("sede", ""), mes=a.get("mes", ""), nivel=a["nivel"],
                 hallazgo=a["mensaje"], accion=a["accion"], valor=a.get("valor"))
        guardado[i] = g
        limite = dt.date.fromisoformat(g["detectada"]) + dt.timedelta(days=dias)
        filas.append({"id": i, "prioridad": prio, "estado": g["estado"], "fecha_limite": limite.isoformat(),
                      "dias_restantes": (limite - hoy).days, "nivel": a["nivel"], "categoria": a["categoria"],
                      "sede": a.get("sede", ""), "mes": a.get("mes", ""), "hallazgo": a["mensaje"],
                      "accion": a["accion"], "valor_en_riesgo": float(a["valor"] or 0) if dinero else None,
                      "responsable": g.get("responsable") or resp, "observaciones": g.get("observaciones", ""),
                      "detectada": g["detectada"], "actualizada": g.get("actualizada", "")})
    # Las que estaban y ya no aparecen: resueltas, verificadas con los datos
    for i, g in guardado.items():
        if i in vistos or "categoria" not in g:
            continue
        if g.get("estado") != VERIFICADA:
            g["estado"], g["actualizada"] = VERIFICADA, hoy.isoformat()
            cambios = True
        prio, dias, resp, dinero = reglas.get(g["categoria"], (3, 30, "Auditoría de cuentas", False))
        filas.append({"id": i, "prioridad": prio, "estado": VERIFICADA, "fecha_limite": "", "dias_restantes": None,
                      "nivel": g.get("nivel", ""), "categoria": g["categoria"], "sede": g.get("sede", ""),
                      "mes": g.get("mes", ""), "hallazgo": g.get("hallazgo", ""), "accion": g.get("accion", ""),
                      "valor_en_riesgo": float(g.get("valor") or 0) if dinero else None,
                      "responsable": g.get("responsable") or resp, "observaciones": g.get("observaciones", ""),
                      "detectada": g.get("detectada", ""), "actualizada": g.get("actualizada", "")})
    if registrar and cambios:
        _guardar(guardado)
    df = pd.DataFrame(filas, columns=COLUMNAS)
    if df.empty:
        return df
    abierta = ~df["estado"].isin(["RESUELTA", VERIFICADA, "NO APLICA"])
    df["_o"] = (~abierta).astype(int)
    return (df.sort_values(["_o", "prioridad", "dias_restantes", "valor_en_riesgo"], ascending=[True, True, True, False],
                           na_position="last").drop(columns="_o").reset_index(drop=True))


def actualizar(id_: str, estado: str | None = None, responsable: str | None = None,
               observaciones: str | None = None) -> dict:
    """Cambia el estado, el responsable o las observaciones de una tarea del plan."""
    guardado = _leer()
    if id_ not in guardado:
        raise KeyError(id_)
    if estado is not None:
        if estado not in ESTADOS:
            raise ValueError(f"Estado no válido: {estado}. Use {', '.join(ESTADOS)}.")
        guardado[id_]["estado"] = estado
    if responsable is not None:
        guardado[id_]["responsable"] = responsable.strip()[:120]
    if observaciones is not None:
        guardado[id_]["observaciones"] = observaciones.strip()[:1000]
    guardado[id_]["actualizada"] = dt.date.today().isoformat()
    _guardar(guardado)
    return guardado[id_]


def resumen(plan: pd.DataFrame) -> dict:
    """Avance del plan: abiertas, vencidas, resueltas (a mano y verificadas) y dinero en riesgo abierto."""
    if plan.empty:
        return {"total": 0, "abiertas": 0, "vencidas": 0, "en_proceso": 0, "resueltas": 0, "verificadas": 0,
                "no_aplica": 0, "valor_en_riesgo_abierto": 0.0, "avance_pct": 0.0, "por_responsable": []}
    abierta = plan["estado"].isin(["PENDIENTE", "EN PROCESO"])
    resueltas = int((plan["estado"] == "RESUELTA").sum())
    verificadas = int((plan["estado"] == VERIFICADA).sum())
    cerradas = resueltas + verificadas + int((plan["estado"] == "NO APLICA").sum())
    por_resp = (plan[abierta].groupby("responsable").agg(abiertas=("id", "size"),
                                                         vencidas=("dias_restantes", lambda s: int((s < 0).sum())))
                .sort_values("abiertas", ascending=False).reset_index())
    return {"total": len(plan), "abiertas": int(abierta.sum()),
            "vencidas": int((abierta & (plan["dias_restantes"].fillna(0) < 0)).sum()),
            "en_proceso": int((plan["estado"] == "EN PROCESO").sum()), "resueltas": resueltas,
            "verificadas": verificadas, "no_aplica": int((plan["estado"] == "NO APLICA").sum()),
            "valor_en_riesgo_abierto": float(plan.loc[abierta, "valor_en_riesgo"].fillna(0).sum()),
            "avance_pct": round(cerradas / len(plan) * 100, 1),
            "por_responsable": por_resp.to_dict(orient="records")}


__all__ = ["ESTADOS", "REGLAS", "VERIFICADA", "actualizar", "construir", "id_alerta", "resumen"]

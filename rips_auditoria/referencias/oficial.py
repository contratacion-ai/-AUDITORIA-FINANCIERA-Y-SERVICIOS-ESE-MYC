"""Serie oficial de control (``config/control_oficial.yaml``), tomada del libro INFORME DE SERVICIOS
2026 vigente con la cifra actual de medicamentos."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from ..config import DIR_CONFIG

ARCHIVO = "control_oficial.yaml"


@lru_cache(maxsize=1)
def cargar() -> dict:
    with open(DIR_CONFIG / ARCHIVO, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def detalle_mensual() -> list[dict]:
    o = cargar()
    return [{"mes": m, "costo": c, "medicamentos": md, "rips_nt": r, "cups": cu, "cums": cm, "pacientes": p,
             "asistencias": a}
            for m, c, md, r, cu, cm, p, a in zip(o["meses"], o["costo_capita_mensual"], o["medicamentos_mensual"],
                                                 o["rips_nt_mensual"], o["cups_mensual"], o["cums_mensual"],
                                                 o["pacientes_mensual"], o["asistencias_mensual"])]


def escribir_desde_informe(datos: bytes, destino: Path | None = None) -> Path:
    """Regenera la serie oficial desde una nueva versión del libro."""
    from .informe import resumen_informe
    r = resumen_informe(datos)
    p = r["pacientes_mes"]
    d = {"fuente": "INFORME DE SERVICIOS (regenerado)",
         "meses": [f"2026-{i:02d}" for i in range(1, len(r["capita_mensual"]) + 1)],
         "costo_capita_mensual": [round(v, 2) for v in r["capita_mensual"]],
         "medicamentos_mensual": [round(v, 2) for v in r["medicamentos_mensual"]],
         "rips_nt_mensual": [round(v, 4) for v in r["rips_nt_mensual"]],
         "costo_grupo_total": {k: round(sum(v), 2) for k, v in r["capita_grupo_mes"].items()},
         "cups_mensual": [int(x["cups"]) for x in p], "cums_mensual": [int(x["cums"]) for x in p],
         "pacientes_mensual": [int(x["pacientes"]) for x in p],
         "asistencias_mensual": [int(x["asistencias"]) for x in p],
         "pacientes_2_mas_mensual": [int(x["pacientes_2_mas"]) for x in p],
         "evento_mensual": [int(v) for v in r.get("evento_mensual", [])],
         "evento_servicios_mensual": [int(v) for v in r.get("evento_servicios", [])]}
    destino = destino or DIR_CONFIG / ARCHIVO
    destino.write_text("# Serie OFICIAL de control (regenerada desde el libro INFORME DE SERVICIOS).\n" +
                       yaml.safe_dump(d, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    cargar.cache_clear()
    return destino

"""Uso de escritorio (sin navegador): procesa carpetas locales y genera informes Excel/PDF/JSON.

Ejemplos::

    python -m rips_auditoria.cli procesar "D:/RIPS/2026/08. Agosto" --salida informes
    python -m rips_auditoria.cli procesar RIPS_TAME.zip RIPS_ARAUCA.rar --periodo 2026-08 --pdf
    python -m rips_auditoria.cli maestro informe "INFORME DE SERVICIOS 2026 BASE DE DATOS ANALISIS.xlsx"
    python -m rips_auditoria.cli maestro tarifario tarifario_evento_2026.xlsx
    python -m rips_auditoria.cli controles
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

from . import maestros
from .analisis import analizar, controles_referencia
from .config import DIR_DATOS
from .informes import excel, pdf
from .ingesta.lote import procesar


def _procesar(a) -> int:
    manual = {k: v for k, v in {"hospital": a.hospital, "municipio": (a.municipio or "").upper(),
                                "periodo": a.periodo, "cod_entidad": a.eps}.items() if v}
    print(f"Leyendo {len(a.rutas)} entrada(s)…")
    lote = procesar(a.rutas, anio_defecto=a.anio, manual_global=manual)
    lote.guardar(DIR_DATOS / "lotes")
    sal = Path(a.salida)
    sal.mkdir(parents=True, exist_ok=True)
    import pandas as pd
    val = sal / f"validacion_{lote.id}.xlsx"
    with pd.ExcelWriter(val, engine="xlsxwriter") as xw:
        lote.resumen().to_excel(xw, sheet_name="Indice_Hospital_Mes", index=False)
        lote.remisiones.to_excel(xw, sheet_name="Remisiones", index=False)
        lote.incidencias.to_excel(xw, sheet_name="Incidencias", index=False)
        lote.archivos.to_excel(xw, sheet_name="Archivos", index=False)
    print(lote.resumen().to_string(index=False))
    sev = lote.incidencias["severidad"].value_counts().to_dict() if not lote.incidencias.empty else {}
    print(f"Incidencias: {sev} · repetidos eliminados entre fuentes: {len(lote.duplicados)}")
    print(f"✔ Validación: {val}")
    try:
        r = analizar([lote], solo_eps_objetivo=not a.todas_eps)
    except ValueError as e:
        print(f"⚠ No se generó análisis: {e}")
        return 1
    for adv in r.advertencias:
        print("⚠", adv)
    x = excel.escribir(r, sal / f"informe_{lote.id}.xlsx")
    r.guardar(sal / f"estado_{lote.id}.json")
    print(f"✔ Informe Excel: {x}")
    if a.pdf:
        print(f"✔ Informe PDF: {pdf.escribir(r, sal / f'informe_{lote.id}.pdf')}")
    k = r.indicadores["capita"]
    print(f"CAPITA total {k['total']:,.0f} · prom/mes {k['promedio_mes']:,.0f} · pacientes {k['pacientes_suma']:,.0f} · "
          f"EVENTO {r.indicadores['evento_total']:,.0f} · hallazgos ALTA {r.indicadores['hallazgos_alta']}")
    return 0


def main(argv=None) -> int:
    warnings.filterwarnings("ignore", category=UserWarning)
    p = argparse.ArgumentParser(prog="rips_auditoria", description="Auditoría RIPS E.S.E. Moreno y Clavijo")
    sp = p.add_subparsers(dest="cmd", required=True)
    pr = sp.add_parser("procesar", help="Procesa carpetas/ZIP/RAR/TXT/JSON y genera informes")
    pr.add_argument("rutas", nargs="+")
    pr.add_argument("--salida", default="informes")
    pr.add_argument("--hospital")
    pr.add_argument("--municipio")
    pr.add_argument("--periodo", help="AAAA-MM: fuerza el mes de toda la carga")
    pr.add_argument("--eps", help="Código EPS cuando el RIPS no lo trae (JSON 2275)")
    pr.add_argument("--anio", type=int, default=2026)
    pr.add_argument("--todas-eps", action="store_true", help="No limitar a Nueva EPS")
    pr.add_argument("--pdf", action="store_true")
    ma = sp.add_parser("maestro", help="Carga un maestro local")
    ma.add_argument("nombre", choices=["informe", *maestros.PLANTILLAS])
    ma.add_argument("archivo")
    sp.add_parser("controles", help="Muestra los totales de control de los dashboards de referencia")
    sp.add_parser("plantillas", help="Escribe las plantillas CSV de maestros en ./plantillas")
    a = p.parse_args(argv)
    if a.cmd == "procesar":
        return _procesar(a)
    if a.cmd == "maestro":
        f = Path(a.archivo)
        print(json.dumps(maestros.cargar(a.nombre, f.read_bytes(), f.name), ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "controles":
        print(json.dumps(controles_referencia(), ensure_ascii=False, indent=1, default=float))
        return 0
    if a.cmd == "plantillas":
        maestros.escribir_plantillas(Path("plantillas"))
        print("✔ plantillas/")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

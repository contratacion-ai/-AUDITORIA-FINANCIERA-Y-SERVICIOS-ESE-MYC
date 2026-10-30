"""Uso de escritorio (sin navegador): carga carpetas RIPS a la base consolidada y genera informes.

Ejemplos::

    python -m rips_auditoria.cli procesar "G:/Mi unidad/RIPS/Hospital San Jose de Cravo Norte" --pdf
    python -m rips_auditoria.cli fuentes agregar "G:/Mi unidad/RIPS/Hospital San Jose de Cravo Norte"
    python -m rips_auditoria.cli sincronizar --informe
    python -m rips_auditoria.cli base listar
    python -m rips_auditoria.cli base eliminar --hospital "Hospital San Jose de Cravo Norte" --periodo 2026-08
    python -m rips_auditoria.cli analizar --municipio TAME --pdf
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

from . import base, fuentes, maestros
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
    act = base.actualizar(lote, origen=", ".join(map(str, a.rutas)), modo=a.modo)
    print(f"Base actualizada: {len(act['nuevas'])} hospital-mes nuevos, {len(act['reemplazadas'])} reemplazados")
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
    return _informes(a, [base.lote()] if a.toda_la_base else [lote], lote.id)


def _informes(a, lotes, nombre: str) -> int:
    sal = Path(a.salida)
    sal.mkdir(parents=True, exist_ok=True)
    try:
        r = analizar(lotes, solo_eps_objetivo=not a.todas_eps)
    except ValueError as e:
        print(f"⚠ No se generó análisis: {e}")
        return 1
    for adv in r.advertencias:
        print("⚠", adv)
    x = excel.escribir(r, sal / f"informe_{nombre}.xlsx")
    r.guardar(sal / f"estado_{nombre}.json")
    print(f"✔ Informe Excel: {x}")
    if a.pdf:
        print(f"✔ Informe PDF: {pdf.escribir(r, sal / f'informe_{nombre}.pdf')}")
    k = r.indicadores["capita"]
    print(f"CAPITA total {k['total']:,.0f} · prom/mes {k['promedio_mes']:,.0f} · pacientes {k['pacientes_suma']:,.0f} · "
          f"EVENTO {r.indicadores['evento_total']:,.0f} · hallazgos ALTA {r.indicadores['hallazgos_alta']}")
    ej = r.indicadores.get("ejecucion_nt_pct")
    if ej is not None:
        print(f"Ejecución frecuencias Nota Técnica Nueva EPS: {ej:.1f} %")
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
    pr.add_argument("--modo", choices=["mes", "carpeta"], default="mes",
                    help="mes: reemplaza el mes completo del hospital; carpeta: solo las carpetas EPS cargadas")
    pr.add_argument("--toda-la-base", action="store_true", help="Informe de toda la base, no solo de esta carga")
    sy = sp.add_parser("sincronizar", help="Procesa las carpetas de Drive que cambiaron y actualiza la base")
    sy.add_argument("--forzar", action="store_true")
    sy.add_argument("--informe", action="store_true", help="Genera informe de toda la base al terminar")
    fu = sp.add_parser("fuentes", help="Carpetas de Drive vigiladas")
    fu.add_argument("accion", choices=["listar", "agregar", "quitar"])
    fu.add_argument("ruta", nargs="?")
    ba = sp.add_parser("base", help="Base consolidada: listar, eliminar (limpiar) o vaciar")
    ba.add_argument("accion", choices=["listar", "eliminar", "vaciar"])
    for x in ("--hospital", "--periodo", "--municipio"):
        ba.add_argument(x)
    an = sp.add_parser("analizar", help="Analiza la base consolidada y genera informes")
    for x in ("--hospital", "--periodo", "--municipio"):
        an.add_argument(x, action="append")
    for q in (sy, an):
        q.add_argument("--salida", default="informes")
        q.add_argument("--pdf", action="store_true")
        q.add_argument("--todas-eps", action="store_true")
    ma = sp.add_parser("maestro", help="Carga un maestro local")
    ma.add_argument("nombre", choices=["informe", *maestros.PLANTILLAS])
    ma.add_argument("archivo")
    sp.add_parser("controles", help="Muestra los totales de control oficiales y su recálculo")
    co = sp.add_parser("control-oficial", help="Regenera config/control_oficial.yaml desde el libro INFORME DE SERVICIOS")
    co.add_argument("archivo")
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
    if a.cmd == "sincronizar":
        r = fuentes.sincronizar(forzar=a.forzar, progreso=lambda u: print("  procesando", u))
        print(f"✔ {len(r['procesadas'])} carpetas de mes actualizadas · {r['sin_cambios']} sin cambios · "
              f"{len(r['errores'])} con error")
        for e in r["errores"]:
            print("  ✖", e["carpeta"], e["error"])
        for u in r["ausentes"]:
            print("  ⚠ ya no está en Drive:", u)
        return _informes(a, [base.lote()], "base") if a.informe else 0
    if a.cmd == "fuentes":
        if a.accion == "agregar":
            fuentes.agregar(a.ruta)
        elif a.accion == "quitar":
            fuentes.quitar(a.ruta)
        print("\n".join(fuentes.leer()["carpetas"]) or "(sin carpetas)")
        return 0
    if a.cmd == "base":
        if a.accion == "eliminar":
            print(f"✔ {base.eliminar(a.hospital, a.periodo, a.municipio)} hospital-mes eliminados")
        elif a.accion == "vaciar":
            print(f"✔ {base.vaciar()} hospital-mes eliminados")
        df = base.listar()
        print(df[["hospital", "municipio", "periodo", "facturas", "servicios", "cargado"]].to_string(index=False)
              if not df.empty else "(base vacía)")
        return 0
    if a.cmd == "analizar":
        return _informes(a, [base.lote(a.hospital, a.municipio, a.periodo)], "base")
    if a.cmd == "control-oficial":
        from .referencias.oficial import escribir_desde_informe
        print(f"✔ {escribir_desde_informe(Path(a.archivo).read_bytes())} (actualice CONTROL en constantes.py si cambian)")
        return 0
    if a.cmd == "plantillas":
        maestros.escribir_plantillas(Path("plantillas"))
        print("✔ plantillas/")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""Línea de comandos.

    python -m homologacion_ceco procesar ENTRADAS... --salida resultado.xlsx [--periodo 2026-01] [--mapeo mapeo.json]
    python -m homologacion_ceco plantilla --salida Plantilla_Carga_CECO.xlsx
    python -m homologacion_ceco validar-referencia [--referencia archivo.xlsx]
    python -m homologacion_ceco ejemplo --salida lote_ejemplo.zip

Códigos de salida: 0 = todo cargado · 2 = terminó, pero hay rechazos, retenidos o archivos con error (revisar el
libro de salida) · 1 = error que impidió procesar (referencia inválida, mapeo mal escrito…).
"""
import argparse
import json
import sys
import pandas as pd

from . import __version__
from .columnas import ErrorMapeo
from .ejemplo import escribir_lote
from .exportar import exportar
from .plantilla import generar_plantilla
from .procesador import Opciones, procesar
from .referencia import ErrorReferencia, cargar_referencia


def _parser():
    p = argparse.ArgumentParser(prog="python -m homologacion_ceco",
                                description="Homologación de costos por centro de costos (CECO) al esquema de la resolución.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--referencia", help="Archivo de referencia HOMOLOGACION_CECO_EF_vs_RESOLUCION (por defecto, el incluido)")
    sub = p.add_subparsers(dest="comando", required=True)

    pr = sub.add_parser("procesar", help="Procesa archivos, carpetas o ZIP y genera el libro de salida")
    pr.add_argument("entradas", nargs="+", help="Archivos .xlsx/.xls/.csv/.txt, carpetas o .zip")
    pr.add_argument("--salida", required=True, help="Libro de salida .xlsx")
    pr.add_argument("--periodo", default="", help="Periodo AAAA-MM para archivos que no lo traen")
    pr.add_argument("--mapeo", help="JSON con sinónimos o columnas fijas por archivo (ver docs/HOMOLOGACION_CECO.md)")
    pr.add_argument("--clases", default="", help="Clases de cuenta a cargar, separadas por coma (por defecto 5,6,7)")
    pr.add_argument("--activar-nuevos", action="store_true",
                    help="Cargar CECO nuevos que una regla homologa (los marcados VERIFICAR/EXCLUIR siguen retenidos)")
    pr.add_argument("--usar-movimiento", action="store_true",
                    help="Si el archivo trae DÉBITO y CRÉDITO, usar débito − crédito aunque traiga VALOR o SALDO")
    pr.add_argument("--conservar-duplicados", action="store_true", help="No excluir filas idénticas")
    pr.add_argument("--incluir-subtotales", action="store_true",
                    help="No excluir cuentas que son prefijo de otras (solo si el archivo no trae subtotales)")

    pl = sub.add_parser("plantilla", help="Genera la plantilla de carga directa desde el maestro")
    pl.add_argument("--salida", default="Plantilla_Carga_CECO.xlsx")
    pl.add_argument("--filas", type=int, default=3000, help="Filas con fórmulas de validación (por defecto 3000)")

    sub.add_parser("validar-referencia", help="Muestra el control de calidad del archivo de referencia")

    ej = sub.add_parser("ejemplo", help="Genera un ZIP de ejemplo con datos sintéticos")
    ej.add_argument("--salida", default="lote_ejemplo.zip")
    return p


def _imprimir(texto):
    try:
        print(texto)
    except UnicodeEncodeError:  # consolas con codificación limitada (Windows cp1252/cp850)
        print(texto.encode("ascii", "replace").decode("ascii"))


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.comando == "ejemplo":
            ruta = escribir_lote(args.salida)
            _imprimir(f"Lote de ejemplo: {ruta}\nPruebe: python -m homologacion_ceco procesar {ruta} --salida resultado.xlsx")
            return 0
        ref = cargar_referencia(args.referencia)
        if args.comando == "validar-referencia":
            _imprimir(f"Referencia: {ref.ruta.name} · {len(ref.maestro)} CECO")
            for r in ref.control_calidad().itertuples(index=False):
                _imprimir(f"  [{r.seccion}] {r.indicador}: {r.valor}")
            if len(ref.hallazgos):
                _imprimir("Hallazgos:")
                for r in ref.hallazgos.itertuples(index=False):
                    _imprimir(f"  - {r.tipo} {r.codigo}: {r.detalle}")
            return 0
        if args.comando == "plantilla":
            ruta = generar_plantilla(ref, args.salida, filas=args.filas)
            _imprimir(f"Plantilla generada: {ruta}")
            return 0

        mapeo = None
        if args.mapeo:
            with open(args.mapeo, encoding="utf-8-sig") as f:
                mapeo = json.load(f)
        clases = tuple(c.strip() for c in args.clases.split(",") if c.strip()) or None
        op = Opciones(periodo=args.periodo, clases=clases, activar_nuevos=args.activar_nuevos, mapeo=mapeo,
                      eliminar_duplicados=not args.conservar_duplicados, excluir_subtotales=not args.incluir_subtotales,
                      usar_movimiento=args.usar_movimiento)
        res = procesar(args.entradas, ref, op)
        salida, extras = exportar(res, args.salida)
        r = res.resumen
        _imprimir(f"Libro de salida: {salida}")
        for extra in extras:
            _imprimir(f"Detalle adicional (CSV): {extra}")
        _imprimir(f"Archivos/hojas: {r['archivos']} (con error: {r['archivos_con_error']}) · registros: {r['registros']}")
        _imprimir(f"Cargados: {r['cargado']} · retenidos: {r['retenido']} · excluidos: {r['excluido']} · rechazados: {r['rechazado']}"
                  f" · líneas consolidadas: {r['lineas_consolidadas']}")
        for c in res.conciliacion.itertuples(index=False):
            if pd.notna(c.registros) and not str(c.concepto).startswith("   "):
                _imprimir(f"  {c.concepto:<28} {int(c.registros):>7}  {c.valor:>20,.2f}")
        _imprimir(res.conciliacion["concepto"].iloc[-1])
        if res.codigo_salida:
            _imprimir("REVISAR: hay registros rechazados o retenidos, o archivos con error (vea el libro de salida).")
        return res.codigo_salida
    except (ErrorReferencia, ErrorMapeo, ValueError, OSError, json.JSONDecodeError) as exc:
        _imprimir(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

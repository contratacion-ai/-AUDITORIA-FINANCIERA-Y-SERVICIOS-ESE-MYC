"""Línea de comandos.

Ejemplos::

    # Procesar un ZIP con carpetas por hospital y mes; exporta todo
    python -m rips_auditoria procesar Hospital_San_Antonio.zip -s salida/

    # Cargar TXT sueltos de una remisión, indicando el hospital
    python -m rips_auditoria procesar AF000123.txt US000123.txt AC000123.txt CT000123.txt \\
        --hospital "Hospital San Antonio" -s salida/

    # Exportar sólo una combinación hospital-mes
    python -m rips_auditoria procesar lote.zip -s salida/ --filtro-hospital "Hospital San Antonio" --filtro-mes 2026-05

    # Ver qué hospitales y meses contiene una carga (sin exportar)
    python -m rips_auditoria listar lote.zip

    # Generar la plantilla Excel vacía del Anexo Técnico No. 1
    python -m rips_auditoria plantilla -s plantillas/Plantilla_RIPS_Res3374.xlsx

Códigos de salida: 0 = procesado (aunque haya incidencias), 1 = error de
uso o configuración, 2 = no se encontró ningún archivo RIPS, 3 = procesado
pero el envío al aplicativo de auditoría falló.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import cargar_configuracion
from .homologacion import ErrorMapeo, cargar_mapeo


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rips_auditoria", description="RIPS Res. 3374/2000 -> auditoría financiera")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="Mostrar detalle del proceso")
    sub = p.add_subparsers(dest="comando", required=True)

    def comunes(sp):
        sp.add_argument("entradas", nargs="+", help="Archivos TXT, carpetas o ZIP (se pueden mezclar)")
        sp.add_argument("-c", "--config", help="Archivo de configuración YAML (por defecto config/configuracion.yaml)")
        sp.add_argument("--hospital", help="Forzar el nombre del hospital para todo lo cargado")

    pr = sub.add_parser("procesar", help="Validar, organizar por hospital/mes y exportar Excel/JSON")
    comunes(pr)
    pr.add_argument("-s", "--salida", required=True, help="Carpeta de salida")
    pr.add_argument("-m", "--mapeo", help="Archivo YAML de homologación (sobrescribe el de la configuración)")
    pr.add_argument("--filtro-hospital", help="Exportar sólo este hospital")
    pr.add_argument("--filtro-mes", help="Exportar sólo este período (AAAA-MM)")
    pr.add_argument("--sin-excel", action="store_true")
    pr.add_argument("--sin-json", action="store_true")
    pr.add_argument("--sin-consolidado", action="store_true")
    pr.add_argument("--sin-homologacion", action="store_true")
    pr.add_argument("--sin-aplicativo", action="store_true",
                    help="No generar los archivos mensuales para el aplicativo de auditoría")
    pr.add_argument("--enviar-aplicativo", action="store_true",
                    help="Enviar cada mes al aplicativo de auditoría abierto (localhost:8000)")

    ls = sub.add_parser("listar", help="Listar hospitales y meses encontrados, con conteo de incidencias")
    comunes(ls)

    pl = sub.add_parser("plantilla", help="Generar la plantilla Excel vacía del Anexo Técnico No. 1")
    pl.add_argument("-s", "--salida", default="Plantilla_RIPS_Res3374_AnexoTecnico1.xlsx")

    dt_ = sub.add_parser("datos", help="Limpiar o depurar los datos cargados, leer JSON y cargar catálogos de CUPS")
    ds = dt_.add_subparsers(dest="accion", required=True)
    li = ds.add_parser("limpiar", help="Eliminar lo cargado de un mes, una sede o ambos (muestra y pide confirmación)")
    li.add_argument("--mes", help="AAAA-MM")
    li.add_argument("--sede", help="Nombre de la sede u hospital")
    du = ds.add_parser("duplicados", help="Detectar y eliminar duplicados reales (muestra y pide confirmación)")
    for x in (li, du):
        x.add_argument("--confirmar", action="store_true", help="Confirmar sin preguntar (después de ver la lista)")
    js = ds.add_parser("json", help="Leer RIPS en JSON (Res. 2275 u homologados) e integrarlos sin duplicar")
    js.add_argument("entradas", nargs="+", help="Archivos JSON, carpetas o ZIP")
    js.add_argument("--hospital", help="Sede de los JSON (si no está en la carpeta)")
    js.add_argument("--mes", help="Mes AAAA-MM de los JSON (si no está en la carpeta ni en las fechas)")
    ca = ds.add_parser("catalogo", help="Cargar el archivo complementario de CUPS y/o el tarifario UVB")
    ca.add_argument("--complemento", help="Excel o CSV con columnas por código CUPS")
    ca.add_argument("--tarifario", help="Tarifario del Manual UVB (.xlsb, .xlsx o .csv)")
    return p


def _confirmar(vista: dict, accion: str, automatico: bool) -> bool:
    """Muestra lo que se eliminaría y pide «SI» (o usa --confirmar)."""
    import pandas as pd

    if not vista["total_filas"]:
        print("No hay registros para eliminar.")
        return False
    print(f"\nSe eliminarían {vista['total_filas']:,} fila(s) · {vista['facturas']:,} factura(s) · "
          f"valor $ {vista['valor']:,.0f}".replace(",", "."))
    print(pd.DataFrame(vista["resumen"]).to_string(index=False))
    muestra = pd.DataFrame(vista["filas"]).head(20)
    cols = [c for c in ["MES", "SEDE", "EPS", "FACTURA_FEV", "TIPO_SERVICIO", "CODIGO_CUPS_CUMS", "CANTIDAD",
                        "VALOR_SERVICIO", "tipo_duplicado"] if c in muestra]
    print("\nPrimeras filas:\n" + muestra[cols].to_string(index=False))
    if automatico:
        return True
    try:
        return input(f"\n¿{accion}? Escriba SI para confirmar: ").strip().upper() == "SI"
    except EOFError:  # sin consola interactiva: no se confirma
        return False


def _datos(args) -> int:
    from . import catalogo_cups, depuracion, json_rips

    if args.accion == "limpiar":
        try:
            v = depuracion.vista_limpieza(args.mes, args.sede)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        if not _confirmar(v, "Eliminar estos registros", args.confirmar):
            print("No se eliminó nada.")
            return 0
        print(depuracion.aplicar_limpieza(v["ficha"], args.mes, args.sede))
    elif args.accion == "duplicados":
        v = depuracion.vista_duplicados()
        if not _confirmar(v, "Eliminar estos duplicados", args.confirmar):
            print("No se eliminó nada.")
            return 0
        print(depuracion.aplicar_duplicados(v["ficha"]))
    elif args.accion == "json":
        r = json_rips.cargar_json(args.entradas, hospital=args.hospital, mes=args.mes)
        for aviso in r.pop("avisos"):
            print("[!]", aviso)
        print(r)
        print(catalogo_cups.actualizar_descripciones())
    elif args.accion == "catalogo":
        if not (args.complemento or args.tarifario):
            print("Indique --complemento y/o --tarifario", file=sys.stderr)
            return 1
        if args.complemento:
            print(catalogo_cups.cargar_complemento(args.complemento))
        if args.tarifario:
            print(catalogo_cups.cargar_tarifario(args.tarifario))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.comando == "datos":
        return _datos(args)

    if args.comando == "plantilla":
        from .excel import generar_plantilla
        ruta = generar_plantilla(args.salida)
        print(f"Plantilla generada: {ruta}")
        return 0

    try:
        cfg = cargar_configuracion(args.config)
        if getattr(args, "mapeo", None):
            cfg["homologacion"]["archivo_mapeo"] = str(Path(args.mapeo).resolve())
        if args.comando == "procesar":
            exp = cfg["exportacion"]
            exp["excel"] &= not args.sin_excel
            exp["json"] &= not args.sin_json
            exp["consolidado"] &= not args.sin_consolidado
            cfg["homologacion"]["habilitada"] &= not args.sin_homologacion
            app = cfg.setdefault("aplicativo_auditoria", {})
            app["habilitado"] = app.get("habilitado", True) and not args.sin_aplicativo
            app["enviar_automaticamente"] = app.get("enviar_automaticamente", False) or args.enviar_aplicativo
            if cfg["homologacion"]["habilitada"]:
                cargar_mapeo(cfg["homologacion"]["archivo_mapeo"])  # falla temprano si el mapeo es inválido
    except (FileNotFoundError, ValueError, ErrorMapeo) as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 1

    from .procesamiento import procesar
    res = procesar(args.entradas, cfg, hospital=args.hospital)
    if res.envios.empty:
        print("No se encontraron archivos RIPS (CT, AF, US, AD, AC, AP, AU, AH, AN, AM, AT) en las entradas.",
              file=sys.stderr)
        if not res.incidencias.empty:
            print(res.incidencias[["regla", "archivo", "mensaje"]].to_string(index=False), file=sys.stderr)
        return 2

    tabla = res.resumen_unidades()
    cols = [c for c in ["hospital", "periodo", "remisiones", "facturas", "valor_neto_facturado_af",
                        "facturas_con_diferencia", "inc_error", "inc_advertencia"] if c in tabla]
    print(tabla[cols].to_string(index=False))

    if args.comando == "procesar":
        from .exportacion import exportar_todo
        if args.filtro_hospital or args.filtro_mes:
            disponibles = res.unidades()
            if not any((not args.filtro_hospital or h == args.filtro_hospital) and
                       (not args.filtro_mes or p == args.filtro_mes) for h, p in disponibles):
                print(f"El filtro no coincide con ninguna unidad. Disponibles: {disponibles}", file=sys.stderr)
                return 1
        archivos = exportar_todo(res, args.salida, cfg, args.filtro_hospital, args.filtro_mes)
        print(f"\n{len(archivos)} archivo(s) generados en {Path(args.salida).resolve()}")
        app = cfg.get("aplicativo_auditoria") or {}
        mensuales = [a for a in archivos if a.parent.name == "APLICATIVO_AUDITORIA"]
        if mensuales and app.get("enviar_automaticamente"):
            from .aplicativo import ErrorAplicativo, enviar_meses
            try:
                resultados = enviar_meses(mensuales, app, aviso=print)
            except (ErrorAplicativo, OSError) as exc:
                print(f"\n[!] No se envió al aplicativo de auditoría: {exc}", file=sys.stderr)
                return 3
            from .aplicativo import resumen_cruce
            cruce = resumen_cruce(app)
            if cruce:
                print("\nValidación del cruce en el aplicativo (RIPS vs. Software 360):")
                for f in cruce:
                    print(f"  {f['llave']:<10} {f['origen']}→{f['destino']}: {f['pct_cruce']}% · {f['estado']}")
            if any(r["estado"] != "ok" for r in resultados):
                return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

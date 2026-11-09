"""Línea de comandos.

Ejemplo::

    python -m rips_conciliacion \
        --rips RIPS_SEDE_TAME.zip RIPS_SEDE_FORTUL.rar \
        --facturacion facturacion_por_ambito.xlsx \
        --catalogo-sedes sedes.csv \
        --salida resultados --formato ambos
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

from .conciliador import ConfiguracionConciliacion, conciliar
from .reportes import exportar_csv, exportar_excel, texto_reporte_general


def _entrada_con_sede(texto: str) -> dict | str:
    """Permite ``archivo.zip@SEDE`` para asignar la sede explícitamente."""
    if "@" in texto:
        ruta, sede = texto.rsplit("@", 1)
        return {"entrada": ruta, "sede": sede}
    return texto


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="rips_conciliacion", description="Conciliación Facturación vs RIPS – ESE"
    )
    p.add_argument(
        "--rips",
        nargs="+",
        required=True,
        help="TXT/JSON/ZIP/RAR o carpetas de RIPS (archivo@SEDE para asignar sede)",
    )
    p.add_argument(
        "--facturacion",
        nargs="+",
        required=True,
        help="CSV/TXT/XLSX de facturación (archivo@SEDE)",
    )
    p.add_argument("--catalogo-sedes", help="CSV/XLSX: codigo_prestador;sede;hospital")
    p.add_argument("--equivalencias", help="CSV/XLSX: codigo_facturacion;codigo_rips")
    p.add_argument(
        "--salida", default="resultados_conciliacion", help="Carpeta de salida"
    )
    p.add_argument("--formato", choices=["excel", "csv", "ambos"], default="ambos")
    p.add_argument("--separador-decimal", choices=["auto", ".", ","], default="auto")
    p.add_argument(
        "--ignorar-ceros-factura", action="store_true", help="FE000123 = FE123"
    )
    p.add_argument(
        "--tolerancia-valor",
        default="0",
        help="Diferencia de valor tolerada (por defecto 0 = exacto)",
    )
    p.add_argument("--top", type=int, default=20)
    p.add_argument(
        "--sin-lineas",
        action="store_true",
        help="No incluir las hojas de líneas (archivos muy grandes)",
    )
    args = p.parse_args(argv)

    cfg = ConfiguracionConciliacion(
        separador_decimal=args.separador_decimal,
        ignorar_ceros_factura=args.ignorar_ceros_factura,
        tolerancia_valor=Decimal(args.tolerancia_valor),
        top_n=args.top,
    )
    resultado = conciliar(
        [_entrada_con_sede(r) for r in args.rips],
        [_entrada_con_sede(f) for f in args.facturacion],
        cfg,
        catalogo_sedes=args.catalogo_sedes,
        equivalencias=args.equivalencias,
    )
    print(texto_reporte_general(resultado))
    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    if args.formato in ("excel", "ambos"):
        exportar_excel(
            resultado,
            salida / "conciliacion_rips_facturacion.xlsx",
            not args.sin_lineas,
        )
    if args.formato in ("csv", "ambos"):
        exportar_csv(resultado, salida / "csv", not args.sin_lineas)
    print(f"\nResultados en: {salida.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

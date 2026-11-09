"""Exportación de resultados: Excel con varias hojas, CSV o ambos.

* Excel: los valores ``Decimal`` se escriben como números con formato de
  miles; Excel solo representa 15 dígitos significativos, suficiente para
  pesos colombianos con centavos. El archivo CSV conserva la cifra exacta.
* CSV: separador ``;`` y punto decimal, UTF-8 con BOM (abre bien en Excel
  en español) y valores escritos exactamente como ``Decimal``.
"""

from __future__ import annotations

import io
import re
import zipfile
from decimal import Decimal
from pathlib import Path

import pandas as pd

from .conciliador import ResultadoConciliacion
from .emparejamiento import __doc__ as CRITERIO_EMPAREJAMIENTO

LIMITE_FILAS_EXCEL = 1_048_000
FORMATO_MONEDA = "#,##0.00########"


def hojas(
    resultado: ResultadoConciliacion, incluir_lineas: bool = True
) -> dict[str, pd.DataFrame]:
    """Tablas del reporte en orden de presentación (nivel 1 y nivel 2)."""
    salida: dict[str, pd.DataFrame] = {
        "Reporte general": pd.DataFrame(
            resultado.resumen, columns=["sección", "indicador", "valor"]
        ),
        "Archivos": resultado.archivos,
        "Incidencias": resultado.incidencias,
        "Diferencias": resultado.diferencias,
        "Detalle factura-código": resultado.detalle,
        "Facturas": resultado.facturas,
        "Cruce de códigos": resultado.cruce_codigos,
        "Cruce códigos por sede": resultado.cruce_codigos_sede,
    }
    salida.update(resultado.comparativos)
    salida.update(resultado.consumo)
    salida["Otros códigos RIPS"] = resultado.otros_codigos
    if incluir_lineas:
        salida["Líneas facturación"] = resultado.lineas_facturacion
        salida["Líneas RIPS"] = resultado.lineas_rips
    salida["Criterio emparejamiento"] = pd.DataFrame(
        {"criterio": [ln for ln in (CRITERIO_EMPAREJAMIENTO or "").splitlines()]}
    )
    return salida


def _nombre_hoja(nombre: str, usados: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "-", nombre)[:31]
    candidato, i = base, 2
    while candidato in usados:
        sufijo = f" ({i})"
        candidato = base[: 31 - len(sufijo)] + sufijo
        i += 1
    usados.add(candidato)
    return candidato


def exportar_excel(
    resultado: ResultadoConciliacion, destino, incluir_lineas: bool = True
) -> None:
    """Escribe el libro .xlsx en ``destino`` (ruta o buffer)."""
    from openpyxl.utils import get_column_letter

    usados: set[str] = set()
    with pd.ExcelWriter(destino, engine="openpyxl") as writer:
        for nombre, df in hojas(resultado, incluir_lineas).items():
            partes = [
                df.iloc[i : i + LIMITE_FILAS_EXCEL]
                for i in range(0, max(len(df), 1), LIMITE_FILAS_EXCEL)
            ]
            for parte in partes:
                hoja = _nombre_hoja(nombre, usados)
                parte.to_excel(writer, sheet_name=hoja, index=False)
                ws = writer.sheets[hoja]
                ws.freeze_panes = "A2"
                if len(parte.columns):
                    ws.auto_filter.ref = ws.dimensions
                for j, col in enumerate(parte.columns, start=1):
                    letra = get_column_letter(j)
                    muestra = parte[col].head(200)
                    ancho = max([len(str(col))] + [len(str(v)) for v in muestra]) + 2
                    ws.column_dimensions[letra].width = min(max(ancho, 10), 60)
                    if any(isinstance(v, Decimal) for v in muestra):
                        for celda in ws[letra][1:]:
                            celda.number_format = FORMATO_MONEDA


def _csv_bytes(df: pd.DataFrame) -> bytes:
    texto = df.to_csv(index=False, sep=";")
    return texto.encode("utf-8-sig")


def nombre_archivo(nombre: str) -> str:
    import unicodedata

    plano = "".join(
        c
        for c in unicodedata.normalize("NFD", nombre)
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^A-Za-z0-9]+", "_", plano).strip("_").lower()


def exportar_csv(
    resultado: ResultadoConciliacion, carpeta, incluir_lineas: bool = True
) -> list[Path]:
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    rutas = []
    for i, (nombre, df) in enumerate(hojas(resultado, incluir_lineas).items(), start=1):
        ruta = carpeta / f"{i:02d}_{nombre_archivo(nombre)}.csv"
        ruta.write_bytes(_csv_bytes(df))
        rutas.append(ruta)
    return rutas


def exportar_zip(
    resultado: ResultadoConciliacion,
    formato: str = "ambos",
    incluir_lineas: bool = True,
) -> bytes:
    """Paquete descargable con Excel, CSV o ambos (``formato``: excel | csv | ambos)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        if formato in ("excel", "ambos"):
            xlsx = io.BytesIO()
            exportar_excel(resultado, xlsx, incluir_lineas)
            zf.writestr("conciliacion_rips_facturacion.xlsx", xlsx.getvalue())
        if formato in ("csv", "ambos"):
            for i, (nombre, df) in enumerate(
                hojas(resultado, incluir_lineas).items(), start=1
            ):
                zf.writestr(f"csv/{i:02d}_{nombre_archivo(nombre)}.csv", _csv_bytes(df))
    return buffer.getvalue()


def texto_reporte_general(resultado: ResultadoConciliacion) -> str:
    """Reporte general en texto plano (para consola)."""
    lineas = [
        "REPORTE GENERAL DE REVISIÓN – CONCILIACIÓN FACTURACIÓN vs RIPS",
        "=" * 64,
    ]
    seccion = None
    for sec, indicador, valor in resultado.resumen:
        if sec != seccion:
            lineas += ["", sec, "-" * len(sec)]
            seccion = sec
        if isinstance(valor, Decimal):
            valor = f"{valor:,}"
        lineas.append(f"{indicador:<60} {valor}")
    errores = resultado.incidencias[resultado.incidencias["severidad"] == "ERROR"]
    if not errores.empty:
        lineas += ["", "Primeros errores de estructura:"]
        for _, e in errores.head(15).iterrows():
            ubic = f" línea {int(e['linea'])}" if pd.notna(e["linea"]) else ""
            campo = f" campo '{e['campo']}'" if e["campo"] else ""
            lineas.append(f"  - {e['archivo']}{ubic}{campo}: {e['mensaje']}")
    return "\n".join(lineas)

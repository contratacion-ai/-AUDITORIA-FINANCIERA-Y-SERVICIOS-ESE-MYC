"""Plantillas y exportación Excel parametrizadas por el Anexo Técnico No. 1.

Todas las hojas de datos se construyen a partir de ``especificacion.py``:
encabezado con el nombre del dato del Anexo, comentario con nombre técnico,
longitud, tipo y valores permitidos, ancho de columna según la longitud y
lista desplegable para los campos con tabla de valores. Así, la plantilla
vacía y los archivos exportados comparten exactamente la misma estructura.

El consolidado anual (cientos de miles de filas) se escribe con xlsxwriter en
modo ``constant_memory`` (fila a fila) para no agotar memoria; los archivos
por hospital-mes usan el modo normal, unas 3 veces más rápido. Si una tabla supera el límite de filas de Excel se reparte en hojas
"AP", "AP (2)", ...
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import pandas as pd
import xlsxwriter

from .especificacion import ESPECIFICACIONES, ORDEN_ARCHIVOS, TIPOS_DATO, EspecArchivo, especificacion
from .incidencias import REGLAS, resumen_incidencias
from .validacion import a_fecha, a_numero

MAX_FILAS_EXCEL = 1_048_575  # sin contar el encabezado
ETIQUETAS_TRAZA = {
    "hospital": "Hospital",
    "periodo": "Período (AAAA-MM)",
    "envio_id": "Id remisión (interno)",
    "remision": "Número de remisión",
    "carpeta_envio": "Carpeta de la remisión",
    "archivo": "Archivo origen",
    "linea": "Línea en archivo",
}
NORMA = "Resolución 3374 de 2000 - Anexo Técnico No. 1"


class LibroRips:
    """Envoltura de xlsxwriter con los formatos y helpers comunes."""

    def __init__(self, ruta: str | Path, memoria_constante: bool = False):
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        self.wb = xlsxwriter.Workbook(str(ruta), {
            "constant_memory": memoria_constante,
            "default_date_format": "dd/mm/yyyy",
            "strings_to_numbers": False,
            "strings_to_formulas": False,
            "strings_to_urls": False,
        })
        self.f_titulo = self.wb.add_format({"bold": True, "font_size": 14, "font_color": "#1F4E79"})
        self.f_sub = self.wb.add_format({"bold": True, "font_color": "#1F4E79"})
        self.f_enc = self.wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#1F4E79",
                                         "text_wrap": True, "valign": "top", "border": 1})
        self.f_enc_traza = self.wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#7F7F7F",
                                               "text_wrap": True, "valign": "top", "border": 1})
        self.f_moneda = self.wb.add_format({"num_format": "#,##0.00"})
        self.f_fecha = self.wb.add_format({"num_format": "dd/mm/yyyy"})
        self.f_texto = self.wb.add_format({"num_format": "@"})
        self.f_wrap = self.wb.add_format({"text_wrap": True, "valign": "top"})
        self.nombres: set[str] = set()

    def nombre_hoja(self, base: str) -> str:
        base = "".join(ch for ch in base if ch not in "[]:*?/\\")[:31]
        nombre, i = base, 2
        while nombre.lower() in self.nombres:
            sufijo = f" ({i})"
            nombre, i = base[: 31 - len(sufijo)] + sufijo, i + 1
        self.nombres.add(nombre.lower())
        return nombre

    def cerrar(self) -> None:
        self.wb.close()

    # -- escritura genérica de un DataFrame ---------------------------------
    def tabla(self, nombre: str, df: pd.DataFrame, encabezados: list[str] | None = None,
              anchos: list[int] | None = None, formatos: list | None = None,
              comentarios: list[str | None] | None = None, validaciones: list[list[str] | None] | None = None,
              formatos_enc: list | None = None):
        """Escribe ``df`` en una o varias hojas (si supera el límite de Excel)."""
        encabezados = encabezados or [str(c) for c in df.columns]
        n = len(df)
        bloques = max(1, math.ceil(n / MAX_FILAS_EXCEL))
        valores = _a_celdas(df)
        hojas = []
        for b in range(bloques):
            ws = self.wb.add_worksheet(self.nombre_hoja(nombre if b == 0 else f"{nombre} ({b + 1})"))
            hojas.append(ws)
            for j, enc in enumerate(encabezados):
                ws.write(0, j, enc, (formatos_enc[j] if formatos_enc else None) or self.f_enc)
                if comentarios and comentarios[j]:
                    ws.write_comment(0, j, comentarios[j], {"x_scale": 2.2, "y_scale": 2.5})
                ancho = anchos[j] if anchos else max(10, min(40, len(enc) + 2))
                ws.set_column(j, j, ancho, formatos[j] if formatos else None)
            ini, fin = b * MAX_FILAS_EXCEL, min(n, (b + 1) * MAX_FILAS_EXCEL)
            for i in range(ini, fin):
                fila = valores[i]
                for j, v in enumerate(fila):
                    if v is None:
                        continue
                    fmt = formatos[j] if formatos else None
                    if isinstance(v, (dt.datetime, dt.date)):
                        ws.write_datetime(i - ini + 1, j, v, self.f_fecha)
                    elif isinstance(v, (int, float)) and not isinstance(v, bool):
                        ws.write_number(i - ini + 1, j, v, fmt)
                    else:
                        ws.write_string(i - ini + 1, j, str(v), fmt)
            ultima = max(fin - ini, 1)
            if encabezados:
                ws.autofilter(0, 0, ultima, len(encabezados) - 1)
            ws.freeze_panes(1, 0)
            if validaciones:
                for j, lista in enumerate(validaciones):
                    if lista:
                        ws.data_validation(1, j, max(ultima, 1000), j, {
                            "validate": "list", "source": lista, "error_type": "warning",
                            "error_title": "Valor fuera de tabla Res. 3374",
                            "error_message": "El valor no está en la tabla de la Res. 3374. Revise el Diccionario.",
                        })
        return hojas


def _a_celdas(df: pd.DataFrame) -> list[list]:
    """Convierte un DataFrame a listas de valores escribibles (None para vacíos)."""
    if df.empty:
        return []
    cols = []
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            vals = [None if pd.isna(x) else x.to_pydatetime() for x in s]
        elif pd.api.types.is_bool_dtype(s):
            vals = [None if pd.isna(x) else ("SI" if x else "NO") for x in s]
        elif pd.api.types.is_numeric_dtype(s):
            vals = [None if pd.isna(x) else (int(x) if float(x).is_integer() and abs(x) < 1e15 else float(x)) for x in s]
        else:
            vals = [None if (x is None or x is pd.NA or (isinstance(x, float) and math.isnan(x)) or x == "")
                    else (x.to_pydatetime() if isinstance(x, pd.Timestamp) else x) for x in s.astype(object)]
        cols.append(vals)
    return [list(f) for f in zip(*cols)]


# ---------------------------------------------------------------------------
# Hojas parametrizadas por la especificación
# ---------------------------------------------------------------------------

def comentario_campo(c) -> str:
    texto = f"{c.nombre}\nLongitud máx.: {c.longitud}\nTipo: {TIPOS_DATO[c.tipo]}\n"
    texto += "Obligatorio" if c.obligatorio else (
        f"Obligatorio si {c.obligatorio_si[0]} = {'/'.join(c.obligatorio_si[1])}" if c.obligatorio_si else "Opcional")
    if c.valores:
        texto += "\nValores: " + "; ".join(f"{k}={v}" for k, v in c.valores.items())
    return texto[:1000]


def datos_para_excel(tipo: str, df: pd.DataFrame) -> pd.DataFrame:
    """Tipifica columnas para Excel: valores (V/E) numéricos y fechas reales
    cuando son válidos; si no son válidos se conserva el texto original para
    que el auditor vea exactamente lo que vino en el TXT."""
    spec = especificacion(tipo)
    out = df.copy()
    for c in spec.campos:
        if c.nombre not in out:
            continue
        s = out[c.nombre]
        if c.tipo in ("V", "E"):
            num = a_numero(s)
            out[c.nombre] = num.astype(object).where(num.notna(), s.astype(object))
        elif c.tipo == "F":
            f = a_fecha(s)
            out[c.nombre] = f.astype(object).where(f.notna(), s.astype(object))
    return out


def hoja_rips(libro: LibroRips, tipo: str, df: pd.DataFrame | None, con_traza: bool = True) -> None:
    spec: EspecArchivo = especificacion(tipo)
    campos = spec.columnas
    traza = [c for c in ETIQUETAS_TRAZA if con_traza and df is not None and c in df.columns]
    if df is None:
        df = pd.DataFrame(columns=campos)
    df = datos_para_excel(tipo, df.reindex(columns=campos + traza))
    enc = [c.etiqueta for c in spec.campos] + [ETIQUETAS_TRAZA[c] for c in traza]
    anchos = [max(8, min(45, max(c.longitud, len(c.etiqueta) // 2) + 2)) for c in spec.campos] + [14] * len(traza)
    formatos = [libro.f_moneda if c.tipo == "V" else (libro.f_texto if c.tipo in ("A", "C", "X") else None)
                for c in spec.campos] + [None] * len(traza)
    coment = [comentario_campo(c) for c in spec.campos] + [None] * len(traza)
    valid = [list(c.valores) if c.valores and sum(len(k) + 1 for k in c.valores) < 250 else None
             for c in spec.campos] + [None] * len(traza)
    fmt_enc = [libro.f_enc] * len(spec.campos) + [libro.f_enc_traza] * len(traza)
    libro.tabla(tipo, df, enc, anchos, formatos, coment, valid, fmt_enc)


def tabla_diccionario() -> pd.DataFrame:
    filas = []
    for t in ORDEN_ARCHIVOS:
        spec = ESPECIFICACIONES[t]
        for i, c in enumerate(spec.campos, start=1):
            filas.append({
                "archivo": t, "nombre_archivo": spec.nombre, "numeral_anexo": spec.numeral, "orden": i,
                "dato_anexo": c.etiqueta, "nombre_tecnico": c.nombre, "longitud": c.longitud,
                "tipo": TIPOS_DATO[c.tipo],
                "obligatorio": "SI" if c.obligatorio else (
                    f"SI cuando {c.obligatorio_si[0]}={'/'.join(c.obligatorio_si[1])}" if c.obligatorio_si else "NO"),
                "valores_permitidos": "; ".join(f"{k}={v}" for k, v in (c.valores or {}).items()),
                "valores_extendidos_advertencia": ", ".join(c.valores_ext or {}),
            })
    return pd.DataFrame(filas)


def tabla_catalogos() -> pd.DataFrame:
    filas, vistos = [], set()
    for t in ORDEN_ARCHIVOS:
        for c in ESPECIFICACIONES[t].campos:
            if c.valores and (c.nombre, tuple(c.valores)) not in vistos:
                vistos.add((c.nombre, tuple(c.valores)))
                filas += [{"campo": c.nombre, "dato_anexo": c.etiqueta, "codigo": k, "descripcion": v,
                           "origen": "Res. 3374/2000"} for k, v in c.valores.items()]
                filas += [{"campo": c.nombre, "dato_anexo": c.etiqueta, "codigo": k, "descripcion": v,
                           "origen": "Catálogo extendido (advertencia)"} for k, v in (c.valores_ext or {}).items()]
    return pd.DataFrame(filas)


def _hojas_referencia(libro: LibroRips) -> None:
    libro.tabla("Diccionario", tabla_diccionario(), anchos=[8, 30, 10, 7, 45, 24, 9, 22, 22, 60, 40])
    libro.tabla("Catalogos", tabla_catalogos(), anchos=[22, 40, 10, 60, 30])
    libro.tabla("Reglas_Validacion", pd.DataFrame(
        [{"regla": k, "severidad_defecto": v[0], "descripcion": v[1]} for k, v in REGLAS.items()]),
        anchos=[32, 16, 100])


# ---------------------------------------------------------------------------
# Plantilla vacía
# ---------------------------------------------------------------------------

INSTRUCCIONES = [
    "PLANTILLA RIPS PARAMETRIZADA - " + NORMA,
    "",
    "1. Cada hoja CT, AF, US, AD, AC, AP, AU, AH, AN, AM, AT corresponde a un archivo plano del Anexo Técnico No. 1.",
    "2. Las columnas están en el mismo orden del Anexo. Pase el cursor sobre cada encabezado para ver",
    "   nombre técnico, longitud máxima, tipo de dato, obligatoriedad y valores permitidos.",
    "3. Fechas: dd/mm/aaaa. Horas: hh:mm (24 h). Valores: punto decimal, sin separador de miles.",
    "4. Los campos con tabla de valores tienen lista desplegable (advierte si el valor no está en la tabla).",
    "5. La hoja 'Diccionario' documenta cada campo; 'Catalogos' lista los valores permitidos;",
    "   'Reglas_Validacion' describe las validaciones que aplica la herramienta.",
    "6. Las columnas grises (Hospital, Período, Remisión, Archivo, Línea) sólo aparecen en archivos exportados",
    "   y permiten rastrear cada registro hasta el TXT original.",
]


def generar_plantilla(ruta: str | Path) -> Path:
    """Genera la plantilla Excel vacía del Anexo Técnico No. 1."""
    libro = LibroRips(ruta)
    ws = libro.wb.add_worksheet(libro.nombre_hoja("Instrucciones"))
    ws.set_column(0, 0, 120)
    for i, linea in enumerate(INSTRUCCIONES):
        ws.write_string(i, 0, linea, libro.f_titulo if i == 0 else None)
    for t in ORDEN_ARCHIVOS:
        hoja_rips(libro, t, None, con_traza=False)
    _hojas_referencia(libro)
    libro.cerrar()
    return Path(ruta)


# ---------------------------------------------------------------------------
# Exportación de datos
# ---------------------------------------------------------------------------

def _hoja_resumen(libro: LibroRips, titulo: str, pares: list[tuple[str, object]], tabla_reg: pd.DataFrame) -> None:
    ws = libro.wb.add_worksheet(libro.nombre_hoja("Resumen"))
    ws.set_column(0, 0, 38)
    ws.set_column(1, 3, 22)
    ws.write_string(0, 0, titulo, libro.f_titulo)
    ws.write_string(1, 0, NORMA)
    fila = 3
    for k, v in pares:
        ws.write_string(fila, 0, k, libro.f_sub)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            ws.write_number(fila, 1, v, libro.f_moneda if isinstance(v, float) else None)
        else:
            ws.write_string(fila, 1, "" if v is None else str(v))
        fila += 1
    fila += 1
    for j, c in enumerate(tabla_reg.columns):
        ws.write_string(fila, j, str(c), libro.f_enc)
    for _, r in tabla_reg.iterrows():
        fila += 1
        for j, v in enumerate(r):
            if isinstance(v, (int, float)):
                ws.write_number(fila, j, v)
            else:
                ws.write_string(fila, j, str(v))


def _tabla_registros(datos: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.DataFrame([{"archivo": t, "descripcion": ESPECIFICACIONES[t].nombre,
                          "registros": int(len(datos.get(t, [])))} for t in ORDEN_ARCHIVOS])


def exportar_unidad_excel(unidad, ruta: str | Path) -> Path:
    """Excel de un hospital-mes con la estructura del Anexo Técnico No. 1."""
    r = unidad.resumen()
    libro = LibroRips(ruta)
    pares = [
        ("Hospital", r["hospital"]), ("Período", r["periodo"]),
        ("Generado", dt.datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Remisiones", r["remisiones"]), ("Facturas (AF)", r["facturas"]),
        ("Usuarios únicos (US)", r["usuarios_unicos"]),
        ("Valor neto facturado (AF)", float(r["valor_neto_facturado_af"])),
        ("Valor servicios RIPS", float(r["valor_servicios_rips"])),
        ("Facturas con diferencia", r["facturas_con_diferencia"]),
    ] + [(f"Incidencias {k}", v) for k, v in r["incidencias"].items()]
    _hoja_resumen(libro, f"RIPS {r['hospital']} - {r['periodo']}", pares, _tabla_registros(unidad.datos))
    libro.tabla("Remisiones", unidad.envios)
    for t in ORDEN_ARCHIVOS:
        hoja_rips(libro, t, unidad.datos.get(t))
    libro.tabla("Conciliacion_Facturas", unidad.conciliacion)
    libro.tabla("Resumen_Incidencias", resumen_incidencias(unidad.incidencias), anchos=[14, 32, 90, 10])
    libro.tabla("Incidencias", unidad.incidencias, anchos=[20, 10, 9, 60, 8, 7, 20, 30, 30, 13, 80])
    _hojas_referencia(libro)
    libro.cerrar()
    return Path(ruta)


def exportar_consolidado_excel(resultado, ruta: str | Path) -> Path:
    """Excel con todos los hospitales y meses (columnas Hospital y Período)."""
    libro = LibroRips(ruta, memoria_constante=True)
    indice = resultado.resumen_unidades()
    total = [("Hospitales", len(resultado.hospitales())), ("Unidades hospital-mes", len(indice)),
             ("Remisiones", len(resultado.envios)), ("Generado", dt.datetime.now().strftime("%Y-%m-%d %H:%M"))]
    _hoja_resumen(libro, "RIPS consolidado por hospital y mes", total, _tabla_registros(resultado.datos))
    libro.tabla("Indice_Hospital_Mes", indice)
    libro.tabla("Remisiones", resultado.envios)
    for t in ORDEN_ARCHIVOS:
        hoja_rips(libro, t, resultado.datos.get(t))
    libro.tabla("Conciliacion_Facturas", resultado.conciliacion)
    libro.tabla("Resumen_Incidencias", resultado.resumen_incidencias(), anchos=[14, 32, 90, 10])
    libro.tabla("Incidencias", resultado.incidencias, anchos=[20, 10, 9, 60, 8, 7, 20, 30, 30, 13, 80])
    libro.tabla("Archivos", resultado.archivos)
    _hojas_referencia(libro)
    libro.cerrar()
    return Path(ruta)

"""Homologación de RIPS (Res. 3374) hacia la estructura que consume la
herramienta de auditoría financiera.

El esquema destino NO está fijo en el código: se describe en un archivo YAML
(por defecto ``config/homologacion_auditoria.yaml``) con hojas, columnas y
tablas de equivalencia. Para cambiar de herramienta destino basta con editar
o reemplazar ese archivo.

Formato de una columna en el YAML::

    - destino: "codMunicipioResidencia (5 dig)"   # nombre de la columna destino
      origen: cod_municipio                        # campo técnico RIPS, o bien:
      plantilla: "{cod_departamento:0>2}{cod_municipio:0>3}"  # str.format sobre la fila
      constante: "05"                              # valor fijo
      transformacion: fecha_iso | fecha_hora_iso | numero | mayusculas | ninguna
      hora: hora_ingreso                           # (fecha_hora_iso) campo con la hora
      tabla: nombre_tabla                          # equivalencia de códigos
      condicional: {campo: valor_cuota_moderadora, mayor_que: 0, entonces: "02", si_no: "05"}
      por_defecto: ""                              # valor si el resultado queda vacío

Cada hoja indica ``origen`` (tipo de archivo RIPS), ``unicos_por`` opcional
(deduplicación) y ``incluir_trazabilidad`` (agrega hospital, período,
remisión, archivo y línea para rastrear el dato hasta el TXT).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from .especificacion import ESPECIFICACIONES
from .incidencias import COLUMNAS as COLS_INC
from .incidencias import REGLAS
from .validacion import a_fecha, a_fecha_hora, a_numero, normalizar_codigo

TRANSFORMACIONES = {"ninguna", "fecha_iso", "fecha_hora_iso", "numero", "mayusculas"}
TRAZA = ["hospital", "periodo", "remision", "archivo", "linea"]


class ErrorMapeo(ValueError):
    """El archivo de homologación tiene un error de definición."""


@dataclass
class ResultadoHomologacion:
    perfil: str
    hojas: dict[str, pd.DataFrame]
    incidencias: pd.DataFrame
    supuestos: list[str]


def cargar_mapeo(ruta: str | Path) -> dict:
    ruta = Path(ruta)
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el archivo de homologación: {ruta}")
    with ruta.open(encoding="utf-8") as fh:
        mapeo = yaml.safe_load(fh) or {}
    validar_mapeo(mapeo, str(ruta))
    return mapeo


def validar_mapeo(mapeo: dict, origen: str = "mapeo") -> None:
    """Valida la definición completa antes de usarla, para fallar temprano y
    con un mensaje que indique exactamente qué corregir."""
    if not isinstance(mapeo.get("hojas"), list) or not mapeo["hojas"]:
        raise ErrorMapeo(f"{origen}: se requiere una lista 'hojas'")
    tablas = mapeo.get("tablas") or {}
    for h in mapeo["hojas"]:
        nombre = h.get("nombre")
        tipo = str(h.get("origen", "")).upper()
        if not nombre or tipo not in ESPECIFICACIONES:
            raise ErrorMapeo(f"{origen}: hoja {nombre!r} con origen inválido {h.get('origen')!r}")
        campos = _campos_disponibles(tipo)
        for col in h.get("columnas") or []:
            dest = col.get("destino")
            if not dest:
                raise ErrorMapeo(f"{origen}: hoja {nombre}: columna sin 'destino'")
            fuentes = [k for k in ("origen", "plantilla", "constante", "condicional") if k in col]
            if len(fuentes) != 1:
                raise ErrorMapeo(f"{origen}: {nombre}.{dest}: defina exactamente uno de origen/plantilla/constante/condicional")
            if "origen" in col and col["origen"] not in campos:
                raise ErrorMapeo(f"{origen}: {nombre}.{dest}: el campo {col['origen']!r} no existe en {tipo}")
            for ref in (col.get("hora"), (col.get("condicional") or {}).get("campo")):
                if ref and ref not in campos:
                    raise ErrorMapeo(f"{origen}: {nombre}.{dest}: el campo {ref!r} no existe en {tipo}")
            if "plantilla" in col:
                try:
                    col["plantilla"].format_map(_FilaVacia(campos))
                except (KeyError, ValueError, IndexError) as exc:
                    raise ErrorMapeo(f"{origen}: {nombre}.{dest}: plantilla inválida ({exc})") from exc
            t = col.get("transformacion", "ninguna")
            if t not in TRANSFORMACIONES:
                raise ErrorMapeo(f"{origen}: {nombre}.{dest}: transformación {t!r} no soportada {sorted(TRANSFORMACIONES)}")
            if col.get("tabla") and col["tabla"] not in tablas:
                raise ErrorMapeo(f"{origen}: {nombre}.{dest}: la tabla {col['tabla']!r} no está definida")


def _campos_disponibles(tipo: str) -> set[str]:
    campos = set(ESPECIFICACIONES[tipo].columnas) | set(TRAZA)
    if tipo not in ("AF", "CT", "US"):
        campos |= {f"af_{c}" for c in ESPECIFICACIONES["AF"].columnas}
    return campos


def _con_datos_af(datos: dict[str, pd.DataFrame], tipo: str, df: pd.DataFrame) -> pd.DataFrame:
    """Agrega a los registros de servicio los campos de su factura (prefijo
    ``af_``), p. ej. ``af_fecha_inicio`` o ``af_cod_entidad``, para que el
    mapeo pueda usarlos (los servicios AM/AT no traen fecha en la 3374)."""
    af = datos.get("AF")
    if tipo in ("AF", "CT", "US") or af is None or df.empty or "envio_id" not in df:
        return df
    cols = ESPECIFICACIONES["AF"].columnas
    ref = af.drop_duplicates(["envio_id", "num_factura"])[["envio_id"] + cols].rename(
        columns={c: f"af_{c}" for c in cols})
    return df.merge(ref, how="left", left_on=["envio_id", "num_factura"],
                    right_on=["envio_id", "af_num_factura"])


class _FilaVacia(dict):
    def __init__(self, campos):
        super().__init__({c: "" for c in campos})

    def __missing__(self, key):
        raise KeyError(key)


def _columna(df: pd.DataFrame, col: dict, tablas: dict, incid: list[dict], hoja: str, tipo: str) -> pd.Series:
    if "origen" in col:
        s = df[col["origen"]].astype("string").fillna("")
    elif "constante" in col:
        s = pd.Series([str(col["constante"] if col["constante"] is not None else "")] * len(df), index=df.index,
                      dtype="string")
    elif "plantilla" in col:
        fmt = col["plantilla"]
        filas = df.astype(object).where(df.notna(), "").to_dict("records")
        s = pd.Series([fmt.format_map(f) for f in filas], index=df.index, dtype="string")
    else:
        c = col["condicional"]
        s = pd.Series(str(c["si_no"]), index=df.index, dtype="string")
        if c.get("no_vacio"):
            cumple = df[c["campo"]].astype("string").fillna("").str.strip() != ""
        else:
            cumple = a_numero(df[c["campo"]]).fillna(0) > float(c.get("mayor_que", 0))
        s[cumple] = str(c["entonces"])

    t = col.get("transformacion", "ninguna")
    if t == "fecha_iso":
        f = a_fecha(s)
        s = f.dt.strftime("%Y-%m-%d").astype("string").where(f.notna(), s)
    elif t == "fecha_hora_iso":
        hora = df[col["hora"]] if col.get("hora") else pd.Series("00:00", index=df.index)
        f = a_fecha_hora(s, hora)
        s = f.dt.strftime("%Y-%m-%d %H:%M").astype("string").where(f.notna(), s)
    elif t == "numero":
        n = a_numero(s)
        s = n.astype(object).where(n.notna(), s.astype(object))
    elif t == "mayusculas":
        s = s.str.upper()

    if col.get("tabla"):
        s = _aplicar_tabla(df, s, tablas[col["tabla"]], col, incid, hoja, tipo)
    if "por_defecto" in col:
        vacio = s.isna() | (s.astype("string") == "")
        s = s.astype(object).where(~vacio, str(col["por_defecto"]))
    return s


def _aplicar_tabla(df, s, tabla: dict, col, incid, hoja, tipo) -> pd.Series:
    valores = {str(k): str(v) if v is not None else "" for k, v in (tabla.get("valores") or {}).items()}
    norm_map = {(k.lstrip("0") or "0") if k.isdigit() else k.upper(): v for k, v in valores.items()}
    destino_valido = {str(v) for v in (tabla.get("destino_valido") or [])}
    s = s.astype("string").fillna("")
    norm = normalizar_codigo(s).fillna("")
    mapeado = norm.map(norm_map)
    out = mapeado.astype("string")
    # Valores que ya vienen en codificación destino (p. ej. RIPS que usan
    # códigos de la Res. 2275) pasan sin cambio.
    # Sólo pasa el código exacto: "9" no se completa a "09" porque en la tabla
    # origen sería un valor inválido y completarlo cambiaría su significado.
    ya_destino = mapeado.isna() & s.isin(destino_valido)
    out[ya_destino] = s[ya_destino]
    sin = mapeado.isna() & ~ya_destino & (s != "")
    if sin.any():
        out[sin] = s[sin]
        sub = df.loc[sin].reindex(columns=["hospital", "periodo", "envio_id", "archivo", "linea"])
        sub["tipo_archivo"] = tipo
        sub["campo"] = f"{hoja}.{col['destino']}"
        sub["valor"] = s[sin].to_numpy()
        sub["regla"] = "HOMOLOGACION_SIN_EQUIVALENCIA"
        sub["severidad"] = REGLAS["HOMOLOGACION_SIN_EQUIVALENCIA"][0]
        sub["mensaje"] = f"Sin equivalencia en la tabla '{col['tabla']}'"
        incid.append(sub.reindex(columns=COLS_INC))
    out[s == ""] = ""
    return out


def homologar(unidad, mapeo: dict) -> ResultadoHomologacion:
    """Transforma los datos de una unidad (o del resultado completo) al
    esquema destino descrito en ``mapeo``."""
    tablas = mapeo.get("tablas") or {}
    hojas: dict[str, pd.DataFrame] = {}
    incid: list[pd.DataFrame] = []
    traza_global = mapeo.get("incluir_trazabilidad", True)
    for h in mapeo["hojas"]:
        tipo = h["origen"].upper()
        df = unidad.datos.get(tipo)
        if df is None:
            df = pd.DataFrame(columns=ESPECIFICACIONES[tipo].columnas)
        df = _con_datos_af(unidad.datos, tipo, df).reset_index(drop=True)
        faltan = _campos_disponibles(tipo) - set(df.columns)
        for c in faltan:  # p. ej. trazabilidad ausente en datos construidos a mano
            df[c] = pd.Series([""] * len(df), dtype="string")
        cols = {}
        for col in h.get("columnas") or []:
            cols[col["destino"]] = _columna(df, col, tablas, incid, h["nombre"], tipo) if len(df) else pd.Series(dtype="string")
        out = pd.DataFrame(cols)
        if h.get("incluir_trazabilidad", traza_global):
            for c in TRAZA:
                if c in df:
                    out[f"_{c}"] = df[c].to_numpy()
        unicos = [c for c in h.get("unicos_por") or [] if c in out.columns]
        if unicos:
            out = out.drop_duplicates(subset=unicos).reset_index(drop=True)
        hojas[h["nombre"]] = out
    inc = pd.concat(incid, ignore_index=True) if incid else pd.DataFrame(columns=COLS_INC)
    return ResultadoHomologacion(
        perfil=str(mapeo.get("perfil", "")),
        hojas=hojas,
        incidencias=inc,
        supuestos=[str(x) for x in mapeo.get("supuestos") or []],
    )


def exportar_homologacion_excel(res: ResultadoHomologacion, conciliacion: pd.DataFrame, ruta: str | Path,
                                titulo: str = "") -> Path:
    from .excel import LibroRips  # import local: evita ciclo

    libro = LibroRips(ruta)
    ws = libro.wb.add_worksheet(libro.nombre_hoja("Supuestos"))
    ws.set_column(0, 0, 140)
    ws.write_string(0, 0, f"Homologación para auditoría financiera - {titulo}", libro.f_titulo)
    ws.write_string(1, 0, f"Perfil destino: {res.perfil}", libro.f_sub)
    ws.write_string(3, 0, "Supuestos y decisiones de homologación (editables en el archivo YAML de mapeo):", libro.f_sub)
    for i, s in enumerate(res.supuestos, start=4):
        ws.write_string(i, 0, f"• {s}", libro.f_wrap)
    for nombre, df in res.hojas.items():
        libro.tabla(nombre, df)
    libro.tabla("ConciliacionFacturas", conciliacion)
    libro.tabla("Incidencias_Homologacion", res.incidencias)
    libro.cerrar()
    return Path(ruta)


def exportar_homologacion_json(res: ResultadoHomologacion, conciliacion: pd.DataFrame, ruta: str | Path,
                               cabecera: dict | None = None) -> Path:
    import json

    from .json_export import _metadatos, registros_json

    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    doc = {"metadatos": _metadatos(perfil_homologacion=res.perfil), **(cabecera or {}),
           "supuestos": res.supuestos,
           "hojas": {n: registros_json(df) for n, df in res.hojas.items()},
           "conciliacion_facturas": registros_json(conciliacion),
           "incidencias_homologacion": registros_json(res.incidencias)}
    with ruta.open("w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1, default=str)
    return ruta

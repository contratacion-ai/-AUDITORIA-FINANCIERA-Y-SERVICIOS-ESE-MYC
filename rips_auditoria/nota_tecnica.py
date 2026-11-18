"""Nota técnica de capitación: número de pacientes por código CUPS y por mes.

Insumos
-------
* **Nota técnica** (p. ej. ``NUEVA_EPS.xlsx``): una hoja con ``CodigoCUP``,
  ``Homologo``, ``ESTADO CUPS`` y ``DescripcionCUP`` (357 códigos para Nueva
  EPS). Supuestos:

  - Códigos CUPS de 6 caracteres: se buscan tal cual en los RIPS (sin espacios,
    en mayúsculas; los numéricos que Excel dejó con menos de 6 dígitos se
    completan con ceros).
  - Códigos internos de medicamentos (``MD…``): se buscan por su
    **Homólogo**, que es el CUM (``19973908-1``), comparado sin ceros a la
    izquierda; también se acepta el propio código ``MD…`` si el RIPS lo trae.
  - ``ESTADO CUPS`` = INACTIVO: el código se cuenta igual y se señala.
  - Columnas con otros nombres se reconocen por contener «COD», «HOMOLOG»,
    «ESTADO» y «DESCRIP».

* **RIPS Res. 3374** (TXT, carpetas o ZIP) que lee ``procesamiento.procesar``:
  AC (``cod_consulta``, ``fecha_consulta``), AP (``cod_procedimiento``,
  ``fecha_procedimiento``), AT (``cod_servicio``) y AM (``cod_medicamento``).
  Paciente = ``tipo_id_usuario`` + ``num_id_usuario``. AT y AM no traen fecha
  de prestación: se usa la fecha inicial de la factura (AF) y se reporta.
  La EPS se toma del AF de la factura (``cod_entidad``/``nombre_entidad``).

* **RIPS JSON Res. 2275**: ``usuarios[].tipoDocumentoIdentificacion`` y
  ``numDocumentoIdentificacion``; códigos ``codConsulta``,
  ``codProcedimiento`` y ``codTecnologiaSalud``; fechas
  ``fechaInicioAtencion``, ``fechaDispensAdmon`` y
  ``fechaSuministroTecnologia``; edad por ``fechaNacimiento``. El JSON no
  trae la EPS: se toma del XML de la factura electrónica (FEV) que viene junto
  al JSON (adquiriente y NIT); si no hay XML pero la factura está en un AF
  cargado se usa esa EPS; si no, se asume la EPS de la nota
  (``json_sin_eps="asumir"``) y se reporta. El documento que trae cada
  servicio del JSON es el del profesional: el paciente es siempre el usuario.

Reglas de conteo
----------------
* Pacientes **únicos** por código y mes (tipo + número de identificación):
  varias atenciones del mismo paciente y código en un mes cuentan 1.
* Cada atención cuenta en el mes de su fecha de prestación; no se reparte.
* Códigos de la nota sin registros: 0, señalados «SIN REGISTROS».
* No se excluye nada en silencio. Se excluyen y se reportan por código:
  sin identificación del paciente, sin fecha válida, fuera del período y
  notas crédito (JSON ``tipoNota`` = NC, anulación). Se cuentan y se
  reportan: duplicados (mismo paciente, código, fecha y factura), registros
  con errores de validación de la Res. 3374 y fechas tomadas de la factura.
* Curso de vida (Res. 3280 de 2018) por la edad del paciente al atenderse.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import unicodedata
from pathlib import Path

import pandas as pd

from .catalogo_cups import normalizar_codigo
from .cruce_medicamentos import normalizar_cum
from .validacion import a_fecha

log = logging.getLogger(__name__)

DEFAULTS = {
    "eps_nombre": "NUEVA EPS",
    # Textos (en el nombre de la entidad del AF) y códigos que identifican a la EPS
    "eps_textos": ["NUEVA EPS", "NUEVA EMPRESA PROMOTORA"],
    # códigos de entidad del AF (EPS037 = Nueva EPS) y NIT de la FEV (900156264 = Nueva EPS S.A.)
    "eps_codigos": ["EPS037", "EPSS37", "EPS041", "EPSS41", "900156264"],
    "json_sin_eps": "asumir",  # asumir | excluir: JSON cuya factura no está en un AF cargado
}
CURSOS = [("Primera infancia (0 a 5 años)", 0, 6), ("Infancia (6 a 11 años)", 6, 12),
          ("Adolescencia (12 a 17 años)", 12, 18), ("Juventud (18 a 28 años)", 18, 29),
          ("Adultez (29 a 59 años)", 29, 60), ("Vejez (60 años y más)", 60, 200)]
SIN_CURSO = "Sin edad registrada"
MESES_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre",
            "Noviembre", "Diciembre"]
TXT = {  # tipo -> (campo código, campo fecha o None)
    "AC": ("cod_consulta", "fecha_consulta"), "AP": ("cod_procedimiento", "fecha_procedimiento"),
    "AT": ("cod_servicio", None), "AM": ("cod_medicamento", None),
}
JSON_SERV = {  # lista -> (campo código, campo fecha, tipo)
    "consultas": ("codConsulta", "fechaInicioAtencion", "AC"),
    "procedimientos": ("codProcedimiento", "fechaInicioAtencion", "AP"),
    "medicamentos": ("codTecnologiaSalud", "fechaDispensAdmon", "AM"),
    "otrosServicios": ("codTecnologiaSalud", "fechaSuministroTecnologia", "AT"),
}
MOTIVOS = {
    "sin_id": "Sin identificación del paciente",
    "sin_fecha": "Sin fecha de prestación válida",
    "fuera_periodo": "Fuera del período analizado",
    "anulado": "Nota crédito / anulación (JSON tipoNota NC)",
}


def _n(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", " ", t).strip()


def _cfg(cfg: dict | None) -> dict:
    return {**DEFAULTS, **{k: v for k, v in ((cfg or {}).get("nota_tecnica") or {}).items() if v is not None}}


def clave(codigo) -> str:
    """Llave de búsqueda: CUM normalizado si parece CUM (lleva guion), si no código CUPS normalizado."""
    c = normalizar_codigo(codigo)
    return normalizar_cum(c) if "-" in c else c


# ---------------------------------------------------------------------------
# Catálogo (nota técnica)
# ---------------------------------------------------------------------------

def cargar_catalogo(ruta: str | Path) -> pd.DataFrame:
    """Lee la nota técnica: codigo, homologo, estado, descripcion, tipo y las llaves de búsqueda."""
    ruta = Path(ruta)
    df = (pd.read_csv(ruta, dtype=object, sep=None, engine="python") if ruta.suffix.lower() == ".csv"
          else pd.read_excel(ruta, dtype=object))
    cols = {_n(c): c for c in df.columns}

    def col(*claves):
        return next((c for n, c in cols.items() if any(k in n for k in claves)), None)

    c_cod, c_hom, c_est, c_des = col("CODIGO", "COD"), col("HOMOLOG"), col("ESTADO"), col("DESCRIP")
    if c_cod is None:
        raise ValueError("La nota técnica debe tener una columna de código (CodigoCUP).")
    cat = pd.DataFrame({
        "codigo": df[c_cod].map(normalizar_codigo),
        "homologo": df[c_hom].map(normalizar_codigo) if c_hom else "",
        "estado": df[c_est].fillna("ACTIVO").astype(str).str.strip().str.upper() if c_est else "ACTIVO",
        "descripcion": df[c_des].fillna("").astype(str).str.strip() if c_des else "",
    })
    cat = cat[cat["codigo"] != ""]
    cat["homologo"] = cat["homologo"].where(cat["homologo"] != "", cat["codigo"])
    cat["tipo"] = cat["codigo"].str.startswith("MD").map({True: "Medicamento (CUM)", False: "CUPS"})
    cat["clave"] = cat["homologo"].map(clave)
    cat["clave_alterna"] = cat["codigo"].map(clave)
    repetidos = int(cat["codigo"].duplicated().sum())
    cat = cat.drop_duplicates("codigo").reset_index(drop=True)
    cat.attrs["repetidos"] = repetidos
    return cat


# ---------------------------------------------------------------------------
# Atenciones (registros paciente-código-fecha)
# ---------------------------------------------------------------------------

COLS_AT = ["fuente", "tipo_archivo", "sede", "archivo", "linea", "factura", "cod_entidad", "nombre_entidad",
           "tipo_id", "num_id", "codigo_rips", "fecha", "fecha_de_factura", "edad_anios", "con_error", "error",
           "nota"]


def atenciones_txt(resultado) -> pd.DataFrame:
    """Registros de AC, AP, AT y AM con paciente, código, fecha, EPS y errores de validación."""
    af = resultado.datos.get("AF")
    us = resultado.datos.get("US")
    inc = resultado.incidencias
    errores = pd.DataFrame(columns=["envio_id", "tipo_archivo", "linea", "error"])
    if inc is not None and not inc.empty:
        e = inc[(inc["severidad"] == "ERROR") & inc["linea"].notna()]
        if len(e):
            errores = (e.assign(linea=pd.to_numeric(e["linea"], errors="coerce"))
                        .groupby(["envio_id", "tipo_archivo", "linea"])["regla"]
                        .agg(lambda s: ", ".join(sorted(set(s)))).rename("error").reset_index())
    facturas = (af[["envio_id", "num_factura", "cod_entidad", "nombre_entidad", "fecha_inicio"]]
                .drop_duplicates(["envio_id", "num_factura"]) if af is not None and not af.empty else
                pd.DataFrame(columns=["envio_id", "num_factura", "cod_entidad", "nombre_entidad", "fecha_inicio"]))
    por_envio = facturas.groupby("envio_id").agg(cod_e=("cod_entidad", "first"), nom_e=("nombre_entidad", "first"),
                                                 fec_e=("fecha_inicio", "first"))
    edades = pd.DataFrame(columns=["envio_id", "tipo_id_usuario", "num_id_usuario", "edad_anios"])
    if us is not None and not us.empty:
        edad = pd.to_numeric(us["edad"], errors="coerce")
        div = us["unidad_edad"].astype(str).str.strip().map({"1": 1, "2": 12, "3": 365}).fillna(1)
        edades = us.assign(edad_anios=(edad / div)).drop_duplicates(["envio_id", "tipo_id_usuario", "num_id_usuario"])[
            ["envio_id", "tipo_id_usuario", "num_id_usuario", "edad_anios"]]
    partes = []
    for tipo, (c_cod, c_fec) in TXT.items():
        d = resultado.datos.get(tipo)
        if d is None or d.empty:
            continue
        x = d.merge(facturas, on=["envio_id", "num_factura"], how="left").join(por_envio, on="envio_id")
        x = x.merge(edades, on=["envio_id", "tipo_id_usuario", "num_id_usuario"], how="left")
        x = x.assign(tipo_archivo=tipo, linea=pd.to_numeric(x["linea"], errors="coerce"))
        x = x.merge(errores, on=["envio_id", "tipo_archivo", "linea"], how="left")
        fecha_fac = a_fecha(x["fecha_inicio"].fillna(x["fec_e"]))
        fecha = a_fecha(x[c_fec]) if c_fec else fecha_fac
        partes.append(pd.DataFrame({
            "fuente": "RIPS TXT", "tipo_archivo": tipo, "sede": x["hospital"], "archivo": x["archivo"],
            "linea": x["linea"], "factura": x["num_factura"].fillna("").astype(str).str.strip(),
            "cod_entidad": x["cod_entidad"].fillna(x["cod_e"]).fillna("").astype(str).str.strip(),
            "nombre_entidad": x["nombre_entidad"].fillna(x["nom_e"]).fillna("").astype(str).str.strip(),
            "tipo_id": x["tipo_id_usuario"].fillna("").astype(str).str.strip().str.upper(),
            "num_id": x["num_id_usuario"].fillna("").astype(str).str.strip().str.upper(),
            "codigo_rips": x[c_cod].fillna("").astype(str).str.strip(), "fecha": fecha,
            "fecha_de_factura": c_fec is None, "edad_anios": x["edad_anios"],
            "con_error": x["error"].notna(), "error": x["error"].fillna(""), "nota": "",
        }))
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLS_AT)


def atenciones_json(entradas, hospital: str | None = None) -> pd.DataFrame:
    """Registros de RIPS JSON (Res. 2275) con paciente, código y fecha de prestación."""
    from .fev_xml import indice_fev
    from .incidencias import RegistroIncidencias
    from .json_rips import documentos_2275
    from .lectura import contexto_desde_ruta, descubrir_archivos

    fev = indice_fev(entradas)
    filas = []
    for fuente in descubrir_archivos(list(entradas), RegistroIncidencias()):
        if not fuente.ruta.lower().endswith(".json"):
            continue
        try:
            doc = json.loads(fuente.contenido.decode("utf-8-sig", errors="replace"))
        except json.JSONDecodeError:
            filas.append({"fuente": "RIPS JSON", "archivo": fuente.ruta, "error": "JSON_INVALIDO", "con_error": True})
            continue
        sede = hospital or contexto_desde_ruta(fuente.ruta).get("hospital") or ""
        for factura in documentos_2275(doc):
            nota = str(factura.get("tipoNota") or "").strip().upper()
            for u in factura.get("usuarios") or []:
                nac = pd.to_datetime(u.get("fechaNacimiento"), errors="coerce")
                for lista, (c_cod, c_fec, tipo) in JSON_SERV.items():
                    for i, s in enumerate(((u.get("servicios") or {}).get(lista)) or []):
                        if not isinstance(s, dict):
                            continue
                        fecha = pd.to_datetime(str(s.get(c_fec) or "")[:10], errors="coerce", format="%Y-%m-%d")
                        edad = (fecha - nac).days / 365.25 if pd.notna(fecha) and pd.notna(nac) else None
                        filas.append({
                            "fuente": "RIPS JSON", "tipo_archivo": tipo, "sede": sede, "archivo": fuente.ruta,
                            "linea": i + 1, "factura": str(factura.get("numFactura") or "").strip(),
                            # EPS y NIT de la FEV (XML) de la factura, si viene junto al JSON
                            "cod_entidad": (fev.get(str(factura.get("numFactura") or "").strip().upper()) or {}).get("eps_nit", ""),
                            "nombre_entidad": (fev.get(str(factura.get("numFactura") or "").strip().upper()) or {}).get("eps_nombre", ""),
                            # paciente = el usuario; el documento dentro de cada servicio es el del profesional
                            "tipo_id": str(u.get("tipoDocumentoIdentificacion") or "").strip().upper(),
                            "num_id": str(u.get("numDocumentoIdentificacion") or "").strip().upper(),
                            "codigo_rips": str(s.get(c_cod) or "").strip(), "fecha": fecha, "fecha_de_factura": False,
                            "edad_anios": edad, "con_error": False, "error": "", "nota": nota,
                        })
    return pd.DataFrame(filas, columns=COLS_AT) if filas else pd.DataFrame(columns=COLS_AT)


# ---------------------------------------------------------------------------
# Cálculo
# ---------------------------------------------------------------------------

def _curso(edad) -> str:
    if edad is None or pd.isna(edad):
        return SIN_CURSO
    return next((n for n, a, b in CURSOS if a <= edad < b), SIN_CURSO)


def _meses(desde: str, hasta: str) -> list[str]:
    return [str(p) for p in pd.period_range(pd.Period(desde, "M"), pd.Period(hasta, "M"), freq="M")]


def nombre_mes(mes: str) -> str:
    a, m = mes.split("-")
    return f"{MESES_ES[int(m) - 1]} {a}"


def calcular(at: pd.DataFrame, catalogo: pd.DataFrame, desde: str | None = None, hasta: str | None = None,
             cfg: dict | None = None) -> dict:
    """Conteo de pacientes únicos por código y mes, con la calidad de datos por código."""
    c = _cfg(cfg)
    at = at.copy()
    for col in COLS_AT:
        if col not in at:
            at[col] = None
    at["fecha"] = pd.to_datetime(at["fecha"], errors="coerce")
    total_leidos = len(at)
    # 1) Códigos de la nota técnica (por homólogo o por el código propio)
    llave = at["codigo_rips"].map(clave)
    mapa = {**dict(zip(catalogo["clave_alterna"], catalogo["codigo"], strict=False)),
            **dict(zip(catalogo["clave"], catalogo["codigo"], strict=False))}
    at["codigo"] = llave.map(mapa)
    fuera_nota = at[at["codigo"].isna() & (at["codigo_rips"].fillna("") != "")]
    at = at[at["codigo"].notna()].copy()

    # 2) EPS: AF de la factura; JSON por la factura en el AF o según json_sin_eps
    txt_fac = at.loc[at["fuente"] == "RIPS TXT", ["factura", "cod_entidad", "nombre_entidad"]].drop_duplicates("factura")
    es_json = at["fuente"] == "RIPS JSON"
    if es_json.any() and len(txt_fac):
        m = at.loc[es_json, "factura"].map(txt_fac.set_index("factura")["nombre_entidad"])
        at.loc[es_json, "nombre_entidad"] = m.fillna("")
        at.loc[es_json, "cod_entidad"] = at.loc[es_json, "factura"].map(txt_fac.set_index("factura")["cod_entidad"]).fillna("")
    textos = [_n(x) for x in c["eps_textos"]]
    codigos = {str(x).strip().upper() for x in c["eps_codigos"]}
    nom = at["nombre_entidad"].fillna("").map(_n)
    cod = at["cod_entidad"].fillna("").astype(str).str.strip().str.upper()
    nit = cod.str.replace(r"[^0-9]", "", regex=True).str[:9]  # NIT sin puntos ni dígito de verificación
    de_eps = nom.map(lambda x: any(t in x for t in textos if t)) | cod.isin(codigos) | (nit.isin(codigos) & (nit != ""))
    json_asumido = es_json & (at["nombre_entidad"].fillna("") == "")
    if c["json_sin_eps"] == "asumir":
        de_eps |= json_asumido
    fechas_archivos = at["fecha"].dt.strftime("%Y-%m").dropna()  # período por defecto si ninguna es de la EPS
    otra_eps = at[~de_eps]
    at = at[de_eps].copy()
    at["json_eps_asumida"] = json_asumido[de_eps]

    # 3) Período
    at["mes"] = at["fecha"].dt.strftime("%Y-%m")
    validas = at["mes"].dropna()
    if validas.empty:
        validas = fechas_archivos
    desde = desde or (validas.min() if len(validas) else dt.date.today().strftime("%Y-01"))
    hasta = hasta or (validas.max() if len(validas) else dt.date.today().strftime("%Y-%m"))
    meses = _meses(desde, hasta)

    # 4) Exclusiones (se reportan por código)
    at["motivo"] = ""
    at.loc[(at["tipo_id"].fillna("") == "") | (at["num_id"].fillna("") == ""), "motivo"] = "sin_id"
    at.loc[(at["motivo"] == "") & at["mes"].isna(), "motivo"] = "sin_fecha"
    at.loc[(at["motivo"] == "") & ~at["mes"].isin(meses), "motivo"] = "fuera_periodo"
    at.loc[(at["motivo"] == "") & (at["nota"].fillna("") == "NC"), "motivo"] = "anulado"
    at["paciente"] = at["tipo_id"].fillna("") + "-" + at["num_id"].fillna("")
    incl = at[at["motivo"] == ""].copy()
    incl["duplicado"] = incl.duplicated(["paciente", "codigo", "fecha", "factura"], keep="first")
    incl["curso_vida"] = incl["edad_anios"].map(_curso)

    # 5) Pacientes únicos por código y mes
    unicos = (incl.sort_values(["fecha"]).groupby(["codigo", "mes", "paciente"], as_index=False)
              .agg(tipo_id=("tipo_id", "first"), num_id=("num_id", "first"), sede=("sede", "first"),
                   curso_vida=("curso_vida", "first"), atenciones=("fecha", "size"),
                   fuentes=("fuente", lambda s: " + ".join(sorted(set(s))))))
    matriz = (unicos.groupby(["codigo", "mes"])["paciente"].nunique().unstack("mes")
              .reindex(index=catalogo["codigo"], columns=meses).fillna(0).astype(int))
    matriz.columns.name = None
    pacientes_periodo = incl.groupby("codigo")["paciente"].nunique()

    # 6) Calidad de datos por código
    cal = pd.DataFrame(index=catalogo["codigo"])
    cal["registros_eps"] = at.groupby("codigo").size()
    cal["registros_incluidos"] = incl.groupby("codigo").size()
    cal["pacientes_periodo"] = pacientes_periodo
    for k in MOTIVOS:
        cal[f"excl_{k}"] = at[at["motivo"] == k].groupby("codigo").size()
    cal["duplicados"] = incl[incl["duplicado"]].groupby("codigo").size()
    cal["con_errores_validacion"] = incl[incl["con_error"].fillna(False).astype(bool)].groupby("codigo").size()
    cal["fecha_de_factura"] = incl[incl["fecha_de_factura"].fillna(False).astype(bool)].groupby("codigo").size()
    cal["json_eps_asumida"] = incl[incl["json_eps_asumida"].fillna(False).astype(bool)].groupby("codigo").size()
    cal["sin_edad"] = incl[incl["curso_vida"] == SIN_CURSO].groupby("codigo").size()
    cal["registros_otra_eps"] = otra_eps.groupby("codigo").size()
    cal = cal.fillna(0).astype(int)
    cal["meses_sin_informacion"] = (matriz == 0).sum(axis=1)
    reglas = incl[incl["con_error"].fillna(False).astype(bool)].groupby("codigo")["error"].agg(
        lambda s: ", ".join(sorted({r for x in s for r in str(x).split(", ") if r})))
    estado = catalogo.set_index("codigo")["estado"]
    obs = []
    for cod, r in cal.iterrows():
        partes = []
        if r["registros_eps"] == 0:
            partes.append("SIN REGISTROS en los RIPS/JSON del período: se reporta 0")
        if estado.get(cod, "ACTIVO") != "ACTIVO":
            partes.append(f"código {estado.get(cod)} en la nota técnica")
        for k, texto in MOTIVOS.items():
            if r[f"excl_{k}"]:
                partes.append(f"{r[f'excl_{k}']} registro(s) excluido(s): {texto.lower()}")
        if r["duplicados"]:
            partes.append(f"{r['duplicados']} registro(s) duplicado(s) contados una vez")
        if r["con_errores_validacion"]:
            partes.append(f"{r['con_errores_validacion']} registro(s) con errores de diligenciamiento "
                          f"({reglas.get(cod, '')}); se cuentan")
        if r["fecha_de_factura"]:
            partes.append(f"{r['fecha_de_factura']} registro(s) sin fecha de prestación (AT/AM): mes por la factura")
        if r["json_eps_asumida"]:
            partes.append(f"{r['json_eps_asumida']} registro(s) JSON sin EPS: se asumió {c['eps_nombre']}")
        if 0 < r["meses_sin_informacion"] < len(meses) and r["registros_eps"]:
            partes.append(f"{r['meses_sin_informacion']} mes(es) sin pacientes")
        obs.append("; ".join(partes))
    cal["observaciones"] = obs

    curso = (unicos.groupby(["codigo", "curso_vida"])["paciente"].nunique().unstack("curso_vida")
             .reindex(index=catalogo["codigo"], columns=[n for n, _a, _b in CURSOS] + [SIN_CURSO]).fillna(0).astype(int))
    meses_vacios = [m for m in meses if matriz[m].sum() == 0]
    fuera = (fuera_nota.groupby("codigo_rips").agg(registros=("codigo_rips", "size"),
                                                   pacientes=("num_id", "nunique")).sort_values("registros", ascending=False)
             if len(fuera_nota) else pd.DataFrame(columns=["registros", "pacientes"]))
    resumen = {
        "eps": c["eps_nombre"], "desde": desde, "hasta": hasta, "meses": meses, "registros_leidos": total_leidos,
        "registros_nota": int(len(at) + len(otra_eps)), "registros_eps": int(len(at)),
        "registros_otra_eps": int(len(otra_eps)), "registros_incluidos": int(len(incl)),
        "registros_fuera_nota": int(len(fuera_nota)), "codigos_nota": int(len(catalogo)),
        "codigos_con_pacientes": int((matriz.sum(axis=1) > 0).sum()),
        "codigos_sin_registros": int((cal["registros_eps"] == 0).sum()),
        "pacientes_unicos_periodo": int(incl["paciente"].nunique()), "meses_sin_informacion": meses_vacios,
        **{f"excl_{k}": int((at["motivo"] == k).sum()) for k in MOTIVOS},
        "duplicados": int(incl["duplicado"].sum()), "con_errores": int(incl["con_error"].fillna(False).astype(bool).sum()),
        "fecha_de_factura": int(incl["fecha_de_factura"].fillna(False).astype(bool).sum()),
        "json_eps_asumida": int(incl["json_eps_asumida"].fillna(False).astype(bool).sum()),
        "fuentes": sorted(set(at["fuente"].dropna())) or ["(ninguna)"],
        "sedes": sorted({s for s in incl["sede"].dropna() if s}),
        "archivos": int(pd.concat([at["archivo"], otra_eps["archivo"]]).nunique()),
        # entidades de los AF con atenciones de códigos de la nota (para revisar el filtro de EPS)
        "entidades_encontradas": "; ".join(
            f"{k or '(sin entidad)'} ({v})" for k, v in pd.concat([at, otra_eps])
            .assign(_e=lambda x: x["cod_entidad"].fillna("").astype(str) + " " + x["nombre_entidad"].fillna("").astype(str))
            ["_e"].str.strip().value_counts().head(8).items()),
    }
    return {"resumen": resumen, "catalogo": catalogo, "matriz": matriz, "calidad": cal, "pacientes": unicos,
            "curso_vida": curso, "fuera_nota": fuera}


def procesar_nota(entradas, catalogo_ruta, desde=None, hasta=None, hospital=None, cfg=None) -> dict:
    """RIPS TXT (con validación Res. 3374) + JSON de ``entradas`` -> resultado de ``calcular``."""
    from .config import cargar_configuracion
    from .procesamiento import procesar

    cfg = cfg or cargar_configuracion()
    catalogo = cargar_catalogo(catalogo_ruta)
    res = procesar(entradas, cfg, hospital=hospital)
    partes = [p for p in (atenciones_txt(res), atenciones_json(entradas, hospital)) if len(p)]
    at = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLS_AT)
    return calcular(at, catalogo, desde, hasta, cfg)


# ---------------------------------------------------------------------------
# Nota técnica guardada (para la ventana de cargue)
# ---------------------------------------------------------------------------

def carpeta_nota() -> Path:
    from .catalogo_cups import carpeta_catalogos

    return carpeta_catalogos() / "nota_tecnica"


def guardar_nota(ruta: str | Path) -> dict:
    """Valida y guarda el archivo de la nota técnica (reemplaza el anterior)."""
    import shutil

    cat = cargar_catalogo(ruta)  # falla si no tiene la estructura esperada
    carpeta = carpeta_nota()
    if carpeta.exists():
        shutil.rmtree(carpeta)
    carpeta.mkdir(parents=True)
    destino = carpeta / Path(ruta).name
    shutil.copyfile(ruta, destino)
    return {"archivo": destino.name, "codigos": len(cat), "inactivos": int((cat["estado"] != "ACTIVO").sum()),
            "medicamentos": int((cat["tipo"] != "CUPS").sum())}


def nota_vigente() -> Path | None:
    carpeta = carpeta_nota()
    archivos = sorted(carpeta.iterdir()) if carpeta.exists() else []
    return archivos[0] if archivos else None


__all__ = ["atenciones_json", "atenciones_txt", "calcular", "cargar_catalogo", "clave", "guardar_nota", "nombre_mes",
           "nota_vigente", "procesar_nota"]

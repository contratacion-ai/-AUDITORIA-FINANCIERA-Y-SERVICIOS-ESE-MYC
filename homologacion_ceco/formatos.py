"""Adaptadores de reportes de software contable al esquema común.

**CTRP — Estado de resultados por tercero y centro de costo** (`CTRPEstadoResultadosTerCen`), exportado por el
software contable de la ESE:

* Filas de **cuenta** (Nivel = Clase, Grupo, Cuenta, Subcuenta, Auxiliar, Subauxiliar) con `OidCuentaContable`,
  `CodigoCuentaContable`, nombre, naturaleza y totales. `NivelAuxiliar = True` marca la cuenta de detalle.
* Filas de **detalle** (una por tercero y centro de costo) **sin código de cuenta**: la cuenta se obtiene con
  `OidCuentaProceso` → `OidCuentaContable` de la cuenta de detalle.
* Pie del reporte con NIT, usuario y la descripción del filtro («ESTADO DE RESULTADOS DEL MES JULIO DE 2026 …»),
  de donde se toma el periodo.

El adaptador devuelve una tabla en el esquema común **conservando el número de fila de Excel** de cada registro
(trazabilidad hasta el reporte original). El tercero se identifica solo por su `OidTercero` (identificador
interno del software) para distinguir filas legítimamente iguales (p. ej. nómina por empleado) sin copiar
nombres ni documentos de personas. Si una cuenta de detalle trae un total distinto a la suma de su detalle por
CECO (o no trae detalle), la diferencia se emite como registro **sin CECO**, que queda rechazado y visible, para
que lo leído cuadre con el total del reporte.

También se acepta el reporte **editado a mano** (caso frecuente: se borran `OidCuentaContable`, `OidTercero` y el pie,
y los valores se renombran `DEBITO`/`CREDITO`): sin `OidCuentaContable` cada fila de detalle se asigna a la cuenta de
detalle (`NivelAuxiliar = True`) que la precede, que es el orden en que el software exporta el reporte; sin
`OidTercero` el tercero se distingue con una huella irreversible del documento (no se copia el documento).
"""
import hashlib
import re

from .normalizar import centavos, clave, cuenta, es_vacio, periodo_en_nombre, texto

COLUMNAS_CTRP = {"CODIGOCUENTACONTABLE", "OIDCUENTAPROCESO", "CODIGOCENTROCOSTO", "NIVEL", "NIVELAUXILIAR"}
PARES_VALOR = (("VALORSALDODEBITO", "VALORSALDOCREDITO"), ("DEBITO", "CREDITO"), ("VALORMOVIMIENTODEBITO", "VALORMOVIMIENTOCREDITO"))
NIVELES = ("CLASE", "GRUPO", "CUENTA", "SUBCUENTA", "AUXILIAR", "SUBAUXILIAR")
ENCABEZADO = ["CODIGO CECO", "NOMBRE CECO", "CUENTA", "NOMBRE CUENTA", "DEBITO", "CREDITO", "PERIODO", "COMPROBANTE",
              "DESCRIPCION"]
FORMATO_CTRP = "CTRP ESTADO DE RESULTADOS POR TERCERO Y CENTRO"


def _clave_col(v):
    return clave(v).replace(" ", "")


def columnas_son_ctrp(columnas):
    """`columnas`: encabezados ya pasados por `clave` y sin espacios."""
    return COLUMNAS_CTRP <= columnas and any(d in columnas and c in columnas for d, c in PARES_VALOR)


def es_ctrp(df):
    if df is None or df.empty:
        return False
    return columnas_son_ctrp({_clave_col(v) for v in df.iloc[0].tolist()})


def _mes(v):
    from .normalizar import MESES

    return MESES.get(clave(v)) if not es_vacio(v) else None


def _tercero(oid, documento):
    if oid is not None:
        return f"OidTercero {oid}"
    if es_vacio(documento):
        return ""
    return "Tercero " + hashlib.sha256(texto(documento).encode()).hexdigest()[:10]


def _entero(v):
    if es_vacio(v):
        return None
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, int):
        return v
    s = texto(v)
    return int(s) if re.fullmatch(r"\d+", s) else None


def adaptar(tabla, usar_movimiento=False):
    """Si la tabla es un reporte CTRP, la convierte al esquema común; si no, la devuelve igual."""
    if not es_ctrp(tabla.datos):
        return tabla
    df = tabla.datos
    cols = {_clave_col(v): j for j, v in enumerate(df.iloc[0].tolist())}
    original = {k: texto(df.iloc[0, j]) for k, j in cols.items()}

    def col(fila, nombre):
        j = cols.get(nombre)
        return fila[j] if j is not None and j < len(fila) else None

    pares = [p for p in PARES_VALOR if p[0] in cols and p[1] in cols]
    deb, cre = next((p for p in pares if p[0].startswith("VALORMOVIMIENTO")), pares[0]) if usar_movimiento else pares[0]
    con_oid = "OIDCUENTACONTABLE" in cols     # sin él (reporte editado) la cuenta se toma por orden de filas
    matriz = df.to_numpy(dtype=object)

    cuentas = {}      # oid (o fila, sin OidCuentaContable) → dict(codigo, nombre, nivel, hoja, fila, debito, credito)
    detalle_por_oid = {}
    filas_detalle = []
    periodo = ""
    meses = set()
    ultima_hoja = None
    for i in range(1, len(matriz)):
        f = list(matriz[i])
        nivel = clave(col(f, "NIVEL"))
        oid = _entero(col(f, "OIDCUENTACONTABLE")) if con_oid else ("fila", i)
        if nivel in NIVELES and oid is not None:
            try:
                codigo = cuenta(col(f, "CODIGOCUENTACONTABLE"))
            except ValueError:
                codigo = ""
            hoja = clave(col(f, "NIVELAUXILIAR")) == "TRUE"
            cuentas[oid] = {
                "codigo": codigo, "nombre": texto(col(f, "NOMBRECUENTACONTABLE")), "nivel": nivel, "fila": i,
                "hoja": hoja, "debito": col(f, deb), "credito": col(f, cre),
            }
            ultima_hoja = oid if hoja else None
            continue
        proceso = _entero(col(f, "OIDCUENTAPROCESO"))
        tercero = _entero(col(f, "OIDTERCERO"))
        if proceso and (not es_vacio(col(f, "CODIGOCENTROCOSTO")) or tercero or not es_vacio(col(f, "DOCUMENTOTERCERO"))):
            filas_detalle.append((i, proceso if con_oid else ultima_hoja, f))
            if _mes(col(f, "MES")):
                meses.add(_mes(col(f, "MES")))
            continue
        # Pie del reporte: buscar la descripción del filtro para el periodo.
        for v in f:
            if isinstance(v, str) and "ESTADO DE RESULTADOS" in clave(v):
                periodo = periodo_en_nombre(v) or periodo

    nuevo = [[None] * len(ENCABEZADO) for _ in range(len(matriz))]
    nuevo[0] = list(ENCABEZADO)
    sin_cuenta = 0
    for i, proceso, f in filas_detalle:
        c = cuentas.get(proceso)
        if c is None:
            sin_cuenta += 1
        nuevo[i] = [
            col(f, "CODIGOCENTROCOSTO"), texto(col(f, "NOMBRECENTROCOSTO")), c["codigo"] if c else "",
            c["nombre"] if c else texto(col(f, "NOMBRECUENTACONTABLE")), col(f, deb), col(f, cre), periodo,
            _tercero(_entero(col(f, "OIDTERCERO")), col(f, "DOCUMENTOTERCERO")), "",
        ]
        detalle_por_oid.setdefault(proceso, [0, 0])
        detalle_por_oid[proceso][0] += centavos(col(f, deb)) or 0
        detalle_por_oid[proceso][1] += centavos(col(f, cre)) or 0

    # Cuentas de detalle cuyo total no se explica por su detalle por CECO.
    diferencias = []
    for oid, c in cuentas.items():
        if not c["hoja"]:
            continue
        d_tot, c_tot = centavos(c["debito"]) or 0, centavos(c["credito"]) or 0
        d_det, c_det = detalle_por_oid.get(oid, (0, 0))
        if (d_tot, c_tot) != (d_det, c_det):
            dd, dc = d_tot - d_det, c_tot - c_det
            nuevo[c["fila"]] = ["", "", c["codigo"], c["nombre"], dd / 100, dc / 100, periodo, "",
                                "Total de la cuenta sin detalle por centro de costo en el reporte"]
            diferencias.append((c["codigo"], c["nombre"], c["fila"] + 1, dd - dc))

    totales = {}
    for c in cuentas.values():
        if c["nivel"] == "CLASE" and c["codigo"]:
            totales[c["codigo"]] = (centavos(c["debito"]) or 0) - (centavos(c["credito"]) or 0)
    nombres = {c["codigo"]: c["nombre"] for c in cuentas.values() if c["codigo"] and c["nombre"]}

    import pandas as pd

    tabla.datos = pd.DataFrame(nuevo, dtype=object)
    tabla.meta = {
        "formato": FORMATO_CTRP,
        "nombres_cuenta": nombres,
        "totales_clase": totales,       # centavos débito − crédito por clase, según el reporte
        "diferencias": diferencias,
        "detalle_sin_cuenta": sin_cuenta,
        "periodo": periodo,
        "mes": next(iter(meses)) if len(meses) == 1 else None,    # columna Mes, si el pie del reporte no está
        "editado": not con_oid,
        "columnas_valor": (deb, cre),
        "mapeo": [
            ("cod_ceco", "CodigoCentroCosto"), ("nombre_ceco", "NombreCentroCosto"),
            ("cuenta", "OidCuentaProceso → OidCuentaContable → CodigoCuentaContable" if con_oid
             else "Cuenta de detalle (NivelAuxiliar = True) que precede a la fila → CodigoCuentaContable"),
            ("nombre_cuenta", "NombreCuentaContable de la cuenta de detalle"),
            ("debito", original[deb]), ("credito", original[cre]), ("periodo", f"Pie del reporte (DescripcionFiltros): {periodo or 'no encontrado'}"),
            ("documento", "OidTercero (identificador interno; no se copian nombres ni documentos)" if "OIDTERCERO" in cols
             else "Huella irreversible de DocumentoTercero (no se copian nombres ni documentos)"),
        ],
    }
    return tabla


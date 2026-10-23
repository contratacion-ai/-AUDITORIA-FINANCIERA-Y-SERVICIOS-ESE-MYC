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
"""
import re

import pandas as pd

from .normalizar import centavos, clave, cuenta, es_vacio, periodo_en_nombre, texto

COLUMNAS_CTRP = {"OIDCUENTACONTABLE", "CODIGOCUENTACONTABLE", "OIDCUENTAPROCESO", "CODIGOCENTROCOSTO",
                 "VALORSALDODEBITO", "VALORSALDOCREDITO"}
NIVELES = ("CLASE", "GRUPO", "CUENTA", "SUBCUENTA", "AUXILIAR", "SUBAUXILIAR")
ENCABEZADO = ["CODIGO CECO", "NOMBRE CECO", "CUENTA", "NOMBRE CUENTA", "DEBITO", "CREDITO", "PERIODO", "COMPROBANTE",
              "DESCRIPCION"]
FORMATO_CTRP = "CTRP ESTADO DE RESULTADOS POR TERCERO Y CENTRO"


def _clave_col(v):
    return clave(v).replace(" ", "")


def es_ctrp(df):
    if df is None or df.empty:
        return False
    return COLUMNAS_CTRP <= {_clave_col(v) for v in df.iloc[0].tolist()}


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

    deb, cre = (("VALORMOVIMIENTODEBITO", "VALORMOVIMIENTOCREDITO") if usar_movimiento and "VALORMOVIMIENTODEBITO" in cols
                else ("VALORSALDODEBITO", "VALORSALDOCREDITO"))
    matriz = df.to_numpy(dtype=object)

    cuentas = {}      # oid → dict(codigo, nombre, nivel, hoja, fila, debito, credito, clase)
    detalle_por_oid = {}
    filas_detalle = []
    periodo = ""
    for i in range(1, len(matriz)):
        f = list(matriz[i])
        nivel = clave(col(f, "NIVEL"))
        oid = _entero(col(f, "OIDCUENTACONTABLE"))
        if nivel in NIVELES and oid is not None:
            try:
                codigo = cuenta(col(f, "CODIGOCUENTACONTABLE"))
            except ValueError:
                codigo = ""
            cuentas[oid] = {
                "codigo": codigo, "nombre": texto(col(f, "NOMBRECUENTACONTABLE")), "nivel": nivel, "fila": i,
                "hoja": clave(col(f, "NIVELAUXILIAR")) == "TRUE", "debito": col(f, deb), "credito": col(f, cre),
            }
            continue
        proceso = _entero(col(f, "OIDCUENTAPROCESO"))
        if proceso and not es_vacio(col(f, "CODIGOCENTROCOSTO")) or (proceso and _entero(col(f, "OIDTERCERO"))):
            filas_detalle.append((i, proceso, f))
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
        oid_tercero = _entero(col(f, "OIDTERCERO"))
        nuevo[i] = [
            col(f, "CODIGOCENTROCOSTO"), texto(col(f, "NOMBRECENTROCOSTO")), c["codigo"] if c else "",
            c["nombre"] if c else texto(col(f, "NOMBRECUENTACONTABLE")), col(f, deb), col(f, cre), periodo,
            f"OidTercero {oid_tercero}" if oid_tercero is not None else "", "",
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

    tabla.datos = pd.DataFrame(nuevo, dtype=object)
    tabla.meta = {
        "formato": FORMATO_CTRP,
        "nombres_cuenta": nombres,
        "totales_clase": totales,       # centavos débito − crédito por clase, según el reporte
        "diferencias": diferencias,
        "detalle_sin_cuenta": sin_cuenta,
        "periodo": periodo,
        "columnas_valor": (deb, cre),
        "mapeo": [
            ("cod_ceco", "CodigoCentroCosto"), ("nombre_ceco", "NombreCentroCosto"),
            ("cuenta", "OidCuentaProceso → OidCuentaContable → CodigoCuentaContable"),
            ("nombre_cuenta", "NombreCuentaContable de la cuenta de detalle"),
            ("debito", original[deb]), ("credito", original[cre]), ("periodo", f"Pie del reporte (DescripcionFiltros): {periodo or 'no encontrado'}"),
            ("documento", "OidTercero (identificador interno; no se copian nombres ni documentos)"),
        ],
    }
    return tabla


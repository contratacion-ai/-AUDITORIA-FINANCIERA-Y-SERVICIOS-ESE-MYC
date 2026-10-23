"""Proceso completo: lectura → mapeo de columnas → limpieza → reglas → homologación → consolidación.

Cada fila leída termina en exactamente un destino y queda registrada con su motivo:

* RECHAZADO (ERROR): dato inválido (sin CECO, sin cuenta, monto o periodo ilegible…);
* EXCLUIDO (INFO/AVISO): regla de limpieza (cuenta fuera de las clases de costo, subtotal de nivel superior,
  valor cero, duplicado exacto);
* RETENIDO: CECO en VERIFICAR, NO APLICA, EXCLUIR o sin homologación (no se carga en silencio);
* CARGADO: CECO ACTIVO; se consolida por periodo + cuenta + llave CECO|SEDE.

La conciliación verifica que la suma de los cuatro destinos sea igual a lo leído, y que el consolidado sea igual
a lo cargado. La bitácora de transformaciones permite rastrear cualquier cifra hasta archivo, hoja y fila.
"""
import re
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import pandas as pd

from . import config as C
from .columnas import Detector, ErrorMapeo
from .formatos import adaptar
from .lectores import ERROR as EV_ERROR
from .lectores import OMITIDO, PROCESADO, EventoArchivo, Lector
from .normalizar import (ErrorValor, centavos, clave, codigo_ceco, cuenta, es_vacio, formato_pesos, periodo,
                         periodo_en_nombre, texto, valor_para_traza)
from .reglas import Homologador

CARGADO, RETENIDO, EXCLUIDO, RECHAZADO = "CARGADO", "RETENIDO", "EXCLUIDO", "RECHAZADO"
CAMPOS_TEXTO = ("nombre_ceco", "nombre_cuenta", "descripcion", "documento", "sede")


class ErrorConciliacion(AssertionError):
    """La suma de destinos no cuadra con lo leído (no debería ocurrir; indica un defecto)."""


@dataclass
class Opciones:
    periodo: str = ""                    # periodo por defecto AAAA-MM si el archivo no lo trae
    clases: tuple = None                 # clases de cuenta a leer; None = datos/sedes_y_puc.json (4, 5, 6, 7)
    activar_nuevos: bool = False         # cargar CECO nuevos homologados por regla (salvo VERIFICAR/EXCLUIR)
    eliminar_duplicados: bool = True
    excluir_subtotales: bool = True
    excluir_ceros: bool = True
    usar_movimiento: bool = False        # si hay DÉBITO y CRÉDITO, usar débito − crédito aunque exista VALOR/SALDO
    mapeo: dict = None                   # mapeo del usuario (ver columnas.Detector)
    limites: dict = None                 # límites de lectura (ver lectores.LIMITES)

    def __post_init__(self):
        if self.periodo:
            self.periodo = periodo(self.periodo)
        if self.clases is None:
            self.clases = tuple(C.sedes_y_puc()["clases_lectura"])
        self.clases = tuple(str(c) for c in self.clases)


@dataclass
class Resultado:
    registros: pd.DataFrame
    consolidado: pd.DataFrame
    archivos: pd.DataFrame
    mapeo: pd.DataFrame
    trazabilidad: pd.DataFrame
    alertas: pd.DataFrame
    nuevos: pd.DataFrame
    conciliacion: pd.DataFrame
    control_referencia: pd.DataFrame
    hallazgos_referencia: pd.DataFrame
    control_origen: pd.DataFrame
    nombres_cuenta: dict
    referencia: object
    opciones: Opciones
    resumen: dict = field(default_factory=dict)

    def de_destino(self, destino):
        return self.registros[self.registros["destino"] == destino]

    @property
    def cargados(self):
        return self.de_destino(CARGADO)

    @property
    def codigo_salida(self):
        """0 = todo cargado; 2 = terminó con rechazos, retenidos o archivos con error (revisar)."""
        hay = (self.registros["destino"].isin([RECHAZADO, RETENIDO]).any()
               or (self.archivos["estado"] == EV_ERROR).any())
        return 2 if hay else 0


class _Bitacora:
    COLUMNAS = ["id_registro", "etapa", "origen", "hoja", "fila", "campo", "valor_origen", "valor_resultado", "detalle"]

    def __init__(self):
        self.filas = []

    def add(self, etapa, detalle, id_registro="", origen="", hoja="", fila=None, campo="", antes="", despues=""):
        self.filas.append((id_registro, etapa, origen, hoja, fila, campo, antes, despues, detalle))

    def df(self):
        return pd.DataFrame(self.filas, columns=self.COLUMNAS)


def procesar(rutas, referencia, opciones=None, contenidos=None):
    """Procesa archivos/carpetas/ZIP (`rutas`) y/o pares (nombre, bytes) en `contenidos`."""
    op = opciones or Opciones()
    lector = Lector(op.limites)
    lector.leer(rutas or [])
    for nombre, contenido in contenidos or []:
        lector.leer_bytes(nombre, contenido)
    return _Proceso(referencia, op).ejecutar(lector.tablas, lector.eventos)


class _Proceso:
    def __init__(self, ref, op):
        self.ref, self.op = ref, op
        self.detector = Detector(op.mapeo)
        self.homologador = Homologador(ref, activar_nuevos=op.activar_nuevos)
        self.traza = _Bitacora()
        self.registros = []
        self.mapeos = []
        self.eventos = []
        self.alertas = []
        self.nombres_cuenta = {}
        self.controles = []   # (origen, hoja, formato, clase, centavos según el reporte)
        self._n = 0

    # ------------------------------------------------------------------ orquestación
    def ejecutar(self, tablas, eventos):
        self.eventos = list(eventos)
        for t in tablas:
            self._tabla(t)
        self._reglas_globales()
        self._homologar()
        consolidado = self._consolidar()
        registros = pd.DataFrame(self.registros, columns=COLUMNAS_REGISTRO)
        conciliacion = self._conciliar(registros, consolidado)
        archivos = pd.DataFrame([e.__dict__ for e in self.eventos],
                                columns=["origen", "hoja", "estado", "motivo", "huella", "tamano", "filas"])
        nuevos = self._nuevos(registros)
        control_origen = self._control_origen(registros)
        resumen = {
            "archivos": int(archivos["origen"].nunique()) if len(archivos) else 0,
            "archivos_con_error": int((archivos["estado"] == EV_ERROR).sum()),
            "registros": len(registros),
            **{d.lower(): int((registros["destino"] == d).sum()) for d in (CARGADO, RETENIDO, EXCLUIDO, RECHAZADO)},
            "lineas_consolidadas": len(consolidado),
        }
        return Resultado(
            registros=registros, consolidado=consolidado, archivos=archivos,
            mapeo=pd.DataFrame(self.mapeos, columns=["origen", "hoja", "formato", "fila_encabezado", "campo",
                                                     "columna_origen", "metodo", "puntaje"]),
            trazabilidad=self.traza.df(),
            alertas=pd.DataFrame(self.alertas, columns=["id_registro", "origen", "hoja", "fila", "tipo", "detalle"]),
            nuevos=nuevos, conciliacion=conciliacion, control_referencia=self.ref.control_calidad(),
            hallazgos_referencia=self.ref.hallazgos, control_origen=control_origen, nombres_cuenta=self.nombres_cuenta,
            referencia=self.ref,
            opciones=self.op, resumen=resumen)

    # ------------------------------------------------------------------ lectura de una tabla
    def _tabla(self, t):
        t = adaptar(t, usar_movimiento=self.op.usar_movimiento)
        try:
            mapeo = self.detector.detectar(t, self.ref)
        except ErrorMapeo as exc:
            estado = EV_ERROR if "mapeo" in str(exc).lower() else OMITIDO
            self.eventos.append(EventoArchivo(t.origen, t.hoja, estado, f"Hoja no procesada: {exc}", t.huella))
            return
        faltan = mapeo.faltantes()
        if faltan:
            presentes = set(mapeo.columnas)
            n_claves = (("cod_ceco" in presentes or bool(mapeo.ceco_fijo) or mapeo.formato == "MATRIZ") + ("cuenta" in presentes)
                        + bool({"valor", "debito", "credito"} & presentes))
            tiene_costos = n_claves >= 2
            estado = EV_ERROR if tiene_costos else OMITIDO
            motivo = (f"Faltan columnas requeridas: {', '.join(faltan)}. Encabezado detectado en la fila "
                      f"{mapeo.fila_encabezado + 1}: {', '.join(e for e in mapeo.encabezados if e)[:300]}")
            if not tiene_costos:
                motivo = "No es una tabla de costos (no tiene al menos dos de: CECO, cuenta, valor). " + motivo
            self.eventos.append(EventoArchivo(t.origen, t.hoja, estado, motivo, t.huella))
            return

        periodo_tabla, fuente_periodo = "", ""
        for fuente, txt in (("nombre de la hoja", t.hoja), ("nombre del archivo", t.origen.rsplit("/", 1)[-1])):
            periodo_tabla = periodo_en_nombre(txt)
            if periodo_tabla:
                fuente_periodo = fuente
                break
        if not periodo_tabla and self.op.periodo:
            periodo_tabla, fuente_periodo = self.op.periodo, "parámetro --periodo"

        formato = t.meta.get("formato", mapeo.formato)
        if t.meta:
            self._meta_formato(t)
        for campo, (met, punt, enc) in mapeo.metodo.items():
            if t.meta:
                continue
            self.mapeos.append((t.origen, t.hoja, mapeo.formato, mapeo.fila_encabezado + 1, campo, enc, met, punt))
            self.traza.add("MAPEO", f"Columna '{enc}' → campo {campo} ({met}, puntaje {punt:.2f})", origen=t.origen,
                           hoja=t.hoja, fila=mapeo.fila_encabezado + 1, campo=campo, antes=enc, despues=campo)
        if mapeo.formato == "MATRIZ":
            for j, cod in mapeo.columnas_ceco.items():
                enc = mapeo.encabezados[j]
                self.mapeos.append((t.origen, t.hoja, "MATRIZ", mapeo.fila_encabezado + 1, "cod_ceco + valor", enc,
                                    "CÓDIGO CECO EN ENCABEZADO", 1.0))
        if mapeo.ceco_fijo:
            self.mapeos.append((t.origen, t.hoja, "POR_CECO", mapeo.fila_encabezado + 1, "cod_ceco", mapeo.ceco_fijo,
                                f"TOMADO DEL {mapeo.ceco_fijo_fuente.upper()}", 1.0))
        if fuente_periodo and "periodo" not in mapeo.columnas:
            self.mapeos.append((t.origen, t.hoja, mapeo.formato, mapeo.fila_encabezado + 1, "periodo", periodo_tabla,
                                f"TOMADO DEL {fuente_periodo.upper()}", 1.0))

        df = t.datos
        filas_con_datos = 0
        cols_relevantes = list(mapeo.columnas.values()) + list(mapeo.columnas_ceco)
        enc_ceco = clave(mapeo.encabezados[mapeo.columnas["cod_ceco"]]) if "cod_ceco" in mapeo.columnas else None
        matriz = df.to_numpy(dtype=object)
        for i in range(mapeo.fila_encabezado + 1, len(matriz)):
            fila = list(matriz[i])
            if all(es_vacio(fila[j]) if j < len(fila) else True for j in cols_relevantes):
                continue
            filas_con_datos += 1
            base = {campo: (fila[j] if j < len(fila) else None) for campo, j in mapeo.columnas.items()}
            if enc_ceco and clave(base.get("cod_ceco")) == enc_ceco:
                self._nuevo(t, i, base, {}, [(C.INFO, "Encabezado repetido dentro de la tabla")], periodo_tabla)
                continue
            if mapeo.formato == "MATRIZ":
                for j, cod in mapeo.columnas_ceco.items():
                    v = fila[j] if j < len(fila) else None
                    if es_vacio(v):
                        continue
                    self._registro(t, i, {**base, "cod_ceco": cod, "valor": v}, periodo_tabla, columna=mapeo.encabezados[j])
            else:
                if mapeo.ceco_fijo:
                    base["cod_ceco"] = mapeo.ceco_fijo
                self._registro(t, i, base, periodo_tabla)
        self.eventos.append(EventoArchivo(t.origen, t.hoja, PROCESADO,
                                          f"Formato {formato}; encabezado en la fila {mapeo.fila_encabezado + 1}",
                                          t.huella, filas=filas_con_datos))

    # ------------------------------------------------------------------ limpieza de un registro
    def _registro(self, t, i, crudo, periodo_tabla, columna=""):
        limpio, problemas = {}, []
        rid = self._siguiente_id()
        fila_excel = i + 1

        def traza_limpieza(campo, antes, despues, regla):
            if isinstance(antes, str) and antes.strip() != str(despues):
                self.traza.add("LIMPIEZA", regla, rid, t.origen, t.hoja, fila_excel, campo, valor_para_traza(antes), despues)

        bruto_ceco = crudo.get("cod_ceco")
        sin_ceco = es_vacio(bruto_ceco) or not any(ch.isdigit() for ch in str(bruto_ceco))
        textos = " ".join(clave(crudo.get(c)) for c in ("cod_ceco", "cuenta", "nombre_cuenta", "nombre_ceco"))
        if sin_ceco and re.search(r"\bTOTAL", textos):
            self._nuevo(t, i, crudo, {"cuenta": texto(crudo.get("cuenta"))}, [(C.INFO, "Fila de total o subtotal del archivo")],
                        periodo_tabla, rid=rid)
            return
        try:
            limpio["cod_ceco"] = codigo_ceco(bruto_ceco)
            traza_limpieza("cod_ceco", bruto_ceco, limpio["cod_ceco"], "Código CECO normalizado (dígitos iniciales, sin espacios ni separadores)")
        except ErrorValor as exc:
            limpio["cod_ceco"] = ""
            problemas.append((C.ERROR, str(exc)))
        if not limpio["cod_ceco"] and not problemas:
            nota = texto(crudo.get("descripcion"))
            problemas.append((C.ERROR, "Sin código de centro de costos" + (f": {nota}" if nota else "")))

        try:
            limpio["cuenta"] = cuenta(crudo.get("cuenta"))
            traza_limpieza("cuenta", crudo.get("cuenta"), limpio["cuenta"], "Cuenta contable: solo dígitos")
            if not limpio["cuenta"]:
                problemas.append((C.ERROR, "Sin cuenta contable"))
        except ErrorValor as exc:
            limpio["cuenta"] = ""
            problemas.append((C.ERROR, str(exc)))

        try:
            usar_mov = self.op.usar_movimiento and ("debito" in crudo or "credito" in crudo)
            if "valor" in crudo and not usar_mov:
                v = centavos(crudo.get("valor"))
                if v is None:
                    problemas.append((C.ERROR, "Sin valor"))
                traza_limpieza("valor", crudo.get("valor"), "" if v is None else f"{v / 100:.2f}",
                               "Monto normalizado (separadores de miles/decimales, $, paréntesis negativos)")
            else:
                d, c = centavos(crudo.get("debito")), centavos(crudo.get("credito"))
                if d is None and c is None:
                    problemas.append((C.ERROR, "Sin débito ni crédito"))
                    v = None
                else:
                    v = (d or 0) - (c or 0)
                    self.traza.add("LIMPIEZA", "Valor = débito − crédito", rid, t.origen, t.hoja, fila_excel, "valor",
                                   f"D {valor_para_traza(crudo.get('debito'))} / C {valor_para_traza(crudo.get('credito'))}",
                                   f"{v / 100:.2f}")
            limpio["valor_cent"] = v
        except ErrorValor as exc:
            limpio["valor_cent"] = None
            problemas.append((C.ERROR, f"Monto inválido: {exc}"))

        try:
            if not es_vacio(crudo.get("periodo")):
                limpio["periodo"] = periodo(crudo.get("periodo"))
                traza_limpieza("periodo", crudo.get("periodo"), limpio["periodo"], "Periodo normalizado a AAAA-MM")
            else:
                limpio["periodo"] = periodo_tabla
            if not limpio["periodo"]:
                problemas.append((C.ERROR, "Sin periodo: agregue una columna PERIODO, póngalo en el nombre del archivo o use --periodo AAAA-MM"))
        except ErrorValor as exc:
            limpio["periodo"] = periodo_tabla
            problemas.append((C.ERROR, str(exc)))

        for campo in CAMPOS_TEXTO:
            limpio[campo] = texto(crudo.get(campo))
        if columna:
            limpio["descripcion"] = (limpio["descripcion"] + " | " if limpio["descripcion"] else "") + f"Columna {texto(columna)}"
        self._nuevo(t, i, crudo, limpio, problemas, periodo_tabla, rid=rid)

    def _siguiente_id(self):
        self._n += 1
        return f"R{self._n:07d}"

    def _nuevo(self, t, i, crudo, limpio, problemas, periodo_tabla, rid=None):
        rid = rid or self._siguiente_id()
        errores = [m for s, m in problemas if s == C.ERROR]
        info = [m for s, m in problemas if s != C.ERROR]
        reg = {c: None for c in COLUMNAS_REGISTRO}
        reg.update({
            "id_registro": rid, "origen": t.origen, "hoja": t.hoja, "fila": i + 1,
            "periodo": limpio.get("periodo", periodo_tabla), "cod_ceco": limpio.get("cod_ceco", texto(crudo.get("cod_ceco"))),
            "nombre_ceco_fuente": limpio.get("nombre_ceco", ""), "sede_fuente": limpio.get("sede", ""),
            "cuenta": limpio.get("cuenta", texto(crudo.get("cuenta"))), "nombre_cuenta": limpio.get("nombre_cuenta", ""),
            "descripcion": limpio.get("descripcion", ""), "documento": limpio.get("documento", ""),
            "valor_cent": limpio.get("valor_cent"),
        })
        if errores:
            reg.update(destino=RECHAZADO, severidad=C.ERROR, motivo="; ".join(errores))
        elif info:
            reg.update(destino=EXCLUIDO, severidad=C.INFO, motivo="; ".join(info))
        self.registros.append(reg)
        if reg["destino"]:
            self.traza.add("VALIDACION", f"{reg['destino']}: {reg['motivo']}", rid, t.origen, t.hoja, i + 1)

    def _meta_formato(self, t):
        """Registra el mapeo, los nombres de cuenta y los totales de control de un formato de software contable."""
        m = t.meta
        for campo, origen_col in m.get("mapeo", []):
            self.mapeos.append((t.origen, t.hoja, m["formato"], 1, campo, origen_col, "FORMATO RECONOCIDO", 1.0))
            self.traza.add("MAPEO", f"{m['formato']}: {origen_col} → campo {campo}", origen=t.origen, hoja=t.hoja, fila=1,
                           campo=campo, antes=origen_col, despues=campo)
        for cod, nombre in m.get("nombres_cuenta", {}).items():
            self.nombres_cuenta.setdefault(cod, nombre)
        for clase, cent in m.get("totales_clase", {}).items():
            self.controles.append((t.origen, t.hoja, m["formato"], clase, cent))
        for cod, nombre, fila, cent in m.get("diferencias", []):
            self.alertas.append(("", t.origen, t.hoja, fila, "CUENTA SIN DETALLE POR CECO",
                                 f"La cuenta {cod} {nombre} tiene {formato_pesos(cent)} sin centro de costo en el reporte: "
                                 "se registra como rechazada para que el total cuadre; pida a contabilidad asignar el CECO"))
        if m.get("detalle_sin_cuenta"):
            self.alertas.append(("", t.origen, t.hoja, None, "DETALLE SIN CUENTA",
                                 f"{m['detalle_sin_cuenta']} fila(s) de detalle apuntan a una cuenta que no está en el reporte"))
        if not m.get("periodo"):
            self.alertas.append(("", t.origen, t.hoja, None, "SIN PERIODO EN EL REPORTE",
                                 "No se encontró el periodo en el pie del reporte; se usa el nombre del archivo o --periodo"))

    def _control_origen(self, registros):
        """Total por clase según el propio reporte vs. total leído de esa hoja (todos los destinos)."""
        filas = []
        for origen, hoja, formato, clase, cent in self.controles:
            sub = registros[(registros["origen"] == origen) & (registros["hoja"] == hoja)
                            & registros["cuenta"].fillna("").str.startswith(clase)]
            leido = sum(int(v) for v in sub["valor_cent"] if v is not None and not pd.isna(v))
            dif = leido - cent
            filas.append({"origen": origen, "hoja": hoja, "formato": formato, "clase": clase,
                          "nombre": self.nombres_cuenta.get(clase, ""), "reporte": cent / 100, "leido": leido / 100,
                          "diferencia": dif / 100, "estado": "CUADRA" if dif == 0 else "DIFERENCIA"})
            if dif:
                self.alertas.append(("", origen, hoja, None, "NO CUADRA CON EL REPORTE",
                                     f"Clase {clase}: el reporte totaliza {formato_pesos(cent)} y se leyeron {formato_pesos(leido)}"))
        return pd.DataFrame(filas, columns=["origen", "hoja", "formato", "clase", "nombre", "reporte", "leido", "diferencia",
                                            "estado"])

    # ------------------------------------------------------------------ reglas que miran varias filas
    def _excluir(self, reg, motivo, severidad=C.INFO):
        reg.update(destino=EXCLUIDO, severidad=severidad, motivo=motivo)
        self.traza.add("REGLA", f"EXCLUIDO: {motivo}", reg["id_registro"], reg["origen"], reg["hoja"], reg["fila"])

    def _reglas_globales(self):
        # 0. Filas sin CECO cuya cuenta agrupa otras cuentas de la misma hoja: son subtotales del reporte, no errores.
        cuentas_hoja = defaultdict(set)
        for r in self.registros:
            if r["cuenta"]:
                cuentas_hoja[(r["origen"], r["hoja"])].add(r["cuenta"])
        for r in self.registros:
            if r["destino"] == RECHAZADO and r["motivo"].startswith("Sin código de centro de costos") and r["cuenta"]:
                if any(c != r["cuenta"] and c.startswith(r["cuenta"]) for c in cuentas_hoja[(r["origen"], r["hoja"])]):
                    if r["nombre_cuenta"]:
                        self.nombres_cuenta.setdefault(r["cuenta"], r["nombre_cuenta"])
                    self._excluir(r, f"Cuenta {r['cuenta']} sin CECO es subtotal de cuentas de mayor detalle del reporte")
        pendientes = [r for r in self.registros if not r["destino"]]
        for r in pendientes:
            if r["nombre_cuenta"]:
                self.nombres_cuenta.setdefault(r["cuenta"], r["nombre_cuenta"])
        # 1. Clases de cuenta
        for r in pendientes:
            if r["cuenta"][:1] not in self.op.clases:
                self._excluir(r, f"Cuenta de clase {r['cuenta'][:1]}: fuera de las clases del estado de resultados ({', '.join(self.op.clases)})")
        # 2. Subtotales: una cuenta que es prefijo de otra del mismo CECO, periodo y hoja es un total de nivel superior.
        if self.op.excluir_subtotales:
            grupos = defaultdict(list)
            for r in pendientes:
                if not r["destino"]:
                    grupos[(r["origen"], r["hoja"], r["periodo"], r["cod_ceco"])].append(r)
            for regs in grupos.values():
                cuentas = {r["cuenta"] for r in regs}
                for r in regs:
                    hijas = [c for c in cuentas if c != r["cuenta"] and c.startswith(r["cuenta"])]
                    if hijas:
                        self._excluir(r, f"Cuenta {r['cuenta']} es subtotal de {len(hijas)} cuenta(s) de mayor detalle: "
                                         "se cargan las de detalle para no duplicar el valor")
        # 3. Valor cero
        if self.op.excluir_ceros:
            for r in pendientes:
                if not r["destino"] and r["valor_cent"] == 0:
                    self._excluir(r, "Valor cero")
        # 4. Duplicados exactos
        if self.op.eliminar_duplicados:
            vistos = {}
            for r in pendientes:
                if r["destino"]:
                    continue
                k = (r["periodo"], r["cod_ceco"], r["cuenta"], r["valor_cent"], clave(r["descripcion"]), clave(r["documento"]))
                if k in vistos:
                    o = vistos[k]
                    self._excluir(r, f"Duplicado exacto de {o['id_registro']} ({o['origen']}{' / ' + o['hoja'] if o['hoja'] else ''}, "
                                     f"fila {o['fila']})", C.AVISO)
                else:
                    vistos[k] = r

    # ------------------------------------------------------------------ homologación
    def _homologar(self):
        fuera = {}  # código de resolución fuera de «Utilizados» → [primer registro, n.º de registros]
        for r in self.registros:
            if r["destino"] in (RECHAZADO,) or not r["cod_ceco"]:
                continue
            h = self.homologador.homologar(r["cod_ceco"], r["nombre_ceco_fuente"], r["sede_fuente"])
            r.update({
                "cod_res": h.cod_res, "nombre_res": h.nombre_res, "sede": h.sede, "sede_ips": C.sede_corta(h.sede),
                "llave": h.llave, "tipo": h.tipo, "estado": h.estado, "confianza": h.confianza, "criterio": h.criterio,
                "metodo": h.metodo, "origen_homologacion": h.origen, "en_utilizados": h.en_utilizados,
                "ceco_nuevo": h.nuevo, "nombre_ceco_maestro": h.nombre_ef if not h.nuevo else "",
            })
            if r["destino"]:
                continue
            self.traza.add("HOMOLOGACION", h.describir(), r["id_registro"], r["origen"], r["hoja"], r["fila"], "cod_ceco",
                           r["cod_ceco"], h.llave)
            if h.cargable:
                r.update(destino=CARGADO, severidad="", motivo="")
            else:
                motivo = C.MOTIVO_ESTADO.get(h.estado, f"Estado {h.estado}")
                if h.criterio:
                    motivo += f". Criterio: {h.criterio}"
                if h.nota:
                    motivo += f". {h.nota}"
                if h.nuevo and h.estado == C.VERIFICAR and h.origen != "SIN REGLA":
                    motivo += f". CECO nuevo: se propone {h.llave}; agréguelo al maestro o use --activar-nuevos"
                r.update(destino=RETENIDO, severidad=C.AVISO, motivo=motivo)
                self.traza.add("REGLA", f"RETENIDO por estado {h.estado}", r["id_registro"], r["origen"], r["hoja"], r["fila"])
                if h.estado == C.NO_APLICA and r["valor_cent"]:
                    self._alerta(r, "NO APLICA CON GASTO", f"El CECO {r['cod_ceco']} está en NO APLICA y recibe {formato_pesos(r['valor_cent'])}: "
                                                           "validar con contabilidad (no debería recibir gasto)")
            if h.nota and h.cargable:
                self._alerta(r, "NOTA DE REGLA", h.nota)
            if h.estado == C.ACTIVO and not h.en_utilizados:
                fuera.setdefault(h.cod_res, [r, 0])[1] += 1
            if r["sede_fuente"] and h.sede:
                s = self.homologador.sede_por_texto(r["sede_fuente"])
                if s and C.sede_corta(s) != C.sede_corta(h.sede):
                    self._alerta(r, "SEDE DISTINTA", f"El archivo indica la sede '{r['sede_fuente']}' y el maestro '{h.sede}'; se usa la del maestro")
            if r["nombre_ceco_fuente"] and not h.nuevo:
                a, b = clave(r["nombre_ceco_fuente"]), clave(h.nombre_ef)
                if a != b and SequenceMatcher(None, a, b).ratio() < 0.6:
                    self._alerta(r, "NOMBRE CECO DISTINTO", f"Nombre en el archivo '{r['nombre_ceco_fuente']}' vs. maestro '{h.nombre_ef}': "
                                                           "verifique que el código sea el correcto")

        for cod, (r, n) in sorted(fuera.items()):
            self._alerta(r, "FUERA DE UTILIZADOS", f"{cod} no está en la lista 'Utilizados' ({n} registro(s); se indica el "
                                                   "primero): agréguelo para que la herramienta de costos lo reconozca")

    def _alerta(self, r, tipo, detalle):
        self.alertas.append((r["id_registro"], r["origen"], r["hoja"], r["fila"], tipo, detalle))

    # ------------------------------------------------------------------ consolidación
    def _consolidar(self):
        cargados = [r for r in self.registros if r["destino"] == CARGADO]
        grupos = {}
        for r in sorted(cargados, key=lambda x: (x["periodo"], x["cuenta"], x["llave"], x["id_registro"])):
            k = (r["periodo"], r["cuenta"], r["llave"])
            g = grupos.get(k)
            if g is None:
                g = grupos[k] = {
                    "id_consolidado": f"C{len(grupos) + 1:06d}", "periodo": r["periodo"], "cuenta": r["cuenta"],
                    "nombre_cuenta": self.nombres_cuenta.get(r["cuenta"], r["nombre_cuenta"]), "cod_res": r["cod_res"],
                    "nombre_res": r["nombre_res"], "sede_ips": r["sede_ips"], "sede": r["sede"], "llave": r["llave"],
                    "tipo": r["tipo"], "en_utilizados": r["en_utilizados"], "valor_cent": 0, "registros": 0, "_cecos": [],
                }
            g["valor_cent"] += r["valor_cent"]
            g["registros"] += 1
            if r["cod_ceco"] not in g["_cecos"]:
                g["_cecos"].append(r["cod_ceco"])
            r["id_consolidado"] = g["id_consolidado"]
        filas = []
        for g in grupos.values():
            g["cecos_ef"] = ", ".join(g.pop("_cecos"))
            filas.append(g)
            self.traza.add("CONSOLIDACION", f"{g['registros']} registro(s) de CECO {g['cecos_ef']} → {g['id_consolidado']} "
                                            f"({g['periodo']} · {g['cuenta']} · {g['llave']})", g["id_consolidado"],
                           campo="valor", despues=f"{g['valor_cent'] / 100:.2f}")
        return pd.DataFrame(filas, columns=COLUMNAS_CONSOLIDADO)

    # ------------------------------------------------------------------ conciliación y CECO nuevos
    def _conciliar(self, registros, consolidado):
        def suma(df):
            return sum(int(v) for v in df["valor_cent"] if v is not None and not pd.isna(v))

        filas = [("Leído (filas con datos)", len(registros), suma(registros))]
        for destino in (RECHAZADO, EXCLUIDO, RETENIDO, CARGADO):
            sub = registros[registros["destino"] == destino]
            filas.append((destino, len(sub), suma(sub)))
            if destino in (RETENIDO, EXCLUIDO):
                col = "estado" if destino == RETENIDO else "motivo"
                for etiqueta, g in sub.groupby(sub[col].fillna("").map(_etiqueta_motivo) if destino == EXCLUIDO else sub[col].fillna("")):
                    filas.append((f"   {destino} · {etiqueta}", len(g), suma(g)))
        filas.append(("Consolidado para carga", len(consolidado), suma(consolidado)))
        total = suma(registros)
        destinos = sum(suma(registros[registros["destino"] == d]) for d in (RECHAZADO, EXCLUIDO, RETENIDO, CARGADO))
        sin_destino = registros["destino"].isna() | (registros["destino"] == "")
        if sin_destino.any() or total != destinos or suma(consolidado) != suma(registros[registros["destino"] == CARGADO]):
            raise ErrorConciliacion("La conciliación no cuadra: revise el proceso (registros sin destino o sumas distintas).")
        filas.append(("Verificación: leído = rechazado + excluido + retenido + cargado; consolidado = cargado", None, None))
        df = pd.DataFrame(filas, columns=["concepto", "registros", "valor_cent"])
        df["valor"] = df["valor_cent"].map(lambda v: None if v is None or pd.isna(v) else int(v) / 100)
        df.loc[df.index[-1], "concepto"] += " → CUADRA"
        return df

    def _nuevos(self, registros):
        sub = registros[registros["ceco_nuevo"].eq(True)]
        if sub.empty:
            return pd.DataFrame(columns=COLUMNAS_NUEVOS)
        g = sub.groupby("cod_ceco", sort=True).agg(
            nombre_ef=("nombre_ceco_fuente", lambda s: next((x for x in s if x), "")),
            cod_res=("cod_res", "first"), nombre_res=("nombre_res", "first"), sede=("sede", "first"),
            llave=("llave", "first"), tipo=("tipo", "first"), estado=("estado", "first"), confianza=("confianza", "first"),
            criterio=("criterio", "first"), metodo=("metodo", "first"), registros=("id_registro", "count"),
            valor_cent=("valor_cent", lambda s: sum(int(v) for v in s if v is not None and not pd.isna(v))),
        ).reset_index()
        return g[COLUMNAS_NUEVOS]


def _etiqueta_motivo(motivo):
    m = motivo or ""
    for prefijo, etiqueta in (("Cuenta de clase", "cuenta fuera de clases de costo"), ("Cuenta ", "subtotal de nivel superior"),
                              ("Valor cero", "valor cero"), ("Duplicado", "duplicado exacto"),
                              ("Encabezado repetido", "encabezado repetido"), ("Fila de total", "fila de total")):
        if m.startswith(prefijo):
            return etiqueta
    return m[:60]


COLUMNAS_REGISTRO = [
    "id_registro", "origen", "hoja", "fila", "periodo", "cod_ceco", "nombre_ceco_fuente", "nombre_ceco_maestro", "sede_fuente",
    "cuenta", "nombre_cuenta", "descripcion", "documento", "valor_cent", "cod_res", "nombre_res", "sede", "sede_ips",
    "llave", "tipo", "estado", "confianza", "criterio", "metodo", "origen_homologacion", "en_utilizados", "ceco_nuevo",
    "destino", "severidad", "motivo", "id_consolidado",
]
COLUMNAS_CONSOLIDADO = [
    "id_consolidado", "periodo", "cuenta", "nombre_cuenta", "cod_res", "nombre_res", "sede_ips", "sede", "llave", "tipo",
    "en_utilizados", "valor_cent", "registros", "cecos_ef",
]
COLUMNAS_NUEVOS = ["cod_ceco", "nombre_ef", "cod_res", "nombre_res", "sede", "llave", "tipo", "estado", "confianza",
                   "criterio", "metodo", "registros", "valor_cent"]

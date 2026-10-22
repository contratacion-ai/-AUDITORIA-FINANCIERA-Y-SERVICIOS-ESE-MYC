"""Procesamiento de cargas: mapeo de columnas → limpieza → validación → resolución de códigos → persistencia.

Una carga con errores se rechaza completa salvo que el usuario elija aplicar solo las filas válidas.
Cada fila guardada conserva la referencia a su carga (archivo) y número de fila para la trazabilidad.
"""
import difflib
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from core.auditoria import registrar
from modelo.models import (
    Ambito, AmbitoHomologacion, CentroCosto, Clase, Cups, CupsCentroCosto, CuentaRecurso,
    DistribucionManoObra, Eps, HomologacionCentro, Inductor, NotaTecnica, NotaTecnicaItem,
    Recurso, Sede, UnidadNegocio, ValorInductor,
)

from . import limpieza as L
from .esquemas import CODIGO, FECHA, NUMERO, esquema
from .lectores import ErrorArchivo, indice_columna, leer_tabla
from .models import Carga, ErrorCarga, Facturacion, MovimientoContable, Produccion, Rips

MAX_ERRORES_GUARDADOS = 2000
# El original de estos archivos trae documentos de pacientes en claro: no se conserva (solo su huella).
TIPOS_CON_PACIENTES = {"FACTURACION", "RIPS"}
DOS = Decimal("0.01")


@dataclass
class Fila:
    numero: int
    datos: dict
    original: dict = field(default_factory=dict)


@dataclass
class Resultado:
    carga: Carga = None
    errores: list = field(default_factory=list)
    advertencias: list = field(default_factory=list)
    leidas: int = 0
    validas: int = 0
    duplicadas: int = 0
    omitidas: int = 0
    valor_total: Decimal = Decimal("0")
    resumen: dict = field(default_factory=dict)

    @property
    def filas_con_error(self):
        return len({e[0] for e in self.errores})


# --------------------------------------------------------------------------- mapeo
def sugerir_mapeo(encabezados, esq):
    """Sugiere {campo: encabezado} por coincidencia exacta de etiqueta/sinónimos y luego por similitud."""
    claves = {L.clave(e): e for e in encabezados if e}
    sugerido, usados = {}, set()
    for campo in esq.campos:
        candidatos = [L.clave(campo.etiqueta), L.clave(campo.nombre.replace("_", " "))] + [L.clave(s) for s in campo.sinonimos]
        for c in candidatos:
            if c in claves and claves[c] not in usados:
                sugerido[campo.nombre] = claves[c]
                usados.add(claves[c])
                break
    for campo in esq.campos:
        if campo.nombre in sugerido:
            continue
        libres = [k for k, v in claves.items() if v not in usados]
        cercano = difflib.get_close_matches(L.clave(campo.etiqueta), libres, n=1, cutoff=0.82)
        if cercano:
            sugerido[campo.nombre] = claves[cercano[0]]
            usados.add(claves[cercano[0]])
    return sugerido


def resolver_columnas(encabezados, esq, plantilla):
    """Devuelve ({campo: índice}, errores_de_estructura)."""
    mapeo = (plantilla.mapeo if plantilla else None) or sugerir_mapeo(encabezados, esq)
    constantes = plantilla.constantes if plantilla else {}
    indices, errores = {}, []
    for campo in esq.campos:
        ref = mapeo.get(campo.nombre)
        if ref in (None, ""):
            continue
        idx = indice_columna(ref, encabezados)
        if idx is None:
            # En la plantilla estándar las columnas opcionales pueden omitirse.
            if not (plantilla is None or plantilla.es_estandar) or campo.requerido:
                errores.append(f"La columna '{ref}' (campo {campo.etiqueta}) no existe en el archivo.")
        else:
            indices[campo.nombre] = idx
    for campo in esq.requeridos:
        if campo.nombre not in indices and L.es_vacio(constantes.get(campo.nombre)):
            if esq.codigo == "ESTADO_FINANCIERO" and campo.nombre == "valor":
                continue
            errores.append(f"Falta la columna obligatoria {campo.etiqueta}.")
    if esq.codigo == "ESTADO_FINANCIERO" and "valor" not in indices and not ({"debito", "credito"} & indices.keys()):
        errores.append("Mapee la columna VALOR o las columnas DEBITO / CREDITO.")
    return indices, errores


# --------------------------------------------------------------------------- resolución de códigos
class Resolutor:
    """Caché de catálogos de la institución para resolver los códigos que trae cada fila."""

    def __init__(self, institucion):
        self.inst = institucion
        self.recursos = {r.codigo.upper(): r for r in Recurso.objects.de(institucion)}
        self.cuentas = sorted(CuentaRecurso.objects.de(institucion).select_related("recurso"),
                              key=lambda c: len(c.prefijo), reverse=True)
        self._centros()
        self.sedes = {}
        for s in Sede.objects.de(institucion):
            self.sedes[L.clave(s.codigo)] = s
            self.sedes.setdefault(L.clave(s.nombre), s)
        self.cups = {c.codigo.upper(): c for c in Cups.objects.de(institucion)}
        self.eps = {}
        for e in Eps.objects.de(institucion):
            for k in (e.codigo, e.nit, e.nombre):
                if k:
                    self.eps.setdefault(L.clave(k), e)
        self.ambitos = {}
        for a in Ambito.objects.de(institucion):
            self.ambitos[L.clave(a.codigo)] = a
            self.ambitos.setdefault(L.clave(a.nombre), a)
        for h in AmbitoHomologacion.objects.de(institucion).select_related("ambito"):
            self.ambitos[L.clave(h.valor_origen)] = h.ambito
        self.inductores = {i.codigo.upper(): i for i in Inductor.objects.de(institucion)}
        self.unidades = {u.codigo.upper(): u for u in UnidadNegocio.objects.de(institucion)}

    def _centros(self):
        self.centros = {}
        todos = list(CentroCosto.objects.de(self.inst).select_related("unidad_negocio", "sede"))
        for c in todos:
            self.centros[L.clave(c.codigo)] = c
        for c in todos:
            self.centros.setdefault(L.clave(c.nombre), c)
        for h in HomologacionCentro.objects.de(self.inst).select_related("centro"):
            self.centros[L.clave(h.codigo_origen)] = h.centro

    def centro(self, valor):
        return self.centros.get(L.clave(valor)) if valor else None

    def recurso_de_cuenta(self, cuenta):
        """(recurso, excluida) por el prefijo más largo."""
        for c in self.cuentas:
            if cuenta.startswith(c.prefijo):
                return c.recurso, c.excluir
        return None, False

    def ambito(self, valor):
        return self.ambitos.get(L.clave(valor)) if valor else None

    def eps_(self, valor):
        return self.eps.get(L.clave(valor)) if valor else None


# --------------------------------------------------------------------------- conversión de filas
def _filas(encabezados, datos, inicio, indices, esq, plantilla, res):
    constantes = plantilla.constantes if plantilla else {}
    sep = plantilla.separador_decimal if plantilla else "auto"
    fmt = plantilla.formato_fecha if plantilla else ""
    omitir_dup = plantilla.omitir_duplicados if plantilla else True
    vistos = set()
    tipos = {c.nombre: c for c in esq.campos}
    for desplazamiento, cruda in enumerate(datos):
        numero = inicio + desplazamiento
        crudo = {n: (cruda[i] if i < len(cruda) else None) for n, i in indices.items()}
        if all(L.es_vacio(v) for v in crudo.values()):
            continue
        for k, v in constantes.items():
            if k in tipos and L.es_vacio(crudo.get(k)):
                crudo[k] = v
        res.leidas += 1
        datos_fila, error = {}, False
        for nombre, valor in crudo.items():
            campo = tipos[nombre]
            try:
                if campo.tipo == NUMERO:
                    datos_fila[nombre] = L.numero(valor, sep)
                elif campo.tipo == FECHA:
                    datos_fila[nombre] = L.fecha(valor, fmt)
                elif campo.tipo == CODIGO:
                    datos_fila[nombre] = L.codigo(valor)
                else:
                    datos_fila[nombre] = L.texto(valor)
            except L.ErrorValor as exc:
                res.errores.append((numero, campo.etiqueta, str(valor)[:200], str(exc)))
                error = True
        for campo in esq.requeridos:
            if campo.nombre in tipos and L.es_vacio(datos_fila.get(campo.nombre)) and not (
                esq.codigo == "ESTADO_FINANCIERO" and campo.nombre == "valor"
            ):
                if not any(e[0] == numero and e[1] == campo.etiqueta for e in res.errores):
                    res.errores.append((numero, campo.etiqueta, "", "Valor obligatorio vacío."))
                error = True
        if error:
            continue
        firma = tuple(sorted((k, str(v)) for k, v in datos_fila.items()))
        if firma in vistos:
            res.duplicadas += 1
            res.advertencias.append((numero, "", "", "Fila duplicada" + (" (omitida)." if omitir_dup else ".")))
            if omitir_dup:
                continue
        vistos.add(firma)
        yield Fila(numero, datos_fila)


# --------------------------------------------------------------------------- manejadores por tipo
def _err(res, fila, columna, valor, mensaje):
    res.errores.append((fila.numero, columna, str(valor)[:200], mensaje))


def _movimientos(filas, ctx, res):
    r, esq = ctx["resolutor"], ctx["esquema"]
    objetos = []
    for f in filas:
        d = f.datos
        valor = d.get("valor")
        if valor is None and esq.codigo == "ESTADO_FINANCIERO":
            if d.get("debito") is None and d.get("credito") is None:
                _err(res, f, "VALOR", "", "Sin valor ni débito/crédito.")
                continue
            valor = (d.get("debito") or Decimal(0)) - (d.get("credito") or Decimal(0))
        valor = valor.quantize(DOS)
        cuenta = L.solo_digitos(d.get("cuenta", ""))
        recurso = None
        if d.get("recurso"):
            recurso = r.recursos.get(d["recurso"].upper())
            if recurso is None:
                _err(res, f, "RECURSO", d["recurso"], "Recurso inexistente en el catálogo.")
                continue
        elif cuenta:
            recurso, excluida = r.recurso_de_cuenta(cuenta)
            if excluida:
                res.omitidas += 1
                continue
            if recurso is None:
                _err(res, f, "CUENTA", cuenta, "La cuenta no está asociada a ningún recurso (Cuentas contables - Recursos).")
                continue
        else:
            _err(res, f, "CUENTA", "", "Indique la cuenta contable o el recurso.")
            continue
        if valor == 0:
            res.omitidas += 1
            continue
        centro = None
        if d.get("centro_costo"):
            centro = r.centro(d["centro_costo"])
            if centro is None:
                _err(res, f, "CENTRO DE COSTOS", d["centro_costo"],
                     "Centro de costos no existe ni está homologado (Homologación centro de costos).")
                continue
            if not centro.activo:
                _err(res, f, "CENTRO DE COSTOS", d["centro_costo"], "El centro de costos está inactivo.")
                continue
        sede = None
        if d.get("sede"):
            sede = r.sedes.get(L.clave(d["sede"]))
            if sede is None:
                _err(res, f, "SEDE", d["sede"], "Sede inexistente en el catálogo.")
                continue
        elif centro is not None:
            sede = centro.sede
        objetos.append(MovimientoContable(
            institucion=ctx["institucion"], periodo=ctx["periodo"], carga=ctx["carga"], fila=f.numero,
            fuente=esq.fuente, tipo_archivo=esq.codigo, cuenta=cuenta, nombre_cuenta=d.get("nombre_cuenta", "")[:200],
            recurso=recurso, centro=centro, centro_origen=(d.get("centro_costo") or "")[:150], sede=sede,
            tercero_documento=L.solo_digitos(d.get("documento_tercero", "")) or d.get("documento_tercero", "")[:20],
            tercero_nombre=d.get("nombre_tercero", "")[:150], descripcion=d.get("descripcion", "")[:200], valor=valor,
        ))
        res.valor_total += valor
    return objetos


def _guardar_movimientos(objetos, ctx, res):
    MovimientoContable.objects.bulk_create(objetos, batch_size=2000)
    por_recurso = defaultdict(Decimal)
    for o in objetos:
        por_recurso[o.recurso.codigo] += o.valor
    res.resumen["por_recurso"] = {k: str(v) for k, v in sorted(por_recurso.items())}


def _valores_inductor(filas, ctx, res):
    r = ctx["resolutor"]
    objetos = []
    for f in filas:
        d = f.datos
        inductor = r.inductores.get(d["inductor"].upper())
        if inductor is None:
            _err(res, f, "INDUCTOR", d["inductor"], "Inductor inexistente.")
            continue
        centro = r.centro(d["centro_costo"])
        if centro is None:
            _err(res, f, "CENTRO DE COSTOS", d["centro_costo"], "Centro de costos inexistente.")
            continue
        origen = None
        if d.get("centro_origen"):
            origen = r.centro(d["centro_origen"])
            if origen is None:
                _err(res, f, "CENTRO ORIGEN", d["centro_origen"], "Centro origen inexistente.")
                continue
        if d["cantidad"] < 0:
            _err(res, f, "CANTIDAD", d["cantidad"], "La cantidad no puede ser negativa.")
            continue
        periodo = None if L.si_no(d.get("permanente")) else ctx["periodo"]
        objetos.append((inductor, centro, origen, periodo, d["cantidad"]))
    return objetos


def _guardar_valores_inductor(objetos, ctx, res):
    for inductor, centro, origen, periodo, cantidad in objetos:
        ValorInductor.objects.update_or_create(
            institucion=ctx["institucion"], inductor=inductor, centro=centro, centro_origen=origen, periodo=periodo,
            defaults={"cantidad": cantidad, "carga": ctx["carga"]},
        )


def _mano_obra(filas, ctx, res):
    r = ctx["resolutor"]
    objetos, totales, filas_doc = [], defaultdict(Decimal), defaultdict(list)
    for f in filas:
        d = f.datos
        tipo = L.clave(d["tipo"])
        tipo = "EMPLEADO" if tipo.startswith("EMP") or tipo == "NOMINA" else "OPS" if tipo.startswith("OPS") or tipo.startswith("CONTRAT") else ""
        if not tipo:
            _err(res, f, "TIPO", d["tipo"], "Use EMPLEADO u OPS.")
            continue
        centro = r.centro(d["centro_costo"])
        if centro is None:
            _err(res, f, "CENTRO DE COSTOS", d["centro_costo"], "Centro de costos inexistente.")
            continue
        documento = L.solo_digitos(d["documento"]) or d["documento"]
        porcentaje = d["porcentaje"]
        if porcentaje < 0 or porcentaje > 100:
            _err(res, f, "PORCENTAJE", porcentaje, "Debe estar entre 0 y 100.")
            continue
        objetos.append(DistribucionManoObra(
            institucion=ctx["institucion"], tipo=tipo, documento=documento, nombre=d.get("nombre", "")[:150],
            centro=centro, porcentaje=porcentaje, periodo=ctx["periodo"], carga=ctx["carga"],
        ))
        totales[(tipo, documento)] += porcentaje
        filas_doc[(tipo, documento)].append(f.numero)
    malos = {k for k, v in totales.items() if abs(v - 100) > Decimal("0.01")}
    for k in malos:
        for n in filas_doc[k]:
            res.errores.append((n, "PORCENTAJE", str(totales[k]), f"Los porcentajes de {k[0]} {k[1]} suman {totales[k]} (deben sumar 100)."))
    return [o for o in objetos if (o.tipo, o.documento) not in malos]


def _guardar_mano_obra(objetos, ctx, res):
    claves = {(o.tipo, o.documento) for o in objetos}
    for tipo, doc in claves:
        DistribucionManoObra.objects.de(ctx["institucion"]).filter(tipo=tipo, documento=doc, periodo=ctx["periodo"]).delete()
    DistribucionManoObra.objects.bulk_create(objetos, batch_size=2000)


def _produccion(filas, ctx, res):
    r = ctx["resolutor"]
    objetos = []
    for f in filas:
        d = f.datos
        centro = r.centro(d["centro_costo"])
        if centro is None:
            _err(res, f, "CENTRO DE COSTOS", d["centro_costo"], "Centro de costos inexistente.")
            continue
        cups = r.cups.get(d["cups"].upper())
        if cups is None:
            _err(res, f, "CUPS", d["cups"], "CUPS no existe en el catálogo de la institución.")
            continue
        if d["cantidad"] < 0:
            _err(res, f, "CANTIDAD", d["cantidad"], "Cantidad negativa.")
            continue
        objetos.append(Produccion(institucion=ctx["institucion"], periodo=ctx["periodo"], carga=ctx["carga"],
                                  fila=f.numero, centro=centro, cups=cups, cantidad=d["cantidad"].quantize(DOS),
                                  ambito=r.ambito(d.get("ambito"))))
    return objetos


def _fuera_de_periodo(res, f, fecha, periodo, columna):
    if fecha and periodo and (fecha.year, fecha.month) != (periodo.anio, periodo.mes):
        res.advertencias.append((f.numero, columna, fecha.isoformat(), f"Fecha fuera del periodo {periodo.etiqueta}."))


def _facturacion(filas, ctx, res):
    r, llave = ctx["resolutor"], settings.PSEUDONIMO_KEY
    objetos = []
    for f in filas:
        d = f.datos
        cantidad = d["cantidad"]
        total = d["valor_total"]
        unitario = d.get("valor_unitario")
        if unitario is None:
            unitario = (total / cantidad).quantize(DOS) if cantidad else Decimal(0)
        if cantidad <= 0:
            _err(res, f, "CANTIDAD", cantidad, "La cantidad debe ser mayor que cero.")
            continue
        _fuera_de_periodo(res, f, d.get("fecha"), ctx["periodo"], "FECHA")
        centro = r.centro(d.get("centro_costo")) if d.get("centro_costo") else None
        objetos.append(Facturacion(
            institucion=ctx["institucion"], periodo=ctx["periodo"], carga=ctx["carga"], fila=f.numero,
            numero_factura=d["numero_factura"][:40], fecha=d.get("fecha"), eps_codigo=d["eps"][:60], eps=r.eps_(d["eps"]),
            cups_codigo=d["cups"][:20], cups=r.cups.get(d["cups"].upper()), cantidad=cantidad.quantize(DOS),
            valor_unitario=unitario.quantize(DOS), valor_total=total.quantize(DOS),
            ambito_origen=(d.get("ambito") or "")[:80], ambito=r.ambito(d.get("ambito")), centro=centro,
            paciente=L.seudonimo(d.get("documento_paciente", ""), llave),
        ))
        res.valor_total += total
    return objetos


def _rips(filas, ctx, res):
    r, llave = ctx["resolutor"], settings.PSEUDONIMO_KEY
    objetos = []
    for f in filas:
        d = f.datos
        cantidad = d.get("cantidad") or Decimal(1)
        _fuera_de_periodo(res, f, d.get("fecha_atencion"), ctx["periodo"], "FECHA ATENCION")
        objetos.append(Rips(
            institucion=ctx["institucion"], periodo=ctx["periodo"], carga=ctx["carga"], fila=f.numero,
            tipo_registro=(d.get("tipo_registro") or "")[:20], numero_factura=d["numero_factura"][:40],
            fecha_atencion=d.get("fecha_atencion"), eps_codigo=(d.get("eps") or "")[:60], eps=r.eps_(d.get("eps")),
            paciente=L.seudonimo(d.get("documento_paciente", ""), llave),
            cups_codigo=d["cups"][:20], cups=r.cups.get(d["cups"].upper()),
            ambito_origen=(d.get("ambito") or "")[:80], ambito=r.ambito(d.get("ambito")),
            finalidad=(d.get("finalidad") or "")[:20], diagnostico=(d.get("diagnostico") or "")[:10],
            cantidad=cantidad.quantize(DOS), valor=(d.get("valor") or Decimal(0)).quantize(DOS),
        ))
        res.valor_total += d.get("valor") or Decimal(0)
    return objetos


def _bulk(modelo):
    def guardar(objetos, ctx, res):
        modelo.objects.bulk_create(objetos, batch_size=2000)
    return guardar


_MODALIDADES = {"EVENTO": "EVENTO", "CAPITA": "CAPITA", "PGP": "PGP", "PAGO GLOBAL PROSPECTIVO": "PGP", "PAQUETE": "PAQUETE"}


def _nota_tecnica(filas, ctx, res):
    r = ctx["resolutor"]
    objetos = []
    for f in filas:
        d = f.datos
        modalidad = _MODALIDADES.get(L.clave(d["modalidad"]))
        if not modalidad:
            _err(res, f, "MODALIDAD", d["modalidad"], "Use EVENTO, CAPITA, PGP o PAQUETE.")
            continue
        if d["vigencia_hasta"] < d["vigencia_desde"]:
            _err(res, f, "VIGENCIA HASTA", d["vigencia_hasta"], "Vigencia final anterior a la inicial.")
            continue
        if d["tarifa"] < 0:
            _err(res, f, "TARIFA", d["tarifa"], "Tarifa negativa.")
            continue
        cups = r.cups.get(d["cups"].upper())
        if cups is None:
            _err(res, f, "CUPS", d["cups"], "CUPS no existe en el catálogo.")
            continue
        objetos.append((f, d, modalidad, cups))
    return objetos


def _guardar_nota_tecnica(objetos, ctx, res):
    inst, r = ctx["institucion"], ctx["resolutor"]
    notas, n_items = {}, 0
    for f, d, modalidad, cups in objetos:
        eps = r.eps_(d["eps"])
        if eps is None:
            eps = Eps.objects.create(institucion=inst, codigo=d["eps"][:20], nombre=(d.get("nombre_eps") or d["eps"])[:200])
            r.eps[L.clave(eps.codigo)] = eps
            res.advertencias.append((f.numero, "EPS", d["eps"], "EPS creada en el catálogo."))
        k = (eps.pk, d["contrato"])
        if k not in notas:
            nota, _ = NotaTecnica.objects.update_or_create(
                institucion=inst, eps=eps, contrato=d["contrato"][:60],
                defaults={"modalidad": modalidad, "vigencia_desde": d["vigencia_desde"], "vigencia_hasta": d["vigencia_hasta"],
                          "manual_tarifario": (d.get("manual_tarifario") or "")[:40],
                          "valor_mensual": (d.get("valor_mensual") or Decimal(0)).quantize(DOS),
                          "poblacion": int(d.get("poblacion") or 0), "condiciones": d.get("condiciones", "")},
            )
            notas[k] = nota
        NotaTecnicaItem.objects.update_or_create(
            institucion=inst, nota=notas[k], cups=cups, ambito=r.ambito(d.get("ambito")),
            defaults={"tarifa": d["tarifa"].quantize(DOS), "frecuencia_esperada": d.get("frecuencia_esperada") or 0},
        )
        n_items += 1
    res.resumen.update({"notas": len(notas), "items": n_items})


def _cups(filas, ctx, res):
    r = ctx["resolutor"]
    objetos = []
    for f in filas:
        d = f.datos
        centro = None
        if d.get("centro_costo"):
            centro = r.centro(d["centro_costo"])
            if centro is None:
                _err(res, f, "CENTRO DE COSTOS", d["centro_costo"], "Centro de costos inexistente.")
                continue
        objetos.append((d, centro))
    return objetos


def _guardar_cups(objetos, ctx, res):
    inst, r = ctx["institucion"], ctx["resolutor"]
    nuevos = asociaciones = 0
    for d, centro in objetos:
        cups, creado = Cups.objects.update_or_create(
            institucion=inst, codigo=d["codigo"][:12],
            defaults={"descripcion": d["descripcion"][:300], "grupo": (d.get("grupo") or "")[:100]},
        )
        nuevos += creado
        if centro is not None:
            CupsCentroCosto.objects.update_or_create(
                institucion=inst, cups=cups, centro=centro, ambito=r.ambito(d.get("ambito")),
                defaults={"peso_relativo": d.get("peso_relativo") or Decimal(1)},
            )
            asociaciones += 1
    res.resumen.update({"cups_nuevos": nuevos, "asociaciones": asociaciones})


def _centros(filas, ctx, res):
    r = ctx["resolutor"]
    objetos = []
    for f in filas:
        d = f.datos
        un = r.unidades.get(d["unidad_negocio"].upper())
        if un is None:
            _err(res, f, "UNIDAD DE NEGOCIO", d["unidad_negocio"], "Unidad de negocio inexistente.")
            continue
        clase = L.clave(d.get("clase"))
        if clase and clase not in Clase.values:
            _err(res, f, "CLASE", d["clase"], "Clase no válida.")
            continue
        sede = r.sedes.get(L.clave(d["sede"])) if d.get("sede") else None
        if d.get("sede") and sede is None:
            _err(res, f, "SEDE", d["sede"], "Sede inexistente.")
            continue
        objetos.append((d, un, clase, sede))
    return objetos


def _guardar_centros(objetos, ctx, res):
    inst = ctx["institucion"]
    for d, un, clase, sede in objetos:
        centro, _ = CentroCosto.objects.update_or_create(
            institucion=inst, codigo=d["codigo"][:30],
            defaults={"nombre": d["nombre"][:200], "unidad_negocio": un, "clase": clase or un.clase, "sede": sede},
        )
        if d.get("codigo_origen"):
            HomologacionCentro.objects.update_or_create(institucion=inst, codigo_origen=d["codigo_origen"][:150],
                                                        defaults={"centro": centro})
    res.resumen["centros"] = len(objetos)


def _cuentas(filas, ctx, res):
    r = ctx["resolutor"]
    objetos = []
    for f in filas:
        d = f.datos
        cuenta = L.solo_digitos(d["cuenta"])
        excluir = L.si_no(d.get("excluir"))
        recurso = r.recursos.get((d.get("recurso") or "").upper())
        if not cuenta:
            _err(res, f, "CUENTA", d["cuenta"], "La cuenta debe tener dígitos.")
            continue
        if not excluir and recurso is None:
            _err(res, f, "RECURSO", d.get("recurso"), "Recurso inexistente.")
            continue
        objetos.append((cuenta, recurso, excluir, d.get("descripcion", "")))
    return objetos


def _guardar_cuentas(objetos, ctx, res):
    for cuenta, recurso, excluir, desc in objetos:
        CuentaRecurso.objects.update_or_create(
            institucion=ctx["institucion"], prefijo=cuenta,
            defaults={"recurso": None if excluir else recurso, "excluir": excluir, "descripcion": desc[:200]},
        )


MANEJADORES = {
    "ESTADO_FINANCIERO": (_movimientos, _guardar_movimientos),
    "NOMINA": (_movimientos, _guardar_movimientos),
    "CONTRATISTAS": (_movimientos, _guardar_movimientos),
    "INVENTARIOS": (_movimientos, _guardar_movimientos),
    "DEPRECIACION": (_movimientos, _guardar_movimientos),
    "OTROS_AREA": (_movimientos, _guardar_movimientos),
    "VALORES_INDUCTOR": (_valores_inductor, _guardar_valores_inductor),
    "MANO_OBRA": (_mano_obra, _guardar_mano_obra),
    "PRODUCCION": (_produccion, _bulk(Produccion)),
    "FACTURACION": (_facturacion, _bulk(Facturacion)),
    "RIPS": (_rips, _bulk(Rips)),
    "NOTA_TECNICA": (_nota_tecnica, _guardar_nota_tecnica),
    "CUPS": (_cups, _guardar_cups),
    "CENTROS_COSTO": (_centros, _guardar_centros),
    "CUENTAS_RECURSO": (_cuentas, _guardar_cuentas),
}


# --------------------------------------------------------------------------- orquestación
class ErrorCargaArchivo(Exception):
    pass


def procesar_carga(*, institucion, usuario, tipo_archivo, contenido, nombre, periodo=None, plantilla=None,
                   hoja=None, aplicar_parcial=False, conservar_archivo=True):
    """Procesa un archivo y devuelve `Resultado`. Lanza ErrorCargaArchivo ante errores de estructura."""
    esq = esquema(tipo_archivo)
    if esq.periodico and tipo_archivo != "MANO_OBRA" and periodo is None:
        raise ErrorCargaArchivo("Este tipo de archivo requiere un periodo.")
    if periodo is not None and periodo.cerrado:
        raise ErrorCargaArchivo(f"El periodo {periodo} está cerrado.")
    if plantilla is not None and (plantilla.institucion_id != institucion.pk or plantilla.tipo_archivo != tipo_archivo):
        raise ErrorCargaArchivo("La plantilla de mapeo no corresponde a la institución o al tipo de archivo.")
    ext = "." + nombre.lower().rsplit(".", 1)[-1] if "." in nombre else ""
    if ext not in settings.CARGA_EXTENSIONES:
        raise ErrorCargaArchivo(f"Extensión no permitida ({ext}). Use: {', '.join(sorted(settings.CARGA_EXTENSIONES))}")
    if len(contenido) > settings.CARGA_MAX_MB * 1024 * 1024:
        raise ErrorCargaArchivo(f"El archivo supera {settings.CARGA_MAX_MB} MB.")
    hoja = hoja if hoja is not None else (plantilla.hoja if plantilla else "")
    huella = hashlib.sha256(contenido).hexdigest()
    repetida = Carga.objects.de(institucion).filter(
        hash_sha256=huella, tipo_archivo=tipo_archivo, periodo=periodo, hoja=hoja or "", estado=Carga.Estado.APLICADA
    ).first()
    if repetida:
        raise ErrorCargaArchivo(f"Este archivo ya fue aplicado el {repetida.creado:%Y-%m-%d %H:%M} (carga #{repetida.pk}). "
                                "Anule esa carga antes de volver a cargarlo.")
    try:
        encabezados, datos, inicio = leer_tabla(contenido, nombre, hoja, plantilla.fila_encabezado if plantilla else 1)
    except ErrorArchivo as exc:
        raise ErrorCargaArchivo(str(exc)) from exc
    indices, errores_estructura = resolver_columnas(encabezados, esq, plantilla)
    if errores_estructura:
        raise ErrorCargaArchivo("Estructura del archivo no válida: " + " ".join(errores_estructura))

    res = Resultado()
    with transaction.atomic():
        carga = Carga(institucion=institucion, periodo=periodo, tipo_archivo=tipo_archivo, plantilla=plantilla,
                      nombre_original=nombre[:255], hoja=hoja or "", hash_sha256=huella, tamano=len(contenido),
                      estado=Carga.Estado.APLICADA, usuario=usuario)
        if conservar_archivo and tipo_archivo not in TIPOS_CON_PACIENTES:
            carga.archivo.save(f"{timezone.now():%Y%m%d%H%M%S}_{huella[:10]}{ext}", ContentFile(contenido), save=False)
        carga.save()
        ctx = {"institucion": institucion, "periodo": periodo, "carga": carga, "esquema": esq,
               "resolutor": Resolutor(institucion)}
        convertir, guardar = MANEJADORES[tipo_archivo]
        objetos = convertir(_filas(encabezados, datos, inicio, indices, esq, plantilla, res), ctx, res)
        res.validas = len(objetos)
        hay_errores = bool(res.errores)
        aplicar = objetos and (not hay_errores or aplicar_parcial)
        if aplicar:
            guardar(objetos, ctx, res)
        else:
            carga.estado = Carga.Estado.CON_ERRORES if hay_errores or not objetos else Carga.Estado.APLICADA
            if not objetos and not hay_errores:
                res.errores.append((None, "", "", "El archivo no contiene filas con datos válidos."))
            res.validas = 0
        carga.filas_leidas = res.leidas
        carga.filas_validas = res.validas
        carga.filas_error = res.filas_con_error
        carga.filas_duplicadas = res.duplicadas
        carga.filas_omitidas = res.omitidas
        carga.valor_total = res.valor_total.quantize(DOS) if aplicar else Decimal(0)
        carga.resumen = {**res.resumen, "columnas": {k: encabezados[v] for k, v in indices.items() if v < len(encabezados)},
                         "aplicacion_parcial": bool(aplicar and hay_errores)}
        carga.save()
        ErrorCarga.objects.bulk_create(
            [ErrorCarga(carga=carga, fila=f, columna=c[:80], valor=v[:200], mensaje=m[:300]) for f, c, v, m in
             res.errores[:MAX_ERRORES_GUARDADOS]]
            + [ErrorCarga(carga=carga, fila=f, columna=c[:80], valor=v[:200], mensaje=m[:300],
                          nivel=ErrorCarga.Nivel.ADVERTENCIA) for f, c, v, m in res.advertencias[:MAX_ERRORES_GUARDADOS]]
        )
        registrar("CARGA", institucion=institucion, objeto=carga, usuario=usuario,
                  descripcion=f"{esq.nombre}: {nombre} → {carga.get_estado_display()}",
                  detalle={"filas": res.leidas, "validas": res.validas, "errores": len(res.errores),
                           "duplicadas": res.duplicadas, "valor_total": carga.valor_total, "hash": huella})
    res.carga = carga
    return res


MODELOS_DE_FILA = (MovimientoContable, Produccion, Facturacion, Rips)


def anular_carga(carga, usuario, motivo):
    if carga.estado != Carga.Estado.APLICADA:
        raise ErrorCargaArchivo("Solo se pueden anular cargas aplicadas.")
    if carga.periodo and carga.periodo.cerrado:
        raise ErrorCargaArchivo("El periodo está cerrado.")
    if not esquema(carga.tipo_archivo).periodico:
        raise ErrorCargaArchivo("Las cargas de catálogo no se anulan: corrija el catálogo (los cambios quedan en la bitácora).")
    with transaction.atomic():
        eliminadas = 0
        for modelo in MODELOS_DE_FILA:
            eliminadas += modelo.objects.filter(carga=carga).delete()[0]
        eliminadas += ValorInductor.objects.filter(carga=carga).delete()[0]
        eliminadas += DistribucionManoObra.objects.filter(carga=carga).delete()[0]
        carga.estado = Carga.Estado.ANULADA
        carga.anulada_por = usuario
        carga.anulada_en = timezone.now()
        carga.motivo_anulacion = motivo[:250]
        carga.save()
        registrar("ANULAR_CARGA", institucion=carga.institucion, objeto=carga, usuario=usuario,
                  descripcion=f"Anulada: {carga.nombre_original}", detalle={"motivo": motivo, "filas_eliminadas": eliminadas})
    return eliminadas


ORDEN_LIBRO = ["CENTROS_COSTO", "CUENTAS_RECURSO", "CUPS", "NOTA_TECNICA", "MANO_OBRA", "VALORES_INDUCTOR",
               "ESTADO_FINANCIERO", "NOMINA", "CONTRATISTAS", "INVENTARIOS", "DEPRECIACION", "OTROS_AREA",
               "PRODUCCION", "FACTURACION", "RIPS"]


def procesar_libro_estandar(*, institucion, usuario, contenido, nombre, periodo, aplicar_parcial=False):
    """Procesa todas las hojas diligenciadas de la plantilla estándar. Devuelve [(hoja, Resultado | mensaje de error)]."""
    from .lectores import hojas_del_libro
    from .models import PlantillaMapeo

    try:
        hojas = {L.clave(h): h for h in hojas_del_libro(contenido, nombre)}
    except Exception as exc:
        raise ErrorCargaArchivo(f"No se pudo leer el libro: {exc}") from exc
    con_datos = []
    for tipo in ORDEN_LIBRO:
        hoja = hojas.get(L.clave(tipo))
        if hoja:
            _, datos, _ = leer_tabla(contenido, nombre, hoja, 1)
            if any(any(not L.es_vacio(v) for v in fila) for fila in datos):
                con_datos.append((tipo, hoja))
    # Si alguna hoja trae pacientes, el libro original no se conserva en ninguna de sus cargas.
    conservar = not any(t in TIPOS_CON_PACIENTES for t, _ in con_datos)
    salida = []
    for tipo, hoja in con_datos:
        plantilla = PlantillaMapeo.objects.de(institucion).filter(tipo_archivo=tipo, es_estandar=True).first()
        try:
            res = procesar_carga(institucion=institucion, usuario=usuario, tipo_archivo=tipo, contenido=contenido,
                                 nombre=nombre, periodo=periodo if esquema(tipo).periodico else None,
                                 plantilla=plantilla, hoja=hoja, aplicar_parcial=aplicar_parcial,
                                 conservar_archivo=conservar)
            salida.append((hoja, res))
        except ErrorCargaArchivo as exc:
            salida.append((hoja, str(exc)))
    if not salida:
        raise ErrorCargaArchivo("El libro no tiene hojas de la plantilla estándar con datos.")
    return salida

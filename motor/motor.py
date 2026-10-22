"""Motor de costeo ABC por distribuciones secuenciales (Res. DDC-000002/2014, Anexo Técnico 3).

1ª distribución  Recursos → centros de costos (directo, % mano de obra Empleado/OPS, o inductor).
2ª distribución  Centros de ESTRUCTURA → servicios, intermedios y finales.
3ª distribución  Centros de SERVICIOS → intermedios y finales.
4ª distribución  Centros INTERMEDIOS → finales.
Costeo CUPS      Costo de cada centro final → CUPS producidos (producción o RIPS), ponderado por peso relativo.

Garantías: el reparto se hace en centavos con el método del mayor residuo, así que la suma de las partes es
exactamente el valor repartido; la corrida verifica la conservación (entrada = finales + pendientes).
Cada asignación conserva el movimiento fuente (→ carga → archivo y fila), la regla, el inductor y la tasa.
"""
import hashlib
import json
import time
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from cargas.models import Facturacion, MovimientoContable, Produccion, Rips
from core.auditoria import registrar
from modelo.models import (
    CLASE_DE_DISTRIBUCION, RANGO_CLASE, CentroCosto, Clase, CupsCentroCosto, DistribucionManoObra, Inductor,
    Recurso, ReglaDistribucion, ValorInductor, lista_clases,
)

from .models import Asignacion, CostoCups, Corrida, Pendiente

CERO = Decimal("0")
CENT = Decimal("0.01")
CORRIDAS_CONSERVADAS = 5


class ErrorMotor(Exception):
    pass


def prorratear(valor, pesos):
    """Reparte `valor` según `pesos` (>= 0) en centavos exactos (método del mayor residuo)."""
    total = sum(pesos, CERO)
    if total <= 0:
        raise ErrorMotor("La suma de los pesos debe ser mayor que cero.")
    centavos = int((Decimal(valor) * 100).to_integral_value(rounding=ROUND_HALF_UP))
    signo = -1 if centavos < 0 else 1
    c = abs(centavos)
    brutos = [Decimal(c) * Decimal(p) / total for p in pesos]
    enteros = [int(b) for b in brutos]
    resto = c - sum(enteros)
    for i in sorted(range(len(pesos)), key=lambda i: (brutos[i] - enteros[i], -i), reverse=True)[:resto]:
        enteros[i] += 1
    return [(Decimal(signo * e) / 100).quantize(CENT) for e in enteros]


def _tasa(valor, total):
    return (Decimal(valor) / Decimal(total)).quantize(Decimal("0.000001")) if total else CERO


class Motor:
    def __init__(self, periodo, usuario=None):
        self.periodo = periodo
        self.inst = periodo.institucion
        self.usuario = usuario
        self.advertencias = []
        self.asignaciones = []
        self.pendientes = []
        self.acumulado = defaultdict(lambda: defaultdict(lambda: CERO))  # centro_id → recurso_id → valor
        self._cache_pesos = {}

    # ------------------------------------------------------------------ carga de parámetros
    def _cargar(self):
        inst, p = self.inst, self.periodo
        self.recursos = {r.pk: r for r in Recurso.objects.de(inst).select_related("elemento")}
        self.centros = {c.pk: c for c in CentroCosto.objects.de(inst).filter(activo=True).select_related("unidad_negocio", "sede")}
        if not self.centros:
            raise ErrorMotor("No hay centros de costos activos.")
        self.inductores = {i.pk: i for i in Inductor.objects.de(inst)}
        self.reglas = defaultdict(list)
        for r in ReglaDistribucion.objects.de(inst).filter(activo=True).select_related("inductor"):
            if r.periodo_id in (None, p.pk):
                self.reglas[r.distribucion].append(r)
        # Valores de inductor: (inductor, origen) → {"P": {destino: cant}, "F": {destino: cant}}
        self.valores = defaultdict(lambda: {"P": {}, "F": {}})
        for v in ValorInductor.objects.de(inst).filter(periodo__in=[p]).values_list("inductor_id", "centro_origen_id", "centro_id", "cantidad"):
            self.valores[(v[0], v[1])]["P"][v[2]] = v[3]
        for v in ValorInductor.objects.de(inst).filter(periodo__isnull=True).values_list("inductor_id", "centro_origen_id", "centro_id", "cantidad"):
            self.valores[(v[0], v[1])]["F"][v[2]] = v[3]
        # Mano de obra: (tipo, documento) → [(centro, %)]; lo del periodo prevalece sobre lo permanente.
        self.mano_obra = {}
        for periodo_filtro in ({"periodo__isnull": True}, {"periodo": p}):
            grupo = defaultdict(list)
            for d in DistribucionManoObra.objects.de(inst).filter(**periodo_filtro):
                grupo[(d.tipo, d.documento)].append((d.centro_id, d.porcentaje))
            self.mano_obra.update(grupo)
        self._cargar_produccion()
        self.facturacion = defaultdict(lambda: CERO)
        for f in Facturacion.objects.de(inst).filter(periodo=p).select_related("cups"):
            centro_id = f.centro_id or self._centro_de_cups(f.cups_id, f.ambito_id)
            if centro_id:
                self.facturacion[centro_id] += f.valor_total

    def _cargar_produccion(self):
        inst, p = self.inst, self.periodo
        self.mapa_cups = defaultdict(list)  # cups → [(ambito, centro, peso)]
        for m in CupsCentroCosto.objects.de(inst).order_by("centro__codigo"):
            self.mapa_cups[m.cups_id].append((m.ambito_id, m.centro_id, m.peso_relativo))
        self.detalle_produccion = defaultdict(lambda: CERO)  # (centro, cups) → cantidad
        filas = list(Produccion.objects.de(inst).filter(periodo=p).values_list("centro_id", "cups_id", "cantidad"))
        if filas:
            self.fuente_produccion = "PRODUCCION"
            for c, cups, cant in filas:
                self.detalle_produccion[(c, cups)] += cant
        else:
            self.fuente_produccion = "RIPS"
            sin_centro = set()
            for cups, ambito, cant in Rips.objects.de(inst).filter(periodo=p, cups__isnull=False).values_list("cups_id", "ambito_id", "cantidad"):
                centro = self._centro_de_cups(cups, ambito)
                if centro:
                    self.detalle_produccion[(centro, cups)] += cant
                else:
                    sin_centro.add(cups)
            if sin_centro:
                self.advertencias.append(f"{len(sin_centro)} CUPS de los RIPS no tienen centro de costos asociado; su producción no se usa.")
        self.produccion = defaultdict(lambda: CERO)
        for (c, _), cant in self.detalle_produccion.items():
            self.produccion[c] += cant

    def _centro_de_cups(self, cups_id, ambito_id):
        opciones = self.mapa_cups.get(cups_id) or []
        for amb, centro, _ in opciones:
            if amb is not None and amb == ambito_id and centro in self.centros:
                return centro
        for amb, centro, _ in opciones:
            if amb is None and centro in self.centros:
                return centro
        return None

    # ------------------------------------------------------------------ reglas y pesos
    def regla(self, distribucion, recurso_id=None, centro=None):
        candidatas = []
        for r in self.reglas.get(distribucion, []):
            if distribucion == 1 and r.recurso_id != recurso_id:
                continue
            if r.centro_origen_id and (centro is None or r.centro_origen_id != centro.pk):
                continue
            if r.unidad_negocio_id and (centro is None or r.unidad_negocio_id != centro.unidad_negocio_id):
                continue
            candidatas.append(r)
        if not candidatas:
            return None
        return max(candidatas, key=lambda r: (r.periodo_id is not None, r.centro_origen_id is not None,
                                              r.unidad_negocio_id is not None, r.pk))

    def destinos(self, regla, clase_origen=None, sede_id=None, recurso=None):
        clases = set(lista_clases(regla.clases_destino))
        if recurso is not None and recurso.clases_permitidas:
            clases &= set(lista_clases(recurso.clases_permitidas))
        if clase_origen is not None:
            clases = {c for c in clases if RANGO_CLASE.get(c, 0) > RANGO_CLASE[clase_origen]}
        salida = [c for c in self.centros.values() if c.clase in clases]
        if regla.misma_sede:
            salida = [c for c in salida if c.sede_id == sede_id]
        return sorted(salida, key=lambda c: c.codigo)

    def pesos(self, inductor, origen_id, destinos):
        if inductor.tipo == Inductor.Tipo.CANTIDAD:
            especifico = self.valores.get((inductor.pk, origen_id)) if origen_id else None
            fuente = especifico if especifico and (especifico["P"] or especifico["F"]) else self.valores.get((inductor.pk, None))
            if not fuente:
                return [CERO] * len(destinos)
            mapa = fuente["P"] or fuente["F"]
            return [Decimal(mapa.get(d.pk, CERO)) for d in destinos]
        if inductor.tipo == Inductor.Tipo.PRODUCCION:
            return [self.produccion.get(d.pk, CERO) for d in destinos]
        if inductor.tipo == Inductor.Tipo.FACTURACION:
            return [max(self.facturacion.get(d.pk, CERO), CERO) for d in destinos]
        if inductor.tipo == Inductor.Tipo.COSTO_DIRECTO:
            return [max(self.costo_directo.get(d.pk, CERO), CERO) for d in destinos]
        raise ErrorMotor(f"Tipo de inductor no soportado: {inductor.tipo}")

    # ------------------------------------------------------------------ 1ª distribución
    def _asignar_movimiento(self, mov, recurso):
        """Devuelve lista de dicts de asignación o None si queda pendiente (registrando el motivo)."""
        valor = mov.valor
        if mov.centro_id:
            centro = self.centros.get(mov.centro_id)
            if centro is None:
                self._pendiente(1, recurso, None, mov, valor, f"Centro {mov.centro_origen} inactivo.")
                return None
            if recurso.clases_permitidas and centro.clase not in lista_clases(recurso.clases_permitidas):
                self.advertencias.append(f"{recurso.codigo} asignado directamente a {centro.codigo} ({centro.clase}); "
                                         f"el recurso admite {recurso.clases_permitidas}.")
            return [dict(destino=centro, metodo=Asignacion.Metodo.DIRECTO, valor=valor, base=valor)]
        if recurso.tipo_mano_obra and mov.tercero_documento:
            reparto = self.mano_obra.get((recurso.tipo_mano_obra, mov.tercero_documento)) or \
                self.mano_obra.get(("OPS" if recurso.tipo_mano_obra == "EMPLEADO" else "EMPLEADO", mov.tercero_documento))
            reparto = [(self.centros[c], pct) for c, pct in (reparto or []) if c in self.centros and pct > 0]
            if reparto:
                total = sum((p for _, p in reparto), CERO)
                partes = prorratear(valor, [p for _, p in reparto])
                return [dict(destino=c, metodo=Asignacion.Metodo.MANO_OBRA, valor=v, base=valor, cantidad=p, total=total,
                             tasa=_tasa(valor, total)) for (c, p), v in zip(reparto, partes) if v]
        regla = self.regla(1, recurso_id=recurso.pk)
        if regla is None:
            self._pendiente(1, recurso, None, mov, valor,
                            f"Movimiento sin centro de costos y sin regla de 1ª distribución para {recurso.codigo}.")
            return None
        if regla.misma_sede and not mov.sede_id:
            self._pendiente(1, recurso, None, mov, valor, "La regla reparte por sede y el movimiento no trae sede.")
            return None
        llave = (regla.pk, mov.sede_id if regla.misma_sede else None, recurso.pk)
        if llave not in self._cache_pesos:
            destinos = self.destinos(regla, sede_id=mov.sede_id, recurso=recurso)
            pesos = self.pesos(regla.inductor, None, destinos)
            self._cache_pesos[llave] = [(d, w) for d, w in zip(destinos, pesos) if w > 0]
        pares = self._cache_pesos[llave]
        if not pares:
            self._pendiente(1, recurso, None, mov, valor,
                            f"El inductor {regla.inductor.codigo} no tiene valores para los centros destino de {recurso.codigo}.")
            return None
        total = sum((w for _, w in pares), CERO)
        partes = prorratear(valor, [w for _, w in pares])
        return [dict(destino=d, metodo=Asignacion.Metodo.INDUCTOR, valor=v, base=valor, cantidad=w, total=total,
                     tasa=_tasa(valor, total), regla=regla, inductor=regla.inductor)
                for (d, w), v in zip(pares, partes) if v]

    def primera(self):
        movs = list(MovimientoContable.objects.de(self.inst).filter(periodo=self.periodo).order_by("pk"))
        if not movs:
            raise ErrorMotor(f"No hay movimientos cargados para {self.periodo}.")
        por_recurso = defaultdict(lambda: {"EF": [], "AREA": []})
        for m in movs:
            por_recurso[m.recurso_id][m.fuente].append(m)
        self.conciliaciones = []
        self.total_entrada = CERO
        for recurso_id in sorted(por_recurso, key=lambda r: (self.recursos[r].orden, self.recursos[r].codigo)):
            recurso = self.recursos[recurso_id]
            ef, area = por_recurso[recurso_id]["EF"], por_recurso[recurso_id]["AREA"]
            items = area or ef
            filas = []
            for mov in items:
                for a in self._asignar_movimiento(mov, recurso) or []:
                    a["movimiento"] = mov
                    filas.append(a)
            total_items = sum((m.valor for m in items), CERO)
            entrada = total_items
            if area and ef:
                total_ef = sum((m.valor for m in ef), CERO)
                diferencia = total_ef - total_items
                entrada = total_ef
                positivas = [a for a in filas if a["valor"] > 0]
                if diferencia and positivas:
                    for a, ajuste in zip(positivas, prorratear(diferencia, [a["valor"] for a in positivas])):
                        a["ajuste"] = ajuste
                        a["valor"] += ajuste
                elif diferencia:
                    self._pendiente(1, recurso, None, None, diferencia, "Diferencia de conciliación sin asignaciones de área.")
                self.conciliaciones.append({"recurso": recurso.codigo, "estado_financiero": str(total_ef),
                                            "soportes_area": str(total_items), "diferencia": str(diferencia)})
            self.total_entrada += entrada
            for a in filas:
                self._agregar(1, recurso, None, a)
        self.costo_directo = {c: sum(r.values(), CERO) for c, r in self.acumulado.items()}

    # ------------------------------------------------------------------ 2ª a 4ª distribución
    def secuencial(self, distribucion):
        clase_origen = CLASE_DE_DISTRIBUCION[distribucion]
        origenes = sorted((c for c in self.centros.values() if c.clase == clase_origen), key=lambda c: c.codigo)
        for origen in origenes:
            pool = {r: v for r, v in self.acumulado.get(origen.pk, {}).items() if v}
            total_pool = sum(pool.values(), CERO)
            if not total_pool:
                continue
            regla = self.regla(distribucion, centro=origen)
            if regla is None:
                self._pendiente(distribucion, None, origen, None, total_pool,
                                f"El centro {origen.codigo} no tiene regla de {distribucion}ª distribución.")
                continue
            destinos = self.destinos(regla, clase_origen=clase_origen, sede_id=origen.sede_id)
            pares = [(d, w) for d, w in zip(destinos, self.pesos(regla.inductor, origen.pk, destinos)) if w > 0]
            if not pares:
                self._pendiente(distribucion, None, origen, None, total_pool,
                                f"El inductor {regla.inductor.codigo} no tiene valores para los destinos de {origen.codigo}.")
                continue
            total_w = sum((w for _, w in pares), CERO)
            for recurso_id, valor in sorted(pool.items(), key=lambda kv: self.recursos[kv[0]].orden):
                recurso = self.recursos[recurso_id]
                for (d, w), parte in zip(pares, prorratear(valor, [w for _, w in pares])):
                    if parte:
                        self._agregar(distribucion, recurso, origen, dict(
                            destino=d, metodo=Asignacion.Metodo.INDUCTOR, valor=parte, base=valor, cantidad=w,
                            total=total_w, tasa=_tasa(valor, total_w), regla=regla, inductor=regla.inductor))
                self.acumulado[origen.pk][recurso_id] = CERO

    # ------------------------------------------------------------------ costeo CUPS
    def costeo_cups(self, corrida):
        por_centro = defaultdict(list)
        for (c, cups), cant in self.detalle_produccion.items():
            if cant > 0:
                por_centro[c].append((cups, cant))
        pesos_mapa = {(centro, cups): peso for cups, lista in self.mapa_cups.items() for _, centro, peso in lista}
        objetos, total_cups = [], CERO
        for centro in sorted((c for c in self.centros.values() if c.clase == Clase.FINALES), key=lambda c: c.codigo):
            por_rec = self.acumulado.get(centro.pk, {})
            costo = sum(por_rec.values(), CERO)
            if not costo:
                continue
            items = sorted(por_centro.get(centro.pk, []))
            if not items:
                self.pendientes.append(Pendiente(institucion=self.inst, corrida=corrida, distribucion=5, centro=centro,
                                                 valor=costo, motivo="Centro final con costo y sin producción en el periodo."))
                continue
            por_elemento = defaultdict(lambda: CERO)
            for r, v in por_rec.items():
                por_elemento[self.recursos[r].elemento.codigo] += v
            unidades = [cant * Decimal(pesos_mapa.get((centro.pk, cups), 1)) for cups, cant in items]
            if sum(unidades, CERO) <= 0:
                unidades = [cant for _, cant in items]
            for (cups, cant), u, parte in zip(items, unidades, prorratear(costo, unidades)):
                fraccion = parte / costo if costo else CERO
                objetos.append(CostoCups(
                    institucion=self.inst, corrida=corrida, centro=centro, cups_id=cups, cantidad=cant,
                    peso_relativo=(u / cant) if cant else 1, unidades_ponderadas=u, costo_total=parte,
                    costo_unitario=(parte / cant).quantize(CENT) if cant else CERO,
                    costo_por_elemento={k: str((v * fraccion).quantize(CENT)) for k, v in por_elemento.items()},
                    fuente_produccion=self.fuente_produccion,
                ))
                total_cups += parte
        CostoCups.objects.bulk_create(objetos, batch_size=5000)
        return len(objetos), total_cups

    # ------------------------------------------------------------------ utilidades
    def _agregar(self, distribucion, recurso, origen, a):
        self.acumulado[a["destino"].pk][recurso.pk] += a["valor"]
        self.asignaciones.append(dict(distribucion=distribucion, recurso=recurso, origen=origen, **a))

    def _pendiente(self, distribucion, recurso, centro, mov, valor, motivo):
        self.pendientes.append(Pendiente(institucion=self.inst, distribucion=distribucion, recurso=recurso, centro=centro,
                                         movimiento=mov, valor=valor, motivo=motivo[:300]))

    def huella(self):
        datos = {
            "reglas": sorted((r.pk, r.distribucion, r.recurso_id, r.unidad_negocio_id, r.centro_origen_id, r.periodo_id,
                              r.inductor_id, r.clases_destino, r.misma_sede) for lista in self.reglas.values() for r in lista),
            "valores": sorted((str(k), str(sorted(v["P"].items())), str(sorted(v["F"].items()))) for k, v in self.valores.items()),
            "mano_obra": sorted((str(k), str(v)) for k, v in self.mano_obra.items()),
            "centros": sorted((c.pk, c.clase, c.sede_id) for c in self.centros.values()),
        }
        return hashlib.sha256(json.dumps(datos, default=str).encode()).hexdigest()

    # ------------------------------------------------------------------ ejecución
    def ejecutar(self):
        if self.periodo.cerrado:
            raise ErrorMotor(f"El periodo {self.periodo} está cerrado.")
        t0 = time.monotonic()
        with transaction.atomic():
            self._cargar()
            self.primera()
            for d in (2, 3, 4):
                self.secuencial(d)
            Corrida.objects.de(self.inst).filter(periodo=self.periodo, vigente=True).update(vigente=False)
            corrida = Corrida.objects.create(institucion=self.inst, periodo=self.periodo, estado=Corrida.Estado.OK,
                                             usuario=self.usuario, huella_parametros=self.huella())
            Asignacion.objects.bulk_create([
                Asignacion(institucion=self.inst, corrida=corrida, distribucion=a["distribucion"], recurso=a["recurso"],
                           centro_origen=a["origen"], centro_destino=a["destino"], movimiento=a.get("movimiento"),
                           metodo=a["metodo"], regla=a.get("regla"), inductor=a.get("inductor"), base=a["base"],
                           cantidad_inductor=a.get("cantidad", CERO), total_inductor=a.get("total", CERO),
                           tasa=a.get("tasa", CERO), ajuste_conciliacion=a.get("ajuste", CERO), valor=a["valor"])
                for a in self.asignaciones
            ], batch_size=5000)
            n_cups, total_cups = self.costeo_cups(corrida)
            for p in self.pendientes:
                p.corrida = corrida
            Pendiente.objects.bulk_create(self.pendientes, batch_size=2000)
            corrida.resumen = self._resumen(n_cups, total_cups, time.monotonic() - t0)
            corrida.estado = Corrida.Estado.CON_PENDIENTES if corrida.resumen["pendiente_asignacion"] != "0.00" else Corrida.Estado.OK
            corrida.fin = timezone.now()
            corrida.save()
            antiguas = list(Corrida.objects.de(self.inst).filter(periodo=self.periodo).order_by("-inicio")
                            .values_list("pk", flat=True)[CORRIDAS_CONSERVADAS:])
            if antiguas:
                Corrida.objects.filter(pk__in=antiguas).delete()
            registrar("EJECUTAR_MOTOR", institucion=self.inst, objeto=corrida, usuario=self.usuario,
                      descripcion=f"Distribuciones 1ª-4ª y costeo CUPS {self.periodo}",
                      detalle={k: corrida.resumen[k] for k in ("total_entrada", "total_finales", "pendiente_asignacion", "diferencia")})
        return corrida

    def _resumen(self, n_cups, total_cups, segundos):
        por_dist = defaultdict(lambda: CERO)
        filas_dist = defaultdict(int)
        for a in self.asignaciones:
            por_dist[a["distribucion"]] += a["valor"]
            filas_dist[a["distribucion"]] += 1
        finales = sum((v for c, r in self.acumulado.items() if self.centros[c].clase == Clase.FINALES for v in r.values()), CERO)
        pendiente = sum((p.valor for p in self.pendientes if p.distribucion <= 4), CERO)
        sin_produccion = sum((p.valor for p in self.pendientes if p.distribucion == 5), CERO)
        diferencia = self.total_entrada - finales - pendiente
        return {
            "total_entrada": str(self.total_entrada.quantize(CENT)),
            "total_finales": str(finales.quantize(CENT)),
            "pendiente_asignacion": str(pendiente.quantize(CENT)),
            "diferencia": str(diferencia.quantize(CENT)),
            "conservacion_ok": diferencia.quantize(CENT) == 0,
            "valor_por_distribucion": {str(k): str(v) for k, v in sorted(por_dist.items())},
            "filas_por_distribucion": {str(k): v for k, v in sorted(filas_dist.items())},
            "pendientes": len([p for p in self.pendientes if p.distribucion <= 4]),
            "finales_sin_produccion": str(sin_produccion.quantize(CENT)),
            "cups_costeados": n_cups,
            "total_costo_cups": str(total_cups.quantize(CENT)),
            "fuente_produccion": self.fuente_produccion,
            "conciliaciones": self.conciliaciones,
            "advertencias": sorted(set(self.advertencias))[:200],
            "segundos": round(segundos, 2),
        }


def ejecutar(periodo, usuario=None):
    return Motor(periodo, usuario).ejecutar()

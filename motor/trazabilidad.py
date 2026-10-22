"""Trazabilidad: descompone el costo de un centro (o de un CUPS) hasta los movimientos fuente.

Cada peso recibido en la 2ª–4ª distribución proviene del acumulado del centro origen; se expande
proporcionalmente hasta llegar a asignaciones de 1ª distribución, que apuntan al movimiento contable,
y de allí a la carga (archivo, hoja, fila) y al periodo.
"""
from collections import defaultdict
from decimal import Decimal

from .models import Asignacion

CERO = Decimal("0")


class Trazador:
    def __init__(self, corrida):
        self.corrida = corrida
        self.recibido = defaultdict(list)  # destino → [(distribucion, origen, recurso, valor, inductor)]
        self.directo = defaultdict(list)  # destino → [(movimiento_id, recurso, valor, metodo, inductor)]
        self.salida = defaultdict(lambda: CERO)
        for a in Asignacion.objects.filter(corrida=corrida).values_list(
            "distribucion", "centro_origen_id", "centro_destino_id", "recurso_id", "valor", "movimiento_id", "metodo",
            "inductor__codigo"
        ):
            dist, origen, destino, recurso, valor, mov, metodo, inductor = a
            if dist == 1:
                self.directo[destino].append((mov, recurso, valor, metodo, inductor))
            else:
                self.recibido[destino].append((dist, origen, recurso, valor, inductor))
                self.salida[(origen, recurso)] += valor
        self._memo = {}

    def fuentes(self, centro_id):
        """{(movimiento_id, recurso_id, ruta): valor} que compone el costo acumulado del centro."""
        if centro_id in self._memo:
            return self._memo[centro_id]
        res = defaultdict(lambda: CERO)
        for mov, recurso, valor, metodo, inductor in self.directo.get(centro_id, []):
            res[(mov, recurso, (f"1ª {metodo.lower()}{' ' + inductor if inductor else ''}",))] += valor
        for dist, origen, recurso, valor, inductor in self.recibido.get(centro_id, []):
            total_origen_rec = self._acumulado_origen(origen, recurso)
            if not total_origen_rec:
                continue
            factor = valor / total_origen_rec
            for (mov, rec, ruta), v in self.fuentes(origen).items():
                if rec == recurso:
                    res[(mov, rec, ruta + (f"{dist}ª {self._codigo(origen)} ({inductor})",))] += v * factor
        self._memo[centro_id] = dict(res)
        return self._memo[centro_id]

    def _acumulado_origen(self, origen, recurso):
        return self.salida.get((origen, recurso), CERO)

    def _codigo(self, centro_id):
        if not hasattr(self, "_codigos"):
            from modelo.models import CentroCosto

            self._codigos = dict(CentroCosto.objects.filter(institucion=self.corrida.institucion).values_list("pk", "codigo"))
        return self._codigos.get(centro_id, "?")


def trazar_centro(corrida, centro, factor=Decimal("1"), limite=None):
    """Lista de contribuciones ordenada de mayor a menor, con datos del movimiento y su archivo."""
    from cargas.models import MovimientoContable
    from modelo.models import Recurso

    fuentes = Trazador(corrida).fuentes(centro.pk)
    movs = {m.pk: m for m in MovimientoContable.objects.filter(pk__in={k[0] for k in fuentes if k[0]})
            .select_related("carga", "periodo", "centro")}
    recursos = {r.pk: r for r in Recurso.objects.filter(pk__in={k[1] for k in fuentes})}
    filas = []
    for (mov_id, rec_id, ruta), valor in fuentes.items():
        m = movs.get(mov_id)
        filas.append({
            "valor": (valor * factor).quantize(Decimal("0.01")),
            "recurso": recursos[rec_id],
            "ruta": " → ".join(ruta + (centro.codigo,)),
            "movimiento": m,
            "archivo": m.carga.nombre_original if m else "",
            "hoja": m.carga.hoja if m else "",
            "fila": m.fila if m else None,
            "cuenta": m.cuenta if m else "",
            "periodo": m.periodo if m else corrida.periodo,
        })
    filas.sort(key=lambda f: -abs(f["valor"]))
    return filas[:limite] if limite else filas

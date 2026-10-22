"""Carga del catálogo base (seed/res002_2014_base.json) en una institución.

El catálogo base es un punto de partida editable: cada institución lo ajusta en Parametrización.
"""
import json
from pathlib import Path

from django.conf import settings
from django.db import transaction

from cargas.esquemas import ESQUEMAS
from cargas.models import PlantillaMapeo
from core.auditoria import registrar

from .models import (
    Ambito, AmbitoHomologacion, CentroCosto, ConfiguracionInforme, CuentaRecurso, Elemento, IndicadorReferencia,
    Inductor, Recurso, ReglaDistribucion, UnidadNegocio,
)

RUTA_BASE = Path(settings.BASE_DIR) / "seed" / "res002_2014_base.json"


def crear_plantillas_estandar(institucion):
    creadas = 0
    for esq in ESQUEMAS.values():
        _, nuevo = PlantillaMapeo.objects.get_or_create(
            institucion=institucion, nombre=f"Estándar · {esq.nombre}"[:120],
            defaults={"tipo_archivo": esq.codigo, "hoja": "", "mapeo": {c.nombre: c.etiqueta for c in esq.campos},
                      "es_estandar": True},
        )
        creadas += nuevo
    return creadas


@transaction.atomic
def cargar_catalogo_base(institucion, ruta=None, usuario=None):
    datos = json.loads(Path(ruta or RUTA_BASE).read_text(encoding="utf-8"))
    inst = institucion
    cont = {}
    elementos = {}
    for e in datos["elementos"]:
        elementos[e["codigo"]], _ = Elemento.objects.get_or_create(institucion=inst, codigo=e["codigo"],
                                                                    defaults={"nombre": e["nombre"], "orden": e["orden"]})
    recursos = {}
    for r in datos["recursos"]:
        recursos[r["codigo"]], _ = Recurso.objects.get_or_create(
            institucion=inst, codigo=r["codigo"],
            defaults={"nombre": r["nombre"], "elemento": elementos[r["elemento"]], "orden": r["orden"],
                      "inductor_convenido": r["inductor_convenido"], "tipo_mano_obra": r["tipo_mano_obra"],
                      "clases_permitidas": r["clases_permitidas"]})
    unidades = {}
    for u in datos["unidades_negocio"]:
        unidades[u["codigo"]], _ = UnidadNegocio.objects.get_or_create(
            institucion=inst, codigo=u["codigo"], defaults={"nombre": u["nombre"], "clase": u["clase"], "orden": u["orden"]})
    centros = {}
    for c in datos["centros_costo"]:
        un = unidades[c["unidad_negocio"]]
        centros[c["codigo"]], _ = CentroCosto.objects.get_or_create(
            institucion=inst, codigo=c["codigo"], defaults={"nombre": c["nombre"], "unidad_negocio": un, "clase": un.clase})
    inductores = {}
    for i in datos["inductores"]:
        inductores[i["codigo"]], _ = Inductor.objects.get_or_create(
            institucion=inst, codigo=i["codigo"],
            defaults={"nombre": i["nombre"], "tipo": i["tipo"], "unidad_medida": i["unidad_medida"]})
    if not ReglaDistribucion.objects.de(inst).exists():
        for r in datos["reglas"]:
            ReglaDistribucion.objects.create(
                institucion=inst, distribucion=r["distribucion"], recurso=recursos.get(r.get("recurso")),
                centro_origen=centros.get(r.get("centro_origen")), inductor=inductores[r["inductor"]],
                clases_destino=r["clases_destino"], misma_sede=r.get("misma_sede", False), observacion=r.get("observacion", ""))
    for c in datos["cuentas_recurso"]:
        CuentaRecurso.objects.get_or_create(institucion=inst, prefijo=c["prefijo"],
                                            defaults={"recurso": recursos[c["recurso"]], "descripcion": c["descripcion"][:200]})
    for a in datos["ambitos"]:
        ambito, _ = Ambito.objects.get_or_create(institucion=inst, codigo=a["codigo"], defaults={"nombre": a["nombre"]})
        for h in a.get("homologaciones", []):
            AmbitoHomologacion.objects.get_or_create(institucion=inst, valor_origen=h, defaults={"ambito": ambito})
    for i in datos["indicadores"]:
        IndicadorReferencia.objects.get_or_create(
            institucion=inst, codigo=i["codigo"],
            defaults={k: i[k] for k in ("nombre", "formula", "minimo", "maximo", "es_valor", "accion_recomendada", "fuente", "orden")})
    ConfiguracionInforme.objects.get_or_create(institucion=inst)
    cont["plantillas"] = crear_plantillas_estandar(inst)
    cont.update(elementos=len(elementos), recursos=len(recursos), unidades=len(unidades), centros=len(centros),
                inductores=len(inductores))
    registrar("CATALOGO_BASE", institucion=inst, usuario=usuario, descripcion="Carga del catálogo base Res. 002/2014",
              detalle=cont)
    return cont

"""Importa el maestro de homologación CECO (estado financiero → resolución) a la parametrización ABC.

El maestro (HOMOLOGACION_CECO_EF_vs_RESOLUCION, ver homologacion_ceco/) define para cada CECO del software
contable su código de resolución, sede/IPS, tipo y estado, y la llave CECO|SEDE «para costear un mismo
servicio por IPS». Esta importación:

1. agrega al catálogo los códigos de resolución que use el maestro y no existan (unidad de negocio por la
   letra del código, igual que el resto del catálogo);
2. crea las **sedes** (sigla = primer token de la sede: ESE01, HSLA…);
3. crea un **centro por llave** (p. ej. ``A10|HSLA`` · MEDICINA GENERAL · HSLA) con la unidad de negocio del
   centro base, la sede y la clase según el tipo del maestro (PRODUCTIVO → FINALES, APOYO ASISTENCIAL →
   INTERMEDIOS, APOYO LOGÍSTICO → SERVICIOS, ADMINISTRATIVO → ESTRUCTURA);
4. registra la **homologación** código E.F. (y nombre E.F. si no se repite) → centro por llave, solo para los
   CECO en estado ACTIVO; los VERIFICAR / NO APLICA / EXCLUIR no se homologan (sus cargas quedan con error
   visible, no se cargan en silencio) y se retira una homologación previa si el maestro cambió de estado;
5. copia las **reglas de distribución** que tienen centro de origen base a cada centro por llave derivado;
6. opcionalmente desactiva los centros base (sin sede), que quedan como plantilla;
7. carga la parametrización **institucional de cuentas → recurso** (seed/institucional/).

Es idempotente: volver a ejecutarla con un maestro actualizado ajusta lo necesario.
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

from django.conf import settings
from django.db import transaction

from core.auditoria import registrar

from .models import CentroCosto, Clase, CuentaRecurso, HomologacionCentro, Recurso, ReglaDistribucion, Sede, UnidadNegocio

RUTA_CUENTAS_INSTITUCIONAL = Path(settings.BASE_DIR) / "seed" / "institucional" / "ese_moreno_clavijo.json"
TIPO_A_CLASE = {
    "PRODUCTIVO (final)": Clase.FINALES,
    "APOYO ASISTENCIAL (intermedio)": Clase.INTERMEDIOS,
    "APOYO LOGÍSTICO": Clase.SERVICIOS,
    "ADMINISTRATIVO": Clase.ESTRUCTURA,
}
SEPARADOR = "|"


class ErrorImportacion(Exception):
    pass


def _clave_texto(s):
    from cargas.limpieza import clave

    return clave(s)


@transaction.atomic
def importar_maestro(institucion, ruta=None, contenido=None, nombre="", cuentas=RUTA_CUENTAS_INSTITUCIONAL,
                     desactivar_base=True, usuario=None):
    """Aplica el maestro a la institución. `ruta` o `contenido` (bytes de un .xlsx); por defecto, el incluido."""
    from homologacion_ceco import config as HC
    from homologacion_ceco.referencia import ErrorReferencia, cargar_referencia

    inst = institucion
    if not UnidadNegocio.objects.de(inst).exists():
        raise ErrorImportacion("Cargue primero el catálogo base (Parametrización → Cuatro niveles → Cargar catálogo base).")
    temporal = None
    if contenido is not None:
        import tempfile

        temporal = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        temporal.write(contenido)
        temporal.close()
        ruta = temporal.name
    try:
        ref = cargar_referencia(ruta)
    except ErrorReferencia as exc:
        raise ErrorImportacion(str(exc)) from exc
    finally:
        if temporal is not None:
            Path(temporal.name).unlink(missing_ok=True)

    res = Counter()
    centros = {c.codigo: c for c in CentroCosto.objects.de(inst).select_related("unidad_negocio")}
    por_letra = defaultdict(Counter)
    for c in centros.values():
        if SEPARADOR not in c.codigo and c.codigo[:1].isalpha():
            por_letra[c.codigo[:1]][c.unidad_negocio_id] += 1
    unidades = {u.pk: u for u in UnidadNegocio.objects.de(inst)}
    nombres_res = dict(zip(ref.resolucion["cod_res"], ref.resolucion["nombre_res"]))
    activos = ref.maestro[ref.maestro["estado"] == HC.ACTIVO]
    sin_unidad = []

    # 1. Códigos de resolución usados por el maestro que faltan en el catálogo.
    for cod in sorted(set(activos["cod_res"])):
        if cod in centros:
            continue
        letra = por_letra.get(cod[:1])
        if not letra:
            sin_unidad.append(cod)
            continue
        un = unidades[letra.most_common(1)[0][0]]
        centros[cod] = CentroCosto.objects.create(institucion=inst, codigo=cod, nombre=nombres_res.get(cod, cod)[:200],
                                                  unidad_negocio=un)
        res["centros_base_creados"] += 1
    if sin_unidad:
        raise ErrorImportacion(f"No se pudo asignar unidad de negocio a: {', '.join(sin_unidad)}. Créelos en Centros de costos.")

    # 2. Sedes (sigla = primer token, como la llave del maestro).
    sedes = {s.codigo: s for s in Sede.objects.de(inst)}
    for sede_larga in activos["sede"].drop_duplicates():
        sigla = HC.sede_corta(sede_larga)
        if sigla and sigla not in sedes:
            sedes[sigla] = Sede.objects.create(institucion=inst, codigo=sigla[:30], nombre=sede_larga[:200])
            res["sedes_creadas"] += 1

    # 3. Un centro por llave CECO|SEDE.
    por_llave = defaultdict(list)
    for r in activos.itertuples(index=False):
        if r.cod_res and HC.sede_corta(r.sede):
            por_llave[r.llave].append(r)
    variantes = defaultdict(list)   # código base → [centros por llave]
    for llave, filas in sorted(por_llave.items()):
        cod_res, sigla = llave.split(SEPARADOR, 1)
        base = centros[cod_res]
        tipo = Counter(f.tipo for f in filas).most_common(1)[0][0]
        clase = TIPO_A_CLASE.get(tipo) or base.unidad_negocio.clase  # el modelo guarda la clase explícita
        datos = {"nombre": f"{nombres_res.get(cod_res, base.nombre)} · {sigla}"[:200], "unidad_negocio": base.unidad_negocio,
                 "clase": clase, "sede": sedes[sigla], "activo": True}
        centro = centros.get(llave)
        if centro is None:
            centro = centros[llave] = CentroCosto.objects.create(institucion=inst, codigo=llave, **datos)
            res["centros_por_sede_creados"] += 1
        else:
            cambios = {k: v for k, v in datos.items() if getattr(centro, k) != v}
            if cambios:
                for k, v in cambios.items():
                    setattr(centro, k, v)
                centro.save()
                res["centros_por_sede_actualizados"] += 1
        variantes[cod_res].append(centro)

    # 4. Homologación código E.F. (y nombre si es único) → centro por llave; retirar las de CECO no activos.
    destino = {}
    nombres = Counter(_clave_texto(n) for n in activos["nombre_ef"])
    for r in activos.itertuples(index=False):
        if r.llave in centros and SEPARADOR in r.llave:
            destino[r.cod_ef] = centros[r.llave]
            if r.nombre_ef and nombres[_clave_texto(r.nombre_ef)] == 1:
                destino[r.nombre_ef[:150]] = centros[r.llave]
    existentes = {h.codigo_origen: h for h in HomologacionCentro.objects.de(inst)}
    for origen, centro in destino.items():
        h = existentes.get(origen)
        if h is None:
            HomologacionCentro.objects.create(institucion=inst, codigo_origen=origen, centro=centro)
            res["homologaciones_creadas"] += 1
        elif h.centro_id != centro.pk:
            h.centro = centro
            h.save(update_fields=["centro"])
            res["homologaciones_actualizadas"] += 1
    no_activos = set(ref.maestro.loc[ref.maestro["estado"] != HC.ACTIVO, "cod_ef"])
    retirar = [h.pk for o, h in existentes.items() if o in no_activos]
    if retirar:
        HomologacionCentro.objects.filter(pk__in=retirar).delete()
        res["homologaciones_retiradas"] += len(retirar)
    res["ceco_no_activos_sin_homologar"] = len(no_activos)

    # 5. Reglas con centro de origen base → copia para cada centro por llave.
    campos = ("distribucion", "recurso_id", "unidad_negocio_id", "periodo_id", "inductor_id", "clases_destino", "misma_sede")
    reglas = list(ReglaDistribucion.objects.de(inst).filter(centro_origen__isnull=False).select_related("centro_origen"))
    firmas = {(r.centro_origen_id,) + tuple(getattr(r, c) for c in campos) for r in reglas}
    for regla in reglas:
        for centro in variantes.get(regla.centro_origen.codigo, []):
            firma = (centro.pk,) + tuple(getattr(regla, c) for c in campos)
            if firma in firmas:
                continue
            ReglaDistribucion.objects.create(
                institucion=inst, centro_origen=centro, activo=regla.activo,
                observacion=f"Copia de la regla de {regla.centro_origen.codigo} para {centro.codigo}"[:200],
                **{c: getattr(regla, c) for c in campos})
            firmas.add(firma)
            res["reglas_copiadas"] += 1

    # 6. Centros base (sin sede) como plantilla: se desactivan para que no reciban asignaciones.
    if desactivar_base:
        base_activos = [c for c in centros.values() if SEPARADOR not in c.codigo and c.activo]
        CentroCosto.objects.filter(pk__in=[c.pk for c in base_activos]).update(activo=False)
        res["centros_base_desactivados"] = len(base_activos)

    # 7. Cuentas contables → recurso de la institución.
    if cuentas:
        datos = json.loads(Path(cuentas).read_text(encoding="utf-8"))
        recursos = {r.codigo: r for r in Recurso.objects.de(inst)}
        for c in datos["cuentas_recurso"]:
            rec = recursos.get(c.get("recurso") or "")
            obj, creada = CuentaRecurso.objects.get_or_create(
                institucion=inst, prefijo=c["prefijo"],
                defaults={"recurso": rec, "excluir": c.get("excluir", False), "descripcion": c["descripcion"][:200]})
            res["cuentas_creadas" if creada else "cuentas_existentes"] += 1

    resumen = {k: int(v) for k, v in sorted(res.items())}
    resumen.update({"maestro": nombre or ref.ruta.name, "huella_maestro": ref.huella, "ceco_activos": len(activos),
                    "llaves": len(por_llave)})
    registrar("IMPORTAR_MAESTRO_CECO", institucion=inst, usuario=usuario,
              descripcion=f"Maestro de homologación CECO {resumen['maestro']}: {len(por_llave)} centros por sede, "
                          f"{res['homologaciones_creadas'] + res['homologaciones_actualizadas']} homologaciones",
              detalle=resumen)
    return resumen

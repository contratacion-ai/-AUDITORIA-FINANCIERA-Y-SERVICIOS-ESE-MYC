"""Lógica de aplicación: gestión de archivos, procesamiento y vínculo con auditoría."""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal

import pandas as pd
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from auditoria.models import Hallazgo
from rips_conciliacion import ConfiguracionConciliacion, conciliar
from rips_conciliacion.emparejamiento import COINCIDE
from rips_conciliacion.reportes import exportar_excel, exportar_zip

from .models import ArchivoCargado, Conciliacion, Diferencia, HistorialArchivo, Incidencia

DECIMALES_BD = 6
LIMITE_TABLA = 5000


# ---------------------------------------------------------------------------
# Gestión de archivos: cargar, actualizar, reemplazar, eliminar, limpiar
# ---------------------------------------------------------------------------


def _historial(conciliacion, accion, archivo=None, detalle="", usuario="", nombre=None, tipo=None, sha=None):
    HistorialArchivo.objects.create(
        conciliacion=conciliacion,
        accion=accion,
        tipo=tipo or (archivo.tipo if archivo else ""),
        nombre=nombre or (archivo.nombre_original if archivo else ""),
        sha256=sha or (archivo.sha256 if archivo else ""),
        detalle=detalle,
        usuario=usuario or "",
    )


def cargar_archivos(conciliacion, tipo, archivos, sede="", hospital="", ambito="", usuario=""):
    """Guarda archivos nuevos. Devuelve (creados, duplicados).

    Un archivo con el mismo contenido (SHA-256) que uno ya cargado en la
    misma conciliación no se vuelve a cargar: se informa como duplicado.
    """
    creados, duplicados = [], []
    for subido in archivos:
        sha = ArchivoCargado.calcular_hash(subido)
        existente = conciliacion.archivos.filter(sha256=sha).first()
        if existente:
            duplicados.append((subido.name, existente.nombre_original))
            continue
        obj = ArchivoCargado(
            conciliacion=conciliacion, tipo=tipo, nombre_original=subido.name,
            sede=sede or "", hospital=hospital or "", ambito=ambito or "", sha256=sha, tamano=subido.size,
        )
        obj.archivo.save(subido.name, subido, save=False)
        obj.save()
        _historial(conciliacion, HistorialArchivo.Accion.CARGA, obj, usuario=usuario)
        creados.append(obj)
    if creados:
        conciliacion.marcar_desactualizada()
    return creados, duplicados


def reemplazar_archivo(archivo: ArchivoCargado, nuevo, usuario=""):
    """Sustituye el contenido de un archivo conservando su registro y su asignación de sede/hospital."""
    sha = ArchivoCargado.calcular_hash(nuevo)
    if sha == archivo.sha256:
        return False
    anterior = archivo.nombre_original
    archivo.archivo.delete(save=False)
    archivo.archivo.save(nuevo.name, nuevo, save=False)
    archivo.nombre_original = nuevo.name
    archivo.sha256 = sha
    archivo.tamano = nuevo.size
    archivo.version += 1
    archivo.save()
    _historial(archivo.conciliacion, HistorialArchivo.Accion.REEMPLAZO, archivo, usuario=usuario,
               detalle=f"Reemplaza a '{anterior}' (versión {archivo.version})")
    archivo.conciliacion.marcar_desactualizada()
    return True


def actualizar_archivo(archivo: ArchivoCargado, usuario="", **campos):
    cambios = []
    for campo in ("tipo", "sede", "hospital", "ambito", "activo"):
        if campo in campos and getattr(archivo, campo) != campos[campo]:
            cambios.append(f"{campo}: '{getattr(archivo, campo)}' → '{campos[campo]}'")
            setattr(archivo, campo, campos[campo])
    if cambios:
        archivo.save()
        _historial(archivo.conciliacion, HistorialArchivo.Accion.ACTUALIZACION, archivo, usuario=usuario,
                   detalle="; ".join(cambios))
        archivo.conciliacion.marcar_desactualizada()
    return cambios


def eliminar_archivo(archivo: ArchivoCargado, usuario=""):
    conciliacion = archivo.conciliacion
    _historial(conciliacion, HistorialArchivo.Accion.ELIMINACION, archivo, usuario=usuario)
    archivo.delete()  # la señal post_delete borra el archivo físico
    conciliacion.marcar_desactualizada()


def limpiar_resultados(conciliacion: Conciliacion):
    conciliacion.diferencias.all().delete()
    conciliacion.incidencias.all().delete()
    for campo in (conciliacion.archivo_excel, conciliacion.archivo_csv):
        if campo:
            campo.delete(save=False)
    conciliacion.resumen = []
    conciliacion.tablas = {}
    conciliacion.version_rips = ""
    conciliacion.procesada = None
    conciliacion.estado = Conciliacion.Estado.BORRADOR
    conciliacion.save()


def limpiar(conciliacion: Conciliacion, alcance: str, usuario="") -> int:
    """Limpia archivos cargados y/o resultados.

    ``alcance``: RIPS | FACTURACION | CATALOGO | EQUIVALENCIAS | ARCHIVOS (todos los archivos) |
    RESULTADOS | TODO (archivos y resultados).
    """
    eliminados = 0
    if alcance in ("RIPS", "FACTURACION", "CATALOGO", "EQUIVALENCIAS", "ARCHIVOS", "TODO"):
        qs = conciliacion.archivos.all()
        if alcance not in ("ARCHIVOS", "TODO"):
            qs = qs.filter(tipo=alcance)
        for archivo in qs:
            archivo.delete()
            eliminados += 1
        _historial(conciliacion, HistorialArchivo.Accion.LIMPIEZA, usuario=usuario, nombre=f"Limpieza: {alcance}",
                   tipo=alcance, detalle=f"{eliminados} archivo(s) eliminados")
        conciliacion.marcar_desactualizada()
    if alcance in ("RESULTADOS", "TODO"):
        limpiar_resultados(conciliacion)
        if alcance == "RESULTADOS":
            _historial(conciliacion, HistorialArchivo.Accion.LIMPIEZA, usuario=usuario,
                       nombre="Limpieza: resultados", tipo="RESULTADOS")
    return eliminados


# ---------------------------------------------------------------------------
# Procesamiento
# ---------------------------------------------------------------------------


def _leer(archivo: ArchivoCargado) -> tuple[str, bytes]:
    with archivo.archivo.open("rb") as fh:
        return archivo.nombre_original, fh.read()


def _a_texto(v):
    if isinstance(v, Decimal):
        return str(v)
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, (bool, int, float, str)):
        return v
    return str(v)


def df_a_registros(df: pd.DataFrame, limite: int | None = LIMITE_TABLA) -> dict:
    df = df if limite is None else df.head(limite)
    return {
        "columnas": [str(c) for c in df.columns],
        "filas": [[_a_texto(v) for v in fila] for fila in df.itertuples(index=False, name=None)],
    }


def _exacto(valor, campo) -> Decimal:
    """Garantiza que el valor cabe en la BD sin redondeo."""
    v = valor if isinstance(valor, Decimal) else Decimal(0)
    if v.as_tuple().exponent < -DECIMALES_BD and v != v.quantize(Decimal(1).scaleb(-DECIMALES_BD)):
        raise ValueError(f"El valor {v} del campo {campo} tiene más de {DECIMALES_BD} decimales; "
                         "no se almacena para no redondearlo")
    return v


def _txt(v, largo):
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return str(v)[:largo]


def configuracion_de(conciliacion: Conciliacion) -> ConfiguracionConciliacion:
    return ConfiguracionConciliacion(
        separador_decimal=conciliacion.separador_decimal,
        ignorar_ceros_factura=conciliacion.ignorar_ceros_factura,
        tolerancia_valor=conciliacion.tolerancia_valor,
        top_n=conciliacion.top_n,
    )


def ejecutar_motor(conciliacion: Conciliacion):
    activos = conciliacion.archivos.filter(activo=True)
    rips = [{"entrada": _leer(a), "sede": a.sede, "hospital": a.hospital}
            for a in activos.filter(tipo=ArchivoCargado.Tipo.RIPS)]
    fact = [{"entrada": _leer(a), "sede": a.sede, "hospital": a.hospital, "ambito": a.ambito}
            for a in activos.filter(tipo=ArchivoCargado.Tipo.FACTURACION)]
    catalogo = activos.filter(tipo=ArchivoCargado.Tipo.CATALOGO).first()
    equivalencias = activos.filter(tipo=ArchivoCargado.Tipo.EQUIVALENCIAS).first()
    return conciliar(
        rips, fact, configuracion_de(conciliacion),
        catalogo_sedes=_leer(catalogo) if catalogo else None,
        equivalencias=_leer(equivalencias) if equivalencias else None,
    )


def procesar(conciliacion: Conciliacion):
    """Ejecuta la conciliación y guarda resultados, tablas y archivos de exportación."""
    activos = conciliacion.archivos.filter(activo=True)
    if not activos.filter(tipo=ArchivoCargado.Tipo.RIPS).exists() or not activos.filter(
            tipo=ArchivoCargado.Tipo.FACTURACION).exists():
        raise ValueError("Se requiere al menos un archivo de RIPS y uno de facturación activos")
    try:
        resultado = ejecutar_motor(conciliacion)
        with transaction.atomic():
            limpiar_resultados(conciliacion)
            _guardar(conciliacion, resultado)
    except Exception as exc:
        conciliacion.estado = Conciliacion.Estado.ERROR
        conciliacion.mensaje_error = f"{exc.__class__.__name__}: {exc}"
        conciliacion.save(update_fields=["estado", "mensaje_error", "actualizada"])
        raise
    return resultado


def _guardar(conciliacion: Conciliacion, r):
    filas = []
    for d in r.detalle.to_dict("records"):
        filas.append(Diferencia(
            conciliacion=conciliacion,
            estado=d["estado"], factura=_txt(d["factura"], 60), factura_clave=_txt(d["factura_clave"], 60),
            mes=_txt(d["mes"], 10), sede=_txt(d["sede"], 120), hospital=_txt(d["hospital"], 120),
            categoria=_txt(d["categoria"], 40), servicio=_txt(d["servicio"], 120),
            tipo_codigo=_txt(d["tipo_codigo"], 10), codigo=_txt(d["codigo"], 60),
            descripcion=_txt(d["descripcion"], 300),
            cantidad_fact=_exacto(d["cantidad_fact"], "cantidad_fact"),
            cantidad_rips=_exacto(d["cantidad_rips"], "cantidad_rips"),
            dif_cantidad=_exacto(d["dif_cantidad"], "dif_cantidad"),
            valor_fact=_exacto(d["valor_fact"], "valor_fact"),
            valor_rips=_exacto(d["valor_rips"], "valor_rips"),
            dif_valor=_exacto(d["dif_valor"], "dif_valor"),
            sede_fact=_txt(d["sede_fact"], 120), sede_rips=_txt(d["sede_rips"], 120),
            mes_fact=_txt(d["mes_fact"], 10), mes_rips=_txt(d["mes_rips"], 10),
            campo_codigo_fact=_txt(d["campo_codigo_fact"], 120), campo_codigo_rips=_txt(d["campo_codigo_rips"], 120),
            ubicacion_fact=_txt(f"{_txt(d['archivo_fact'], 150)} {_txt(d['ubicacion_fact'], 100)}".strip(), 300),
            ubicacion_rips=_txt(d["ubicacion_rips"], 300), archivo_rips=_txt(d["archivo_rips"], 300),
            posible_homologacion=_txt(d["posible_homologacion"], 60),
        ))
    Diferencia.objects.bulk_create(filas, batch_size=2000)
    Incidencia.objects.bulk_create([
        Incidencia(conciliacion=conciliacion, archivo=_txt(i["archivo"], 500), severidad=i["severidad"],
                   mensaje=i["mensaje"], linea=int(i["linea"]) if pd.notna(i["linea"]) else None,
                   campo=_txt(i["campo"], 300), valor=_txt(i["valor"], 2000))
        for i in r.incidencias.to_dict("records")
    ], batch_size=2000)

    tablas = {nombre: df_a_registros(df, None) for nombre, df in r.comparativos.items()}
    for nombre, df in r.consumo.items():
        tablas[nombre] = df_a_registros(df[df["top"]], None)
    tablas["Cruce de códigos"] = df_a_registros(r.cruce_codigos[r.cruce_codigos["resultado"] != "EN_AMBOS"])
    tablas["Cruce códigos por sede"] = df_a_registros(
        r.cruce_codigos_sede[r.cruce_codigos_sede["resultado"] != "EN_AMBOS"])
    tablas["Facturas con diferencia"] = df_a_registros(r.facturas[r.facturas["estado"] != "COINCIDE"])
    tablas["Archivos"] = df_a_registros(r.archivos, None)
    tablas["Otros códigos RIPS"] = df_a_registros(r.otros_codigos)

    conciliacion.resumen = [[s, i, _a_texto(v)] for s, i, v in r.resumen]
    conciliacion.tablas = tablas
    conciliacion.version_rips = r.carga_rips.version[:200]
    conciliacion.mensaje_error = ""

    xlsx = io.BytesIO()
    exportar_excel(r, xlsx)
    sello = timezone.localtime().strftime("%Y%m%d_%H%M%S")
    conciliacion.archivo_excel.save(f"conciliacion_{conciliacion.pk}_{sello}.xlsx", ContentFile(xlsx.getvalue()),
                                    save=False)
    conciliacion.archivo_csv.save(f"conciliacion_{conciliacion.pk}_{sello}_csv.zip",
                                  ContentFile(exportar_zip(r, "csv")), save=False)
    conciliacion.estado = Conciliacion.Estado.PROCESADA
    conciliacion.procesada = timezone.now()
    conciliacion.save()


def paquete_exportacion(conciliacion: Conciliacion, formato: str) -> tuple[str, bytes, str]:
    """Devuelve (nombre, contenido, content_type) para excel | csv | ambos."""
    if formato == "excel":
        with conciliacion.archivo_excel.open("rb") as fh:
            return ("conciliacion_rips_facturacion.xlsx", fh.read(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if formato == "csv":
        with conciliacion.archivo_csv.open("rb") as fh:
            return "conciliacion_rips_facturacion_csv.zip", fh.read(), "application/zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        with conciliacion.archivo_excel.open("rb") as fh:
            zf.writestr("conciliacion_rips_facturacion.xlsx", fh.read())
        with conciliacion.archivo_csv.open("rb") as fh, zipfile.ZipFile(io.BytesIO(fh.read())) as csvs:
            for nombre in csvs.namelist():
                zf.writestr(nombre, csvs.read(nombre))
    return "conciliacion_rips_facturacion.zip", buffer.getvalue(), "application/zip"


# ---------------------------------------------------------------------------
# Vínculo con la herramienta de auditoría: diferencias -> hallazgos
# ---------------------------------------------------------------------------

ACCIONES = {
    "FACTURA_NO_REPORTADA_EN_RIPS": (
        Hallazgo.Prioridad.ALTA,
        "Factura sin RIPS",
        "Generar y reportar el RIPS de la factura antes de radicar; una factura sin RIPS es causal de "
        "devolución o glosa y retrasa el recaudo.",
    ),
    "FACTURA_NO_ESTA_EN_FACTURACION": (
        Hallazgo.Prioridad.ALTA,
        "RIPS sin factura en el sistema de facturación",
        "Verificar si la factura fue anulada, si pertenece a otro periodo o si hay servicios prestados no "
        "facturados (posible pérdida de ingreso). Depurar el RIPS o facturar.",
    ),
    "CODIGO_NO_REPORTADO_EN_RIPS": (
        Hallazgo.Prioridad.MEDIA,
        "Servicio facturado no reportado en RIPS",
        "Completar el RIPS con el servicio facturado o corregir la codificación CUPS/CUMS; registrar la "
        "equivalencia si se trata de una homologación.",
    ),
    "CODIGO_NO_ESTA_EN_FACTURACION": (
        Hallazgo.Prioridad.MEDIA,
        "Servicio reportado en RIPS y no facturado",
        "Validar si el servicio se prestó y no se cobró (ingreso dejado de facturar) o retirarlo del RIPS.",
    ),
    "DIFERENCIA_VALOR": (
        Hallazgo.Prioridad.MEDIA,
        "Diferencia de valor entre factura y RIPS",
        "Revisar la tarifa contratada y ajustar el valor en la factura o en el RIPS para evitar glosa por "
        "inconsistencia.",
    ),
    "DIFERENCIA_CANTIDAD": (
        Hallazgo.Prioridad.BAJA,
        "Diferencia de cantidad entre factura y RIPS",
        "Conciliar las cantidades dispensadas/prestadas con los soportes clínicos y corregir la fuente "
        "errada.",
    ),
    "DIFERENCIA_VALOR_Y_CANTIDAD": (
        Hallazgo.Prioridad.MEDIA,
        "Diferencia de valor y cantidad entre factura y RIPS",
        "Conciliar cantidades con soportes y validar tarifa; corregir factura o RIPS.",
    ),
}


def generar_hallazgos(conciliacion: Conciliacion, valor_minimo: Decimal = Decimal(0)) -> dict:
    """Crea/actualiza un hallazgo por (factura, tipo de diferencia).

    * Conserva el estado, responsable y observaciones de hallazgos ya gestionados.
    * Si al reprocesar una diferencia desaparece, el hallazgo abierto/en gestión
      se marca como SUBSANADO automáticamente.
    """
    diferencias = list(conciliacion.diferencias.exclude(estado=COINCIDE).values(
        "estado", "factura", "factura_clave", "sede", "hospital", "mes", "categoria", "codigo",
        "dif_valor", "dif_cantidad"))
    grupos: dict[tuple, dict] = {}
    for d in diferencias:
        g = grupos.setdefault((d["factura"], d["estado"]), {
            "sede": d["sede"], "hospital": d["hospital"], "mes": d["mes"], "servicios": set(), "codigos": [],
            "valor": Decimal(0), "cantidad": Decimal(0)})
        g["servicios"].add(d["categoria"] or "")
        g["codigos"].append(d["codigo"])
        g["valor"] += d["dif_valor"]
        g["cantidad"] += d["dif_cantidad"]

    creados = actualizados = 0
    vigentes = set()
    with transaction.atomic():
        for (factura, estado), g in grupos.items():
            if abs(g["valor"]) < valor_minimo and estado not in ("FACTURA_NO_REPORTADA_EN_RIPS",
                                                                "FACTURA_NO_ESTA_EN_FACTURACION"):
                continue
            prioridad, titulo, accion = ACCIONES[estado]
            datos = {
                "auditoria": conciliacion.auditoria,
                "origen": "CONCILIACION_RIPS",
                "titulo": f"{titulo} – factura {factura}",
                "descripcion": (f"Conciliación '{conciliacion.nombre}': {len(g['codigos'])} código(s) con "
                                f"{estado.replace('_', ' ').lower()} en la factura {factura} "
                                f"(sede {g['sede']}, hospital {g['hospital']}, mes {g['mes']})."),
                "sede": g["sede"], "hospital": g["hospital"], "mes": g["mes"],
                "servicio": ", ".join(sorted(s for s in g["servicios"] if s))[:120],
                "codigos": ", ".join(g["codigos"]),
                "valor_diferencia": g["valor"], "cantidad_diferencia": g["cantidad"],
                "accion_recomendada": accion,
            }
            hallazgo, creado = Hallazgo.objects.get_or_create(
                conciliacion=conciliacion, factura=factura, tipo=estado, defaults={**datos, "prioridad": prioridad})
            vigentes.add(hallazgo.pk)
            if creado:
                creados += 1
            else:
                for k, v in datos.items():
                    setattr(hallazgo, k, v)
                if hallazgo.estado == Hallazgo.Estado.SUBSANADO:
                    hallazgo.estado = Hallazgo.Estado.ABIERTO
                    hallazgo.observaciones += "\nReabierto: la diferencia persiste tras reprocesar."
                hallazgo.save()
                actualizados += 1
        subsanados = 0
        for h in conciliacion.hallazgos.exclude(pk__in=vigentes).filter(
                estado__in=[Hallazgo.Estado.ABIERTO, Hallazgo.Estado.EN_GESTION]):
            h.estado = Hallazgo.Estado.SUBSANADO
            h.observaciones += f"\nSubsanado automáticamente: la diferencia no aparece en el procesamiento del " \
                               f"{timezone.localtime().strftime('%Y-%m-%d %H:%M')}."
            h.save()
            subsanados += 1
    return {"creados": creados, "actualizados": actualizados, "subsanados": subsanados}

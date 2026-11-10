"""Aplicación Django: gestión de archivos, procesamiento, tablero, exportación y vínculo con auditoría."""

import io
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from auditoria.models import Auditoria, Hallazgo
from conciliacion.models import ArchivoCargado, Conciliacion, Diferencia, HistorialArchivo
from utilidades import FACTURACION_CSV, archivos_3374, crear_rar, crear_zip, json_2275

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_temporal(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.CONCILIACION_SEGUNDO_PLANO = False   # las pruebas procesan en la misma petición


@pytest.fixture
def conciliacion():
    auditoria = Auditoria.objects.create(nombre="Auditoría EPS 2024", entidad="EPS PRUEBA")
    return Conciliacion.objects.create(auditoria=auditoria, nombre="Enero-Febrero 2024")


def subir(client, c, tipo, *archivos, **extra):
    return client.post(reverse("conciliacion:cargar", args=[c.pk]),
                       {"tipo": tipo, "archivos": list(archivos), **extra}, follow=True)


def zip_rips():
    return SimpleUploadedFile("RIPS_SEDE_TAME.zip", crear_zip({f"2024-01/{n}": c for n, c in archivos_3374().items()}))


def rar_rips():
    return SimpleUploadedFile("RIPS_SEDE_TAME_FEB.rar", crear_rar({"FEBRERO_2024/FE2001.json": json_2275()}))


def csv_fact(contenido=FACTURACION_CSV):
    return SimpleUploadedFile("facturacion.csv", contenido.encode())


def procesar(client, c):
    r = client.post(reverse("conciliacion:procesar", args=[c.pk]), follow=True)
    c.refresh_from_db()
    return r


def test_flujo_completo(client, conciliacion):
    c = conciliacion
    subir(client, c, "RIPS", zip_rips(), rar_rips())
    subir(client, c, "FACTURACION", csv_fact())
    assert c.archivos.count() == 3

    procesar(client, c)
    assert c.estado == Conciliacion.Estado.PROCESADA, c.mensaje_error
    assert "3374" in c.version_rips and "2275" in c.version_rips
    med = Diferencia.objects.get(conciliacion=c, codigo="19912345-1")
    assert med.dif_valor == Decimal("1000") and med.dif_cantidad == Decimal("1")
    assert med.sede == "TAME" and med.mes == "2024-01"

    # Detalle y tablero con filtros
    assert client.get(reverse("conciliacion:detalle", args=[c.pk])).status_code == 200
    r = client.get(reverse("conciliacion:tablero", args=[c.pk]), {"sede": "TAME", "mes": "2024-01"})
    assert r.status_code == 200
    assert r.context["datos"]["kpi"]["dif_valor"] == Decimal("60000.00")
    assert b"g_sede" in r.content
    r = client.get(reverse("conciliacion:tablero", args=[c.pk]), {"estado": "DIFERENCIA_VALOR"})
    assert r.context["pagina"].paginator.count == 1

    # Exportaciones: Excel, CSV, ambos y filtro
    r = client.get(reverse("conciliacion:exportar", args=[c.pk]), {"formato": "excel"})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    r = client.get(reverse("conciliacion:exportar", args=[c.pk]), {"formato": "csv"})
    assert any(n.endswith(".csv") for n in zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    r = client.get(reverse("conciliacion:exportar", args=[c.pk]), {"formato": "ambos"})
    nombres = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "conciliacion_rips_facturacion.xlsx" in nombres and any(n.startswith("csv/") for n in nombres)
    r = client.get(reverse("conciliacion:exportar_filtro", args=[c.pk]), {"formato": "csv", "sede": "TAME"})
    assert "19912345-1" in r.content.decode("utf-8-sig")


def test_archivo_duplicado_no_se_carga(client, conciliacion):
    subir(client, conciliacion, "FACTURACION", csv_fact())
    r = subir(client, conciliacion, "FACTURACION", SimpleUploadedFile("copia.csv", FACTURACION_CSV.encode()))
    assert conciliacion.archivos.count() == 1
    assert "mismo contenido" in r.content.decode()


def test_cualquier_archivo_se_acepta_y_lo_no_legible_se_reporta(client, conciliacion):
    subir(client, conciliacion, "AUTO", zip_rips(), csv_fact(), SimpleUploadedFile("soporte.pdf", b"%PDF-1.4\x00\x01"))
    assert conciliacion.archivos.count() == 3
    procesar(client, conciliacion)
    assert conciliacion.estado == Conciliacion.Estado.PROCESADA, conciliacion.mensaje_error
    assert conciliacion.incidencias.filter(mensaje__icontains="no es un archivo de datos").exists() or \
        conciliacion.incidencias.filter(mensaje__icontains="Formato no reconocido").exists()


def test_reemplazar_actualizar_eliminar_y_limpiar(client, conciliacion):
    c = conciliacion
    subir(client, c, "RIPS", zip_rips())
    subir(client, c, "FACTURACION", csv_fact())
    procesar(client, c)
    fact = c.archivos.get(tipo="FACTURACION")
    ruta_anterior = Path(fact.archivo.path)

    # Reemplazar: corrige la factura FE1002 a 30.000 (debe desaparecer esa diferencia)
    corregido = FACTURACION_CSV.replace("FE1002;20/01/2024;890201;CONSULTA MEDICINA GENERAL;1;32.000;32.000",
                                        "FE1002;20/01/2024;890201;CONSULTA MEDICINA GENERAL;1;30.000;30.000")
    client.post(reverse("conciliacion:reemplazar", args=[fact.pk]),
                {"archivo": SimpleUploadedFile("facturacion_v2.csv", corregido.encode())})
    fact.refresh_from_db()
    c.refresh_from_db()
    assert fact.version == 2 and fact.nombre_original == "facturacion_v2.csv"
    assert not ruta_anterior.exists()
    assert c.estado == Conciliacion.Estado.DESACTUALIZADA
    procesar(client, c)
    assert Diferencia.objects.get(conciliacion=c, factura="FE1002").estado == "COINCIDE"

    # Actualizar sede asignada / desactivar
    rips = c.archivos.get(tipo="RIPS")
    client.post(reverse("conciliacion:actualizar", args=[rips.pk]),
                {"tipo": "RIPS", "sede": "SEDE TAME", "hospital": "HOSPITAL SAN ANTONIO", "ambito": "",
                 "activo": "on"})
    rips.refresh_from_db()
    assert rips.sede == "SEDE TAME"
    assert HistorialArchivo.objects.filter(conciliacion=c, accion="ACTUALIZACION").exists()

    # Eliminar un archivo borra el archivo físico
    ruta = Path(rips.archivo.path)
    client.post(reverse("conciliacion:eliminar_archivo", args=[rips.pk]))
    assert not ArchivoCargado.objects.filter(pk=rips.pk).exists() and not ruta.exists()

    # Limpiar resultados y luego todo
    client.post(reverse("conciliacion:limpiar", args=[c.pk]), {"alcance": "RESULTADOS"})
    c.refresh_from_db()
    assert c.diferencias.count() == 0 and c.estado == Conciliacion.Estado.BORRADOR and not c.archivo_excel
    client.post(reverse("conciliacion:limpiar", args=[c.pk]), {"alcance": "TODO"})
    assert c.archivos.count() == 0
    assert set(HistorialArchivo.objects.filter(conciliacion=c).values_list("accion", flat=True)) >= {
        "CARGA", "REEMPLAZO", "ACTUALIZACION", "ELIMINACION", "LIMPIEZA"}


def test_procesar_sin_archivos_informa(client, conciliacion):
    r = procesar(client, conciliacion)
    assert "Se requiere al menos un archivo" in r.content.decode()


def test_vinculo_con_auditoria_hallazgos(client, conciliacion):
    c = conciliacion
    subir(client, c, "RIPS", zip_rips(), rar_rips())
    fact = subir(client, c, "FACTURACION", csv_fact()) and c.archivos.get(tipo="FACTURACION")
    procesar(client, c)
    client.post(reverse("conciliacion:hallazgos", args=[c.pk]), {"valor_minimo": "0"})
    hallazgos = Hallazgo.objects.filter(conciliacion=c)
    tipos = set(hallazgos.values_list("factura", "tipo"))
    assert ("FE9999", "FACTURA_NO_REPORTADA_EN_RIPS") in tipos
    assert ("FE2001", "FACTURA_NO_ESTA_EN_FACTURACION") in tipos
    assert ("FE1002", "DIFERENCIA_VALOR") in tipos
    h = hallazgos.get(factura="FE1002")
    assert h.valor_diferencia == Decimal("2000") and h.auditoria == c.auditoria and h.accion_recomendada

    # Gestionar un hallazgo
    client.post(reverse("auditoria:hallazgo", args=[h.pk]),
                {"estado": "EN_GESTION", "prioridad": "ALTA", "responsable": "Facturación", "observaciones": "x"})
    h.refresh_from_db()
    assert h.estado == "EN_GESTION"

    # Corregir la fuente, reprocesar y regenerar: el hallazgo pasa a SUBSANADO
    corregido = FACTURACION_CSV.replace(";1;32.000;32.000", ";1;30.000;30.000")
    client.post(reverse("conciliacion:reemplazar", args=[fact.pk]),
                {"archivo": SimpleUploadedFile("facturacion_v2.csv", corregido.encode())})
    procesar(client, c)
    client.post(reverse("conciliacion:hallazgos", args=[c.pk]), {"valor_minimo": "0"})
    h.refresh_from_db()
    assert h.estado == "SUBSANADO" and h.responsable == "Facturación"
    assert Hallazgo.objects.filter(conciliacion=c).count() == len(tipos)   # sin duplicados

    # Vistas de auditoría
    a = c.auditoria
    assert client.get(reverse("auditoria:inicio")).status_code == 200
    r = client.get(reverse("auditoria:detalle", args=[a.pk]), {"estado": "SUBSANADO"})
    assert r.status_code == 200 and "FE1002" in r.content.decode()
    r = client.get(reverse("auditoria:exportar_hallazgos", args=[a.pk]))
    assert r.content[:2] == b"PK"

    # Eliminar la conciliación conserva los hallazgos en la auditoría
    client.post(reverse("conciliacion:eliminar", args=[c.pk]))
    assert Hallazgo.objects.filter(auditoria=a).count() == len(tipos)


def test_formato_moneda_sin_redondeo():
    from conciliacion.templatetags.formato import moneda

    assert moneda(Decimal("1234567.505000")) == "1.234.567,505"
    assert moneda("-7500.25") == "-7.500,25"
    assert moneda(Decimal("0")) == "0"


def test_carga_con_deteccion_automatica_del_tipo(client, conciliacion):
    c = conciliacion
    r = subir(client, c, "AUTO", zip_rips(), rar_rips(), csv_fact(),
              SimpleUploadedFile("catalogo_sedes.csv", b"codigo_prestador;sede;hospital\n810010000101;TAME;HSA\n"),
              SimpleUploadedFile("AC000009.txt", archivos_3374()["AC000001.txt"]),
              SimpleUploadedFile("facturacion_urgencias.txt", b"Factura;Codigo;Valor\nF1;890201;100\n"))
    tipos = dict(c.archivos.values_list("nombre_original", "tipo"))
    assert tipos == {
        "RIPS_SEDE_TAME.zip": "RIPS", "RIPS_SEDE_TAME_FEB.rar": "RIPS", "facturacion.csv": "FACTURACION",
        "catalogo_sedes.csv": "CATALOGO", "AC000009.txt": "RIPS", "facturacion_urgencias.txt": "FACTURACION",
    }
    assert "cargado como" in r.content.decode()
    procesar(client, c)
    assert c.estado == Conciliacion.Estado.PROCESADA, c.mensaje_error


def test_cambiar_tipo_de_un_archivo(client, conciliacion):
    subir(client, conciliacion, "RIPS", SimpleUploadedFile("datos.txt", b"Factura;Codigo;Valor\nF1;X;1\n"))
    a = conciliacion.archivos.get()
    client.post(reverse("conciliacion:actualizar", args=[a.pk]),
                {"tipo": "FACTURACION", "sede": "", "hospital": "", "ambito": "", "activo": "on"})
    a.refresh_from_db()
    assert a.tipo == "FACTURACION"


def test_cargar_sin_archivos_muestra_mensaje(client, conciliacion):
    r = client.post(reverse("conciliacion:cargar", args=[conciliacion.pk]), {"tipo": "AUTO"}, follow=True)
    assert "Seleccione al menos un archivo" in r.content.decode()


def test_inicio_rapido_crea_auditoria_y_conciliacion(client):
    assert "Cargar datos" in client.get("/").content.decode()
    r = client.post(reverse("conciliacion:inicio_rapido"), {"nombre": "Enero", "auditoria": "", "nueva_auditoria": ""},
                    follow=True)
    c = Conciliacion.objects.get(nombre="Enero")
    assert c.auditoria.nombre.startswith("Auditoría general")
    assert "Cargar archivos seleccionados" in r.content.decode()


def test_error_inesperado_muestra_causa(client, conciliacion, monkeypatch, settings):
    from conciliacion import servicios as srv

    def falla(*a, **k):
        raise PermissionError("Acceso denegado a la carpeta de datos")

    monkeypatch.setattr(srv, "cargar_archivos", falla)
    r = subir(client, conciliacion, "AUTO", csv_fact())
    assert "Acceso denegado a la carpeta de datos" in r.content.decode()


def test_pagina_500_con_detalle(rf):
    from ese_auditoria.errores import error_500

    try:
        raise OSError("disco lleno")
    except OSError:
        r = error_500(rf.get("/"))
    assert r.status_code == 500 and "disco lleno" in r.content.decode()


@pytest.mark.django_db(transaction=True)
def test_procesamiento_en_segundo_plano_con_avance(client, settings):
    from conciliacion import servicios as srv

    settings.CONCILIACION_SEGUNDO_PLANO = True
    auditoria = Auditoria.objects.create(nombre="A")
    c = Conciliacion.objects.create(auditoria=auditoria, nombre="C")
    subir(client, c, "AUTO", zip_rips(), csv_fact())
    r = client.post(reverse("conciliacion:procesar", args=[c.pk]), follow=True)
    assert "Procesando" in r.content.decode()
    hilo = srv._EN_CURSO.get(c.pk)
    if hilo:
        hilo.join(timeout=120)
    c.refresh_from_db()
    assert c.estado == Conciliacion.Estado.PROCESADA, c.mensaje_error
    assert c.progreso_pct == 100 and c.diferencias.exists()


def test_procesamiento_interrumpido_se_informa(client, conciliacion, settings):
    settings.CONCILIACION_SEGUNDO_PLANO = True
    Conciliacion.objects.filter(pk=conciliacion.pk).update(estado=Conciliacion.Estado.PROCESANDO)
    r = client.get(reverse("conciliacion:detalle", args=[conciliacion.pk]))
    conciliacion.refresh_from_db()
    assert conciliacion.estado == Conciliacion.Estado.ERROR and "interrumpió" in r.content.decode()


def test_pagina_de_avance_se_actualiza_sola(client, conciliacion):
    Conciliacion.objects.filter(pk=conciliacion.pk).update(estado=Conciliacion.Estado.PROCESANDO,
                                                           progreso_texto="Leyendo facturación: ABRIL.xlsx",
                                                           progreso_pct=35)
    from conciliacion import servicios as srv
    import threading
    srv._EN_CURSO[conciliacion.pk] = threading.Thread(target=lambda: None)
    srv._EN_CURSO[conciliacion.pk].is_alive = lambda: True
    try:
        html = client.get(reverse("conciliacion:detalle", args=[conciliacion.pk])).content.decode()
    finally:
        srv._EN_CURSO.pop(conciliacion.pk, None)
    assert 'http-equiv="refresh"' in html and "Leyendo facturación: ABRIL.xlsx" in html and "35%" in html


def test_filtros_del_tablero_muestran_valores_distintos(client, conciliacion):
    subir(client, conciliacion, "AUTO", zip_rips(), csv_fact())
    procesar(client, conciliacion)
    r = client.get(reverse("conciliacion:tablero", args=[conciliacion.pk]))
    assert r.context["opciones"]["sede"] == ["TAME"]
    assert len(r.context["opciones"]["estado"]) == len(set(r.context["opciones"]["estado"]))

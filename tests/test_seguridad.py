"""Control de acceso por rol, aislamiento entre instituciones, bitácora y bloqueo de inicio de sesión."""
import pytest
from django.core.cache import cache
from django.urls import reverse

from core.models import Bitacora, Periodo
from modelo.models import CentroCosto
from motor.motor import ejecutar

from .conftest import entrar


def test_sin_sesion_redirige_a_login(client, caso):
    r = client.get(reverse("analisis:tablero"))
    assert r.status_code == 302 and "/cuenta/ingresar/" in r.url


def test_consultor_solo_ve_instituciones_asignadas(client, caso, usuarios):
    entrar(client, usuarios["consultor_otra"], caso.inst)  # intenta forzar una institución no asignada
    r = client.get(reverse("analisis:tablero"))
    assert r.status_code == 302 and "institucion" in r.url
    r = client.get(reverse("core:seleccionar_institucion") + "?cambiar=1")
    assert caso.inst.nombre not in r.content.decode() and usuarios["otra"].nombre in r.content.decode()
    r = client.post(reverse("core:seleccionar_institucion"), {"institucion": caso.inst.pk})
    assert r.status_code == 404


def test_objetos_de_otra_institucion_dan_404(client, caso, usuarios):
    corrida = ejecutar(caso.periodo)
    entrar(client, usuarios["consultor_otra"], usuarios["otra"])
    assert client.get(reverse("motor:corrida", args=[corrida.pk])).status_code == 404
    assert client.get(reverse("modelo:catalogo_editar", args=["centros", caso.F1.pk])).status_code == 404
    assert client.get(reverse("cargas:detalle", args=[caso.carga.pk])).status_code == 404
    assert client.get(reverse("motor:traza_centro", args=[corrida.pk, caso.F1.pk])).status_code == 404


@pytest.mark.parametrize("rol,puede", [("AUDITORIA", False), ("GERENCIA", False), ("FINANCIERO", False), ("COSTOS", True),
                                       ("CONSULTOR", True), ("ADMIN", True)])
def test_parametrizar_por_rol(client, caso, usuarios, rol, puede):
    entrar(client, usuarios[rol], caso.inst)
    url = reverse("modelo:catalogo_editar", args=["centros", caso.F1.pk])
    assert client.get(url).status_code == 200  # todos consultan
    r = client.post(url, {"codigo": "F1", "nombre": "CONSULTA EXTERNA", "unidad_negocio": caso.F1.unidad_negocio_id,
                          "clase": "FINALES", "sede": caso.sede.pk, "activo": "on"})
    assert (r.status_code == 302) == puede and (r.status_code == 403) != puede
    caso.F1.refresh_from_db()
    assert (caso.F1.nombre == "CONSULTA EXTERNA") == puede


@pytest.mark.parametrize("rol,codigo", [("GERENCIA", 403), ("AUDITORIA", 403), ("COSTOS", 302)])
def test_ejecutar_motor_por_rol(client, caso, usuarios, rol, codigo):
    entrar(client, usuarios[rol], caso.inst)
    assert client.post(reverse("motor:panel"), {"periodo": caso.periodo.pk}).status_code == codigo


@pytest.mark.parametrize("rol,codigo", [("AUDITORIA", 200), ("ADMIN", 200), ("COSTOS", 403), ("GERENCIA", 403)])
def test_bitacora_por_rol(client, caso, usuarios, rol, codigo):
    entrar(client, usuarios[rol], caso.inst)
    assert client.get(reverse("core:bitacora")).status_code == codigo


def test_cambios_quedan_en_bitacora_con_antes_y_despues(client, caso, usuarios):
    entrar(client, usuarios["COSTOS"], caso.inst)
    client.post(reverse("modelo:catalogo_editar", args=["centros", caso.F1.pk]),
                {"codigo": "F1", "nombre": "CONSULTA EXTERNA", "unidad_negocio": caso.F1.unidad_negocio_id,
                 "clase": "FINALES", "sede": caso.sede.pk, "activo": "on"})
    b = Bitacora.objects.filter(accion="EDITAR", institucion=caso.inst).latest("fecha")
    assert b.usuario == usuarios["COSTOS"]
    assert b.detalle["cambios"]["nombre"] == {"antes": "CONSULTA", "despues": "CONSULTA EXTERNA"}
    with pytest.raises(Exception):
        b.delete()


def test_formularios_no_aceptan_fk_de_otra_institucion(client, caso, usuarios):
    from modelo.models import UnidadNegocio

    un_otra = UnidadNegocio.objects.create(institucion=usuarios["otra"], codigo="X", nombre="X", clase="FINALES")
    entrar(client, usuarios["COSTOS"], caso.inst)
    r = client.post(reverse("modelo:catalogo_nuevo", args=["centros"]),
                    {"codigo": "F9", "nombre": "INTRUSO", "unidad_negocio": un_otra.pk, "clase": "FINALES"})
    assert r.status_code == 200 and not CentroCosto.objects.filter(codigo="F9").exists()


def test_periodo_cerrado_y_permiso_de_cierre(client, caso, usuarios):
    entrar(client, usuarios["COSTOS"], caso.inst)
    assert client.post(reverse("core:periodos"), {"accion": "cerrar", "periodo": caso.periodo.pk}).status_code == 403
    entrar(client, usuarios["FINANCIERO"], caso.inst)
    client.post(reverse("core:periodos"), {"accion": "cerrar", "periodo": caso.periodo.pk})
    assert Periodo.objects.get(pk=caso.periodo.pk).cerrado


def test_bloqueo_por_intentos_fallidos(client, caso, usuarios, settings):
    cache.clear()
    settings.LOGIN_MAX_INTENTOS = 3
    for _ in range(3):
        client.post(reverse("login"), {"username": "costos", "password": "mala"})
    r = client.post(reverse("login"), {"username": "costos", "password": "Clave-Segura-2025"})
    assert r.status_code == 200 and "Demasiados intentos" in r.content.decode()
    assert Bitacora.objects.filter(accion="LOGIN_FALLIDO").count() == 3
    cache.clear()
    r = client.post(reverse("login"), {"username": "costos", "password": "Clave-Segura-2025"})
    assert r.status_code == 302

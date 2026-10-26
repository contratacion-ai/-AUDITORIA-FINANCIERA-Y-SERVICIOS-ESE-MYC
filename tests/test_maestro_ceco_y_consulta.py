"""Maestro de homologación CECO en la herramienta ABC, carga directa del reporte CTRP y consulta de costos."""
from decimal import Decimal as D

import openpyxl
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from analisis import consulta as C
from analisis import consultas as Q
from cargas.models import MovimientoContable
from cargas.procesador import ErrorCargaArchivo, procesar_carga
from core.demo import crear_demo
from core.models import Institucion, Membresia, Periodo
from homologacion_ceco.ejemplo import ctrp_editado, ctrp_estado_resultados
from modelo.catalogo_base import cargar_catalogo_base
from modelo.importar_maestro import ErrorImportacion, importar_maestro
from modelo.models import CentroCosto, CuentaRecurso, HomologacionCentro, ReglaDistribucion, Sede

from .conftest import entrar


@pytest.fixture
def ese(db):
    inst = Institucion.objects.create(codigo="ESE-T", nombre="ESE de prueba")
    cargar_catalogo_base(inst)
    resumen = importar_maestro(inst)
    return inst, resumen


def test_importar_maestro(ese):
    inst, r = ese
    assert r["ceco_activos"] == 399 and r["llaves"] == 272 and r["ceco_no_activos_sin_homologar"] == 150
    a10 = CentroCosto.objects.get(institucion=inst, codigo="A10|HSLA")
    assert a10.sede.codigo == "HSLA" and a10.unidad_negocio.codigo == "UN01" and a10.clase == "FINALES" and a10.activo
    # Tipo del maestro manda sobre la clase de la unidad de negocio (N03 P y P: intermedio en el Anexo 4, final en el maestro).
    assert CentroCosto.objects.get(institucion=inst, codigo="N03|HSLA").clase == "FINALES"
    assert CentroCosto.objects.get(institucion=inst, codigo="K25|ESE01").unidad_negocio.codigo == "UN08"
    assert not CentroCosto.objects.get(institucion=inst, codigo="A10").activo          # base sin sede = plantilla
    h = {x.codigo_origen: x.centro.codigo for x in HomologacionCentro.objects.filter(institucion=inst)}
    assert h["1020201"] == "A10|HSLA" and h["HSLA-ARAUQUITA-CONSULTA EXTERNA"] == "A10|HSLA"
    assert "1020304" not in h and "3012199" not in h                                     # VERIFICAR y EXCLUIR no se homologan
    assert {"ESE01", "HSLA", "HSAT", "CSJSC"} <= set(Sede.objects.filter(institucion=inst).values_list("codigo", flat=True))
    # Las reglas del centro base (p. ej. talento humano R05 por personas) se copian a cada centro por sede.
    assert ReglaDistribucion.objects.filter(institucion=inst, centro_origen__codigo="R05|ESE01", distribucion=2).exists()
    assert CuentaRecurso.objects.get(institucion=inst, prefijo="6310010202").recurso.codigo == "R20"
    assert CuentaRecurso.objects.get(institucion=inst, prefijo="4").excluir


def test_importar_maestro_es_idempotente_y_retira_estados(ese, tmp_path):
    inst, _ = ese
    r = importar_maestro(inst)
    assert not any(k in r for k in ("centros_por_sede_creados", "centros_por_sede_actualizados", "homologaciones_creadas",
                                    "reglas_copiadas", "cuentas_creadas"))
    # El maestro cambia 1020201 a VERIFICAR: se retira su homologación.
    from homologacion_ceco.config import REFERENCIA_POR_DEFECTO

    wb = openpyxl.load_workbook(REFERENCIA_POR_DEFECTO)
    wb["C COSTOS ESTADO FINANCIERO"]["J4"] = "VERIFICAR"
    ruta = tmp_path / "maestro.xlsx"
    wb.save(ruta)
    r = importar_maestro(inst, contenido=ruta.read_bytes(), nombre="maestro.xlsx")
    assert r["homologaciones_retiradas"] == 1 and r["maestro"] == "maestro.xlsx"
    assert not HomologacionCentro.objects.filter(institucion=inst, codigo_origen="1020201").exists()


def test_importar_maestro_requiere_catalogo(db):
    with pytest.raises(ErrorImportacion):
        importar_maestro(Institucion.objects.create(codigo="V", nombre="Vacía"))


def test_carga_directa_reporte_ctrp(ese):
    inst, _ = ese
    periodo = Periodo.objects.create(institucion=inst, anio=2026, mes=7)
    r = procesar_carga(institucion=inst, usuario=None, tipo_archivo="ESTADO_FINANCIERO", contenido=ctrp_estado_resultados(),
                       nombre="CTRPEstadoResultadosTerCen.xlsx", periodo=periodo)
    assert not r.errores and r.validas == 4 and r.omitidas == 2                       # 2 filas de ingresos (clase 4) excluidas
    assert r.valor_total == D("1250000.00")                                            # = clases 5 + 6 del reporte
    movs = {(m.fila, m.cuenta): m for m in MovimientoContable.objects.filter(institucion=inst)}
    sueldo = movs[(16, "5101010101")]
    assert sueldo.centro.codigo == "R05|ESE01" and sueldo.sede.codigo == "ESE01" and sueldo.recurso.codigo == "R1"
    assert movs[(17, "5101010101")].valor == D("300000.00")                            # otro tercero, no es duplicado
    assert movs[(27, "6310500201")].centro.codigo == "A10|HSAT" and movs[(27, "6310500201")].recurso.codigo == "R2"
    sin_ceco = movs[(23, "6310190101")]
    assert sin_ceco.centro is None and sin_ceco.recurso.codigo == "R2"                 # lo reparte la 1.ª distribución
    assert any("sin centro de costo" in a[3] for a in r.advertencias)
    assert "TERCERO FICTICIO" not in " ".join(m.descripcion + m.tercero_nombre for m in movs.values())


def test_carga_ctrp_valida_periodo_y_tipo(ese):
    inst, _ = ese
    otro = Periodo.objects.create(institucion=inst, anio=2026, mes=6)
    with pytest.raises(ErrorCargaArchivo, match="2026-07"):
        procesar_carga(institucion=inst, usuario=None, tipo_archivo="ESTADO_FINANCIERO", contenido=ctrp_estado_resultados(),
                       nombre="c.xlsx", periodo=otro)
    with pytest.raises(ErrorCargaArchivo, match="ESTADO_FINANCIERO"):
        procesar_carga(institucion=inst, usuario=None, tipo_archivo="NOMINA", contenido=ctrp_estado_resultados(),
                       nombre="c.xlsx", periodo=otro)


def test_carga_ctrp_editado_a_mano(ese):
    """Reporte sin OidCuentaContable/OidTercero ni pie: la cuenta sale del orden de filas y el mes de la columna Mes."""
    inst, _ = ese
    periodo = Periodo.objects.create(institucion=inst, anio=2026, mes=7)
    r = procesar_carga(institucion=inst, usuario=None, tipo_archivo="ESTADO_FINANCIERO", contenido=ctrp_editado(),
                       nombre="CTRP_JULIO_HOMOLOGADO.xlsx", periodo=periodo)
    assert not r.errores and r.validas == 4 and r.omitidas == 2 and r.duplicadas == 0
    assert r.valor_total == D("1250000.00")
    movs = {(m.fila, m.cuenta): m for m in MovimientoContable.objects.filter(institucion=inst)}
    assert movs[(16, "5101010101")].centro.codigo == "R05|ESE01" and (17, "5101010101") in movs
    assert movs[(27, "6310500201")].centro.codigo == "A10|HSAT"
    assert "9000000" not in " ".join(m.descripcion for m in movs.values())               # no se copia el documento
    with pytest.raises(ErrorCargaArchivo, match="mes 7"):
        procesar_carga(institucion=inst, usuario=None, tipo_archivo="ESTADO_FINANCIERO", contenido=ctrp_editado(),
                       nombre="c.xlsx", periodo=Periodo.objects.create(institucion=inst, anio=2026, mes=8))


def test_pantalla_importar_maestro(client, db):
    inst = Institucion.objects.create(codigo="W", nombre="W")
    cargar_catalogo_base(inst)
    u = get_user_model().objects.create_user("costos_w", password="Clave-Segura-2025")
    Membresia.objects.create(usuario=u, institucion=inst, rol="COSTOS")
    entrar(client, u, inst)
    r = client.post(reverse("modelo:parametrizacion"), {"accion": "maestro_ceco", "cuentas": "1", "desactivar_base": "1"},
                    follow=True)
    assert r.status_code == 200 and "272 centros por sede" in r.content.decode()
    assert CentroCosto.objects.filter(institucion=inst, codigo="A10|HSLA").exists()
    g = get_user_model().objects.create_user("ger_w", password="Clave-Segura-2025")
    Membresia.objects.create(usuario=g, institucion=inst, rol="GERENCIA")
    entrar(client, g, inst)
    assert client.post(reverse("modelo:parametrizacion"), {"accion": "maestro_ceco"}).status_code == 403


def test_consulta_de_costos(client, db):
    inst, corridas = crear_demo()
    corrida = corridas[-1]
    f = {"sede": None, "unidad": None, "centro": None, "clase": "", "q": ""}
    cups = C.por_cups(inst, corrida, f)
    assert cups and sum(x["costo_total"] for x in cups) == sum(c.costo_total for c in corrida.costos_cups.all())
    detalle = C.por_cups(inst, corrida, f, detalle=True)
    assert len(detalle) == corrida.costos_cups.count()
    centros = C.por_centro(inst, corrida, f)
    finales = sum(x["final"] for x in centros if x["clase"] == "FINALES")
    entrada = sum(x["directo"] for x in centros)
    assert abs(finales - entrada) < D("0.05")                                           # todo el costo termina en finales
    servicios, sedes = C.por_servicio(inst, corrida, f)
    assert sum(x["final"] for x in servicios) == sum(x["final"] for x in centros)
    recursos, total = C.por_recurso(inst, corrida, f)
    assert total == entrada == sum(Q.por_recurso(corrida).values())
    un = servicios[0]["unidad"]
    solo_un = C.por_centro(inst, corrida, {**f, "unidad": un})
    assert solo_un and all(x["centro"].unidad_negocio == un for x in solo_un)

    u = get_user_model().objects.create_user("aud", password="Clave-Segura-2025")
    Membresia.objects.create(usuario=u, institucion=inst, rol="AUDITORIA")
    entrar(client, u, inst)
    url = reverse("analisis:consulta")
    for vista in ("cups", "servicios", "centros", "recursos"):
        r = client.get(url, {"vista": vista, "periodo": corrida.periodo.pk})
        assert r.status_code == 200, vista
        x = client.get(url, {"vista": vista, "periodo": corrida.periodo.pk, "exportar": 1})
        assert x["Content-Type"].startswith("application/vnd.openxmlformats"), vista
    r = client.get(url, {"vista": "cups", "detalle": "1", "q": cups[0]["cups"].codigo, "periodo": corrida.periodo.pk})
    assert cups[0]["cups"].codigo in r.content.decode()

"""Cruces RIPS / facturación / notas técnicas, trazabilidad, informe y flujo completo con la demo."""
from datetime import date
from decimal import Decimal as D

import pytest
from django.urls import reverse

from analisis import cruces as X
from analisis.informe import calcular_indicador, construir
from cargas.models import Facturacion, Rips
from core.demo import crear_demo
from modelo.models import Ambito, Eps, IndicadorReferencia, NotaTecnica, NotaTecnicaItem
from motor.models import CostoCups
from motor.motor import ejecutar
from motor.trazabilidad import trazar_centro

from .conftest import entrar


def _fac(caso, factura, cups, cant, unit, eps, ambito=None, fila=1):
    return Facturacion.objects.create(institucion=caso.inst, periodo=caso.periodo, carga=caso.carga, fila=fila, numero_factura=factura,
                                      eps_codigo=eps.codigo, eps=eps, cups_codigo=cups.codigo, cups=cups, cantidad=cant,
                                      valor_unitario=unit, valor_total=unit * cant, ambito=ambito, ambito_origen=ambito.codigo if ambito else "X")


def _rips(caso, factura, codigo, cant, cups=None, eps=None):
    return Rips.objects.create(institucion=caso.inst, periodo=caso.periodo, carga=caso.carga, fila=1, numero_factura=factura,
                               cups_codigo=codigo, cups=cups, cantidad=cant, eps=eps)


@pytest.fixture
def cruce(caso):
    eps = Eps.objects.create(institucion=caso.inst, codigo="EPS1", nombre="EPS UNO")
    amb = Ambito.objects.create(institucion=caso.inst, codigo="1", nombre="AMBULATORIO")
    nota = NotaTecnica.objects.create(institucion=caso.inst, eps=eps, contrato="C1", modalidad="EVENTO",
                                      vigencia_desde=date(2025, 1, 1), vigencia_hasta=date(2025, 12, 31))
    NotaTecnicaItem.objects.create(institucion=caso.inst, nota=nota, cups=caso.cups_a, tarifa=D("8"))      # costo 8.57 → pérdida
    NotaTecnicaItem.objects.create(institucion=caso.inst, nota=nota, cups=caso.cups_b, tarifa=D("30"))
    _fac(caso, "F1", caso.cups_a, 10, D("8"), eps, amb)
    _fac(caso, "F2", caso.cups_b, 5, D("35"), eps, amb)        # facturado distinto a tarifa
    _fac(caso, "F3", caso.cups_c, 2, D("100"), eps, amb)       # sin RIPS
    _rips(caso, "F1", "890201", 10, caso.cups_a, eps)
    _rips(caso, "F2", "890301", 4, caso.cups_b, eps)           # cantidad diferente
    _rips(caso, "F9", "123456", 1)                             # sin factura y CUPS fuera de catálogo
    caso.corrida = ejecutar(caso.periodo)
    caso.eps, caso.amb = eps, amb
    return caso


def test_inconsistencias(cruce):
    tipos = {i.tipo for i in X.inconsistencias(cruce.inst, cruce.periodo, cruce.corrida)}
    assert {"RIPS_SIN_FACTURA", "FACTURA_SIN_RIPS", "CANTIDAD_DIFERENTE", "CUPS_NO_CATALOGO", "TARIFA_MENOR_COSTO",
            "TARIFA_FACTURADA_DIFERENTE"} <= tipos
    assert "EPS_SIN_NOTA_TECNICA" not in tipos


def test_costo_vs_tarifa_y_ambito(cruce):
    filas = {f["cups"].codigo: f for f in X.costo_vs_tarifa(cruce.inst, cruce.periodo, cruce.corrida)}
    assert filas["890201"]["costo_unitario"] == D("8.57") and filas["890201"]["resultado"] == "PÉRDIDA"
    assert filas["890301"]["margen_unitario"] == D("30") - D("17.14")
    amb = X.facturacion_por_ambito(cruce.inst, cruce.periodo, cruce.corrida)[0]
    assert amb["facturado"] == D("80") + D("175") + D("200")
    assert amb["costo"] == D("85.70") + D("85.70") + D("157.20")
    assert amb["facturas"] == 3


def test_trazabilidad_cuadra_con_el_costo_final(caso):
    corrida = ejecutar(caso.periodo)
    filas = trazar_centro(corrida, caso.F1)
    assert sum(f["valor"] for f in filas) == D("1714.00")
    rutas = {f["ruta"] for f in filas}
    assert any("2ª E1" in r and "3ª S1" in r and "4ª I1" in r for r in rutas)  # energía de E1 → S1 → I1 → F1
    assert all(f["archivo"] == "ef.xlsx" and f["fila"] for f in filas)
    # Costo de un CUPS = su fracción del centro
    cc = CostoCups.objects.get(corrida=corrida, cups=caso.cups_a)
    assert sum(f["valor"] for f in trazar_centro(corrida, caso.F1, factor=cc.costo_total / D("1714"))) == D("857.00")


def test_indicadores_e_informe(caso):
    ejecutar(caso.periodo)
    IndicadorReferencia.objects.create(institucion=caso.inst, codigo="P", nombre="Personal", formula="pct_elemento:CP",
                                       minimo=40, maximo=50)
    d = construir(caso.inst, caso.periodo)
    assert d["total"] == D("2500")
    ind = d["indicadores"][0]
    assert ind["valor"] == D("64") and ind["estado"] == "ALTO"  # (1600) / 2500
    assert calcular_indicador("valor_recurso:R14", {"por_recurso": {"R14": D("400")}, "total": D(1)}) == D("400")
    assert any("Personal" in h[1] for h in d["hallazgos"])


def test_flujo_completo_demo_y_pantallas(client, db):
    inst, corridas = crear_demo()
    for c in corridas:
        assert c.resumen["conservacion_ok"] and c.resumen["pendiente_asignacion"] == "0.00"
        assert c.resumen["cups_costeados"] > 0
        assert c.resumen["conciliaciones"][0]["recurso"] == "R1"
    from django.contrib.auth import get_user_model

    from core.models import Membresia

    u = get_user_model().objects.create_user("ger", password="Clave-Segura-2025")
    Membresia.objects.create(usuario=u, institucion=inst, rol="GERENCIA")
    entrar(client, u, inst)
    for nombre in ("analisis:tablero", "analisis:informe", "analisis:cruces", "motor:matriz", "motor:cups", "modelo:parametrizacion"):
        assert client.get(reverse(nombre)).status_code == 200, nombre
    r = client.get(reverse("analisis:informe") + "?exportar=1")
    assert r["Content-Type"].startswith("application/vnd.openxmlformats")
    incons = {i.tipo for i in X.inconsistencias(inst, corridas[1].periodo, corridas[1])}
    assert {"CUPS_NO_CATALOGO", "AMBITO_NO_HOMOLOGADO", "TARIFA_FACTURADA_DIFERENTE", "CUPS_SIN_COSTO"} <= incons

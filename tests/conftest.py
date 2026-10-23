"""Caso de prueba pequeño (calculado a mano en docs/CASO_DE_PRUEBA.md).

Centros:  E1 Gerencia (ESTRUCTURA) · S1 Aseo (SERVICIOS) · I1 Laboratorio (INTERMEDIOS) · F1 Consulta · F2 Urgencias (FINALES)
"""
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model

from cargas.models import Carga, MovimientoContable, Produccion
from core.models import Institucion, Membresia, Periodo, Rol
from modelo.models import (
    CentroCosto, Cups, CupsCentroCosto, Elemento, Inductor, Recurso, ReglaDistribucion, Sede, UnidadNegocio,
    ValorInductor,
)


class Caso:
    pass


def construir_caso(codigo="IPS-T"):
    c = Caso()
    c.inst = inst = Institucion.objects.create(codigo=codigo, nombre=f"IPS Prueba {codigo}")
    c.periodo = Periodo.objects.create(institucion=inst, anio=2025, mes=9)
    cp = Elemento.objects.create(institucion=inst, codigo="CP", nombre="COSTOS DE PERSONAL", orden=1)
    ih = Elemento.objects.create(institucion=inst, codigo="IH", nombre="INSUMOS HOSPITALARIOS", orden=2)
    cg = Elemento.objects.create(institucion=inst, codigo="CG", nombre="COSTOS GENERALES", orden=3)
    c.R1 = Recurso.objects.create(institucion=inst, codigo="R1", nombre="PLANTA", elemento=cp, orden=1, tipo_mano_obra="EMPLEADO")
    c.R3 = Recurso.objects.create(institucion=inst, codigo="R3", nombre="CONTRATO CON TERCEROS", elemento=cp, orden=3, tipo_mano_obra="OPS")
    c.R6 = Recurso.objects.create(institucion=inst, codigo="R6", nombre="REACTIVOS", elemento=ih, orden=6,
                                  clases_permitidas="INTERMEDIOS,FINALES")
    c.R14 = Recurso.objects.create(institucion=inst, codigo="R14", nombre="ENERGIA", elemento=cg, orden=14)
    c.R21 = Recurso.objects.create(institucion=inst, codigo="R21", nombre="ASEO", elemento=cg, orden=21)
    un = {}
    for cod, clase in (("UE", "ESTRUCTURA"), ("US", "SERVICIOS"), ("UI", "INTERMEDIOS"), ("UF", "FINALES")):
        un[cod] = UnidadNegocio.objects.create(institucion=inst, codigo=cod, nombre=cod, clase=clase)
    c.sede = Sede.objects.create(institucion=inst, codigo="S01", nombre="SEDE PRINCIPAL")
    c.E1 = CentroCosto.objects.create(institucion=inst, codigo="E1", nombre="GERENCIA", unidad_negocio=un["UE"], sede=c.sede)
    c.S1 = CentroCosto.objects.create(institucion=inst, codigo="S1", nombre="ASEO", unidad_negocio=un["US"], sede=c.sede)
    c.I1 = CentroCosto.objects.create(institucion=inst, codigo="I1", nombre="LABORATORIO", unidad_negocio=un["UI"], sede=c.sede)
    c.F1 = CentroCosto.objects.create(institucion=inst, codigo="F1", nombre="CONSULTA", unidad_negocio=un["UF"], sede=c.sede)
    c.F2 = CentroCosto.objects.create(institucion=inst, codigo="F2", nombre="URGENCIAS", unidad_negocio=un["UF"], sede=c.sede)
    c.M2 = Inductor.objects.create(institucion=inst, codigo="M2", nombre="Metros cuadrados")
    c.PERS = Inductor.objects.create(institucion=inst, codigo="PERSONAS", nombre="No. personas")
    c.SOL = Inductor.objects.create(institucion=inst, codigo="SOLICITUDES", nombre="Solicitudes")
    for centro, m2 in ((c.E1, 10), (c.S1, 10), (c.I1, 20), (c.F1, 30), (c.F2, 30)):
        ValorInductor.objects.create(institucion=inst, inductor=c.M2, centro=centro, cantidad=D(m2))  # permanente
    for centro, n in ((c.S1, 2), (c.I1, 2), (c.F1, 4), (c.F2, 2)):
        ValorInductor.objects.create(institucion=inst, inductor=c.PERS, centro=centro, periodo=c.periodo, cantidad=D(n))
    for centro, n in ((c.F1, 1), (c.F2, 2)):
        ValorInductor.objects.create(institucion=inst, inductor=c.SOL, centro=centro, centro_origen=c.I1, periodo=c.periodo,
                                     cantidad=D(n))
    todas = "ESTRUCTURA,SERVICIOS,INTERMEDIOS,FINALES"
    ReglaDistribucion.objects.create(institucion=inst, distribucion=1, recurso=c.R14, inductor=c.M2, clases_destino=todas)
    ReglaDistribucion.objects.create(institucion=inst, distribucion=2, inductor=c.PERS, clases_destino="SERVICIOS,INTERMEDIOS,FINALES")
    ReglaDistribucion.objects.create(institucion=inst, distribucion=3, inductor=c.M2, clases_destino="INTERMEDIOS,FINALES")
    ReglaDistribucion.objects.create(institucion=inst, distribucion=4, inductor=c.SOL, clases_destino="FINALES")
    c.carga = Carga.objects.create(institucion=inst, periodo=c.periodo, tipo_archivo="ESTADO_FINANCIERO",
                                   nombre_original="ef.xlsx", hash_sha256="x" * 64, estado=Carga.Estado.APLICADA)

    def mov(recurso, valor, centro=None, fila=2, fuente="EF", doc="", carga=None):
        return MovimientoContable.objects.create(
            institucion=inst, periodo=c.periodo, carga=carga or c.carga, fila=fila, fuente=fuente,
            tipo_archivo="ESTADO_FINANCIERO" if fuente == "EF" else "NOMINA", cuenta="5101", recurso=recurso,
            centro=centro, tercero_documento=doc, valor=D(valor))

    c.mov = mov
    mov(c.R1, 1000, c.F1, 2)
    mov(c.R1, 600, c.E1, 3)
    mov(c.R14, 400, None, 4)
    mov(c.R6, 300, c.I1, 5)
    mov(c.R21, 200, c.S1, 6)
    c.cups_a = Cups.objects.create(institucion=inst, codigo="890201", descripcion="CONSULTA PRIMERA VEZ MEDICINA GENERAL")
    c.cups_b = Cups.objects.create(institucion=inst, codigo="890301", descripcion="CONSULTA CONTROL MEDICINA GENERAL")
    c.cups_c = Cups.objects.create(institucion=inst, codigo="890701", descripcion="CONSULTA DE URGENCIAS")
    CupsCentroCosto.objects.create(institucion=inst, cups=c.cups_b, centro=c.F1, peso_relativo=D(2))
    CupsCentroCosto.objects.create(institucion=inst, cups=c.cups_a, centro=c.F1)
    CupsCentroCosto.objects.create(institucion=inst, cups=c.cups_c, centro=c.F2)
    for centro, cups, cant in ((c.F1, c.cups_a, 100), (c.F1, c.cups_b, 50), (c.F2, c.cups_c, 10)):
        Produccion.objects.create(institucion=inst, periodo=c.periodo, carga=c.carga, fila=1, centro=centro, cups=cups,
                                  cantidad=D(cant))
    return c


@pytest.fixture
def caso(db):
    return construir_caso()


@pytest.fixture
def usuarios(db, caso):
    User = get_user_model()
    otra = Institucion.objects.create(codigo="OTRA", nombre="Otra IPS")
    Periodo.objects.create(institucion=otra, anio=2025, mes=9)
    salida = {"otra": otra}
    for rol in Rol.values:
        u = User.objects.create_user(username=rol.lower(), password="Clave-Segura-2025")
        Membresia.objects.create(usuario=u, institucion=caso.inst, rol=rol)
        salida[rol] = u
    consultor_otra = User.objects.create_user(username="consultor_otra", password="Clave-Segura-2025")
    Membresia.objects.create(usuario=consultor_otra, institucion=otra, rol=Rol.CONSULTOR)
    salida["consultor_otra"] = consultor_otra
    return salida


def entrar(client, usuario, institucion):
    client.force_login(usuario)
    s = client.session
    s["institucion_id"] = institucion.pk
    s.save()
    return client

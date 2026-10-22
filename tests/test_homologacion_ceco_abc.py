"""La hoja CARGA HERRAMIENTA del libro de homologación se carga tal cual en la herramienta ABC (estado financiero)."""
from decimal import Decimal as D

from cargas.models import MovimientoContable
from cargas.procesador import procesar_carga
from core.models import Institucion, Periodo
from homologacion_ceco.ejemplo import ef_enero
from homologacion_ceco.exportar import exportar
from homologacion_ceco.procesador import Opciones, procesar
from homologacion_ceco.referencia import cargar_referencia
from modelo.catalogo_base import cargar_catalogo_base
from modelo.models import Sede


def test_carga_herramienta_entra_directo_al_abc(db, tmp_path):
    inst = Institucion.objects.create(codigo="ESE-MYC-T", nombre="ESE prueba homologación")
    cargar_catalogo_base(inst)
    for codigo in ("ESE01", "HSLA"):
        Sede.objects.create(institucion=inst, codigo=codigo, nombre=codigo)
    periodo = Periodo.objects.create(institucion=inst, anio=2026, mes=1)

    res = procesar([], cargar_referencia(), Opciones(), contenidos=[("EF_ENERO_2026.xlsx", ef_enero())])
    salida, _ = exportar(res, tmp_path / "homologado.xlsx")

    r = procesar_carga(institucion=inst, usuario=None, tipo_archivo="ESTADO_FINANCIERO", contenido=salida.read_bytes(),
                       nombre="homologado.xlsx", periodo=periodo, hoja="CARGA HERRAMIENTA")
    assert not r.errores, r.errores
    movs = MovimientoContable.objects.filter(institucion=inst, periodo=periodo)
    assert movs.count() == 4
    assert sum(m.valor for m in movs) == D("15300000.50")
    a10 = movs.get(centro__codigo="A10")
    assert a10.sede.codigo == "HSLA" and a10.recurso.codigo == "R1" and a10.valor == D("3000000.00")

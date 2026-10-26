"""Matriz de permisos por rol.

La matriz es lógica de la aplicación (no datos de la institución): define qué puede
hacer cada rol. El aislamiento entre instituciones lo garantiza la membresía.
"""
from .models import Rol

VER = "ver"
CARGAR = "cargar"
PARAMETRIZAR = "parametrizar"
EJECUTAR_MOTOR = "ejecutar_motor"
CERRAR_PERIODO = "cerrar_periodo"
VER_BITACORA = "ver_bitacora"
GESTIONAR_USUARIOS = "gestionar_usuarios"
EDITAR_INFORME = "editar_informe"

MATRIZ = {
    VER: {Rol.ADMIN, Rol.FINANCIERO, Rol.COSTOS, Rol.AUDITORIA, Rol.GERENCIA, Rol.CONSULTOR},
    CARGAR: {Rol.ADMIN, Rol.FINANCIERO, Rol.COSTOS, Rol.CONSULTOR},
    PARAMETRIZAR: {Rol.ADMIN, Rol.COSTOS, Rol.CONSULTOR},
    EJECUTAR_MOTOR: {Rol.ADMIN, Rol.COSTOS, Rol.CONSULTOR},
    CERRAR_PERIODO: {Rol.ADMIN, Rol.FINANCIERO},
    VER_BITACORA: {Rol.ADMIN, Rol.AUDITORIA},
    GESTIONAR_USUARIOS: {Rol.ADMIN},
    EDITAR_INFORME: {Rol.ADMIN, Rol.COSTOS, Rol.GERENCIA, Rol.CONSULTOR},
}

DESCRIPCION = {
    VER: "Consultar tablero, catálogos, resultados e informes",
    CARGAR: "Cargar y anular archivos mensuales",
    PARAMETRIZAR: "Modificar catálogos, inductores, reglas y mapeos",
    EJECUTAR_MOTOR: "Ejecutar las distribuciones 1ª a 4ª y el costeo CUPS",
    CERRAR_PERIODO: "Abrir y cerrar periodos contables",
    VER_BITACORA: "Consultar la bitácora de auditoría",
    GESTIONAR_USUARIOS: "Asignar usuarios y roles a la institución",
    EDITAR_INFORME: "Editar textos del informe gerencial",
}


def tiene_permiso(usuario, rol, permiso):
    if usuario is not None and usuario.is_superuser:
        return True
    return rol in MATRIZ.get(permiso, set())

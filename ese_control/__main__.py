"""Línea de comandos: ``python -m ese_control <orden>``.

  iniciar                     crea la base de datos y el primer usuario central
  usuario                     crea un usuario (central o de sede)
  plantillas [carpeta]        genera las tres plantillas de Excel
  informe [archivo.xlsx]      genera el informe de múltiples hojas
  cargar <archivo> [--item servicios_publicos|combustible|mantenimiento] [--periodo AAAA-MM] [--usuario u]
  catalogo <archivo.xlsx>     reemplaza el catálogo por el del Excel
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from . import auth, catalogo, cargues, config, db, informe, plantillas
from .validacion import Opciones


def _pedir_usuario(rol_defecto: str = "central") -> None:
    usuario = input("Usuario: ").strip()
    nombre = input("Nombre completo: ").strip()
    rol = (input(f"Rol [central/sede] ({rol_defecto}): ").strip() or rol_defecto).lower()
    hospital = None
    if rol == "sede":
        hospitales = sorted(db.consulta("SELECT DISTINCT hospital FROM sedes WHERE activo=1")["hospital"])
        for i, h in enumerate(hospitales, 1):
            print(f"  {i}. {h}")
        hospital = hospitales[int(input("Número del hospital: ")) - 1]
    clave = getpass.getpass("Contraseña (mínimo 8 caracteres): ")
    if clave != getpass.getpass("Repita la contraseña: "):
        sys.exit("Las contraseñas no coinciden.")
    auth.crear(usuario, nombre, clave, rol, hospital)
    print(f"Usuario «{usuario}» creado.")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m ese_control", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="orden", required=True)
    sub.add_parser("iniciar")
    sub.add_parser("usuario")
    x = sub.add_parser("plantillas"); x.add_argument("carpeta", nargs="?", default="entregables/plantillas")
    x = sub.add_parser("informe"); x.add_argument("archivo", nargs="?", default="informe_control.xlsx")
    x.add_argument("--desde"); x.add_argument("--hasta")
    x = sub.add_parser("cargar"); x.add_argument("archivo"); x.add_argument("--item", choices=list(config.ITEMS))
    x.add_argument("--periodo"); x.add_argument("--usuario", default="consola")
    x.add_argument("--complementar", action="store_true")
    x = sub.add_parser("catalogo"); x.add_argument("archivo")
    a = p.parse_args(argv)

    db.inicializar()
    if a.orden == "iniciar":
        print(f"Base de datos lista en {config.RUTA_BD}")
        if not auth.hay_usuarios():
            print("Cree el primer usuario del área administrativa central:")
            _pedir_usuario()
    elif a.orden == "usuario":
        _pedir_usuario("sede")
    elif a.orden == "plantillas":
        carpeta = Path(a.carpeta); carpeta.mkdir(parents=True, exist_ok=True)
        for item, nombre in plantillas.NOMBRE_ARCHIVO.items():
            (carpeta / nombre).write_bytes(plantillas.generar(item))
            print("Generada", carpeta / nombre)
    elif a.orden == "informe":
        Path(a.archivo).write_bytes(informe.generar(desde=a.desde, hasta=a.hasta))
        print("Informe generado en", a.archivo)
    elif a.orden == "catalogo":
        tablas, errores = catalogo.leer_excel(Path(a.archivo).read_bytes())
        if errores:
            sys.exit("El catálogo tiene errores y no se aplicó:\n  - " + "\n  - ".join(errores))
        print(catalogo.aplicar(tablas, "consola", Path(a.archivo).name))
    elif a.orden == "cargar":
        ruta = Path(a.archivo)
        op = Opciones(periodo=a.periodo, alcance="periodo" if a.periodo else "todos")
        prep = cargues.preparar(ruta.name, ruta.read_bytes(), a.item, op)
        for i in prep.incidencias:
            print(f"[{i.severidad}] {i.hoja or '-'} fila {i.fila or '-'} {i.columna}: {i.motivo} {i.valor}")
        print(f"Filas válidas: {prep.total_validas} · rechazadas: {sum(prep.rechazadas.values())}")
        if prep.total_validas:
            ids = cargues.confirmar(prep, a.usuario, "complementar" if a.complementar else "reemplazar")
            print("Cargue(s) registrado(s):", ", ".join(f"#{i}" for i in ids))
        else:
            sys.exit("No se cargó nada.")


if __name__ == "__main__":
    main()

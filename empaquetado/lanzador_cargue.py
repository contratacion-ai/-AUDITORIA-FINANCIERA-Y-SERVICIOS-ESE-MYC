"""Punto de entrada del ejecutable CargueRIPS (PyInstaller).

Equivale a iniciar_cargue.bat: sin argumentos pregunta qué cargar; con
argumentos (p. ej. al arrastrar un ZIP sobre CargueRIPS.exe) los procesa.
"""

import sys

from rips_auditoria.cargue import main

if __name__ == "__main__":
    try:
        codigo = main()
    except Exception as exc:  # noqa: BLE001 - mostrar el error antes de cerrar la ventana
        print(f"\nError inesperado: {exc}")
        codigo = 1
    if getattr(sys, "frozen", False):
        # La consola del .exe se cierra al terminar: se espera a que el usuario lea el resultado.
        try:
            input("\nPresione Enter para cerrar...")
        except EOFError:
            pass
    sys.exit(codigo)

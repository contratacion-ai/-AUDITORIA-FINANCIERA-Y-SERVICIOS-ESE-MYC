#!/usr/bin/env python
import os
import sys


def redirigir_salida():
    """Con pythonw.exe (sin ventana) no hay consola: la salida va al archivo de --log o se descarta."""
    if "--log" in sys.argv:
        i = sys.argv.index("--log")
        ruta = sys.argv[i + 1] if i + 1 < len(sys.argv) else os.devnull
        del sys.argv[i:i + 2]
        sys.stdout = sys.stderr = open(ruta, "a", encoding="utf-8", buffering=1)
    elif sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")


def main():
    redirigir_salida()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "abc_salud.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

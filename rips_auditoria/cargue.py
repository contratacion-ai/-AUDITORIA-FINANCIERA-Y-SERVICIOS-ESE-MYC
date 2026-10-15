"""Inicio guiado del proceso de cargue (lo usan iniciar_cargue.bat / .sh).

Pensado para el personal de facturación y auditoría que no usa la consola:

* Si se arrastra un ZIP, una carpeta o varios TXT sobre ``iniciar_cargue``,
  se procesan esas entradas.
* Si se abre sin argumentos, se muestra un cuadro para elegir los archivos
  (o la carpeta). Si el equipo no tiene interfaz gráfica (tkinter), se piden
  las rutas por consola.
* La salida queda en ``<carpeta de datos>/cargue_AAAAMMDD_HHMMSS`` (por
  defecto ``salida/`` de la aplicación; ``RIPS_DATOS_DIR`` la cambia) o en
  ``--salida``, y al terminar se abre esa carpeta.

Todo el procesamiento lo hace ``python -m rips_auditoria procesar``; este
módulo sólo recoge las rutas y lanza ese comando.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

from .cli import main as cli_main
from .config import carpeta_datos

TIPOS_ARCHIVO = [("RIPS (ZIP o TXT)", "*.zip *.ZIP *.txt *.TXT"), ("Todos los archivos", "*.*")]


def elegir_con_ventana() -> list[str] | None:
    """Devuelve las rutas elegidas, [] si se canceló, o None si no hay interfaz gráfica."""
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except ImportError:
        return None
    try:
        raiz = tk.Tk()
    except tk.TclError:  # sin pantalla (servidor, sesión remota sin escritorio)
        return None
    raiz.withdraw()
    raiz.attributes("-topmost", True)
    opcion = messagebox.askyesnocancel(
        "Cargue de RIPS",
        "¿Qué desea cargar?\n\nSí: seleccionar archivos ZIP o TXT\nNo: seleccionar una carpeta completa",
        parent=raiz)
    rutas: list[str] = []
    if opcion is True:
        rutas = list(filedialog.askopenfilenames(title="Seleccione el ZIP o los TXT de RIPS",
                                                 filetypes=TIPOS_ARCHIVO, parent=raiz))
    elif opcion is False:
        carpeta = filedialog.askdirectory(title="Seleccione la carpeta con los RIPS", parent=raiz)
        rutas = [carpeta] if carpeta else []
    raiz.destroy()
    return rutas


def elegir_por_consola() -> list[str]:
    print("Escriba la ruta del ZIP, carpeta o TXT a cargar (varias rutas separadas por ';').")
    print("Puede arrastrar el archivo a esta ventana y presionar Enter.")
    texto = input("> ").strip()
    return [r.strip().strip('"').strip("'") for r in texto.split(";") if r.strip()]


def abrir_carpeta(ruta: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(ruta)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(ruta)])
        else:
            subprocess.Popen(["xdg-open", str(ruta)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass  # no es crítico: la ruta ya se mostró en pantalla


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="iniciar_cargue", description="Cargue guiado de RIPS")
    p.add_argument("entradas", nargs="*", help="ZIP, carpetas o TXT (opcional: si no se indican se preguntan)")
    p.add_argument("-s", "--salida", help="Carpeta de salida (por defecto salida/cargue_<fecha>)")
    p.add_argument("--hospital", help="Nombre del hospital, si los TXT no vienen en carpetas por hospital")
    p.add_argument("--sin-consolidado", action="store_true", help="No generar el consolidado de todo el lote")
    p.add_argument("--no-abrir", action="store_true", help="No abrir la carpeta de salida al terminar")
    args = p.parse_args(argv)

    entradas = args.entradas
    if not entradas:
        elegidas = elegir_con_ventana()
        entradas = elegidas if elegidas is not None else elegir_por_consola()
    if not entradas:
        print("No se seleccionó ningún archivo. Cargue cancelado.")
        return 1
    faltan = [e for e in entradas if not Path(e).exists()]
    if faltan:
        print("No se encontraron estas rutas:\n  " + "\n  ".join(faltan))
        return 1

    salida = Path(args.salida) if args.salida else (
        carpeta_datos() / f"cargue_{dt.datetime.now():%Y%m%d_%H%M%S}")
    print("=" * 70)
    print("CARGUE DE RIPS - Resolución 3374 de 2000")
    print("Entradas:\n  " + "\n  ".join(str(e) for e in entradas))
    print(f"Salida:   {salida}")
    print("Procesando... (un lote anual puede tardar varios minutos)")
    print("=" * 70)

    cmd = ["-v", "procesar", *map(str, entradas), "-s", str(salida)]
    if args.hospital:
        cmd += ["--hospital", args.hospital]
    if args.sin_consolidado:
        cmd.append("--sin-consolidado")
    codigo = cli_main(cmd)

    if codigo == 0:
        print("\nCargue terminado. Revise en cada carpeta Hospital/AAAA-MM:")
        print("  RIPS_*.xlsx       datos validados con la estructura del Anexo Técnico No. 1")
        print("  AUDITORIA_*.xlsx  información homologada para la auditoría financiera")
        print("  hojas 'Incidencias' y 'Conciliacion_Facturas' con los hallazgos")
        if not args.no_abrir:
            abrir_carpeta(salida)
    else:
        print(f"\nEl cargue terminó con código {codigo}. Revise los mensajes anteriores.")
    return codigo


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

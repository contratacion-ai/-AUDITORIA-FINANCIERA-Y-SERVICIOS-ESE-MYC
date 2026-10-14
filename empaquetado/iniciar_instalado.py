"""Lanzador de la aplicación instalada en Windows (lo usa el acceso «Cargue RIPS ESE MyC»).

- Los resultados y el acumulado para el aplicativo de auditoría se guardan en
  «Documentos\\Cargue RIPS ESE MyC» (la carpeta del programa no es para datos).
- Si la ventana de cargue ya está abierta, sólo abre el navegador.
"""

import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
if BASE not in sys.path:
    sys.path.insert(0, BASE)


def _documentos() -> str:
    """Carpeta Documentos real (respeta la redirección a OneDrive)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # CSIDL_PERSONAL
                return buf.value
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Documents")


os.environ.setdefault("RIPS_DATOS_DIR", os.path.join(_documentos(), "Cargue RIPS ESE MyC"))
os.makedirs(os.environ["RIPS_DATOS_DIR"], exist_ok=True)

if __name__ == "__main__":
    from rips_auditoria.web.__main__ import main

    sys.exit(main())

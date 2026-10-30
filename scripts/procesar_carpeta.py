"""Script de escritorio: procesa una carpeta local de RIPS y deja los informes en ./informes.

    python scripts/procesar_carpeta.py "D:/RIPS/Hospital San Jose de Cravo Norte" --pdf

Equivale a ``python -m rips_auditoria.cli procesar ...``.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rips_auditoria.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["procesar", *sys.argv[1:]]))

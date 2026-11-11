"""Empaqueta la aplicación en un ZIP instalable: dist/ESE_Control_Instalador.zip

Incluye el código, el instalador (Windows y Linux), la guía y una base de DEMOSTRACIÓN con cifras ficticias
y usuarios de prueba. No incluye datos reales ni la carpeta privado/.

Uso:  python scripts/crear_instalador.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

INCLUIR = ["app.py", "requirements.txt", "README.md", ".streamlit/config.toml"]
CARPETAS = ["ese_control", "catalogo_inicial", "docs", "entregables"]
DEL_INSTALADOR = ["instalar.bat", "iniciar_app.bat", "instalar.sh", "iniciar_app.sh", "LEEME_PRIMERO.txt"]


def main() -> Path:
    import generar_demo
    from ese_control import auth, db

    with tempfile.TemporaryDirectory() as tmp:
        demo = Path(tmp) / "demo"
        generar_demo.main(demo)
        bd = demo / "control_ese.db"
        auth.crear("admin", "Administración Central (demo)", "Demo2026*", "central", ruta=bd)
        auth.crear("fortul", "Auxiliar Fortul (demo)", "Demo2026*", "sede", "Hospital San Francisco de Fortul", ruta=bd)
        con = db.conectar(bd)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()

        salida = RAIZ / "dist" / "ESE_Control_Instalador.zip"
        salida.parent.mkdir(exist_ok=True)
        base = "ESE_Control"
        with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as z:
            for f in INCLUIR:
                z.write(RAIZ / f, f"{base}/{f}")
            for c in CARPETAS:
                for f in sorted((RAIZ / c).rglob("*")):
                    if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                        z.write(f, f"{base}/{f.relative_to(RAIZ)}")
            for f in DEL_INSTALADOR:
                ruta = RAIZ / "instalador" / f
                datos = ruta.read_bytes()
                if f.endswith(".bat"):                       # Windows: saltos de línea CRLF
                    datos = datos.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                info = zipfile.ZipInfo(f"{base}/{f}", date_time=(2026, 11, 11, 0, 0, 0))
                info.external_attr = (0o755 if f.endswith(".sh") else 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, datos)
            z.write(bd, f"{base}/demo/control_ese.db")
    print(f"Instalador creado: {salida} ({salida.stat().st_size / 1e6:.1f} MB)")
    return salida


if __name__ == "__main__":
    main()

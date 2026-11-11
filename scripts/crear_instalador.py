"""Empaqueta la aplicación en ZIP instalables dentro de dist/:

* ``ESE_Control_Windows_Instalador.zip``: instalador AUTOCONTENIDO para Windows 10/11 x64 (lleva su propio Python
  y las librerías; no requiere internet ni permisos de administrador). Se arma con las ruedas win_amd64 de PyPI y el
  paquete oficial de Python de NuGet, por lo que necesita internet SOLO al construirlo.
* ``ESE_Control_Instalador.zip``: paquete ligero (Linux/macOS o Windows con Python e internet).

Incluyen el código, el instalador (Windows y Linux), la guía y una base de DEMOSTRACIÓN con cifras ficticias
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


PY_NUGET = "https://api.nuget.org/v3-flatcontainer/python/3.12.8/python.3.12.8.nupkg"
CACHE = RAIZ / ".cache_instalador"


def _descargar(url: str, destino: Path) -> Path:
    import urllib.request

    if not destino.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        print("Descargando", url)
        urllib.request.urlretrieve(url, destino)
    return destino


def _armar_python_windows(destino: Path) -> None:
    """Python oficial de Windows (x64) con todas las dependencias ya instaladas en site-packages."""
    import subprocess

    nupkg = _descargar(PY_NUGET, CACHE / "python.nupkg")
    tmp = CACHE / "py"
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(nupkg) as z:
        z.extractall(tmp)
    shutil.copytree(tmp / "tools", destino)
    for sobrante in ("include", "libs", "LICENSE.txt"):
        ruta = destino / sobrante
        shutil.rmtree(ruta, ignore_errors=True) if ruta.is_dir() else ruta.unlink(missing_ok=True)
    for sobrante in ("test", "idlelib", "turtledemo", "lib2to3"):
        shutil.rmtree(destino / "Lib" / sobrante, ignore_errors=True)
    ruedas = CACHE / "wheels"
    subprocess.run([sys.executable, "-m", "pip", "download", "-q", "--platform", "win_amd64", "--python-version", "3.12",
                    "--implementation", "cp", "--only-binary=:all:", "-r", str(RAIZ / "requirements.txt"), "-d", str(ruedas)],
                   check=True)
    sp = destino / "Lib" / "site-packages"
    sp.mkdir(parents=True, exist_ok=True)
    for rueda in sorted(ruedas.glob("*.whl")):           # una rueda es un zip que se extrae tal cual
        with zipfile.ZipFile(rueda) as z:
            z.extractall(sp)
    for pyc in destino.rglob("__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)


def _crlf(datos: bytes) -> bytes:
    return datos.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def construir_exe(py: Path, bd_demo: Path) -> Path | None:
    """Setup.exe con asistente (NSIS). Requiere `makensis` (apt install nsis)."""
    import subprocess

    if not shutil.which("makensis"):
        print("makensis no está instalado: se omite el Setup.exe (apt install nsis).")
        return None
    prep = CACHE / "staging"
    shutil.rmtree(prep, ignore_errors=True)
    for f in INCLUIR:
        (prep / "app" / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RAIZ / f, prep / "app" / f)
    for c in CARPETAS:
        shutil.copytree(RAIZ / c, prep / "app" / c, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(py, prep / "python")
    (prep / "demo").mkdir()
    shutil.copy2(bd_demo, prep / "demo" / "control_ese.db")
    (prep / "iniciar_app.bat").write_bytes(_crlf((RAIZ / "instalador" / "windows_iniciar.bat").read_bytes()))
    (prep / "LEEME_PRIMERO.txt").write_bytes(_crlf((RAIZ / "instalador" / "windows_LEEME_exe.txt").read_bytes()))
    salida = RAIZ / "dist" / "ESE_Control_Setup.exe"
    subprocess.run(["makensis", "-V2", f"-DORIGEN={prep}", f"-DSALIDA={salida}", "-DVERSION=1.0.0",
                    str(RAIZ / "instalador" / "windows_setup.nsi")], check=True)
    print(f"Setup.exe creado: {salida} ({salida.stat().st_size / 1e6:.0f} MB)")
    return salida


def construir_windows(bd_demo: Path) -> Path:
    salida = RAIZ / "dist" / "ESE_Control_Windows_Instalador.zip"
    salida.parent.mkdir(exist_ok=True)
    py = CACHE / "python_win"
    shutil.rmtree(py, ignore_errors=True)
    _armar_python_windows(py)
    base = "ESE_Control_Instalador_Windows"
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in INCLUIR:
            z.write(RAIZ / f, f"{base}/app/{f}")
        for c in CARPETAS:
            for f in sorted((RAIZ / c).rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                    z.write(f, f"{base}/app/{f.relative_to(RAIZ)}")
        for origen, nombre in (("windows_instalar.bat", "instalar.bat"), ("windows_iniciar.bat", "iniciar_app.bat"),
                               ("windows_desinstalar.bat", "desinstalar.bat"), ("windows_LEEME.txt", "LEEME_PRIMERO.txt")):
            z.writestr(f"{base}/{nombre}", _crlf((RAIZ / "instalador" / origen).read_bytes()))
        z.write(bd_demo, f"{base}/demo/control_ese.db")
        for f in sorted(py.rglob("*")):
            if f.is_file():
                z.write(f, f"{base}/python/{f.relative_to(py)}")
    print(f"Instalador Windows (ZIP) creado: {salida} ({salida.stat().st_size / 1e6:.0f} MB)")
    construir_exe(py, bd_demo)
    return salida


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
        print(f"Instalador ligero creado: {salida} ({salida.stat().st_size / 1e6:.1f} MB)")
        if "--sin-windows" not in sys.argv:
            construir_windows(bd)
    return salida


if __name__ == "__main__":
    main()

import pytest


@pytest.fixture(autouse=True)
def _datos_temporales(tmp_path, monkeypatch):
    """Cada prueba usa su propia carpeta de datos (acumulado del aplicativo)."""
    monkeypatch.setenv("RIPS_DATOS_DIR", str(tmp_path / "datos"))

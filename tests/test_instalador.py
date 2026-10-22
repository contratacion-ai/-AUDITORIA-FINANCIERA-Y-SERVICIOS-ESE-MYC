import pytest
from django.core.management import CommandError, call_command

from core.models import Institucion, Membresia
from modelo.models import CentroCosto


def test_comando_instalar_es_idempotente(db, monkeypatch, settings, tmp_path):
    settings.STATIC_ROOT = tmp_path / "static"
    args = ["instalar", "--institucion-codigo", "INST-9", "--institucion-nombre", "ESE PRUEBA", "--usuario", "jefe"]
    with pytest.raises(CommandError, match="contraseña"):
        call_command(*args)
    monkeypatch.setenv("ABC_ADMIN_PASSWORD", "123")
    with pytest.raises(CommandError, match="no válida"):
        call_command(*args)
    monkeypatch.setenv("ABC_ADMIN_PASSWORD", "Clave-Instalador-2025")
    call_command(*args)
    call_command(*args)  # segunda ejecución (actualización) no duplica nada
    inst = Institucion.objects.get(codigo="INST-9")
    assert CentroCosto.objects.filter(institucion=inst).count() == 179
    m = Membresia.objects.get(institucion=inst)
    assert m.rol == "ADMIN" and m.usuario.is_superuser and m.usuario.check_password("Clave-Instalador-2025")

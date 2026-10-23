import pytest
from django.core.management import CommandError, call_command
from django.urls import reverse

from core import primer_acceso
from core.models import Bitacora, Institucion, Membresia
from modelo.models import CentroCosto


@pytest.fixture
def instalado(db, settings, tmp_path):
    settings.STATIC_ROOT = tmp_path / "static"
    settings.DATOS_DIR = tmp_path / "datos"  # aquí se guarda el enlace de primer acceso
    call_command("instalar", "--institucion-codigo", "INST-9", "--institucion-nombre", "ESE PRUEBA", "--usuario", "jefe")
    return tmp_path


def test_instalar_sin_contrasena_e_idempotente(instalado):
    call_command("instalar", "--institucion-codigo", "INST-9", "--institucion-nombre", "ESE PRUEBA", "--usuario", "jefe")
    inst = Institucion.objects.get(codigo="INST-9")
    assert CentroCosto.objects.filter(institucion=inst).count() == 184
    m = Membresia.objects.get(institucion=inst)
    assert m.rol == "ADMIN" and m.usuario.is_superuser and not m.usuario.has_usable_password()
    assert primer_acceso.pendiente() == m.usuario
    assert (instalado / "datos" / "primer_acceso.token").is_file()


def test_contrasena_debil_por_variable_se_rechaza(db, monkeypatch, settings, tmp_path):
    settings.STATIC_ROOT = tmp_path / "static"
    monkeypatch.setenv("ABC_ADMIN_PASSWORD", "123")
    with pytest.raises(CommandError, match="no válida"):
        call_command("instalar", "--institucion-codigo", "X", "--institucion-nombre", "X", "--usuario", "otro")


def test_primer_acceso_crea_contrasena_una_sola_vez(client, instalado):
    token = (instalado / "datos" / "primer_acceso.token").read_text()
    url = reverse("primer_acceso")
    assert client.get(url + "?t=falso").status_code == 403
    assert "Cree su contraseña" in client.get(url + f"?t={token}").content.decode()
    r = client.post(url, {"t": token, "usuario": "jefe", "clave1": "corta", "clave2": "corta"})
    assert r.status_code == 200 and not primer_acceso.pendiente().has_usable_password()
    r = client.post(url, {"t": token, "usuario": "gerente.costos", "clave1": "Mi-Clave-Propia-2025",
                          "clave2": "Mi-Clave-Propia-2025"})
    assert r.status_code == 302
    m = Membresia.objects.get()
    assert m.usuario.username == "gerente.costos" and m.usuario.check_password("Mi-Clave-Propia-2025")
    assert primer_acceso.pendiente() is None and not (instalado / "datos" / "primer_acceso.token").exists()
    assert Bitacora.objects.filter(accion="PRIMER_ACCESO").exists()
    client.logout()
    r = client.get(url + f"?t={token}")  # el enlace ya no sirve
    assert r.status_code == 302 and r.url == reverse("login")


def test_enlace_nuevo_si_se_pierde(instalado, capsys):
    viejo = (instalado / "datos" / "primer_acceso.token").read_text()
    call_command("enlace_primer_acceso", "--puerto", "8080")
    salida = capsys.readouterr().out
    nuevo = (instalado / "datos" / "primer_acceso.token").read_text()
    assert nuevo != viejo and f"http://localhost:8080/primer-acceso/?t={nuevo}" in salida
    assert not primer_acceso.valido(viejo)

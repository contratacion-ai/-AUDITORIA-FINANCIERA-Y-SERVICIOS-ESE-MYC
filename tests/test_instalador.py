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


def test_respaldo_diario_sin_programador_de_tareas(tmp_path):
    """La instalación sin administrador respalda desde el propio servidor: copia la base, media y .env, y depura."""
    import os
    import sqlite3
    import time
    from datetime import datetime, timedelta

    from core.respaldo import respaldar, respaldo_pendiente, ultimo_respaldo

    datos = tmp_path / "datos"
    (datos / "media").mkdir(parents=True)
    (datos / "media" / "x.txt").write_text("a")
    (tmp_path / ".env").write_text("PSEUDONIMO_KEY=k")
    con = sqlite3.connect(datos / "db.sqlite3")
    con.execute("create table t (v)")
    con.execute("insert into t values (1)")
    con.commit()
    con.close()
    assert respaldo_pendiente(datos)
    viejo = datos / "respaldos" / "20000101_0000"
    viejo.mkdir(parents=True)
    os.utime(viejo, (time.time() - 40 * 86400,) * 2)
    carpeta = respaldar(tmp_path, datos)
    assert sqlite3.connect(carpeta / "db.sqlite3").execute("select v from t").fetchone() == (1,)
    assert (carpeta / "media" / "x.txt").exists() and (carpeta / "env.txt").exists()
    assert not viejo.exists()                                       # respaldos de más de 30 días se borran
    assert not respaldo_pendiente(datos)
    assert respaldo_pendiente(datos, ahora=ultimo_respaldo(datos) + timedelta(hours=25))
    assert ultimo_respaldo(datos) <= datetime.now()


def test_instalador_por_usuario_no_usa_programador_ni_firewall():
    """El modo sin administrador no llama a schtasks ni netsh (pueden pedir credenciales)."""
    import ast
    from pathlib import Path

    fuente = (Path(__file__).resolve().parents[1] / "instalador" / "windows" / "configurar.py").read_text(encoding="utf-8")
    funcion = next(n for n in ast.walk(ast.parse(fuente)) if isinstance(n, ast.FunctionDef) and n.name == "registrar_usuario")
    texto = ast.get_source_segment(fuente, funcion)
    assert '"schtasks"' not in texto and '"netsh"' not in texto
    assert "--respaldo-diario" in fuente

"""Configura Costos ABC en Windows después de copiar los archivos (lo ejecuta CostosABC-Setup.exe con el Python incluido).

No depende de PowerShell: genera .env, crea base de datos/institución/administrador, registra el servidor como tarea
programada que arranca con Windows (schtasks), abre el puerto (netsh), programa el respaldo diario y comprueba que la
aplicación responda. El administrador queda sin contraseña: la crea en el navegador con un enlace de un solo uso
(datos\\primer_acceso.token), que el instalador abre al terminar.

Salida 0 = todo correcto · 2 = instalada con advertencias (p. ej. sin arranque automático) · 1 = error.
Detalle en datos\\logs\\instalacion.log.
"""
import argparse
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from xml.sax.saxutils import escape
from datetime import datetime
from pathlib import Path

TAREA = "CostosABC-Servidor"
TAREA_RESPALDO = "CostosABC-Respaldo"


class Registro:
    def __init__(self, ruta):
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(ruta, "a", encoding="utf-8")
        self.escribir(f"--- Configuración {datetime.now():%Y-%m-%d %H:%M:%S} ---")

    def escribir(self, texto):
        print(texto, flush=True)
        self.f.write(texto + "\n")
        self.f.flush()


def ejecutar(log, comando, entorno=None, obligatorio=True, cwd=None):
    log.escribir("> " + " ".join(str(c) for c in comando))
    r = subprocess.run(comando, cwd=cwd, env=entorno, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for linea in (r.stdout + r.stderr).splitlines():
        log.escribir("  " + linea)
    if obligatorio and r.returncode != 0:
        raise RuntimeError(f"Falló: {comando[0]} (código {r.returncode})")
    return r.returncode


def ips_locales():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


def crear_env(destino, puerto, log, local=False):
    ruta = destino / ".env"
    if ruta.exists():
        log.escribir("[--] .env existente conservado (no se regeneran las claves)")
        return
    equipo = socket.gethostname().lower()
    hosts = ["localhost", "127.0.0.1"] if local else ["localhost", "127.0.0.1", equipo] + ips_locales()
    origenes = [f"http://{h}:{puerto}" for h in hosts]
    lineas = [
        "DJANGO_ENV=production", "DJANGO_DEBUG=0", f"DJANGO_SECRET_KEY={secrets.token_urlsafe(50)}",
        "# No cambie PSEUDONIMO_KEY después de cargar RIPS o facturación",
        f"PSEUDONIMO_KEY={secrets.token_urlsafe(50)}",
        f"DJANGO_ALLOWED_HOSTS={','.join(hosts)}", f"DJANGO_CSRF_TRUSTED_ORIGINS={','.join(origenes)}",
        "# Red interna sin certificado. Si publica con HTTPS (IIS/nginx) ponga USAR_HTTPS=1 y SECURE_SSL_REDIRECT=1",
        "USAR_HTTPS=0", "SECURE_SSL_REDIRECT=0",
        f"MEDIA_ROOT={destino / 'datos' / 'media'}", f"ABC_PUERTO={puerto}", "CARGA_MAX_MB=25", "SESSION_COOKIE_AGE=1800",
        f"DATABASE_URL=sqlite:///{(destino / 'datos' / 'db.sqlite3').as_posix()}",
    ]
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    if os.name == "nt" and not local:  # solo administradores y SYSTEM leen las claves (modo servidor)
        ejecutar(log, ["icacls", str(ruta), "/inheritance:r", "/grant:r", "*S-1-5-32-544:F", "*S-1-5-18:F"], obligatorio=False)
    log.escribir(f"[OK] .env creado (accesos: {', '.join(hosts)})")


def escribir_lanzadores(destino, python, puerto, log, local=False):
    # cmd.exe lee los .bat en la página de códigos OEM (rutas de usuario con tildes o ñ)
    codificacion = "oem" if os.name == "nt" else "ascii"
    host = "--host 127.0.0.1 " if local else ""
    (destino / "iniciar_servidor.bat").write_text(
        "@echo off\r\nset PYTHONUTF8=1\r\n"
        f'cd /d "{destino}"\r\n'
        f'"{python}" manage.py servir {host}--puerto {puerto} < NUL >> "{destino}\\datos\\logs\\servidor.log" 2>&1\r\n',
        encoding=codificacion)
    (destino / "respaldar.bat").write_text(
        "@echo off\r\nset PYTHONUTF8=1\r\n"
        f'"{python}" "{destino}\\instalador\\windows\\respaldar.py" "{destino}" >> "{destino}\\datos\\logs\\respaldo.log" 2>&1\r\n',
        encoding=codificacion)
    log.escribir("[OK] iniciar_servidor.bat y respaldar.bat")


def xml_tarea(comando, disparador, reiniciar):
    reinicio = ("<RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>" if reiniciar else "")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Costos ABC</Description></RegistrationInfo>
  <Triggers>{disparador}</Triggers>
  <Principals><Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    {reinicio}
  </Settings>
  <Actions Context="Author"><Exec><Command>cmd.exe</Command><Arguments>/c "{escape(str(comando))}"</Arguments></Exec></Actions>
</Task>
"""


def registrar_tareas(destino, puerto, log):
    """Devuelve la lista de tareas que no se pudieron registrar."""
    advertencias = []
    for nombre, bat, disparador, reiniciar in (
        (TAREA, destino / "iniciar_servidor.bat", "<BootTrigger><Enabled>true</Enabled></BootTrigger>", True),
        (TAREA_RESPALDO, destino / "respaldar.bat",
         "<CalendarTrigger><StartBoundary>2025-01-01T23:00:00</StartBoundary><ScheduleByDay><DaysInterval>1</DaysInterval>"
         "</ScheduleByDay></CalendarTrigger>", False),
    ):
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as f:
            f.write(xml_tarea(bat, disparador, reiniciar))
        try:
            rc = ejecutar(log, ["schtasks", "/Create", "/TN", nombre, "/XML", f.name, "/F"], obligatorio=False)
        finally:
            os.unlink(f.name)
        if rc != 0:  # alternativa sin XML (sin reinicio automático ante fallos)
            programa = ["/SC", "ONSTART"] if reiniciar else ["/SC", "DAILY", "/ST", "23:00"]
            rc = ejecutar(log, ["schtasks", "/Create", "/TN", nombre, "/TR", f'cmd.exe /c "{bat}"', *programa,
                                "/RU", "SYSTEM", "/RL", "HIGHEST", "/F"], obligatorio=False)
        if rc != 0:
            log.escribir(f"[!!] No se pudo registrar la tarea '{nombre}'. Créela en el Programador de tareas: "
                         f"ejecutar {bat} como SYSTEM {'al iniciar el equipo' if reiniciar else 'cada día a las 23:00'}.")
            advertencias.append(nombre)
    regla = f"Costos ABC ({puerto})"
    ejecutar(log, ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={regla}"], obligatorio=False)
    ejecutar(log, ["netsh", "advfirewall", "firewall", "add", "rule", f"name={regla}", "dir=in", "action=allow",
                   "protocol=TCP", f"localport={puerto}", "profile=domain,private"], obligatorio=False)
    if not advertencias:
        log.escribir(f"[OK] Tareas '{TAREA}' (al iniciar Windows) y '{TAREA_RESPALDO}' (23:00); puerto {puerto} abierto")
    return advertencias


def comando_servidor(destino, python, puerto):
    """Servidor sin ventana (pythonw) solo para este equipo, con registro en archivo."""
    pythonw = python.with_name("pythonw.exe") if python.with_name("pythonw.exe").exists() else python
    return [str(pythonw), str(destino / "manage.py"), "servir", "--host", "127.0.0.1", "--puerto", str(puerto),
            "--respaldo-diario", "--log", str(destino / "datos" / "logs" / "servidor.log")]


def registrar_usuario(destino, python, puerto, log):
    """Modo sin administrador: inicio al iniciar sesión (HKCU\\...\\Run). Nada aquí pide permisos de administrador:
    no usa el Programador de tareas, el firewall ni carpetas del sistema; el respaldo diario lo hace el servidor."""
    advertencias = []
    comando = subprocess.list2cmdline(comando_servidor(destino, python, puerto))
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as clave:
            winreg.SetValueEx(clave, "CostosABC", 0, winreg.REG_SZ, comando)
        log.escribir("[OK] Costos ABC iniciará automáticamente al iniciar sesión en Windows")
    except Exception as exc:
        log.escribir(f"[!!] No se pudo programar el inicio automático: {exc}")
        advertencias.append("inicio")
    # Sin Programador de tareas (schtasks puede pedir credenciales): el propio servidor respalda cada 24 h.
    log.escribir(f"[OK] Respaldo diario automático en {destino / 'datos' / 'respaldos'} (lo hace la aplicación, 30 días)")
    return advertencias


def iniciar_directo(destino, python, puerto, local):
    if local:
        orden = comando_servidor(destino, python, puerto)
    else:
        orden = ["cmd.exe", "/c", str(destino / "iniciar_servidor.bat")]
    subprocess.Popen(orden, cwd=destino,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)


def esperar_servidor(puerto, log, segundos=90):
    url = f"http://127.0.0.1:{puerto}/cuenta/ingresar/"
    limite = time.time() + segundos
    while time.time() < limite:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    log.escribir(f"[OK] La aplicación responde en http://localhost:{puerto}/")
                    return True
        except Exception:
            time.sleep(3)
    log.escribir("[!!] La aplicación no respondió a tiempo; revise datos\\logs\\servidor.log")
    return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--destino", required=True)
    p.add_argument("--puerto", type=int, default=8000)
    p.add_argument("--codigo", default="")
    p.add_argument("--nombre", default="")
    p.add_argument("--nit", default="")
    p.add_argument("--municipio", default="")
    p.add_argument("--usuario", default="admin")
    p.add_argument("--demo", default="N")
    p.add_argument("--actualizar", action="store_true", help="Solo migra, reescribe lanzadores y reinicia.")
    p.add_argument("--sin-servicio", action="store_true", help="No registra tareas ni firewall (pruebas).")
    p.add_argument("--por-usuario", action="store_true",
                   help="Instalación sin permisos de administrador: solo este equipo, inicia al iniciar sesión.")
    a = p.parse_args()
    destino = Path(a.destino).resolve()
    python = Path(sys.executable)
    for d in ("datos/media", "datos/logs", "datos/respaldos"):
        (destino / d).mkdir(parents=True, exist_ok=True)
    log = Registro(destino / "datos" / "logs" / "instalacion.log")
    entorno = dict(os.environ, PYTHONUTF8="1")
    manage = [str(python), str(destino / "manage.py")]
    try:
        local = a.por_usuario
        crear_env(destino, a.puerto, log, local)
        if a.actualizar:
            ejecutar(log, manage + ["migrate", "--noinput"], entorno, cwd=destino)
            ejecutar(log, manage + ["collectstatic", "--noinput", "-v", "0"], entorno, cwd=destino)
        else:
            # Sin contraseña: el administrador la crea en el navegador con el enlace de primer acceso.
            comando = manage + ["instalar", "--puerto", str(a.puerto), "--institucion-codigo", a.codigo, "--institucion-nombre", a.nombre,
                                "--nit", a.nit, "--municipio", a.municipio, "--usuario", a.usuario]
            if a.demo.upper().startswith("S"):
                comando.append("--demo")
            ejecutar(log, comando, entorno, cwd=destino)
        escribir_lanzadores(destino, python, a.puerto, log, local)
        if a.sin_servicio:
            return 0
        if local:
            advertencias = registrar_usuario(destino, python, a.puerto, log)
            iniciar_directo(destino, python, a.puerto, local)
        else:
            advertencias = registrar_tareas(destino, a.puerto, log)
            ejecutar(log, ["schtasks", "/End", "/TN", TAREA], obligatorio=False)
            if advertencias or ejecutar(log, ["schtasks", "/Run", "/TN", TAREA], obligatorio=False) != 0:
                log.escribir("[--] No se pudo iniciar la tarea; se inicia el servidor directamente")
                iniciar_directo(destino, python, a.puerto, local)
        responde = esperar_servidor(a.puerto, log)
        log.escribir("Configuración terminada" + (" con advertencias." if advertencias or not responde else "."))
        return 2 if advertencias or not responde else 0
    except Exception as exc:
        log.escribir(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

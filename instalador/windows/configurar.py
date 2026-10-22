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


def crear_env(destino, puerto, log):
    ruta = destino / ".env"
    if ruta.exists():
        log.escribir("[--] .env existente conservado (no se regeneran las claves)")
        return
    equipo = socket.gethostname().lower()
    hosts = ["localhost", "127.0.0.1", equipo] + ips_locales()
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
    if os.name == "nt":  # solo administradores y SYSTEM leen las claves
        ejecutar(log, ["icacls", str(ruta), "/inheritance:r", "/grant:r", "*S-1-5-32-544:F", "*S-1-5-18:F"], obligatorio=False)
    log.escribir(f"[OK] .env creado (accesos: {', '.join(hosts)})")


def escribir_lanzadores(destino, python, puerto, log):
    (destino / "iniciar_servidor.bat").write_text(
        "@echo off\r\nset PYTHONUTF8=1\r\n"
        f'cd /d "{destino}"\r\n'
        f'"{python}" manage.py servir --puerto {puerto} < NUL >> "{destino}\\datos\\logs\\servidor.log" 2>&1\r\n',
        encoding="ascii")
    (destino / "respaldar.bat").write_text(
        "@echo off\r\nset PYTHONUTF8=1\r\n"
        f'"{python}" "{destino}\\instalador\\windows\\respaldar.py" "{destino}" >> "{destino}\\datos\\logs\\respaldo.log" 2>&1\r\n',
        encoding="ascii")
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
    a = p.parse_args()
    destino = Path(a.destino).resolve()
    python = Path(sys.executable)
    for d in ("datos/media", "datos/logs", "datos/respaldos"):
        (destino / d).mkdir(parents=True, exist_ok=True)
    log = Registro(destino / "datos" / "logs" / "instalacion.log")
    entorno = dict(os.environ, PYTHONUTF8="1")
    manage = [str(python), str(destino / "manage.py")]
    try:
        crear_env(destino, a.puerto, log)
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
        escribir_lanzadores(destino, python, a.puerto, log)
        if a.sin_servicio:
            return 0
        advertencias = registrar_tareas(destino, a.puerto, log)
        ejecutar(log, ["schtasks", "/End", "/TN", TAREA], obligatorio=False)
        if advertencias or ejecutar(log, ["schtasks", "/Run", "/TN", TAREA], obligatorio=False) != 0:
            log.escribir("[--] No se pudo iniciar la tarea; se inicia el servidor directamente")
            subprocess.Popen(["cmd.exe", "/c", str(destino / "iniciar_servidor.bat")], cwd=destino,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        responde = esperar_servidor(a.puerto, log)
        log.escribir("Configuración terminada" + (" con advertencias." if advertencias or not responde else "."))
        return 2 if advertencias or not responde else 0
    except Exception as exc:
        log.escribir(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

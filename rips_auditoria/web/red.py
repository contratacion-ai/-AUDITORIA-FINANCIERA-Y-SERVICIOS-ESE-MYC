"""Acceso desde otros computadores de la red (modo «en red»).

Por defecto la ventana de cargue sólo atiende al propio equipo (127.0.0.1).
Con ``--red`` (o ``red.habilitada: true`` en configuracion.yaml, o la
variable ``RIPS_RED=1``) escucha en la red local y cualquier equipo de la
ESE puede abrir ``http://<IP del servidor>:8100``.

Seguridad (son datos de facturación de pacientes):

* Los equipos remotos deben escribir una **clave de acceso**. Se toma de
  ``red.clave`` en configuracion.yaml o se genera una sola vez y se guarda en
  ``<datos>/acceso_red.json``; la consola del servidor la muestra al iniciar.
  Tras 5 intentos fallidos desde una IP, esa IP espera 2 minutos.
* El propio servidor (127.0.0.1) no necesita clave.
* La clave queda en una cookie HttpOnly con un token derivado (no la clave).

Aplicativo de Auditoría ESE MyC (v5): su lanzador escucha sólo en
127.0.0.1:8000 y no se modifica. En modo red se publica un **puente** en el
mismo puerto (``http://<IP del servidor>:8000``) que exige la misma clave y
reenvía cada petición al aplicativo local. Así los demás equipos usan el
aplicativo de auditoría y el cargue con una sola clave.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie

from ..config import cargar_configuracion, carpeta_datos

log = logging.getLogger(__name__)

COOKIE = "ese_myc_acceso"
LOCALES = {"127.0.0.1", "::1", "localhost"}
ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O ni 1/I
INTENTOS_MAXIMOS = 5
BLOQUEO_SEGUNDOS = 120
LIBRES = ("/acceso", "/static/logo_ese.png", "/static/icono_ese.png", "/favicon.ico")

_fallos: dict[str, tuple[int, float]] = {}


def red_habilitada(cfg: dict | None = None) -> bool:
    if os.environ.get("RIPS_RED", "").strip().lower() in ("1", "true", "si", "sí"):
        return True
    return bool(((cfg or cargar_configuracion()).get("red") or {}).get("habilitada"))


def clave_acceso(cfg: dict | None = None) -> str:
    """Clave de configuracion.yaml (red.clave) o la generada y guardada en los datos."""
    fija = ((cfg or cargar_configuracion()).get("red") or {}).get("clave")
    if fija:
        return str(fija).strip()
    ruta = carpeta_datos() / "acceso_red.json"
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))["clave"]
    except (OSError, ValueError, KeyError):
        clave = "".join(secrets.choice(ALFABETO) for _ in range(8))
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps({"clave": clave}), encoding="utf-8")
        return clave


def token(clave: str) -> str:
    return hashlib.sha256(f"ese-myc|{clave.strip().upper()}".encode()).hexdigest()[:40]


def ips_locales() -> list[str]:
    """Direcciones IPv4 de este equipo en la red local (sin 127.x)."""
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # interfaz de la ruta por defecto (no envía datos)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127.") and not ip.startswith("169.254."))


def _cliente(scope) -> str:
    c = scope.get("client")
    return c[0] if c else ""


def _cookie(scope) -> str:
    for k, v in scope.get("headers") or []:
        if k == b"cookie":
            c = SimpleCookie()
            try:
                c.load(v.decode("latin-1"))
            except Exception:  # cookie mal formada = sin acceso
                return ""
            if COOKIE in c:
                return c[COOKIE].value
    return ""


async def _responder(send, estado: int, cuerpo: bytes, tipo: str = "text/html; charset=utf-8", extra=()):
    await send({"type": "http.response.start", "status": estado,
                "headers": [(b"content-type", tipo.encode()), (b"content-length", str(len(cuerpo)).encode()),
                            (b"cache-control", b"no-store"), *extra]})
    await send({"type": "http.response.body", "body": cuerpo})


def pagina_acceso(siguiente: str = "/", error: str = "", titulo: str = "Cargue RIPS y Tablero") -> bytes:
    s = html.escape(siguiente, quote=True)
    e = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Acceso · ESE Moreno y Clavijo</title><link rel="icon" href="/favicon.ico"><style>
body{{margin:0;font-family:"Segoe UI",Calibri,Arial,sans-serif;background:#F4F7F4;color:#1D2B22;display:grid;place-items:center;min-height:100vh}}
form{{background:#fff;border:1px solid #DCE3DD;border-top:6px solid #18A020;border-radius:14px;padding:28px;width:min(380px,92vw);box-shadow:0 2px 10px rgba(16,40,20,.08)}}
img{{height:56px}} h1{{font-size:19px;color:#0E6B16;margin:14px 0 4px}} p{{color:#5F6B63;font-size:14px;margin:0 0 14px}}
input{{width:100%;box-sizing:border-box;font:inherit;font-size:18px;letter-spacing:3px;text-transform:uppercase;padding:11px;border:1.5px solid #DCE3DD;border-radius:10px}}
button{{width:100%;margin-top:12px;font:inherit;font-weight:600;border:0;border-radius:10px;padding:12px;background:#A01038;color:#fff;cursor:pointer}}
.err{{color:#B3261E;font-weight:600}}</style></head><body>
<form method="post" action="/acceso"><img src="/static/logo_ese.png" alt="ESE Moreno y Clavijo" onerror="this.remove()">
<h1>{html.escape(titulo)}</h1><p>Escriba la clave de acceso que muestra la ventana del servidor (o pídasela al administrador).</p>{e}
<input name="clave" autocomplete="current-password" autofocus required><input type="hidden" name="siguiente" value="{s}">
<button>Entrar</button></form></body></html>""".encode()


class ProteccionRed:
    """Middleware ASGI: los clientes que no son este equipo necesitan la clave."""

    def __init__(self, app, titulo: str = "Cargue RIPS y Tablero", clave=None):
        self.app = app
        self.titulo = titulo
        self._clave = clave  # función que devuelve la clave (se lee una vez por petición remota)

    def clave(self) -> str:
        return self._clave() if self._clave else clave_acceso()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or _cliente(scope) in LOCALES:
            return await self.app(scope, receive, send)
        ruta = scope.get("path", "/")
        clave = self.clave()
        if ruta == "/acceso":
            return await self._acceso(scope, receive, send, clave)
        if secrets.compare_digest(_cookie(scope), token(clave)) or ruta in LIBRES:
            return await self.app(scope, receive, send)
        if ruta.startswith("/api/"):
            return await _responder(send, 401, json.dumps({"detail": "Se requiere la clave de acceso"}).encode(),
                                    "application/json")
        destino = ruta + (("?" + scope["query_string"].decode()) if scope.get("query_string") else "")
        return await _responder(send, 303, b"", extra=[(b"location", ("/acceso?siguiente=" + urllib.parse.quote(destino)).encode())])

    async def _acceso(self, scope, receive, send, clave):
        q = urllib.parse.parse_qs(scope.get("query_string", b"").decode())
        if scope["method"] != "POST":
            return await _responder(send, 200, pagina_acceso(q.get("siguiente", ["/"])[0], titulo=self.titulo))
        cuerpo = b""
        while True:
            m = await receive()
            cuerpo += m.get("body", b"")
            if not m.get("more_body") or len(cuerpo) > 10_000:
                break
        datos = urllib.parse.parse_qs(cuerpo.decode("utf-8", "replace"))
        siguiente = datos.get("siguiente", ["/"])[0]
        if not siguiente.startswith("/") or siguiente.startswith("//"):
            siguiente = "/"
        ip = _cliente(scope)
        n, hasta = _fallos.get(ip, (0, 0.0))
        if hasta > time.monotonic():
            return await _responder(send, 429, pagina_acceso(siguiente, "Demasiados intentos. Espere 2 minutos.", self.titulo))
        if secrets.compare_digest(token(datos.get("clave", [""])[0]), token(clave)):
            _fallos.pop(ip, None)
            galleta = f"{COOKIE}={token(clave)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=43200"
            return await _responder(send, 303, b"", extra=[(b"location", siguiente.encode()), (b"set-cookie", galleta.encode())])
        n += 1
        _fallos[ip] = (0, time.monotonic() + BLOQUEO_SEGUNDOS) if n >= INTENTOS_MAXIMOS else (n, 0.0)
        log.warning("Clave incorrecta desde %s", ip)
        return await _responder(send, 401, pagina_acceso(siguiente, "Clave incorrecta.", self.titulo))


# ---------------------------------------------------------------------------
# Puente hacia el aplicativo de auditoría (v5) para los equipos de la red
# ---------------------------------------------------------------------------

SALTAR = {b"host", b"connection", b"keep-alive", b"transfer-encoding", b"upgrade", b"accept-encoding", b"content-length"}


class PuenteAuditoria:
    """App ASGI que reenvía todo a ``destino`` (p. ej. http://127.0.0.1:8000)."""

    def __init__(self, destino: str, timeout: float = 600):
        self.destino = destino.rstrip("/")
        self.timeout = timeout

    def _pedir(self, metodo: str, url: str, cuerpo: bytes, cabeceras: dict):
        req = urllib.request.Request(url, data=cuerpo if metodo not in ("GET", "HEAD") else None, method=metodo,
                                     headers=cabeceras)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.getheaders(), r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.items(), exc.read()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        cuerpo = b""
        while True:
            m = await receive()
            cuerpo += m.get("body", b"")
            if not m.get("more_body"):
                break
        url = self.destino + scope.get("raw_path", scope["path"].encode()).decode("latin-1")
        if scope.get("query_string"):
            url += "?" + scope["query_string"].decode("latin-1")
        cabeceras = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"] if k.lower() not in SALTAR}
        try:
            estado, hdrs, datos = await asyncio.to_thread(self._pedir, scope["method"], url, cuerpo, cabeceras)
        except (OSError, urllib.error.URLError):
            return await _responder(send, 503, (
                '<!doctype html><meta charset="utf-8"><body style="font-family:Segoe UI,Arial;padding:40px;color:#1D2B22">'
                '<h2 style="color:#A01038">El aplicativo de Auditoría ESE MyC está cerrado en el servidor</h2>'
                "<p>Pida que lo abran en el equipo servidor (acceso «Auditoría ESE MyC») y recargue esta página.</p>").encode())
        salida = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in hdrs
                  if k.lower().encode("latin-1") not in SALTAR]
        salida.append((b"content-length", str(len(datos)).encode()))
        await send({"type": "http.response.start", "status": estado, "headers": salida})
        await send({"type": "http.response.body", "body": datos})


def iniciar_puente(destino: str, puerto: int, ips: list[str], titulo: str = "Auditoría de Facturación ESE MyC"):
    """Sirve el puente en ``ips``:``puerto`` en un hilo. No ocupa 127.0.0.1, así el
    aplicativo v5 puede seguir abriendo su propio 127.0.0.1:puerto."""
    import threading

    import uvicorn

    socks = []
    for ip in ips:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((ip, puerto))
        except OSError as exc:
            log.warning("No se pudo abrir el puente %s:%s (%s)", ip, puerto, exc)
            s.close()
            continue
        socks.append(s)
    if not socks:
        return None
    app = ProteccionRed(PuenteAuditoria(destino), titulo=titulo)
    servidor = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    threading.Thread(target=servidor.run, kwargs={"sockets": socks}, daemon=True).start()
    return [f"http://{s.getsockname()[0]}:{puerto}" for s in socks]

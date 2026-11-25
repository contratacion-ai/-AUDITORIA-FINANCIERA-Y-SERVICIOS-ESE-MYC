"""Vínculo con la carpeta de RIPS en Google Drive (JSON + XML + CUV por hospital y mes).

Estructura esperada en Drive (la de facturación de la ESE)::

    Rips TXT y JSON 2026/
      Hospital San Antonio/
        01. Enero/
          JSON ENERO/FCSJ6099/FCSJ6099.json, FCSJ6099.xml, ResultadosMSPS_FCSJ6099_…_CUV.txt
          TXT ENERO/…                       (RIPS Res. 3374, si los hay)
        02. Febrero/…
      Centro de Salud Jesus Coronel/…

Dos formas de conectarse (``drive.modo`` en la configuración o en la ventana):

* ``local`` — **Google Drive para escritorio**: la carpeta (o un acceso directo
  a ella en «Mi unidad») se sincroniza en el equipo, p. ej.
  ``G:\\Mi unidad\\Rips TXT y JSON 2026``. No necesita credenciales.
* ``api`` — **API de Google Drive**: la aplicación descarga una copia (espejo)
  de la carpeta con su identificador o enlace. Requiere un cliente OAuth de
  «Aplicación de escritorio» de Google Cloud (archivo de credenciales JSON) y
  autorizar una vez con la cuenta institucional (permiso de sólo lectura).
  Sólo se descargan los archivos nuevos o modificados.

En ambos casos se procesa sólo lo que cambió desde la última vez: por cada
**hospital y mes** con archivos nuevos se leen sus RIPS (TXT y JSON con su
XML y su CUV), se agregan al acumulado sin duplicar y se envían esos meses al
aplicativo de Auditoría de Facturación (todas las sedes del mes). El estado
queda en ``<datos>/drive/``.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import secrets
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import carpeta_datos
from .lectura import detectar_mes

log = logging.getLogger(__name__)

DEFAULTS = {
    "modo": "local",              # local | api
    "carpeta_local": "",          # ruta de la carpeta sincronizada por Google Drive para escritorio
    "carpeta_id": "",             # id o enlace de la carpeta en Drive (modo api)
    "revisar_cada_min": 0,        # 0 = sólo a mano; > 0 = revisión automática mientras la ventana esté abierta
    "enviar_aplicativo": True,    # enviar los meses actualizados al aplicativo de auditoría
    "extensiones": [".json", ".xml", ".txt", ".zip"],
}
API = "https://www.googleapis.com/drive/v3"
URL_TOKEN = "https://oauth2.googleapis.com/token"
URL_AUTORIZACION = "https://accounts.google.com/o/oauth2/v2/auth"
ALCANCE = "https://www.googleapis.com/auth/drive.readonly"
CARPETA = "application/vnd.google-apps.folder"
ACCESO_DIRECTO = "application/vnd.google-apps.shortcut"


class ErrorDrive(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Configuración y estado (en <datos>/drive)
# ---------------------------------------------------------------------------

def carpeta_drive() -> Path:
    return carpeta_datos() / "drive"


def _leer(nombre: str, defecto):
    ruta = carpeta_drive() / nombre
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def _guardar(nombre: str, datos) -> None:
    ruta = carpeta_drive() / nombre
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ruta)


def configuracion(cfg: dict | None = None) -> dict:
    """DEFAULTS + ``drive`` de configuracion.yaml + lo guardado desde la ventana."""
    return {**DEFAULTS, **((cfg or {}).get("drive") or {}), **_leer("config.json", {})}


def guardar_configuracion(cambios: dict) -> dict:
    actual = _leer("config.json", {})
    permitidas = {"modo", "carpeta_local", "carpeta_id", "revisar_cada_min", "enviar_aplicativo"}
    actual.update({k: v for k, v in cambios.items() if k in permitidas and v is not None})
    if "carpeta_id" in actual:
        actual["carpeta_id"] = id_de_enlace(actual["carpeta_id"])
    if actual.get("modo") not in ("local", "api"):
        actual["modo"] = "local"
    actual["revisar_cada_min"] = max(0, int(actual.get("revisar_cada_min") or 0))
    _guardar("config.json", actual)
    return actual


def id_de_enlace(texto: str) -> str:
    """'https://drive.google.com/drive/folders/<id>?usp=…' -> '<id>' (o el texto si ya es un id)."""
    t = (texto or "").strip()
    m = re.search(r"/folders/([\w-]{10,})", t) or re.search(r"[?&]id=([\w-]{10,})", t)
    return m.group(1) if m else t


_MARCAS_DRIVE = ("mi unidad", "my drive", "unidades compartidas", "shared drives", "otros ordenadores", "other computers",
                 "compartido conmigo", "shared with me")


def unidades_drive() -> list[Path]:
    """Raíces de Google Drive para escritorio en este equipo: «G:\\Mi unidad», «G:\\Unidades compartidas»…
    (en cualquier letra de unidad) y las carpetas espejo de la carpeta personal."""
    raices: list[Path] = []
    letras = [Path(f"{x}:/") for x in string.ascii_uppercase[3:]] if os.name == "nt" else []
    for raiz in letras + [Path.home(), Path.home() / "Google Drive", Path.home() / "GoogleDrive"]:
        try:
            if not raiz.is_dir():
                continue
            for x in raiz.iterdir():
                # «.shortcut-targets-by-id»: donde Drive para escritorio pone el destino de los accesos directos
                if x.is_dir() and (x.name.lower() in _MARCAS_DRIVE or x.name.lower() == ".shortcut-targets-by-id"):
                    raices.append(x)
        except OSError:
            continue
    return raices


def _parece_raiz_rips(carpeta: Path) -> bool:
    """Carpeta con hospitales que tienen carpetas de mes («Hospital X/01. Enero»)."""
    try:
        for h in list(carpeta.iterdir())[:40]:
            if h.is_dir() and any(m.is_dir() and detectar_mes(m.name)[0] for m in list(h.iterdir())[:40]):
                return True
    except OSError:
        pass
    return False


def destino_lnk(ruta: Path) -> Path | None:
    """Carpeta a la que apunta un acceso directo de Windows (.lnk), como los que crea Drive para escritorio."""
    import struct

    try:
        d = ruta.read_bytes()
        if len(d) < 76 or d[:4] != b"\x4c\x00\x00\x00":
            return None
        flags = struct.unpack_from("<I", d, 20)[0]
        pos = 76
        if flags & 0x1:  # HasLinkTargetIDList
            pos += 2 + struct.unpack_from("<H", d, pos)[0]
        if flags & 0x2:  # HasLinkInfo
            inicio = pos
            cab = struct.unpack_from("<I", d, inicio + 4)[0]
            base = struct.unpack_from("<I", d, inicio + 16)[0]
            if cab >= 0x24:  # ruta en Unicode
                off = struct.unpack_from("<I", d, inicio + 28)[0]
                fin = d.index(b"\x00\x00", inicio + off)
                while (fin - inicio - off) % 2:
                    fin = d.index(b"\x00\x00", fin + 1)
                destino = d[inicio + off:fin].decode("utf-16-le", "ignore")
            else:
                fin = d.index(b"\x00", inicio + base)
                destino = d[inicio + base:fin].decode("mbcs" if os.name == "nt" else "latin-1", "ignore")
            if destino:
                return Path(destino)
    except (OSError, ValueError, struct.error, LookupError):
        pass
    if os.name == "nt":  # respaldo: el propio Windows resuelve el acceso directo
        import subprocess

        try:
            ps = str(ruta).replace("'", "''")
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                f"(New-Object -ComObject WScript.Shell).CreateShortcut('{ps}').TargetPath"],
                               capture_output=True, text=True, timeout=15, creationflags=0x08000000)
            t = r.stdout.strip()
            return Path(t) if t else None
        except (OSError, subprocess.SubprocessError):
            return None
    return None


def _subcarpetas(carpeta: Path) -> list[Path]:
    """Subcarpetas, incluidos los accesos directos (.lnk) a carpetas, ya resueltos."""
    salida = []
    for x in sorted(carpeta.iterdir()):
        if x.is_dir():
            salida.append(x)
        elif x.suffix.lower() == ".lnk":
            destino = destino_lnk(x)
            if destino and destino.is_dir():
                salida.append(destino)
    return salida


def buscar_carpetas_locales(nombre: str = "rips", profundidad: int = 4, limite: int = 3000) -> list[Path]:
    """Carpetas candidatas dentro de las unidades de Google Drive: las que se llaman «…RIPS…» o tienen la
    estructura Hospital/Mes. Las de nombre «Rips TXT y JSON» van primero."""
    candidatas, vistas = [], 0
    for raiz in unidades_drive():
        pendientes = [(raiz, 0)]
        while pendientes and vistas < limite:
            carpeta, nivel = pendientes.pop(0)
            try:
                hijos = _subcarpetas(carpeta)
            except OSError:
                continue
            for x in hijos:
                vistas += 1
                if detectar_mes(x.name)[0]:
                    continue
                if nombre in x.name.lower() and _parece_raiz_rips(x) or (nivel == 0 and _parece_raiz_rips(x)):
                    candidatas.append(x)
                elif nivel + 1 < profundidad:
                    pendientes.append((x, nivel + 1))
    candidatas.sort(key=lambda x: ("rips txt y json" not in x.name.lower(), len(str(x))))
    return list(dict.fromkeys(candidatas))


def buscar_carpeta_local(nombre: str = "Rips TXT y JSON") -> Path | None:
    """La mejor candidata (ver ``buscar_carpetas_locales``) o None."""
    c = buscar_carpetas_locales()
    return c[0] if c else None


def diagnostico_local(ruta: str = "") -> str:
    """Texto para el usuario: qué ruta se probó y qué hay en las unidades de Google Drive del equipo."""
    if ruta.lower().startswith(("http://", "https://")):
        partes = ["En este campo va la carpeta del computador (p. ej. G:\\Mi unidad\\Rips TXT y JSON 2026), no el enlace "
                  "web de Drive; pulse «Buscar la carpeta en este equipo»."]
    else:
        partes = [f"No existe la carpeta «{ruta}» en este equipo." if ruta else "No se escribió la ruta de la carpeta."]
    raices = unidades_drive()
    if not raices:
        partes.append("No se encontró Google Drive para escritorio en este equipo (ninguna unidad con «Mi unidad»): "
                      "ábralo e inicie sesión con la cuenta institucional, o use los ZIP descargados de drive.google.com.")
    for r in raices[:5]:
        try:
            hijos = sorted(x for x in r.iterdir() if x.is_dir() or x.suffix.lower() == ".lnk")
        except OSError:
            hijos = []
        partes.append(f"En «{r}» hay: {', '.join(x.name for x in hijos[:15]) or '(vacío)'}" +
                      (" …" if len(hijos) > 15 else "") + ".")
        for h in hijos:  # dentro de las carpetas «RIPS…»: qué hay (para ver dónde quedó el acceso directo)
            if not any(k in h.name.lower() for k in ("rips", "json", "jason")):
                continue
            try:
                nietos = sorted(h.iterdir())[:12] if h.is_dir() else []
            except OSError:
                nietos = []
            partes.append(f"Dentro de «{h.name}»: " + (", ".join(
                x.name + (" (acceso directo)" if x.suffix.lower() == ".lnk" else "" if x.is_dir() else " (archivo)")
                for x in nietos) or "(vacío)") + ".")
    if raices:
        partes.append("Si «Rips TXT y JSON 2026» no aparece en «Mi unidad»: en drive.google.com > Compartido conmigo, clic "
                      "derecho sobre la carpeta > Organizar > Agregar acceso directo > Mi unidad; espere un minuto y pulse "
                      "«Buscar la carpeta».")
    return " ".join(partes)


# ---------------------------------------------------------------------------
# API de Google Drive (sólo librería estándar)
# ---------------------------------------------------------------------------

def cargar_credenciales(ruta_o_datos) -> dict:
    """Archivo de cliente OAuth descargado de Google Cloud (``{"installed": {...}}``)."""
    datos = ruta_o_datos if isinstance(ruta_o_datos, dict) else json.loads(Path(ruta_o_datos).read_text(encoding="utf-8"))
    c = datos.get("installed") or datos.get("web") or datos
    if not c.get("client_id") or not c.get("client_secret"):
        raise ErrorDrive("El archivo no es un cliente OAuth de Google (falta client_id o client_secret). Descárguelo en "
                         "Google Cloud > APIs y servicios > Credenciales > ID de cliente OAuth (Aplicación de escritorio).")
    return {"client_id": c["client_id"], "client_secret": c["client_secret"],
            "token_uri": c.get("token_uri") or URL_TOKEN, "auth_uri": c.get("auth_uri") or URL_AUTORIZACION}


def guardar_credenciales(datos: dict) -> dict:
    c = cargar_credenciales(datos)
    _guardar("credenciales.json", c)
    return c


def estado_api() -> dict:
    cred, token = _leer("credenciales.json", None), _leer("token.json", None)
    return {"credenciales": bool(cred), "autorizado": bool(token and token.get("refresh_token")),
            "cuenta": (token or {}).get("cuenta", "")}


def url_autorizacion(redirect_uri: str) -> tuple[str, str]:
    cred = _leer("credenciales.json", None)
    if not cred:
        raise ErrorDrive("Primero cargue el archivo de credenciales OAuth de Google (Aplicación de escritorio).")
    estado = secrets.token_urlsafe(16)
    _guardar("oauth_pendiente.json", {"state": estado, "redirect_uri": redirect_uri})
    q = urllib.parse.urlencode({"client_id": cred["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
                                "scope": ALCANCE, "access_type": "offline", "prompt": "consent", "state": estado})
    return f"{cred['auth_uri']}?{q}", estado


def _post_formulario(url: str, datos: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(datos).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        raise ErrorDrive(f"Google rechazó la autorización ({exc.code}): {exc.read().decode(errors='ignore')[:300]}") from exc
    except urllib.error.URLError as exc:
        raise ErrorDrive(f"No hay conexión con Google ({exc.reason}). Revise internet o el proxy.") from exc


def canjear_codigo(codigo: str, estado: str) -> dict:
    """Paso final de la autorización: código -> token (con refresh_token), guardado en <datos>/drive."""
    pendiente, cred = _leer("oauth_pendiente.json", {}), _leer("credenciales.json", None)
    if not cred or not pendiente or not secrets.compare_digest(pendiente.get("state", ""), estado or ""):
        raise ErrorDrive("La autorización no corresponde a la solicitada desde esta aplicación. Vuelva a conectar.")
    tok = _post_formulario(cred["token_uri"], {"code": codigo, "client_id": cred["client_id"],
                                               "client_secret": cred["client_secret"],
                                               "redirect_uri": pendiente["redirect_uri"],
                                               "grant_type": "authorization_code"})
    if not tok.get("refresh_token"):
        raise ErrorDrive("Google no entregó el permiso permanente (refresh_token). Quite el acceso de la aplicación en "
                         "myaccount.google.com/permissions y vuelva a conectar.")
    tok["vence"] = time.time() + float(tok.get("expires_in") or 3000) - 60
    _guardar("token.json", tok)
    (carpeta_drive() / "oauth_pendiente.json").unlink(missing_ok=True)
    try:
        cuenta = ClienteDrive().cuenta()
        _guardar("token.json", {**tok, "cuenta": cuenta})
    except ErrorDrive:
        pass
    return estado_api()


def desconectar() -> None:
    for n in ("token.json", "oauth_pendiente.json"):
        (carpeta_drive() / n).unlink(missing_ok=True)


class ClienteDrive:
    """Lectura de carpetas y descarga de archivos con el token guardado."""

    def __init__(self, api: str = API):
        self.api = api.rstrip("/")
        self.cred = _leer("credenciales.json", None)
        self.token = _leer("token.json", None)
        if not self.cred or not self.token or not self.token.get("refresh_token"):
            raise ErrorDrive("La aplicación no está conectada a Google Drive: cargue las credenciales y pulse «Conectar con Google».")

    def _acceso(self) -> str:
        if not self.token.get("access_token") or time.time() >= float(self.token.get("vence") or 0):
            nuevo = _post_formulario(self.cred["token_uri"], {
                "client_id": self.cred["client_id"], "client_secret": self.cred["client_secret"],
                "refresh_token": self.token["refresh_token"], "grant_type": "refresh_token"})
            self.token.update(access_token=nuevo["access_token"],
                              vence=time.time() + float(nuevo.get("expires_in") or 3000) - 60)
            _guardar("token.json", self.token)
        return self.token["access_token"]

    def _abrir(self, url: str, intentos: int = 4):
        for i in range(intentos):
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._acceso()}"})
            try:
                return urllib.request.urlopen(req, timeout=120)
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503) and i < intentos - 1:
                    time.sleep(2 ** i)
                    continue
                if exc.code == 404:
                    raise ErrorDrive("Drive no encontró la carpeta o el archivo: revise el enlace y que la cuenta "
                                     "conectada tenga acceso.") from exc
                raise ErrorDrive(f"Error de Google Drive ({exc.code}): {exc.read().decode(errors='ignore')[:300]}") from exc
            except urllib.error.URLError as exc:
                if i < intentos - 1:
                    time.sleep(2 ** i)
                    continue
                raise ErrorDrive(f"No hay conexión con Google Drive ({exc.reason}).") from exc
        raise ErrorDrive("No se pudo leer Google Drive.")

    def _json(self, ruta: str, params: dict) -> dict:
        with self._abrir(f"{self.api}/{ruta}?{urllib.parse.urlencode(params)}") as r:
            return json.loads(r.read().decode())

    def cuenta(self) -> str:
        return self._json("about", {"fields": "user(emailAddress)"}).get("user", {}).get("emailAddress", "")

    def metadatos(self, file_id: str) -> dict:
        return self._json(f"files/{file_id}", {"fields": "id,name,mimeType", "supportsAllDrives": "true"})

    def listar(self, carpeta_id: str) -> list[dict]:
        salida, pagina = [], None
        while True:
            params = {"q": f"'{carpeta_id}' in parents and trashed = false", "pageSize": 1000,
                      "fields": "nextPageToken,files(id,name,mimeType,size,md5Checksum,modifiedTime,"
                                "shortcutDetails(targetId,targetMimeType))",
                      "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
            if pagina:
                params["pageToken"] = pagina
            d = self._json("files", params)
            salida += d.get("files", [])
            pagina = d.get("nextPageToken")
            if not pagina:
                return salida

    def descargar(self, file_id: str, destino: Path) -> None:
        destino.parent.mkdir(parents=True, exist_ok=True)
        tmp = destino.with_name(destino.name + ".descargando")
        with self._abrir(f"{self.api}/files/{file_id}?alt=media&supportsAllDrives=true") as r, tmp.open("wb") as fh:
            while bloque := r.read(1 << 20):
                fh.write(bloque)
        tmp.replace(destino)


def _nombre_seguro(nombre: str) -> str:
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nombre).strip().rstrip(".")
    return n or "_"


def espejo(cliente: ClienteDrive, carpeta_id: str, destino: Path, extensiones=DEFAULTS["extensiones"],
           aviso=log.info) -> dict:
    """Copia local de la carpeta de Drive: descarga sólo lo nuevo o modificado (por md5 / fecha)."""
    estado = _leer("espejo_estado.json", {})
    vistos, descargados, pendientes = set(), 0, [(carpeta_id, destino)]
    carpetas_vistas = {carpeta_id}  # un acceso directo a una carpeta ya recorrida (o a sí misma) no se repite
    while pendientes:
        fid, local = pendientes.pop()
        for f in cliente.listar(fid):
            mime, ident = f.get("mimeType"), f["id"]
            if mime == ACCESO_DIRECTO:
                det = f.get("shortcutDetails") or {}
                mime, ident = det.get("targetMimeType"), det.get("targetId") or ident
            ruta = local / _nombre_seguro(f["name"])
            if mime == CARPETA:
                if ident not in carpetas_vistas:
                    carpetas_vistas.add(ident)
                    pendientes.append((ident, ruta))
                continue
            if ruta.suffix.lower() not in extensiones:
                continue
            firma = f.get("md5Checksum") or f.get("modifiedTime") or ""
            clave = str(ruta.relative_to(destino))
            vistos.add(clave)
            if estado.get(clave) == firma and ruta.exists():
                continue
            cliente.descargar(ident, ruta)
            estado[clave] = firma
            descargados += 1
            if descargados % 50 == 0:
                aviso(f"Drive: {descargados} archivos descargados …")
                _guardar("espejo_estado.json", estado)
    _guardar("espejo_estado.json", estado)
    return {"descargados": descargados, "archivos": len(vistos)}


# ---------------------------------------------------------------------------
# Novedades por hospital y mes, y procesamiento
# ---------------------------------------------------------------------------

def _firma(p: Path) -> str:
    st = p.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


HILOS = 16  # Drive para escritorio atiende varias carpetas a la vez: se listan y se leen en paralelo


def _scandir(carpeta: Path) -> list[tuple[str, bool, int, int]]:
    try:
        with os.scandir(carpeta) as it:
            salida = []
            for e in it:
                try:
                    es_dir = e.is_dir(follow_symlinks=False)
                    st = None if es_dir else e.stat(follow_symlinks=False)  # en Windows viene con el listado
                    salida.append((e.name, es_dir, st.st_size if st else 0, st.st_mtime_ns if st else 0))
                except OSError:
                    continue
            return salida
    except OSError:
        return []


def _listar(raiz: Path, extensiones=DEFAULTS["extensiones"], aviso=None, profundidad: int = 8) -> dict[str, str]:
    """{ruta relativa: «tamaño:fecha»} de los archivos bajo ``raiz``, listando varias carpetas en paralelo.
    No entra en un acceso directo a la misma carpeta, ni en carpetas ocultas, ni a más de ``profundidad``."""
    from concurrent.futures import ThreadPoolExecutor

    archivos: dict[str, str] = {}
    pendientes, carpetas, ultimo = [(raiz, 0)], 0, time.time()
    with ThreadPoolExecutor(HILOS) as ex:
        while pendientes:
            lote, pendientes = pendientes, []
            for (carpeta, nivel), entradas in zip(lote, ex.map(_scandir, [c for c, _n in lote]), strict=False):
                carpetas += 1
                for nombre, es_dir, tam, fecha in entradas:
                    if es_dir:
                        if not nombre.startswith(".") and nombre.lower() != raiz.name.lower() and nivel + 1 < profundidad:
                            pendientes.append((carpeta / nombre, nivel + 1))
                    elif Path(nombre).suffix.lower() in extensiones:
                        archivos[(carpeta / nombre).relative_to(raiz).as_posix()] = f"{tam}:{fecha}"
                if aviso and time.time() - ultimo > 5:
                    ultimo = time.time()
                    aviso(f"Revisando la carpeta de Drive: {carpetas:,} carpetas y {len(archivos):,} archivos …"
                          .replace(",", "."))
    return archivos


def _archivos(raiz: Path, profundidad: int = 8) -> list[Path]:
    """Compatibilidad: rutas de los archivos bajo ``raiz`` (ver ``_listar``)."""
    return [raiz / r for r in sorted(_listar(raiz, extensiones=_TODAS, profundidad=profundidad))]


class _Todas:
    def __contains__(self, _x):
        return True


_TODAS = _Todas()


def precargar(rutas: list[Path], extensiones=DEFAULTS["extensiones"], aviso=None) -> int:
    """Lee en paralelo los archivos de ``rutas`` para que Drive para escritorio los descargue a la vez
    (después el procesamiento los lee de la copia local, sin esperar a internet)."""
    from concurrent.futures import ThreadPoolExecutor

    archivos = [r / x for r in rutas for x in _listar(r, extensiones)] if rutas else []

    def leer(f: Path) -> None:
        try:
            with f.open("rb") as fh:
                while fh.read(1 << 20):
                    pass
        except OSError:
            pass

    hechos, ultimo = 0, time.time()
    with ThreadPoolExecutor(HILOS) as ex:
        for _ in ex.map(leer, archivos):
            hechos += 1
            if aviso and time.time() - ultimo > 5:
                ultimo = time.time()
                aviso(f"  Descargando de Drive: {hechos:,} de {len(archivos):,} archivos …".replace(",", "."))
    return len(archivos)


def novedades(raiz: Path, extensiones=DEFAULTS["extensiones"], aviso=None) -> tuple[dict[tuple[str, str], list[Path]], dict]:
    """Unidades (hospital, carpeta del mes) con archivos nuevos o modificados desde lo procesado.

    Devuelve ({(hospital, mes): [carpeta a procesar]}, firmas actuales por archivo)."""
    previas = _leer("procesado.json", {})
    unidades = {}
    actuales = {k: v for k, v in _listar(raiz, extensiones, aviso).items() if "/" in k}  # sin archivos sueltos en la raíz
    for clave, firma in sorted(actuales.items()):
        if previas.get(clave) == firma:
            continue
        partes = clave.split("/")
        hospital = partes[0]
        mes = partes[1] if len(partes) > 2 and detectar_mes(partes[1])[0] else ""
        unidades.setdefault((hospital, mes), [raiz / hospital / mes if mes else raiz / hospital])
    return unidades, actuales


def raiz_actual(c: dict, aviso=log.info) -> Path:
    """Carpeta local a procesar: la sincronizada (modo local) o el espejo descargado (modo api)."""
    if c["modo"] == "api":
        carpeta_id = id_de_enlace(c.get("carpeta_id") or "")
        if not carpeta_id:
            raise ErrorDrive("Escriba el enlace o el identificador de la carpeta de Drive.")
        cliente = ClienteDrive()
        nombre = _nombre_seguro(cliente.metadatos(carpeta_id).get("name") or carpeta_id)
        destino = carpeta_drive() / "espejo" / nombre
        aviso(f"Revisando la carpeta «{nombre}» en Google Drive …")
        r = espejo(cliente, carpeta_id, destino, c.get("extensiones") or DEFAULTS["extensiones"], aviso)
        aviso(f"Drive: {r['archivos']} archivos en la carpeta, {r['descargados']} descargados (nuevos o modificados).")
        return destino
    escrita = (c.get("carpeta_local") or "").strip().strip('"')
    ruta = Path(escrita) if escrita else None
    if ruta and ruta.is_file() and ruta.suffix.lower() == ".zip":
        ruta = ruta.parent
    if not ruta or not ruta.is_dir():
        detectada = buscar_carpeta_local()
        if detectada:
            aviso(f"Se usa la carpeta encontrada en Google Drive: {detectada}")
            guardar_configuracion({"carpeta_local": str(detectada)})
            ruta = detectada
    if not ruta or not ruta.is_dir():
        raise ErrorDrive(diagnostico_local(escrita))
    zips = zips_de_drive(ruta)
    if zips:
        destino = carpeta_drive() / "desde_zip"
        r = extraer_zips(zips, destino, c.get("extensiones") or DEFAULTS["extensiones"])
        aviso(f"ZIP descargados de Drive: {len(zips)} archivo(s), {r['archivos']} archivos RIPS, {r['nuevos']} nuevos o "
              f"modificados" + (f", {r['sin_hospital']} sin carpeta de hospital/mes (omitidos)" if r["sin_hospital"] else ""))
        return destino
    return ruta


_RE_ZIP_DRIVE = re.compile(r"-\d{8}T\d{6}Z(-\d+)*-\d{3}\.zip$", re.I)


def zips_de_drive(carpeta: Path) -> list[Path]:
    """ZIP que entrega «Descargar» de drive.google.com (``Nombre-20261123T140000Z-1-001.zip``) en la carpeta.
    Si la carpeta no tiene subcarpetas, cualquier ZIP suyo cuenta."""
    try:
        hijos = list(carpeta.iterdir())
    except OSError:
        return []
    zips = sorted(x for x in hijos if x.is_file() and x.suffix.lower() == ".zip")
    de_drive = [z for z in zips if _RE_ZIP_DRIVE.search(z.name)]
    if de_drive:
        return de_drive
    return zips if zips and not any(x.is_dir() for x in hijos) else []


def extraer_zips(zips: list[Path], destino: Path, extensiones=DEFAULTS["extensiones"]) -> dict:
    """Extrae los ZIP de Drive en ``destino/Hospital/Mes/...`` (la carpeta raíz exportada se quita). Un archivo
    sólo se reescribe si cambió (CRC), para que la siguiente sincronización vea únicamente lo nuevo."""
    import zipfile

    crc = _leer("zip_crc.json", {})
    archivos = nuevos = sin_hospital = 0
    for z in zips:
        try:
            zf = zipfile.ZipFile(z)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ErrorDrive(f"No se pudo abrir {z.name}: {exc}. Vuelva a descargarlo de Drive.") from exc
        with zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                partes = [x for x in info.filename.replace("\\", "/").split("/") if x not in ("", ".", "..")]
                if Path(partes[-1]).suffix.lower() not in extensiones:
                    continue
                i = next((k for k, x in enumerate(partes[:-1]) if k > 0 and detectar_mes(x)[0]), None)
                if i is None:
                    sin_hospital += 1
                    continue
                rel = "/".join(_nombre_seguro(x) for x in partes[i - 1:])  # Hospital/Mes/...
                archivos += 1
                ruta = destino / rel
                if crc.get(rel) == f"{info.CRC}:{info.file_size}" and ruta.exists():
                    continue
                ruta.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as origen, ruta.open("wb") as fh:
                    while bloque := origen.read(1 << 20):
                        fh.write(bloque)
                crc[rel] = f"{info.CRC}:{info.file_size}"
                nuevos += 1
    _guardar("zip_crc.json", crc)
    return {"archivos": archivos, "nuevos": nuevos, "sin_hospital": sin_hospital}


def sincronizar(cfg: dict | None = None, enviar: bool | None = None, aviso=log.info, salida: Path | None = None,
                solo_revisar: bool = False) -> dict:
    """Trae lo nuevo de Drive, procesa cada hospital-mes con novedades y envía esos meses al aplicativo."""
    from . import json_rips
    from . import tablero as tb
    from .aplicativo import ErrorAplicativo, enviar_meses, resumen_cruce
    from .config import cargar_configuracion
    from .cuv import reporte
    from .exportacion import exportar_todo
    from .procesamiento import procesar

    cfg = cfg or cargar_configuracion()
    c = configuracion(cfg)
    enviar = c["enviar_aplicativo"] if enviar is None else enviar
    raiz = raiz_actual(c, aviso)
    aviso(f"Revisando la carpeta de Drive: {raiz} (la primera vez puede tardar varios minutos) …")
    unidades, actuales = novedades(raiz, c.get("extensiones") or DEFAULTS["extensiones"], aviso)
    resumen = {"carpeta": str(raiz), "inicio": dt.datetime.now().isoformat(timespec="seconds"),
               "unidades": [{"hospital": h, "mes": m or "(sin mes)"} for h, m in sorted(unidades)],
               "procesadas": [], "meses_enviados": [], "envio": None, "cruce": None, "aviso_envio": None,
               "archivos": []}
    pendientes = [m for m in _leer("pendientes_envio.json", []) if enviar]
    if not unidades and not pendientes:
        aviso("No hay archivos nuevos en Drive desde la última revisión.")
        _guardar("ultima.json", {**resumen, "fin": dt.datetime.now().isoformat(timespec="seconds")})
        return resumen
    if not unidades:
        aviso(f"Sin archivos nuevos; se reintenta el envío al aplicativo de: {', '.join(pendientes)}")
    aviso(f"Novedades en {len(unidades)} hospital-mes: " +
          ", ".join(f"{h} / {m or 'sin mes'}" for h, m in sorted(unidades)))
    if solo_revisar:
        return resumen
    salida = salida or carpeta_datos() / f"drive_{dt.datetime.now():%Y%m%d_%H%M%S}"
    previas = _leer("procesado.json", {})
    meses_tocados: set[str] = set()
    cuv_total: dict[str, dict] = {}
    por_hospital: dict[str, list[tuple[str, list[Path]]]] = {}
    for (hospital, mes), rutas in sorted(unidades.items()):
        por_hospital.setdefault(hospital, []).append((mes, rutas))
    for hospital, lista in por_hospital.items():
        entradas = [r for _m, rutas in lista for r in rutas]
        aviso(f"Procesando {hospital}: {', '.join(m or 'todo' for m, _r in lista)}")
        n = precargar(entradas, c.get("extensiones") or DEFAULTS["extensiones"], aviso)
        aviso(f"  {n:,} archivos listos; leyendo y validando …".replace(",", "."))
        res = procesar(entradas, cfg, hospital=hospital)
        if not res.envios.empty:
            generados = exportar_todo(res, salida, cfg)
            resumen["archivos"] += [str(a) for a in generados if Path(a).parent.name != "APLICATIVO_AUDITORIA"]
            meses_tocados |= {m.group(1) for a in generados if Path(a).parent.name == "APLICATIVO_AUDITORIA"
                              for m in [re.search(r"(\d{4}-\d{2})", Path(a).name)] if m}
        r_json = {}
        if json_rips.hay_json(entradas):
            r_json = json_rips.cargar_json(entradas, hospital=hospital, cfg=cfg, carpeta=tb.carpeta_acumulado(cfg))
            meses_tocados |= set(r_json.get("meses_json") or [])
            cuv_total.update(r_json.get("cuv") or {})
            for a in r_json.get("avisos") or []:
                aviso(f"  {a}")
        resumen["procesadas"].append({
            "hospital": hospital, "meses": [m for m, _r in lista], "remisiones_txt": len(res.envios),
            "json_leidos": r_json.get("registros_leidos", 0), "filas_agregadas": r_json.get("agregadas", 0),
            "ya_cargadas": r_json.get("ya_cargadas", 0), "validadas_msps": r_json.get("facturas_validadas_msps", 0),
            "rechazadas_msps": r_json.get("facturas_rechazadas_msps", 0)})
        aviso(f"  {hospital}: {r_json.get('agregadas', 0)} filas nuevas de JSON, {r_json.get('ya_cargadas', 0)} ya "
              f"cargadas, {len(res.envios)} remisiones TXT; CUV: {r_json.get('facturas_validadas_msps', 0)} validadas, "
              f"{r_json.get('facturas_rechazadas_msps', 0)} rechazadas")
        # se marca como procesado lo de este hospital (si falla otro hospital, éste no se repite)
        for clave, firma in actuales.items():
            if clave.split("/", 1)[0] == hospital and any(
                    (clave.split("/")[1] == m) if m else True for m, _r in lista):
                previas[clave] = firma
        _guardar("procesado.json", previas)
    if cuv_total:
        ruta = salida / "VALIDACION_MSPS.xlsx"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        reporte(cuv_total).to_excel(ruta, index=False, sheet_name="CUV por factura")
        resumen["archivos"].append(str(ruta))
    meses = sorted({m for m in meses_tocados if re.fullmatch(r"\d{4}-\d{2}", m)} | set(pendientes))
    resumen["meses_enviados"] = meses
    if meses:
        archivos = tb.exportar_meses(meses, salida / "APLICATIVO_AUDITORIA", cfg)
        resumen["archivos"] += [str(a) for a in archivos]
        if enviar and archivos:
            try:
                app = (cfg.get("aplicativo_auditoria") or {})
                resumen["envio"] = enviar_meses(archivos, app, aviso=aviso)
                resumen["cruce"] = resumen_cruce(app)
                _guardar("pendientes_envio.json", [])
            except ErrorAplicativo as exc:
                # se reintentan en la próxima revisión, aunque no llegue nada nuevo
                _guardar("pendientes_envio.json", meses)
                resumen["aviso_envio"] = f"{exc} Los meses {', '.join(meses)} se reenviarán en la próxima revisión."
                aviso(resumen["aviso_envio"])
    resumen["salida"] = str(salida)
    resumen["fin"] = dt.datetime.now().isoformat(timespec="seconds")
    _guardar("ultima.json", {k: v for k, v in resumen.items() if k != "cruce"})
    return resumen


def autovincular(c: dict | None = None) -> str:
    """Si no hay una carpeta válida configurada, busca la de Google Drive en este equipo y la deja vinculada
    (con envío al aplicativo y revisión cada hora). Devuelve la ruta vinculada o ''."""
    from .config import cargar_configuracion

    c = c or configuracion(cargar_configuracion())
    actual = (c.get("carpeta_local") or "").strip()
    if c.get("modo") != "local" or (actual and Path(actual).is_dir()):
        return ""
    encontrada = buscar_carpeta_local()
    if not encontrada:
        return ""
    guardar_configuracion({"carpeta_local": str(encontrada), "enviar_aplicativo": True,
                           "revisar_cada_min": int(c.get("revisar_cada_min") or 0) or 60})
    log.info("Carpeta de Google Drive vinculada automáticamente: %s", encontrada)
    return str(encontrada)


def estado() -> dict:
    """Configuración, conexión y última sincronización (para la ventana)."""
    from .config import cargar_configuracion

    vinculada = autovincular()
    c = configuracion(cargar_configuracion())
    local = c.get("carpeta_local") or ""
    detectada = None if local else buscar_carpeta_local()
    return {"autovinculada": vinculada, "pendientes_envio": _leer("pendientes_envio.json", []),
            "config": {k: c[k] for k in ("modo", "carpeta_local", "carpeta_id", "revisar_cada_min", "enviar_aplicativo")},
            "carpeta_detectada": str(detectada) if detectada else "", "api": estado_api(),
            "ultima": _leer("ultima.json", None), "archivos_procesados": len(_leer("procesado.json", {}))}


def reiniciar_procesado() -> None:
    """Olvida lo procesado: la próxima sincronización vuelve a leer todo (sin duplicar lo ya cargado)."""
    (carpeta_drive() / "procesado.json").unlink(missing_ok=True)


__all__ = ["ClienteDrive", "ErrorDrive", "autovincular", "buscar_carpeta_local", "buscar_carpetas_locales", "diagnostico_local",
           "canjear_codigo", "configuracion", "espejo", "estado",
           "guardar_configuracion", "guardar_credenciales", "id_de_enlace", "novedades", "sincronizar", "url_autorizacion"]

"""Usuarios y permisos.

Dos roles:

* ``central``: área administrativa. Ve y carga todo, administra catálogo, usuarios
  y parámetros.
* ``sede``: personal de un hospital o centro. Solo ve, carga, reemplaza y elimina
  datos de las sedes de su hospital.

Las contraseñas se guardan con PBKDF2-SHA256 y sal por usuario; nunca en texto claro.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from . import db

ITERACIONES = 240_000
LARGO_MINIMO = 8


@dataclass
class Usuario:
    usuario: str
    nombre: str
    rol: str
    hospital: str | None

    @property
    def es_central(self) -> bool:
        return self.rol == "central"

    @property
    def ambito(self) -> str | None:
        """Hospital al que está restringido (None = toda la ESE)."""
        return None if self.es_central else self.hospital


def _hash(clave: str, sal: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", clave.encode(), bytes.fromhex(sal), ITERACIONES).hex()


def hay_usuarios(ruta=None) -> bool:
    return int(db.consulta("SELECT COUNT(*) AS n FROM usuarios", ruta=ruta)["n"].iloc[0]) > 0


def crear(usuario: str, nombre: str, clave: str, rol: str, hospital: str | None = None, actor: str = "sistema",
          ruta=None) -> None:
    usuario = usuario.strip().lower()
    if not usuario or not usuario.replace(".", "").replace("_", "").isalnum():
        raise ValueError("El usuario solo puede tener letras, números, punto y guion bajo.")
    if len(clave) < LARGO_MINIMO:
        raise ValueError(f"La contraseña debe tener al menos {LARGO_MINIMO} caracteres.")
    if rol not in ("central", "sede"):
        raise ValueError("Rol no válido.")
    if rol == "sede" and not hospital:
        raise ValueError("Un usuario de sede debe tener un hospital asignado.")
    sal = secrets.token_hex(16)
    with db.transaccion(ruta) as con:
        if con.execute("SELECT 1 FROM usuarios WHERE usuario=?", (usuario,)).fetchone():
            raise ValueError(f"El usuario «{usuario}» ya existe.")
        con.execute("INSERT INTO usuarios(usuario, nombre, rol, hospital, sal, clave) VALUES (?,?,?,?,?,?)",
                    (usuario, nombre.strip(), rol, hospital if rol == "sede" else None, sal, _hash(clave, sal)))
        db.registrar_evento(con, actor, "usuario_creado", f"{usuario} ({rol}{', ' + hospital if rol == 'sede' else ''})")


def autenticar(usuario: str, clave: str, ruta=None) -> Usuario | None:
    df = db.consulta("SELECT * FROM usuarios WHERE usuario=? AND activo=1", (usuario.strip().lower(),), ruta)
    if df.empty:
        _hash(clave, "00" * 16)   # mismo costo aunque el usuario no exista
        return None
    f = df.iloc[0]
    if not hmac.compare_digest(_hash(clave, f["sal"]), f["clave"]):
        return None
    return Usuario(f["usuario"], f["nombre"] or f["usuario"], f["rol"], f["hospital"])


def cambiar_clave(usuario: str, nueva: str, actor: str, ruta=None) -> None:
    if len(nueva) < LARGO_MINIMO:
        raise ValueError(f"La contraseña debe tener al menos {LARGO_MINIMO} caracteres.")
    sal = secrets.token_hex(16)
    with db.transaccion(ruta) as con:
        con.execute("UPDATE usuarios SET sal=?, clave=? WHERE usuario=?", (sal, _hash(nueva, sal), usuario))
        db.registrar_evento(con, actor, "clave_cambiada", usuario)


def activar(usuario: str, activo: bool, actor: str, ruta=None) -> None:
    with db.transaccion(ruta) as con:
        if not activo:
            otros = con.execute("SELECT COUNT(*) FROM usuarios WHERE rol='central' AND activo=1 AND usuario<>?",
                                (usuario,)).fetchone()[0]
            if otros == 0:
                raise ValueError("No se puede desactivar el único usuario central activo.")
        con.execute("UPDATE usuarios SET activo=? WHERE usuario=?", (int(activo), usuario))
        db.registrar_evento(con, actor, "usuario_activado" if activo else "usuario_desactivado", usuario)


def listar(ruta=None):
    return db.consulta("SELECT usuario, nombre, rol, hospital, activo FROM usuarios ORDER BY rol, usuario", ruta=ruta)

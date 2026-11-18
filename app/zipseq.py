"""Lectura de un .zip recorriendo sus encabezados locales, sin depender del directorio central.

Algunas descargas grandes de Google Drive (y las descargas interrumpidas) traen el directorio central con posiciones erradas:
Python responde «BadZipFile: Bad magic number for file header» y el Explorador de Windows tampoco las abre. Aquí el archivo se
recorre de principio a fin: cada miembro empieza con «PK\\x03\\x04»; si el encabezado no trae el tamaño (bit 3: «data descriptor»),
el fin del contenido comprimido se encuentra descomprimiéndolo (deflate marca su propio final). Se construye un índice
(nombre, posición, método, tamaños) y cada miembro se lee después directamente de su posición, sin extraer nada al disco.
"""
import io, struct, zlib

LOCAL, CENTRAL, DESCRIPTOR = b"PK\x03\x04", b"PK\x01\x02", b"PK\x07\x08"
BLOQUE = 1 << 20

class Entrada:
    __slots__ = ("nombre", "datos", "metodo", "csize", "usize")
    def __init__(self, nombre, datos, metodo, csize, usize): self.nombre, self.datos, self.metodo, self.csize, self.usize = nombre, datos, metodo, csize, usize

def _zip64(extra):
    """Tamaños del campo extra ZIP64 (0x0001), si existe."""
    i = 0
    while i + 4 <= len(extra):
        tag, n = struct.unpack("<HH", extra[i:i + 4])
        if tag == 1 and n >= 16: return struct.unpack("<QQ", extra[i + 4:i + 20])[::-1]     # (csize, usize)
        i += 4 + n
    return None

def _buscar(f, desde, firmas=(LOCAL, CENTRAL)):
    """Posición de la siguiente firma a partir de `desde` (para saltar basura o un miembro dañado); None si no hay más."""
    f.seek(desde); pos, cola = desde, b""
    while True:
        b = f.read(BLOQUE)
        if not b: return None
        buf = cola + b; base = pos - len(cola)
        hits = [x for x in (buf.find(s) for s in firmas) if x >= 0]
        if hits: return base + min(hits)
        cola = buf[-3:]; pos += len(b)

def indexar(ruta, avance=None):
    """Índice de los miembros del zip recorriendo los encabezados locales."""
    out = []
    with open(ruta, "rb") as f:
        total = f.seek(0, 2); pos = _buscar(f, 0, (LOCAL,))
        while pos is not None:
            f.seek(pos); cab = f.read(30)
            if len(cab) < 30 or cab[:4] != LOCAL: break
            _, flags, metodo, _, _, _, csize, usize, nlen, xlen = struct.unpack("<HHHHHIIIHH", cab[4:30])
            nombre_b = f.read(nlen); extra = f.read(xlen)
            nombre = nombre_b.decode("utf-8" if flags & 0x800 else "cp437", errors="replace")
            try: nombre = nombre_b.decode("utf-8")                       # Google Drive escribe UTF-8 aunque no siempre marque el bit
            except UnicodeDecodeError: pass
            datos = pos + 30 + nlen + xlen; z64 = _zip64(extra)
            if z64 and (csize == 0xFFFFFFFF or usize == 0xFFFFFFFF): csize, usize = z64
            if flags & 8:                                                # tamaños al final: se halla el fin descomprimiendo
                if metodo == 8:
                    d = zlib.decompressobj(-15); f.seek(datos); leido = usize = 0
                    while not d.eof:
                        b = f.read(BLOQUE)
                        if not b: break
                        usize += len(d.decompress(b)); leido += len(b)
                    csize = leido - len(d.unused_data)
                elif metodo == 0:
                    sig = _buscar(f, datos, (DESCRIPTOR,)); csize = usize = (sig - datos) if sig is not None else 0
                fin = datos + csize; f.seek(fin); s = f.read(4)
                fin += (4 if s == DESCRIPTOR else 0) + (20 if z64 else 12)
            else:
                fin = datos + csize
            if not nombre.endswith("/"): out.append(Entrada(nombre, datos, metodo, csize, usize))
            if avance and len(out) % 500 == 0: avance(f"Leyendo el índice del comprimido: {len(out):,} archivos ({fin / max(total, 1):.0%})…")
            f.seek(fin)
            pos = fin if f.read(4) == LOCAL else _buscar(f, fin)
            if pos is not None:
                f.seek(pos)
                if f.read(4) != LOCAL: break                             # llegó al directorio central
    return out

class _Lector(io.RawIOBase):
    def __init__(self, ruta, e):
        self.f = open(ruta, "rb"); self.f.seek(e.datos); self.resta = e.csize; self.e = e
        self.d = zlib.decompressobj(-15) if e.metodo == 8 else None; self.buf = b""
        if e.metodo not in (0, 8): raise NotImplementedError(f"Método de compresión {e.metodo} no soportado en «{e.nombre}».")
    def readable(self): return True
    def readinto(self, b):
        while not self.buf and (self.resta > 0 or (self.d and self.d.unconsumed_tail)):
            if self.d and self.d.unconsumed_tail: self.buf = self.d.decompress(self.d.unconsumed_tail, BLOQUE); continue
            c = self.f.read(min(BLOQUE, self.resta)); self.resta -= len(c)
            if not c: self.resta = 0; break
            self.buf = self.d.decompress(c, BLOQUE) if self.d else c
        n = min(len(b), len(self.buf)); b[:n] = self.buf[:n]; self.buf = self.buf[n:]
        return n
    def close(self):
        try: self.f.close()
        finally: super().close()

def abrir(ruta, e):
    """Archivo binario (con peek) para leer un miembro del índice."""
    return io.BufferedReader(_Lector(ruta, e), BLOQUE)

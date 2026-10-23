"""Motor de homologación CECO del estado financiero → CECO de resolución.

Precedencia:
1. **Maestro** (archivo de referencia): si el código existe, manda el maestro (código, sede, tipo, estado).
2. Para códigos **nuevos** (no están en el maestro) se propone una homologación:
   a. reglas de nombre que obligan a VERIFICAR o EXCLUIR (recién nacidos, quirófanos, consulta especializada,
      saldos iniciales…), que prevalecen sobre cualquier otra;
   b. **estructura del código**: grupo (1.er dígito) + servicio CHIP (últimos 4 dígitos), aprendida del maestro
      solo cuando todos los CECO con esa estructura tienen el mismo código de resolución (excluye la sede 01,
      que es irregular);
   c. **nombre** (sede central y casos irregulares): reglas de datos/reglas_nombre.json;
   d. sin resultado → SIN HOMOLOGAR.
   Un CECO nuevo queda en VERIFICAR (no se carga) salvo que se active `activar_nuevos`; aun así, los resueltos
   por una regla de VERIFICAR/EXCLUIR conservan ese estado.

Código del CECO E.F. (7 dígitos): G SS NNNN → G grupo (1 asistencial, 2 administrativo, 3 financiero,
4 logístico, 5 seguros, 6 mantenimiento sedes, 7 transporte), SS sede, NNNN servicio CHIP.
"""
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

import pandas as pd

from . import config as C
from .normalizar import clave

MAESTRO, ESTRUCTURA, NOMBRE, SIN_REGLA = "MAESTRO", "ESTRUCTURA DE CÓDIGO", "NOMBRE", "SIN REGLA"
SEDE_CENTRAL = "01"


@dataclass(frozen=True)
class Homologacion:
    cod_ef: str
    nombre_ef: str
    cod_res: str
    nombre_res: str
    sede: str
    llave: str
    tipo: str
    estado: str
    confianza: str
    criterio: str
    metodo: str
    origen: str          # MAESTRO | ESTRUCTURA DE CÓDIGO | NOMBRE | SIN REGLA
    en_utilizados: bool
    nuevo: bool
    fila_referencia: int | None = None
    nota: str = ""

    def como_dict(self):
        return asdict(self)

    @property
    def cargable(self):
        return self.estado == C.ACTIVO

    def describir(self):
        if self.origen == MAESTRO:
            return (f"{self.cod_ef} → {self.llave} según maestro fila {self.fila_referencia} "
                    f"({self.metodo or 'sin método'}; confianza {self.confianza or 'n/d'}; estado {self.estado})")
        if self.origen == SIN_REGLA:
            return f"{self.cod_ef} sin homologación (no está en el maestro y ninguna regla aplica)"
        return f"{self.cod_ef} → {self.llave} por {self.origen.lower()} (CECO nuevo; {self.criterio}; estado {self.estado})"


def partes_codigo(cod_ef):
    """(grupo, sede, servicio) para códigos de 7 dígitos; None si no tiene esa estructura."""
    if re.fullmatch(r"\d{7}", cod_ef or ""):
        return cod_ef[0], cod_ef[1:3], cod_ef[3:]
    return None


class Homologador:
    def __init__(self, referencia, activar_nuevos=False, reglas=None):
        self.ref = referencia
        self.activar_nuevos = activar_nuevos
        self.reglas = [dict(r, _re=re.compile(r["patron"])) for r in (reglas if reglas is not None else C.reglas_nombre())]
        self._cache = {}
        self._aprender()

    # ------------------------------------------------------------------ aprendizaje desde el maestro
    def _aprender(self):
        m = self.ref.maestro
        por_estructura = defaultdict(set)
        estado_estructura = defaultdict(Counter)
        sede_segmento = defaultdict(Counter)
        tipo_res = defaultdict(Counter)
        for r in m.itertuples(index=False):
            tipo_res[r.cod_res][r.tipo] += 1
            p = partes_codigo(r.cod_ef)
            if not p:
                continue
            grupo, seg, servicio = p
            sede_segmento[seg][r.sede] += 1
            if seg != SEDE_CENTRAL and r.estado in C.ESTADOS:
                por_estructura[(grupo, servicio)].add(r.cod_res)
                estado_estructura[(grupo, servicio)][r.estado] += 1
        self.estructura = {k: next(iter(v)) for k, v in por_estructura.items() if len(v) == 1}
        self.estado_estructura = {k: c.most_common(1)[0][0] for k, c in estado_estructura.items()}
        # Una sede por segmento solo si es claramente mayoritaria (≥ 80 %).
        self.sede_segmento = {}
        for seg, c in sede_segmento.items():
            sede, n = c.most_common(1)[0]
            if n / sum(c.values()) >= 0.8:
                self.sede_segmento[seg] = sede
        self.tipo_res = {k: c.most_common(1)[0][0] for k, c in tipo_res.items()}
        self.alias_sedes = [(clave(a), s["sede"]) for s in C.sedes_y_puc()["sedes"] for a in s["alias"]]
        self.alias_sedes.sort(key=lambda x: -len(x[0]))
        self.alias_prioritarios = [(clave(a), s["sede"]) for s in C.sedes_y_puc()["sedes"] if s.get("prioritario")
                                   for a in s["alias"]]

    # ------------------------------------------------------------------ API
    def homologar(self, cod_ef, nombre_ef="", sede_fuente=""):
        llave_cache = (cod_ef, clave(nombre_ef), clave(sede_fuente))
        if llave_cache not in self._cache:
            self._cache[llave_cache] = self._homologar(cod_ef, nombre_ef, sede_fuente)
        return self._cache[llave_cache]

    def _homologar(self, cod_ef, nombre_ef, sede_fuente):
        fila = self.ref.buscar(cod_ef)
        if fila is not None:
            estado = fila["estado"] if fila["estado"] in C.ESTADOS else C.VERIFICAR
            nota = ""
            if fila["estado"] not in C.ESTADOS:
                nota = f"Estado '{fila['estado']}' no válido en el maestro: se trata como VERIFICAR"
            if fila["nombre_res"] == "CÓDIGO NO VÁLIDO":
                estado, nota = C.VERIFICAR, f"Código de resolución '{fila['cod_res']}' no válido en el maestro"
            return Homologacion(
                cod_ef=cod_ef, nombre_ef=fila["nombre_ef"], cod_res=fila["cod_res"], nombre_res=fila["nombre_res"],
                sede=fila["sede"], llave=fila["llave"], tipo=fila["tipo"], estado=estado, confianza=fila["confianza"],
                criterio=fila["criterio"], metodo=fila["metodo"], origen=MAESTRO, en_utilizados=fila["en_utilizados"],
                nuevo=False, fila_referencia=fila["fila_referencia"], nota=nota)
        return self.proponer(cod_ef, nombre_ef, sede_fuente)

    def proponer(self, cod_ef, nombre_ef="", sede_fuente=""):
        """Homologación propuesta por reglas (sin consultar el maestro)."""
        nombre_k = clave(nombre_ef)
        partes = partes_codigo(cod_ef)
        regla = self._regla_nombre(nombre_k)
        sede = self._sede(partes, nombre_k, sede_fuente)

        cod_res, estado_regla, criterio, origen, confianza, nota = "", "", "", SIN_REGLA, "Baja", ""
        if regla and regla.get("estado") in (C.VERIFICAR, C.EXCLUIR):
            cod_res, estado_regla, criterio, origen = regla["cod_res"], regla["estado"], regla["criterio"], NOMBRE
            nota = regla.get("nota", "")
        elif partes and partes[1] != SEDE_CENTRAL and (partes[0], partes[2]) in self.estructura:
            clave_e = (partes[0], partes[2])
            cod_res, origen, confianza = self.estructura[clave_e], ESTRUCTURA, "Media"
            estado_regla = self.estado_estructura.get(clave_e, "")
            estado_regla = estado_regla if estado_regla in (C.NO_APLICA, C.EXCLUIR, C.VERIFICAR) else ""
            criterio = f"Servicio CHIP {partes[2]} del grupo {partes[0]} homologado a {cod_res} en todas las sedes del maestro"
        elif regla:
            cod_res, criterio, origen, confianza = regla["cod_res"], regla["criterio"], NOMBRE, "Media"
            nota = regla.get("nota", "")

        nombre_res = self.ref.nombre_resolucion(cod_res) if cod_res else None
        if cod_res and nombre_res is None:
            nota = (nota + "; " if nota else "") + f"La regla propone {cod_res}, que no existe en la resolución"
            nombre_res, estado_regla = "CÓDIGO NO VÁLIDO", C.VERIFICAR
        if origen == SIN_REGLA:
            estado = C.SIN_HOMOLOGAR
        elif estado_regla:
            estado = estado_regla
        elif not sede:
            estado, nota = C.VERIFICAR, (nota + "; " if nota else "") + "No se pudo determinar la sede"
        else:
            estado = C.ACTIVO if self.activar_nuevos else C.VERIFICAR
        return Homologacion(
            cod_ef=cod_ef, nombre_ef=nombre_ef, cod_res=cod_res, nombre_res=nombre_res or "", sede=sede,
            llave=C.llave(cod_res, sede) if cod_res and sede else "", tipo=self.tipo_res.get(cod_res, ""), estado=estado,
            confianza=confianza, criterio=criterio, metodo=origen.capitalize() if origen != SIN_REGLA else "",
            origen=origen, en_utilizados=self.ref.en_utilizados(cod_res), nuevo=True, nota=nota)

    def _regla_nombre(self, nombre_k):
        if not nombre_k:
            return None
        for r in self.reglas:
            if r["_re"].search(nombre_k):
                return r
        return None

    def _sede(self, partes, nombre_k, sede_fuente):
        # Prioridad: sede indicada en el archivo → subsede en el nombre → segmento SS del código → sigla o municipio.
        if sede_fuente:
            s = self.sede_por_texto(sede_fuente)
            if s:
                return s
        envuelto = f" {nombre_k} "
        for alias, sede in self.alias_prioritarios:
            if f" {alias} " in envuelto:
                return sede
        if partes and partes[1] in self.sede_segmento:
            return self.sede_segmento[partes[1]]
        return self.sede_por_texto(nombre_k)

    def sede_por_texto(self, valor):
        """Sede del catálogo a partir de un texto: nombre exacto, sigla o alias contenido en el texto."""
        k = clave(valor)
        if not k:
            return ""
        for s in C.sedes_y_puc()["sedes"]:
            if k == clave(s["sede"]):
                return s["sede"]
        for s in self.ref.maestro["sede"].drop_duplicates():
            if k == clave(s):
                return s
        envuelto = f" {k} "
        for alias, sede in self.alias_sedes:
            if f" {alias} " in envuelto:
                return sede
        return ""

    def comparar_con_maestro(self):
        """Contrasta la propuesta de las reglas con el maestro (control de las reglas, no cambia nada)."""
        filas = []
        for r in self.ref.maestro.itertuples(index=False):
            p = self.proponer(r.cod_ef, r.nombre_ef)
            filas.append({
                "cod_ef": r.cod_ef, "nombre_ef": r.nombre_ef, "cod_res_maestro": r.cod_res, "cod_res_regla": p.cod_res,
                "origen_regla": p.origen, "coincide": p.cod_res == r.cod_res, "estado_maestro": r.estado,
                "sede_maestro": r.sede, "sede_regla": p.sede,
            })
        return pd.DataFrame(filas)

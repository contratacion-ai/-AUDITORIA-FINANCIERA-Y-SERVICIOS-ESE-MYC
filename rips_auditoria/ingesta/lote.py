"""Orquestación de la carga: archivos -> remisiones -> tablas RIPS normalizadas y deduplicadas."""
from __future__ import annotations

import pickle
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

import pandas as pd

from ..config import columnas, red_prestadores
from ..constantes import NIT_ESE, TIPOS_RIPS, TIPOS_SERVICIO
from . import contexto as ctxmod
from .archivos import ArchivoEntrada, recolectar
from .deduplicacion import deduplicar
from .incidencias import Incidencias
from .json_xlsx import (cargar_json, detectar_json, fecha_txt, leer_consolidado_json,
                        leer_consolidado_xlsx, leer_json_2275)
from .txt3374 import leer_txt, tipo_y_remision

META = ["hospital", "municipio", "periodo", "envio_id", "remision", "carpeta_envio", "modalidad_declarada",
        "cod_entidad_envio", "archivo", "origen", "formato", "linea", "fuente_hospital", "fuente_municipio",
        "fuente_periodo"]


@dataclass
class Lote:
    id: str
    creado: str
    tablas: dict[str, pd.DataFrame]
    incidencias: pd.DataFrame
    archivos: pd.DataFrame
    remisiones: pd.DataFrame
    duplicados: pd.DataFrame
    notas: list[str] = field(default_factory=list)

    def guardar(self, carpeta: Path) -> Path:
        carpeta.mkdir(parents=True, exist_ok=True)
        p = carpeta / f"{self.id}.pkl"
        with open(p, "wb") as fh:
            pickle.dump(self, fh)
        return p

    @staticmethod
    def abrir(ruta: Path) -> "Lote":
        with open(ruta, "rb") as fh:
            return pickle.load(fh)

    def resumen(self) -> pd.DataFrame:
        """Índice hospital-mes equivalente a la hoja ``Indice_Hospital_Mes`` del consolidado."""
        af = self.tablas["AF"]
        if af.empty:
            return pd.DataFrame()
        g = ["hospital", "municipio", "periodo"]
        res = af.groupby(g, dropna=False).agg(remisiones=("envio_id", "nunique"), facturas=("num_factura", "nunique"),
                                              valor_neto_facturado_af=("valor_neto", "sum")).reset_index()
        us = self.tablas["US"]
        if not us.empty:
            u = (us.assign(_id=us["tipo_id_usuario"].astype(str) + us["num_id_usuario"].astype(str))
                 .groupby(g, dropna=False)["_id"].nunique().rename("usuarios_unicos").reset_index())
            res = res.merge(u, on=g, how="left")
        for t in TIPOS_RIPS:
            df = self.tablas[t]
            c = df.groupby(g, dropna=False).size().rename(f"reg_{t}").reset_index() if not df.empty else None
            res = res.merge(c, on=g, how="left") if c is not None else res.assign(**{f"reg_{t}": 0})
        inc = self.incidencias
        for sev, col in (("ERROR", "inc_error"), ("ADVERTENCIA", "inc_advertencia")):
            c = inc[inc.severidad == sev].groupby(["hospital", "periodo"], dropna=False).size().rename(col).reset_index()
            res = res.merge(c, on=["hospital", "periodo"], how="left")
        return res.fillna({c: 0 for c in res.columns if c.startswith(("reg_", "inc_", "usuarios"))})


def combinar(lotes: list[Lote]) -> Lote:
    """Une varios lotes para analizarlos juntos sin sumar dos veces la misma información.

    Si el mismo mes se cargó dos veces (dos lotes con los mismos archivos) se aplica de nuevo la
    deduplicación, ahora entre lotes.
    """
    if len(lotes) == 1:
        return lotes[0]
    tablas, dups = {}, [l.duplicados for l in lotes if not l.duplicados.empty]
    for t in TIPOS_RIPS:
        partes = []
        for l in lotes:
            d = l.tablas[t].copy()
            if not d.empty:
                d["envio_id"] = l.id + ":" + d["envio_id"].astype(str)
                d["origen"] = l.id + ":" + d["origen"].astype(str)
            partes.append(d)
        df = pd.concat(partes, ignore_index=True)
        df, el = deduplicar(df, t)
        if not el.empty:
            el.insert(0, "tipo_archivo", t)
            el["motivo"] = "REGISTRO_REPETIDO_ENTRE_LOTES"
            dups.append(el)
        tablas[t] = df
    rem = []
    for l in lotes:
        r = l.remisiones.copy()
        if not r.empty:
            r["envio_id"] = l.id + ":" + r["envio_id"].astype(str)
        rem.append(r)
    inc = pd.concat([l.incidencias for l in lotes], ignore_index=True)
    return Lote(id="+".join(l.id for l in lotes), creado=datetime.now().isoformat(timespec="seconds"), tablas=tablas,
                incidencias=inc, archivos=pd.concat([l.archivos for l in lotes], ignore_index=True),
                remisiones=pd.concat(rem, ignore_index=True),
                duplicados=pd.concat(dups, ignore_index=True) if dups else pd.DataFrame(),
                notas=[n for l in lotes for n in l.notas])


def corregir(lote: Lote, envio_id: str, **campos) -> int:
    """Corrección manual de hospital, municipio, periodo o EPS de una remisión (trazable)."""
    validos = {k: v for k, v in campos.items() if k in ("hospital", "municipio", "periodo", "cod_entidad") and v}
    if not validos:
        return 0
    n = 0
    for t, df in lote.tablas.items():
        m = df["envio_id"] == envio_id
        if not m.any():
            continue
        for k, v in validos.items():
            if k == "cod_entidad":
                if t == "AF":
                    df.loc[m, "cod_entidad"] = v
                df.loc[m, "cod_entidad_envio"] = v
            else:
                df.loc[m, k] = v
                df.loc[m, f"fuente_{k}"] = "manual"
        n += int(m.sum())
    m = lote.remisiones["envio_id"] == envio_id
    for k, v in validos.items():
        lote.remisiones.loc[m, k] = v
        if k != "cod_entidad":
            lote.remisiones.loc[m, f"fuente_{k}"] = "manual"
    lote.notas.append(f"Corrección manual {envio_id}: {validos}")
    return n


def _vacias() -> dict[str, list[dict]]:
    return {t: [] for t in TIPOS_RIPS}


class _Constructor:
    def __init__(self, anio_defecto: int | None, manual_global: dict | None, manual_ruta: dict | None):
        self.inc = Incidencias()
        self.regs = _vacias()
        self.archivos: list[dict] = []
        self.remisiones: list[dict] = []
        self.n_envio = 0
        self.anio = anio_defecto
        self.manual_global = manual_global or {}
        self.manual_ruta = manual_ruta or {}
        self.eps = set(red_prestadores().get("eps_objetivo", []))

    def _manual(self, directorio: str) -> dict:
        m = dict(self.manual_global)
        for pref, val in self.manual_ruta.items():
            if directorio.startswith(pref):
                m.update(val)
        return m

    def nuevo_envio(self) -> str:
        self.n_envio += 1
        return f"E{self.n_envio:05d}"

    # ------------------------------------------------------------------ TXT
    def remision_txt(self, directorio: str, remision: str, archivos: dict[str, ArchivoEntrada]) -> None:
        envio = self.nuevo_envio()
        ctx = ctxmod.desde_ruta(f"{directorio}/x.txt", self.anio)
        meta0 = {"hospital": ctx.hospital, "periodo": ctx.periodo, "envio_id": envio}
        leidos: dict[str, list[dict]] = {}
        for tipo in ("AF", "CT", "US", *TIPOS_SERVICIO):
            a = archivos.get(tipo)
            if a is None:
                continue
            leidos[tipo] = leer_txt(tipo, a.datos, a.ruta, self.inc, meta0)
            self.archivos.append({"archivo": a.ruta, "tipo_archivo": tipo, "estado": "procesado", "motivo": None,
                                  "bytes": len(a.datos), "envio_id": envio, "formato": "TXT",
                                  "registros": len(leidos[tipo])})
        af = leidos.get("AF", [])
        cod_prest = next((r.get("cod_prestador") for t in leidos.values() for r in t if r.get("cod_prestador")), None)
        contrato = next((r.get("num_contrato") for r in af if r.get("num_contrato")), None)
        ctx = ctxmod.completar(ctx, af, cod_prest, contrato, self._manual(directorio))
        self._registrar(envio, remision, directorio, ctx, leidos, "TXT", archivos)

    # ------------------------------------------------------------------ comunes
    def _registrar(self, envio, remision, directorio, ctx, leidos, formato, archivos=None, origen=None):
        cod_ent = next((r.get("cod_entidad") for r in leidos.get("AF", []) if r.get("cod_entidad")), None)
        if not cod_ent:
            cod_ent = self._manual(directorio).get("cod_entidad")
        base_inc = {"hospital": ctx.hospital, "periodo": ctx.periodo, "envio_id": envio}
        for n in ctx.notas:
            self.inc.add(**base_inc, archivo=directorio, regla="CONTEXTO", severidad="INFO", mensaje=n)
        if not ctx.periodo or "?" in str(ctx.periodo):
            self.inc.add(**base_inc, archivo=directorio, regla="PERIODO_NO_DETERMINADO", severidad="ADVERTENCIA",
                         mensaje="No se pudo determinar el mes; corríjalo manualmente")
        if ctx.fuente_hospital != "carpeta" and ctx.fuente_hospital != "manual":
            self.inc.add(**base_inc, archivo=directorio, regla="HOSPITAL_NO_DETERMINADO", severidad="ADVERTENCIA",
                         mensaje="Hospital tomado del código del prestador")
        if not ctx.municipio:
            self.inc.add(**base_inc, archivo=directorio, regla="MUNICIPIO_NO_DETERMINADO", severidad="ADVERTENCIA",
                         mensaje="No se pudo mapear el municipio; revise config/red_prestadores.yaml")
        _validar_envio(leidos, self.inc, base_inc, directorio, ctx.periodo, self.eps, formato)
        for tipo, regs in leidos.items():
            ruta_arch = (archivos[tipo].ruta if archivos and tipo in archivos else origen or directorio)
            for r in regs:
                r.update({"hospital": ctx.hospital, "municipio": ctx.municipio, "periodo": ctx.periodo,
                          "envio_id": envio, "remision": remision, "carpeta_envio": ctx.carpeta_envio,
                          "modalidad_declarada": ctx.modalidad_declarada, "cod_entidad_envio": cod_ent,
                          "archivo": r.get("archivo") or ruta_arch, "origen": origen or ruta_arch, "formato": formato,
                          "fuente_hospital": ctx.fuente_hospital, "fuente_municipio": ctx.fuente_municipio,
                          "fuente_periodo": ctx.fuente_periodo})
                self.regs[tipo].append(r)
        self.remisiones.append({"envio_id": envio, "remision": remision, "directorio": directorio,
                                "hospital": ctx.hospital, "municipio": ctx.municipio, "periodo": ctx.periodo,
                                "carpeta_envio": ctx.carpeta_envio, "modalidad_declarada": ctx.modalidad_declarada,
                                "cod_entidad": cod_ent, "formato": formato, "fuente_hospital": ctx.fuente_hospital,
                                "fuente_municipio": ctx.fuente_municipio, "fuente_periodo": ctx.fuente_periodo,
                                "archivos": ",".join(t for t in TIPOS_RIPS if leidos.get(t)),
                                **{f"reg_{t}": len(leidos.get(t, [])) for t in TIPOS_RIPS}})

    # ------------------------------------------------------------------ JSON / XLSX
    def json_2275(self, a: ArchivoEntrada, obj) -> None:
        envio = self.nuevo_envio()
        directorio = str(PurePosixPath(a.ruta).parent)
        leidos = leer_json_2275(obj, a.ruta, self.inc)
        ctx = ctxmod.desde_ruta(a.ruta, self.anio)
        cod_prest = next((r.get("cod_prestador") for r in leidos["AF"] if r.get("cod_prestador")), None)
        ctx = ctxmod.completar(ctx, leidos["AF"], cod_prest, None, self._manual(directorio))
        man = self._manual(directorio)
        for r in leidos["AF"]:
            r["cod_entidad"] = r.get("cod_entidad") or man.get("cod_entidad") or _eps_por_carpeta(ctx.carpeta_envio)
        self.archivos.append({"archivo": a.ruta, "tipo_archivo": "JSON", "estado": "procesado", "formato": "JSON_2275",
                              "bytes": len(a.datos), "envio_id": envio, "registros": sum(map(len, leidos.values()))})
        self._registrar(envio, PurePosixPath(a.ruta).stem, directorio, ctx, leidos, "JSON_2275", origen=a.ruta)

    def consolidado(self, a: ArchivoEntrada, leidos_planos: dict[str, list[dict]], formato: str) -> None:
        """Re-agrupa los registros del consolidado por su envío original."""
        por_envio: dict[tuple, dict[str, list[dict]]] = defaultdict(_vacias)
        for tipo, regs in leidos_planos.items():
            for r in regs:
                r = {k: (fecha_txt(v) if k.startswith("fecha") else v) for k, v in r.items()}
                k = (r.get("hospital"), r.get("periodo"), r.get("envio_id") or r.get("remision"))
                por_envio[k][tipo].append(r)
        for (hosp, per, _), leidos in por_envio.items():
            envio = self.nuevo_envio()
            muestra = next(r for t in leidos.values() for r in t)
            directorio = str(PurePosixPath(str(muestra.get("archivo") or a.ruta)).parent)
            ctx = ctxmod.desde_ruta(f"{directorio}/x.txt", self.anio)
            ctx.hospital, ctx.periodo = hosp or ctx.hospital, per or ctx.periodo
            ctx.fuente_hospital = ctx.fuente_periodo = "consolidado"
            ctx.carpeta_envio = muestra.get("carpeta_envio") or ctx.carpeta_envio
            ctx.modalidad_declarada = ctxmod.modalidad_por_texto(ctx.carpeta_envio)
            cod_prest = next((r.get("cod_prestador") for t in leidos.values() for r in t if r.get("cod_prestador")), None)
            contrato = next((r.get("num_contrato") for r in leidos["AF"] if r.get("num_contrato")), None)
            ctx = ctxmod.completar(ctx, leidos["AF"], cod_prest, contrato, self._manual(directorio))
            self._registrar(envio, muestra.get("remision"), directorio, ctx, leidos, formato, origen=a.ruta)
        self.archivos.append({"archivo": a.ruta, "tipo_archivo": formato, "estado": "procesado", "formato": formato,
                              "bytes": len(a.datos), "envio_id": None,
                              "registros": sum(map(len, leidos_planos.values()))})


def _eps_por_carpeta(carpeta: str | None) -> str | None:
    from ..constantes import norm
    return "EPS037" if "NUEVA EPS" in norm(carpeta) or "NEPS" in norm(carpeta) else None


def _validar_envio(leidos, inc: Incidencias, base: dict, directorio: str, periodo: str | None,
                   eps_obj: set[str], formato: str) -> None:
    """Reglas entre archivos de una misma remisión."""
    def add(regla, sev, msg, tipo=None, linea=None, campo=None, valor=None):
        inc.add(**base, archivo=directorio, tipo_archivo=tipo, linea=linea, campo=campo, valor=valor,
                regla=regla, severidad=sev, mensaje=msg)

    if formato == "TXT":
        for t in ("CT", "AF", "US"):
            if not leidos.get(t):
                add("ARCHIVO_OBLIGATORIO_FALTANTE", "ERROR", f"La remisión no tiene archivo {t}", t)
        for c in leidos.get("CT", []):
            cod = str(c.get("codigo_archivo") or "")
            t = cod[:2].upper()
            if t not in leidos:
                add("CT_ARCHIVO_NO_ENCONTRADO", "ERROR", f"El CT declara {cod} que no está en la remisión", "CT")
            elif c.get("total_registros") is not None and int(c["total_registros"]) != len(leidos[t]):
                add("CT_TOTAL_REGISTROS", "ERROR",
                    f"CT declara {c['total_registros']} registros para {cod}; el archivo tiene {len(leidos[t])}", "CT")
        declarados = {str(c.get("codigo_archivo") or "")[:2].upper() for c in leidos.get("CT", [])}
        for t in leidos:
            if t != "CT" and leidos.get("CT") and t not in declarados:
                add("CT_ARCHIVO_NO_DECLARADO", "ADVERTENCIA", f"Archivo {t} presente pero no declarado en el CT", t)
    if not any(leidos.get(t) for t in TIPOS_SERVICIO):
        add("ENVIO_SIN_SERVICIOS", "ADVERTENCIA", "La remisión no tiene archivos de servicios")
    facturas: dict[str, dict] = {}
    for r in leidos.get("AF", []):
        nf = r.get("num_factura")
        if nf in facturas:
            add("FACTURA_DUPLICADA", "ERROR", f"Factura {nf} repetida en el AF", "AF", r.get("linea"), "num_factura", nf)
        facturas[nf] = r
        nit = str(r.get("num_id_prestador") or "")
        if nit and nit != NIT_ESE:
            add("NIT_PRESTADOR_DIFERENTE", "ADVERTENCIA", f"NIT {nit} distinto al de la E.S.E. ({NIT_ESE})",
                "AF", r.get("linea"), "num_id_prestador", nit)
        if periodo and "?" not in periodo:
            f = fecha_txt(r.get("fecha_expedicion"))
            fi = fecha_txt(r.get("fecha_inicio"))
            ref = fi or f
            if ref and len(ref) == 10 and f"{ref[6:10]}-{ref[3:5]}" != periodo:
                add("FACTURA_FUERA_DE_PERIODO", "ADVERTENCIA",
                    f"Factura {nf}: periodo facturado {ref[3:5]}/{ref[6:10]} distinto al asignado {periodo}",
                    "AF", r.get("linea"), "fecha_inicio", ref)
    if leidos.get("AF") and not any(str(r.get("cod_entidad") or "").upper() in eps_obj for r in leidos["AF"]):
        add("EPS_FUERA_DE_ALCANCE", "INFO", "La remisión no corresponde a Nueva EPS (EPS037/EPSS37); "
            "se carga pero se excluye del análisis cápita/evento Nueva EPS", "AF")
    usuarios = {(str(u.get("tipo_id_usuario")), str(u.get("num_id_usuario"))) for u in leidos.get("US", [])}
    vistos_us = set()
    for u in leidos.get("US", []):
        k = (str(u.get("tipo_id_usuario")), str(u.get("num_id_usuario")))
        if k in vistos_us:
            add("USUARIO_DUPLICADO_US", "ADVERTENCIA", "Usuario repetido en el US", "US", u.get("linea"))
        vistos_us.add(k)
    valor_serv: dict[str, float] = defaultdict(float)
    con_serv = set()
    campo_valor = {"AC": "valor_consulta", "AP": "valor", "AM": "valor_total", "AT": "valor_total", "AD": "valor_total"}
    for t in TIPOS_SERVICIO:
        for r in leidos.get(t, []):
            nf = r.get("num_factura")
            con_serv.add(nf)
            if facturas and nf not in facturas:
                add("FACTURA_NO_EXISTE_EN_AF", "ERROR", f"Factura {nf} del {t} no existe en el AF", t, r.get("linea"),
                    "num_factura", nf)
            if t in ("AC", "AP", "AU", "AH", "AM", "AT") and usuarios:
                k = (str(r.get("tipo_id_usuario")), str(r.get("num_id_usuario")))
                if k not in usuarios:
                    add("USUARIO_NO_EXISTE_EN_US", "ERROR", "Usuario del servicio no existe en el US", t, r.get("linea"))
            if t in campo_valor:
                valor_serv[nf] += float(r.get(campo_valor[t]) or 0)
            if t in ("AM", "AT"):
                q = float(r.get("num_unidades" if t == "AM" else "cantidad") or 0)
                vu, vt = float(r.get("valor_unitario") or 0), float(r.get("valor_total") or 0)
                if vu and abs(q * vu - vt) > 1:
                    add("INCONSISTENCIA_VALOR", "ADVERTENCIA", f"{t}: cantidad × valor unitario ≠ valor total",
                        t, r.get("linea"))
            if t == "AT" and not r.get("cod_servicio") and not r.get("nombre_servicio"):
                add("NOMBRE_O_CODIGO_SERVICIO", "ERROR", "AT sin código ni nombre del servicio", t, r.get("linea"))
    for nf, r in facturas.items():
        if nf not in con_serv:
            add("FACTURA_SIN_SERVICIOS", "ADVERTENCIA", f"Factura {nf} sin servicios en la remisión", "AF", r.get("linea"))
        elif r.get("valor_neto") is not None:
            esperado = valor_serv[nf] - float(r.get("valor_copago") or 0) - float(r.get("valor_descuentos") or 0)
            if abs(esperado - float(r["valor_neto"])) > 1:
                add("DIFERENCIA_VALOR_FACTURA", "ADVERTENCIA",
                    f"Factura {nf}: neto AF {float(r['valor_neto']):,.0f} vs servicios − copago − descuentos "
                    f"{esperado:,.0f}", "AF", r.get("linea"), "valor_neto", r.get("valor_neto"))


def procesar(entradas, anio_defecto: int | None = 2026, manual_global: dict | None = None,
             manual_ruta: dict | None = None) -> Lote:
    """Punto de entrada: rutas o tuplas (ruta lógica, bytes). Nunca se detiene por un archivo con error."""
    archivos, ignorados = recolectar(entradas)
    b = _Constructor(anio_defecto, manual_global, manual_ruta)
    for i in ignorados:
        b.archivos.append({"archivo": i.ruta, "tipo_archivo": None, "estado": "ignorado", "motivo": i.motivo,
                           "bytes": i.bytes, "formato": None})
        if i.severidad == "ERROR":
            b.inc.add(archivo=i.ruta, regla=i.motivo.split(":")[0], severidad="ERROR", mensaje=i.motivo)
    remisiones: dict[tuple[str, str], dict[str, ArchivoEntrada]] = defaultdict(dict)
    for a in archivos:
        try:
            if a.extension == ".txt":
                tipo, rem, normativo = tipo_y_remision(a.nombre)
                if tipo is None:
                    b.archivos.append({"archivo": a.ruta, "estado": "ignorado", "formato": "TXT",
                                       "motivo": "ARCHIVO_NO_RECONOCIDO", "bytes": len(a.datos)})
                    b.inc.add(archivo=a.ruta, regla="ARCHIVO_NO_RECONOCIDO", severidad="ADVERTENCIA",
                              mensaje="Nombre TXT no corresponde a un tipo RIPS")
                    continue
                if not normativo:
                    b.inc.add(archivo=a.ruta, tipo_archivo=tipo, regla="NOMBRE_ARCHIVO_NO_NORMATIVO",
                              severidad="ADVERTENCIA", mensaje="Nombre no cumple tipo(2)+remisión(1-6)+.txt")
                clave = (str(PurePosixPath(a.ruta).parent), rem)
                previo = remisiones[clave].get(tipo)
                if previo is not None:
                    corregido = "CORREGIDO" in a.nombre.upper()
                    b.inc.add(archivo=a.ruta, tipo_archivo=tipo,
                              regla="ARCHIVO_CORREGIDO_USADO" if corregido else "ARCHIVO_DUPLICADO",
                              severidad="INFO" if corregido else "ADVERTENCIA",
                              mensaje="Se usa la versión _CORREGIDO" if corregido else "Más de un archivo del mismo tipo")
                    if not corregido:
                        continue
                remisiones[clave][tipo] = a
            elif a.extension == ".json":
                obj = cargar_json(a.datos)
                tipo_json = detectar_json(obj)
                if tipo_json == "JSON_2275":
                    b.json_2275(a, obj)
                elif tipo_json == "CONSOLIDADO_JSON":
                    b.consolidado(a, leer_consolidado_json(obj, a.ruta, b.inc), "CONSOLIDADO_JSON")
                else:
                    b.archivos.append({"archivo": a.ruta, "estado": "ignorado", "formato": "JSON",
                                       "motivo": "JSON sin estructura RIPS reconocida", "bytes": len(a.datos)})
            elif a.extension in (".xlsx", ".xlsm"):
                planos = leer_consolidado_xlsx(a.datos, a.ruta, b.inc)
                if planos is None:
                    b.archivos.append({"archivo": a.ruta, "estado": "ignorado", "formato": "XLSX",
                                       "motivo": "XLSX no es un RIPS consolidado (cárguelo como maestro)",
                                       "bytes": len(a.datos)})
                else:
                    b.consolidado(a, planos, "CONSOLIDADO_XLSX")
        except Exception as e:  # un archivo dañado no detiene la carga
            b.archivos.append({"archivo": a.ruta, "estado": "error", "motivo": f"ARCHIVO_ILEGIBLE: {e}",
                               "bytes": len(a.datos)})
            b.inc.add(archivo=a.ruta, regla="ARCHIVO_ILEGIBLE", severidad="ERROR", mensaje=str(e)[:300])
    for (directorio, rem), arch in sorted(remisiones.items()):
        try:
            b.remision_txt(directorio, rem, arch)
        except Exception as e:
            b.inc.add(archivo=directorio, regla="ARCHIVO_ILEGIBLE", severidad="ERROR", mensaje=f"Remisión {rem}: {e}")

    tablas, dups = {}, []
    for t in TIPOS_RIPS:
        cols = columnas(t)
        df = pd.DataFrame(b.regs[t])
        for c in cols + META:
            if c not in df.columns:
                df[c] = None
        extra = [c for c in df.columns if c not in cols + META]
        df = df[cols + META + extra]
        df, el = deduplicar(df, t)
        if not el.empty:
            el.insert(0, "tipo_archivo", t)
            dups.append(el)
        tablas[t] = df
    duplicados = pd.concat(dups, ignore_index=True) if dups else pd.DataFrame()
    if not duplicados.empty:
        par = duplicados.assign(a=duplicados["archivo"].map(lambda x: str(PurePosixPath(str(x)).parent)),
                                c=duplicados["conservado_en"].map(lambda x: str(PurePosixPath(str(x)).parent)))
        for (a, c), g in par[par.tipo_archivo != "US"].groupby(["a", "c"]):
            b.inc.add(hospital=g.iloc[0]["hospital"], periodo=g.iloc[0]["periodo"], envio_id=g.iloc[0]["envio_id"],
                      archivo=a, regla="REMISION_DUPLICADA", severidad="ADVERTENCIA", valor=c,
                      mensaje=f"{len(g)} registros de servicios/facturas ya cargados desde '{c}'; no se suman de nuevo")
    # facturas repetidas en remisiones distintas (ya sin repeticiones entre formatos)
    af = tablas["AF"]
    if not af.empty:
        rep = af[af.duplicated(["num_id_prestador", "num_factura"], keep=False)]
        for nf, g in rep.groupby("num_factura"):
            if g["envio_id"].nunique() > 1:
                b.inc.add(hospital=g.iloc[0]["hospital"], periodo=g.iloc[0]["periodo"], archivo=";".join(g["archivo"]),
                          tipo_archivo="AF", campo="num_factura", valor=nf, regla="FACTURA_DUPLICADA_ENTRE_REMISIONES",
                          severidad="ERROR", mensaje=f"Factura {nf} aparece en {g['envio_id'].nunique()} remisiones")
    lote = Lote(id=datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6],
                creado=datetime.now().isoformat(timespec="seconds"), tablas=tablas,
                incidencias=b.inc.df(), archivos=pd.DataFrame(b.archivos), remisiones=pd.DataFrame(b.remisiones),
                duplicados=duplicados)
    if not duplicados.empty:
        lote.notas.append(f"Se eliminaron {len(duplicados)} registros repetidos entre fuentes/formatos")
    return lote

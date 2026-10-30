"""Lectores JSON y XLSX.

* **RIPS JSON Res. 2275/2023** (un JSON por factura electrónica: ``numFactura`` + ``usuarios``).
  Se traduce a la estructura de la Res. 3374 para que todo el análisis use un solo modelo.
* **Consolidado** generado por la herramienta ``rips_auditoria`` (``RIPS_consolidado.json`` /
  ``RIPS_consolidado.xlsx``): trae los registros ya organizados por hospital y mes.
"""
from __future__ import annotations

import io
import json
from datetime import date, datetime

import pandas as pd

from ..config import columnas, etiquetas_a_tecnico
from ..constantes import TIPOS_RIPS
from .incidencias import Incidencias

META = ["hospital", "periodo", "envio_id", "remision", "carpeta_envio", "archivo", "linea"]


def detectar_json(obj) -> str | None:
    if isinstance(obj, dict):
        if "hospitales" in obj and "indice" in obj:
            return "CONSOLIDADO_JSON"
        if "numFactura" in obj and "usuarios" in obj:
            return "JSON_2275"
        if "rips" in obj and isinstance(obj["rips"], dict):   # envoltorio del validador MinSalud
            return "JSON_2275"
    if isinstance(obj, list) and obj and isinstance(obj[0], dict) and "numFactura" in obj[0]:
        return "JSON_2275"
    return None


# --------------------------------------------------------------------- utilidades
def fecha_txt(v) -> str | None:
    """Fecha canónica dd/mm/aaaa desde str (varios formatos) o datetime."""
    if v is None or v == "":
        return None
    if isinstance(v, (datetime, date)):
        return v.strftime("%d/%m/%Y")
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19] if "T" in s else s, fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return s


def hora_txt(v) -> str | None:
    if not v:
        return None
    s = str(v).strip()
    return s[11:16] if len(s) >= 16 and s[4] == "-" else (s[:5] if ":" in s else None)


def _n(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _zona(v):
    return {"01": "U", "02": "R"}.get(str(v), v)


# --------------------------------------------------------------------- JSON 2275
def leer_json_2275(obj, archivo: str, inc: Incidencias) -> dict[str, list[dict]]:
    if isinstance(obj, dict) and "rips" in obj:
        obj = obj["rips"]
    facturas = obj if isinstance(obj, list) else [obj]
    out: dict[str, list[dict]] = {t: [] for t in TIPOS_RIPS}
    for nf, fac in enumerate(facturas, start=1):
        num_fac = fac.get("numFactura")
        nit = str(fac.get("numDocumentoIdObligado") or "")
        if not num_fac:
            inc.add(archivo=archivo, tipo_archivo="AF", linea=nf, campo="numFactura", regla="CAMPO_OBLIGATORIO",
                    severidad="ERROR", mensaje="JSON 2275 sin numFactura")
        if not fac.get("usuarios"):
            inc.add(archivo=archivo, tipo_archivo="US", linea=nf, campo="usuarios", regla="CAMPO_OBLIGATORIO",
                    severidad="ERROR", mensaje="JSON 2275 sin usuarios")
        fechas, total, moderador, cod_prest = [], 0.0, 0.0, None
        for u in fac.get("usuarios") or []:
            tid, nid = u.get("tipoDocumentoIdentificacion"), str(u.get("numDocumentoIdentificacion") or "")
            mun = str(u.get("codMunicipioResidencia") or "")
            sv = u.get("servicios") or {}
            primera = None
            for lista in sv.values():
                for s in lista or []:
                    f = s.get("fechaInicioAtencion") or s.get("fechaDispensAdmon") or s.get("fechaSuministroTecnologia")
                    if f:
                        primera = min(primera or str(f), str(f))
            edad = None
            if u.get("fechaNacimiento") and primera:
                try:
                    fn = datetime.strptime(str(u["fechaNacimiento"])[:10], "%Y-%m-%d")
                    edad = int((datetime.strptime(primera[:10], "%Y-%m-%d") - fn).days // 365.25)
                except ValueError:
                    pass
            out["US"].append({"tipo_id_usuario": tid, "num_id_usuario": nid, "cod_entidad": None,
                              "tipo_usuario": u.get("tipoUsuario"), "edad": edad, "unidad_edad": "1",
                              "sexo": u.get("codSexo"), "cod_departamento": mun[:2] or None,
                              "cod_municipio": mun[2:] or None, "zona": _zona(u.get("codZonaTerritorialResidencia")),
                              "linea": u.get("consecutivo")})
            base = {"num_factura": num_fac, "tipo_id_usuario": tid, "num_id_usuario": nid}

            def comun(s):
                nonlocal cod_prest, total, moderador
                cod_prest = cod_prest or s.get("codPrestador")
                total += _n(s.get("vrServicio")) or 0
                moderador += _n(s.get("valorPagoModerador")) or 0
                f = s.get("fechaInicioAtencion") or s.get("fechaDispensAdmon") or s.get("fechaSuministroTecnologia")
                if f:
                    fechas.append(str(f))
                return {**base, "cod_prestador": s.get("codPrestador"), "num_autorizacion": s.get("numAutorizacion"),
                        "linea": s.get("consecutivo")}

            for s in sv.get("consultas") or []:
                vr, mo = _n(s.get("vrServicio")) or 0, _n(s.get("valorPagoModerador")) or 0
                out["AC"].append({**comun(s), "fecha_consulta": fecha_txt(s.get("fechaInicioAtencion")),
                                  "cod_consulta": s.get("codConsulta"), "finalidad": s.get("finalidadTecnologiaSalud"),
                                  "causa_externa": s.get("causaMotivoAtencion"),
                                  "dx_principal": s.get("codDiagnosticoPrincipal"),
                                  "dx_relacionado1": s.get("codDiagnosticoRelacionado1"),
                                  "dx_relacionado2": s.get("codDiagnosticoRelacionado2"),
                                  "dx_relacionado3": s.get("codDiagnosticoRelacionado3"),
                                  "tipo_dx_principal": s.get("tipoDiagnosticoPrincipal"),
                                  "valor_consulta": vr, "valor_cuota_moderadora": mo, "valor_neto": vr - mo})
            for s in sv.get("procedimientos") or []:
                # SUPUESTO: ámbito 3374 desde grupoServicios 2275 (03/04 internación/quirúrgico -> 2; 05 -> 3).
                g = str(s.get("grupoServicios") or "")
                out["AP"].append({**comun(s), "fecha_procedimiento": fecha_txt(s.get("fechaInicioAtencion")),
                                  "cod_procedimiento": s.get("codProcedimiento"),
                                  "ambito": "2" if g in ("03", "04") else "3" if g == "05" else "1",
                                  "finalidad": s.get("finalidadTecnologiaSalud"),
                                  "dx_principal": s.get("codDiagnosticoPrincipal"),
                                  "dx_relacionado": s.get("codDiagnosticoRelacionado"),
                                  "dx_complicacion": s.get("codComplicacion"),
                                  "valor": _n(s.get("vrServicio"))})
            for s in sv.get("urgencias") or []:
                out["AU"].append({**comun(s), "fecha_ingreso": fecha_txt(s.get("fechaInicioAtencion")),
                                  "hora_ingreso": hora_txt(s.get("fechaInicioAtencion")),
                                  "causa_externa": s.get("causaMotivoAtencion"),
                                  "dx_salida": s.get("codDiagnosticoPrincipalE") or s.get("codDiagnosticoPrincipal"),
                                  "destino_salida": s.get("condicionDestinoUsuarioEgreso"),
                                  "causa_muerte": s.get("codDiagnosticoCausaMuerte"),
                                  "fecha_salida": fecha_txt(s.get("fechaEgreso")), "hora_salida": hora_txt(s.get("fechaEgreso"))})
            for s in sv.get("hospitalizacion") or []:
                out["AH"].append({**comun(s), "via_ingreso": s.get("viaIngresoServicioSalud"),
                                  "fecha_ingreso": fecha_txt(s.get("fechaInicioAtencion")),
                                  "hora_ingreso": hora_txt(s.get("fechaInicioAtencion")),
                                  "causa_externa": s.get("causaMotivoAtencion"),
                                  "dx_ingreso": s.get("codDiagnosticoPrincipal"),
                                  "dx_egreso": s.get("codDiagnosticoPrincipalE"),
                                  "dx_complicacion": s.get("codComplicacion"),
                                  "estado_salida": s.get("condicionDestinoUsuarioEgreso"),
                                  "causa_muerte": s.get("codDiagnosticoCausaMuerte"),
                                  "fecha_egreso": fecha_txt(s.get("fechaEgreso")), "hora_egreso": hora_txt(s.get("fechaEgreso"))})
            for s in sv.get("recienNacidos") or []:
                c = comun(s)
                out["AN"].append({"num_factura": num_fac, "cod_prestador": c["cod_prestador"],
                                  "tipo_id_madre": tid, "num_id_madre": nid,
                                  "fecha_nacimiento": fecha_txt(s.get("fechaNacimiento")),
                                  "hora_nacimiento": hora_txt(s.get("fechaNacimiento")),
                                  "edad_gestacional": s.get("edadGestacional"),
                                  "control_prenatal": "1" if (_n(s.get("numConsultasCPrenatal")) or 0) > 0 else "2",
                                  "sexo": s.get("codSexoBiologico"), "peso": s.get("peso"),
                                  "dx_recien_nacido": s.get("codDiagnosticoPrincipal"),
                                  "causa_muerte": s.get("codDiagnosticoCausaMuerte"), "linea": c["linea"]})
            for s in sv.get("medicamentos") or []:
                out["AM"].append({**comun(s), "cod_medicamento": s.get("codTecnologiaSalud"),
                                  "tipo_medicamento": s.get("tipoMedicamento"),
                                  "nombre_generico": s.get("nomTecnologiaSalud"),
                                  "forma_farmaceutica": s.get("formaFarmaceutica"),
                                  "concentracion": s.get("concentracionMedicamento"),
                                  "unidad_medida": s.get("unidadMedida"),
                                  "num_unidades": _n(s.get("cantidadMedicamento")),
                                  "valor_unitario": _n(s.get("vrUnitMedicamento")),
                                  "valor_total": _n(s.get("vrServicio")),
                                  "fecha_servicio": fecha_txt(s.get("fechaDispensAdmon"))})
            for s in sv.get("otrosServicios") or []:
                out["AT"].append({**comun(s), "tipo_servicio": s.get("tipoOS"),
                                  "cod_servicio": s.get("codTecnologiaSalud"),
                                  "nombre_servicio": s.get("nomTecnologiaSalud"),
                                  "cantidad": _n(s.get("cantidadOS")), "valor_unitario": _n(s.get("vrUnitOS")),
                                  "valor_total": _n(s.get("vrServicio")),
                                  "fecha_servicio": fecha_txt(s.get("fechaSuministroTecnologia"))})
        fs = sorted(fechas)
        out["AF"].append({"cod_prestador": cod_prest, "razon_social": None, "tipo_id_prestador": "NI",
                          "num_id_prestador": nit, "num_factura": num_fac, "fecha_expedicion": None,
                          "fecha_inicio": fecha_txt(fs[0]) if fs else None,
                          "fecha_final": fecha_txt(fs[-1]) if fs else None,
                          # El JSON 2275 no trae la EPS: se toma de la carpeta o de la corrección manual.
                          "cod_entidad": None, "nombre_entidad": None, "num_contrato": None,
                          "valor_copago": moderador, "valor_comision": 0, "valor_descuentos": 0,
                          "valor_neto": total - moderador, "linea": nf})
    inc.add(archivo=archivo, tipo_archivo="AF", regla="JSON2275_SIN_EPS", severidad="INFO",
            mensaje="El RIPS JSON 2275 no incluye código de EPS; se asigna desde carpeta/corrección manual")
    return out


# --------------------------------------------------------------------- consolidado
def leer_consolidado_json(obj: dict, archivo: str, inc: Incidencias) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {t: [] for t in TIPOS_RIPS}
    for hosp, h in (obj.get("hospitales") or {}).items():
        for per, m in (h.get("meses") or {}).items():
            for tipo, regs in (m.get("registros") or {}).items():
                if tipo not in out:
                    continue
                for r in regs:
                    r = dict(r)
                    r.setdefault("hospital", hosp)
                    r.setdefault("periodo", per)
                    out[tipo].append(r)
    return out


def leer_consolidado_xlsx(datos: bytes, archivo: str, inc: Incidencias) -> dict[str, list[dict]] | None:
    xl = pd.ExcelFile(io.BytesIO(datos), engine="openpyxl")
    hojas = set(xl.sheet_names)
    if not {"AF", "US"} <= hojas:
        return None
    mapa = etiquetas_a_tecnico()
    out: dict[str, list[dict]] = {t: [] for t in TIPOS_RIPS}
    for tipo in TIPOS_RIPS:
        if tipo not in hojas:
            continue
        df = xl.parse(tipo, dtype=object)
        df = df.rename(columns=lambda c: mapa.get(str(c).strip(), str(c).strip()))
        desconocidas = [c for c in df.columns if c not in set(columnas(tipo)) | set(META)]
        if desconocidas:
            inc.add(archivo=archivo, tipo_archivo=tipo, regla="COLUMNA_NO_RECONOCIDA", severidad="ADVERTENCIA",
                    mensaje=f"Columnas no reconocidas: {', '.join(map(str, desconocidas[:6]))}")
        df = df.astype(object).where(df.notna(), None)
        out[tipo] = df.to_dict("records")
    return out


def cargar_json(datos: bytes):
    return json.loads(datos.decode("utf-8-sig"))

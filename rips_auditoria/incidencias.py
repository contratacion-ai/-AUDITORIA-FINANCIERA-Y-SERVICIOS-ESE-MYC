"""Registro de incidencias de validación.

Cada incidencia es una fila con: dónde ocurrió (envío, archivo, línea,
campo), qué regla falló, su severidad y un mensaje legible. Las reglas y su
severidad por defecto están en ``REGLAS``; la severidad se puede cambiar por
configuración (``validacion.severidades``), incluido ``OMITIR``.
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd

ERROR = "ERROR"
ADVERTENCIA = "ADVERTENCIA"
INFO = "INFO"
OMITIR = "OMITIR"
SEVERIDADES = (ERROR, ADVERTENCIA, INFO, OMITIR)

# código -> (severidad por defecto, descripción)
REGLAS: dict[str, tuple[str, str]] = {
    # Carga de archivos
    "ZIP_CORRUPTO": (ERROR, "El archivo ZIP no se pudo abrir o está dañado"),
    "ARCHIVO_ILEGIBLE": (ERROR, "El archivo no se pudo leer"),
    "ARCHIVO_NO_RECONOCIDO": (ADVERTENCIA, "Archivo TXT cuyo nombre no corresponde a un tipo RIPS del Anexo Técnico No. 1"),
    "ARCHIVO_DUPLICADO": (ADVERTENCIA, "Hay más de un archivo del mismo tipo y remisión en la misma carpeta"),
    "ARCHIVO_CORREGIDO_USADO": (INFO, "Se usó la versión _CORREGIDO del archivo en lugar del original"),
    "NOMBRE_ARCHIVO_NO_NORMATIVO": (ADVERTENCIA, "El nombre no cumple: tipo (2) + remisión (1 a 6 caracteres) + .txt"),
    "CODIFICACION_NO_UTF8": (INFO, "El archivo no está en UTF-8; se leyó con otra codificación"),
    "TEXTO_REPARADO": (INFO, "Se repararon caracteres con doble codificación (p. ej. 'Ã“' -> 'Ó')"),
    "ARCHIVO_VACIO": (ERROR, "El archivo no contiene registros"),
    # Estructura del registro
    "LINEA_VACIA": (ADVERTENCIA, "Línea en blanco dentro del archivo"),
    "CANTIDAD_CAMPOS": (ERROR, "El registro no tiene la cantidad de campos definida en el Anexo; se excluye del análisis"),
    "SEPARADOR_FINAL": (ADVERTENCIA, "El registro termina en coma (campo adicional vacío)"),
    "COMILLAS": (ADVERTENCIA, "Campo encerrado entre comillas (no permitido por el Anexo); se retiraron"),
    "REGISTRO_DUPLICADO": (ADVERTENCIA, "Registro idéntico a otro del mismo archivo"),
    # Campos
    "CAMPO_OBLIGATORIO": (ERROR, "Campo obligatorio sin diligenciar"),
    "LONGITUD_EXCEDIDA": (ERROR, "El valor supera la longitud máxima del Anexo"),
    "FORMATO_FECHA": (ERROR, "Fecha inválida; se espera dd/mm/aaaa"),
    "FORMATO_HORA": (ERROR, "Hora inválida; se espera hh:mm (24 horas)"),
    "FORMATO_NUMERICO": (ERROR, "Valor numérico inválido (use punto decimal y sin separador de miles)"),
    "FORMATO_CIE10": (ADVERTENCIA, "El código no tiene formato CIE-10 (letra + 3 caracteres)"),
    "VALOR_NO_PERMITIDO": (ERROR, "Valor fuera de la tabla de valores permitidos del Anexo"),
    "VALOR_CATALOGO_EXTENDIDO": (ADVERTENCIA, "Valor no previsto en la Res. 3374 pero válido en normas posteriores (Res. 2275/2023)"),
    # Consistencia de la remisión (referencia cruzada, Art. 9 num. 2)
    "ARCHIVO_OBLIGATORIO_FALTANTE": (ERROR, "La remisión no tiene un archivo obligatorio (CT, AF o US)"),
    "ENVIO_SIN_SERVICIOS": (ADVERTENCIA, "La remisión no tiene archivos de servicios (AC, AP, AU, AH, AN, AM, AT, AD)"),
    "CT_ARCHIVO_NO_ENCONTRADO": (ERROR, "El CT declara un archivo que no está en la remisión"),
    "CT_TOTAL_REGISTROS": (ERROR, "El total de registros declarado en el CT no coincide con el archivo"),
    "CT_ARCHIVO_NO_DECLARADO": (ADVERTENCIA, "Archivo presente en la remisión pero no declarado en el CT"),
    "FACTURA_DUPLICADA": (ERROR, "Número de factura repetido en el archivo AF"),
    "FACTURA_NO_EXISTE_EN_AF": (ERROR, "La factura del servicio no existe en el AF de la remisión"),
    "USUARIO_NO_EXISTE_EN_US": (ERROR, "El usuario del servicio no existe en el US de la remisión"),
    "USUARIO_DUPLICADO_US": (ADVERTENCIA, "Usuario repetido en el archivo US"),
    "FACTURA_SIN_SERVICIOS": (ADVERTENCIA, "Factura del AF sin servicios asociados en la remisión"),
    "NOMBRE_O_CODIGO_SERVICIO": (ERROR, "AT: si no hay código del servicio, el nombre es obligatorio"),
    "INCONSISTENCIA_VALOR": (ADVERTENCIA, "Valor total no corresponde a cantidad x valor unitario (o neto != valor - cuota)"),
    "INCONSISTENCIA_FECHAS": (ADVERTENCIA, "Secuencia de fechas incoherente (fin antes de inicio, egreso antes de ingreso)"),
    "FACTURA_FUERA_DE_PERIODO": (ADVERTENCIA, "La fecha de expedición de la factura no corresponde al período asignado"),
    "DIFERENCIA_VALOR_FACTURA": (ADVERTENCIA, "El valor neto del AF no concilia con la suma de los servicios RIPS"),
    # Organización
    "PERIODO_NO_DETERMINADO": (ADVERTENCIA, "No se pudo determinar el mes de la remisión"),
    "HOSPITAL_NO_DETERMINADO": (ADVERTENCIA, "No se pudo determinar el hospital; se usó el código del prestador"),
    # Homologación
    "HOMOLOGACION_SIN_EQUIVALENCIA": (ADVERTENCIA, "Código sin equivalencia en la tabla de homologación; se conservó el valor original"),
}

COLUMNAS = [
    "hospital",
    "periodo",
    "envio_id",
    "archivo",
    "tipo_archivo",
    "linea",
    "campo",
    "valor",
    "regla",
    "severidad",
    "mensaje",
]


class RegistroIncidencias:
    """Acumula incidencias (filas sueltas o DataFrames ya vectorizados)."""

    def __init__(self, severidades: dict[str, str] | None = None):
        self._severidades = {k.upper(): str(v).upper() for k, v in (severidades or {}).items()}
        for regla, sev in self._severidades.items():
            if sev not in SEVERIDADES:
                raise ValueError(f"Severidad inválida para {regla}: {sev!r}. Use una de {SEVERIDADES}")
        self._filas: list[dict] = []
        self._bloques: list[pd.DataFrame] = []

    def severidad(self, regla: str) -> str:
        if regla in self._severidades:
            return self._severidades[regla]
        return REGLAS.get(regla, (ADVERTENCIA, ""))[0]

    def agregar(self, regla: str, mensaje: str | None = None, **contexto) -> None:
        sev = self.severidad(regla)
        if sev == OMITIR:
            return
        fila = {c: contexto.get(c) for c in COLUMNAS}
        fila.update(regla=regla, severidad=sev, mensaje=mensaje or REGLAS.get(regla, ("", regla))[1])
        self._filas.append(fila)

    def agregar_bloque(self, df: pd.DataFrame, regla: str, mensaje: str | None = None) -> None:
        """Agrega muchas incidencias de una misma regla. ``df`` debe traer las
        columnas de ubicación que apliquen (envio_id, archivo, linea, ...)."""
        if df is None or df.empty:
            return
        sev = self.severidad(regla)
        if sev == OMITIR:
            return
        bloque = df.reindex(columns=COLUMNAS).copy()
        bloque["regla"] = regla
        bloque["severidad"] = sev
        if mensaje is not None or bloque["mensaje"].isna().all():
            bloque["mensaje"] = mensaje or REGLAS.get(regla, ("", regla))[1]
        self._bloques.append(bloque)

    def extender(self, otras: Iterable[dict]) -> None:
        for o in otras:
            self.agregar(**o)

    def como_dataframe(self) -> pd.DataFrame:
        partes = [p for p in self._bloques if not p.empty]
        if self._filas:
            partes.append(pd.DataFrame(self._filas, columns=COLUMNAS))
        if not partes:
            return pd.DataFrame(columns=COLUMNAS)
        df = pd.concat(partes, ignore_index=True)
        df["linea"] = pd.to_numeric(df["linea"], errors="coerce").astype("Int64")
        return df[COLUMNAS]


def resumen_incidencias(df: pd.DataFrame) -> pd.DataFrame:
    """Conteo por regla y severidad, con la descripción de la regla."""
    if df.empty:
        return pd.DataFrame(columns=["severidad", "regla", "descripcion", "cantidad"])
    out = df.groupby(["severidad", "regla"], dropna=False).size().reset_index(name="cantidad")
    out["descripcion"] = out["regla"].map(lambda r: REGLAS.get(r, ("", ""))[1])
    orden = {ERROR: 0, ADVERTENCIA: 1, INFO: 2}
    out["_o"] = out["severidad"].map(orden).fillna(9)
    out = out.sort_values(["_o", "cantidad"], ascending=[True, False]).drop(columns="_o")
    return out[["severidad", "regla", "descripcion", "cantidad"]].reset_index(drop=True)

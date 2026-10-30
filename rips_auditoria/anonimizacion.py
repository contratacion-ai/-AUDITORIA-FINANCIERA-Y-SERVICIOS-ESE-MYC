"""Seudonimización de pacientes para informes agregados.

Los informes nunca muestran nombres ni números de documento: se reemplazan por
``PAC-<hash>`` (SHA-256 con sal local, no reversible sin la sal). La sal vive en
``datos/.sal_anonimizacion`` (excluida de git) para que el mismo paciente tenga el mismo
seudónimo entre meses y se puedan contar pacientes únicos.
"""
from __future__ import annotations

import hashlib

import pandas as pd

from .config import sal_anonimizacion

CAMPOS_PERSONALES = ["primer_apellido", "segundo_apellido", "primer_nombre", "segundo_nombre",
                     "num_id_usuario", "num_id_madre"]


def seudonimo(tipo_id, num_id, sal: str | None = None) -> str:
    sal = sal or sal_anonimizacion()
    h = hashlib.sha256(f"{sal}|{str(tipo_id or '').strip().upper()}|{str(num_id or '').strip()}".encode()).hexdigest()
    return "PAC-" + h[:12]


def columna_paciente(df: pd.DataFrame, tipo_col="tipo_id_usuario", num_col="num_id_usuario") -> pd.Series:
    sal = sal_anonimizacion()
    return pd.Series([seudonimo(t, n, sal) for t, n in zip(df[tipo_col], df[num_col])], index=df.index, dtype=object)


def anonimizar(df: pd.DataFrame) -> pd.DataFrame:
    """Copia sin datos personales directos (para exportar detalles)."""
    d = df.copy()
    if "tipo_id_usuario" in d and "num_id_usuario" in d and "paciente" not in d:
        d["paciente"] = columna_paciente(d)
    return d.drop(columns=[c for c in CAMPOS_PERSONALES if c in d.columns])

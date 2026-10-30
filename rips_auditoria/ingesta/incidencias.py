"""Acumulador de incidencias de validación (no detiene el proceso)."""
from __future__ import annotations

import pandas as pd

COLUMNAS = ["hospital", "periodo", "envio_id", "archivo", "tipo_archivo", "linea", "campo", "valor",
            "regla", "severidad", "mensaje"]


class Incidencias:
    def __init__(self) -> None:
        self.filas: list[dict] = []

    def add(self, **kw) -> None:
        self.filas.append({k: kw.get(k) for k in COLUMNAS})

    def extend(self, otras: "Incidencias") -> None:
        self.filas.extend(otras.filas)

    def df(self) -> pd.DataFrame:
        return pd.DataFrame(self.filas, columns=COLUMNAS)

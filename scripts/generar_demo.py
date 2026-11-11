"""Crea una base de demostración con DATOS FICTICIOS y genera los entregables de ejemplo.

Uso:  python scripts/generar_demo.py [carpeta_de_datos]

Las cifras son aleatorias (semilla fija) y no corresponden a la ESE; solo los nombres
de sedes vienen del catálogo inicial. Sirve para conocer la herramienta antes de
cargar información real y para producir ``entregables/Informe_ejemplo.xlsx``.
"""
from __future__ import annotations

import datetime as dt
import io
import random
import sys
from pathlib import Path

import openpyxl

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from ese_control import cargues, catalogo, db, esquema, informe, plantillas  # noqa: E402
from ese_control.config import COMBUSTIBLE, MANTENIMIENTO, SERVICIOS  # noqa: E402
from ese_control.validacion import Opciones  # noqa: E402

HOY = dt.date(2026, 11, 11)
MESES = [f"2026-{m:02d}" for m in range(5, 11)]          # mayo a octubre de 2026


def _llenar(item: str, filas: list, bd) -> bytes:
    wb = openpyxl.load_workbook(io.BytesIO(plantillas.generar(item, ruta=bd)))
    ws = wb["Datos"]
    for i, fila in enumerate(filas, start=plantillas.FILA_ENCABEZADO + 1):
        for j, v in enumerate(fila, start=1):
            ws.cell(row=i, column=j, value=v)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def main(carpeta: Path) -> None:
    carpeta.mkdir(parents=True, exist_ok=True)
    bd = carpeta / "control_ese.db"
    for f in carpeta.glob("control_ese.db*"):
        f.unlink()
    db.inicializar(bd)
    rnd = random.Random(2026)

    # Vehículos ficticios: dos por hospital, asignados a la sede principal.
    t = catalogo.tablas_actuales(bd)
    principales = t["Sedes"][(t["Sedes"]["es_principal"] == "SI") & (t["Sedes"]["tipo"] != "Administrativa")]
    veh = []
    for k, s in enumerate(principales.itertuples()):
        veh.append([f"DEM{100 + 2 * k}", s.sede_id, "Ambulancia TAB diésel (demostración)", "Ambulancia",
                    "Diésel (ACPM)", 38, "SI", "Dato ficticio"])
        veh.append([f"DEM{101 + 2 * k}", s.sede_id, "Ambulancia TAB gasolina (demostración)", "Ambulancia",
                    "Gasolina corriente", 21, "SI", "Dato ficticio"])
    import pandas as pd
    t["Vehiculos"] = pd.DataFrame(veh, columns=catalogo.HOJAS["Vehiculos"])
    catalogo.aplicar(t, "demo", "vehículos de demostración", bd)

    sedes = t["Sedes"]
    base = {s.sede_id: (rnd.uniform(8000, 30000) if s.tipo == "Hospital" else rnd.uniform(1500, 6000)
                        if s.es_principal == "SI" else rnd.uniform(30, 400)) for s in sedes.itertuples()}
    for i, per in enumerate(MESES):
        anio, mes = int(per[:4]), int(per[5:])
        sp, comb, mant = [], [], []
        for s in sedes.itertuples():
            kwh = round(base[s.sede_id] * rnd.uniform(0.9, 1.12) * (1.75 if (s.sede_id == "FOR-01" and i == 5) else 1))
            valor = round(kwh * rnd.uniform(1020, 1090))
            mora = valor * (3.4 if s.sede_id == "CRN-01" else 1) if i == 5 else valor
            if s.sede_id == "ARQ-03" and i == 5:
                continue                                              # sede sin reporte en el último mes
            sp.append([per, s.sede_id, None, "Energía eléctrica", kwh, "kWh", valor, round(mora), "Empresa de energía",
                       f"FE-{rnd.randint(10000, 99999)}", dt.date(anio, mes, 28), None])
            if s.es_principal == "SI":
                m3 = round(base[s.sede_id] / 40 * rnd.uniform(0.85, 1.15))
                sp.append([per, s.sede_id, None, "Acueducto", m3, "m3", m3 * 2700, None, "Empresa de acueducto", None, None, None])
                sp.append([per, s.sede_id, None, "Aseo", None, "N/A", 61000, None, "Empresa de acueducto", None, None, None])
        for v in veh:
            for _ in range(rnd.randint(5, 9)):
                gal = round(rnd.uniform(6, 16) * (2.3 if (v[0] == "DEM104" and i == 5) else 1), 1)
                precio = 11300 if "diésel" in v[2] else 15900
                comb.append([dt.date(anio, mes, rnd.randint(1, 28)), None, None, esquema.CAT_VEHICULOS, v[0], v[4], gal,
                             round(gal * precio), None, "Estación de servicio", None, None])
            if rnd.random() < 0.45:
                mant.append([dt.date(anio, mes, rnd.randint(1, 28)), v[1], None, esquema.CAT_VEHICULOS, v[0],
                             rnd.choice(["Preventivo", "Correctivo"]), "Mantenimiento general (demostración)",
                             round(rnd.uniform(0.6, 9) * 1e6 * (4 if v[0] == "DEM109" else 1)), "Taller", None, None])
        for s in principales.itertuples():
            if rnd.random() < 0.6:
                gal = rnd.randint(15, 60)
                comb.append([dt.date(anio, mes, rnd.randint(1, 28)), s.sede_id, None, esquema.CAT_PLANTAS,
                             "PLANTA ELECTRICA DIESEL", "Diésel (ACPM)", gal, gal * 11300, None, "Estación", None, None])
        for item, filas, nombre in ((SERVICIOS, sp, "servicios"), (COMBUSTIBLE, comb, "combustible"), (MANTENIMIENTO, mant, "mantenimientos")):
            prep = cargues.preparar(f"{nombre}_{per}.xlsx", _llenar(item, filas, bd), item,
                                    Opciones(periodo=per, alcance="periodo", hoy=HOY), bd)
            assert prep.conteo("Error") == 0, prep.incidencias_df().to_string()
            cargues.confirmar(prep, "demo", observacion="Datos de demostración", ruta=bd)
    print(f"Base de demostración creada en {bd}")

    salida = RAIZ / "entregables"
    (salida / "plantillas").mkdir(parents=True, exist_ok=True)
    for item, nombre in plantillas.NOMBRE_ARCHIVO.items():
        (salida / "plantillas" / nombre).write_bytes(plantillas.generar(item, ruta=bd))
    (salida / "Informe_ejemplo.xlsx").write_bytes(informe.generar(bd, hoy=HOY, nota="DATOS FICTICIOS DE DEMOSTRACIÓN"))
    print(f"Plantillas e informe de ejemplo generados en {salida}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ / "datos_demo")

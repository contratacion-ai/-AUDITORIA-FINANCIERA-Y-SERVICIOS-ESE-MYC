"""Pruebas del motor ABC (sin Django): python -m unittest discover abc_engine"""

import math
import random
import unittest

from abc_engine import datos_estudio as D
from abc_engine.calculo import (
    METODO_CAPACIDAD,
    METODO_REAL,
    PoolAmbulancia,
    TrasladoDriver,
    asignar_periodo,
    costo_recorrido,
    indicadores_flota,
)
from abc_engine.estudio import perfiles_estudio, precios_estudio, verificar_estudio


def pools_estudio(dias=30):
    return [
        PoolAmbulancia(
            placa=p.placa,
            sede=p.sede,
            combustible=p.combustible_dia * dias,
            mantenimiento=p.mantenimiento_dia * dias,
            tripulacion=p.tripulacion_dia * dias,
        )
        for p in perfiles_estudio()
    ]


class ReproduccionEstudioTests(unittest.TestCase):
    def setUp(self):
        self.precios = precios_estudio()
        self.perfiles = perfiles_estudio()
        self.ind = indicadores_flota(self.perfiles, self.precios)

    def test_total_dia_por_ambulancia_igual_al_anexo(self):
        for fila, perfil in zip(D.ANEXO_A, self.perfiles):
            # el anexo redondea cada componente al peso: diferencias de ±1
            self.assertAlmostEqual(perfil.total_dia, fila[7], delta=1, msg=perfil.placa)

    def test_promedios_tabla_3(self):
        self.assertEqual(self.ind["n_ambulancias"], 20)
        self.assertAlmostEqual(self.ind["total_dia"]["promedio"], 866077, delta=1)
        self.assertAlmostEqual(self.ind["km_dia"]["promedio"], 122.6, delta=0.05)
        self.assertAlmostEqual(self.ind["combustible_dia"]["promedio"], 211162, delta=1)
        self.assertAlmostEqual(self.ind["mantenimiento_dia"]["promedio"], 196449, delta=1)
        self.assertAlmostEqual(self.ind["costo_hora_disponibilidad"]["promedio"], 36087, delta=1)
        self.assertEqual(self.ind["total_dia"]["min_placa"], "OYX016")
        self.assertEqual(self.ind["total_dia"]["max_placa"], "OEU846")

    def test_costo_flota_dia(self):
        self.assertAlmostEqual(self.ind["costo_flota_dia"], D.REPORTADO["flota_total_dia"], delta=25)

    def test_okz364_excluida_de_promedios(self):
        self.assertNotIn("OKZ364", {self.ind["total_dia"]["min_placa"], self.ind["km_dia"]["min_placa"]})

    def test_verificacion_detecta_rendimientos_no_reproducibles(self):
        hallazgos = verificar_estudio()
        indicadores = {h["indicador"] for h in hallazgos}
        self.assertIn("Rendimiento real máximo (km/gal)", indicadores)
        self.assertNotIn("Costo total promedio ($/día)", indicadores)


class CostoRecorridoTests(unittest.TestCase):
    def setUp(self):
        self.precios = precios_estudio()
        self.perfiles = {p.placa: p for p in perfiles_estudio()}

    def test_absorcion_reproduce_costo_por_km_del_estudio(self):
        p = self.perfiles["OKZ365"]
        c = costo_recorrido(p, 100, self.precios, METODO_REAL)
        self.assertAlmostEqual(c.total, 100 * p.costo_km, places=4)
        self.assertAlmostEqual(c.total / 100, 5360, delta=1)

    def test_capacidad_cobra_hora_de_disponibilidad(self):
        p = self.perfiles["OEU846"]
        c = costo_recorrido(p, p.km_dia, self.precios, METODO_CAPACIDAD)
        self.assertAlmostEqual(c.horas, p.horas_dia)
        self.assertAlmostEqual(c.tripulacion, p.horas_dia * p.tripulacion_dia / 24)
        self.assertAlmostEqual(c.combustible, p.combustible_dia)
        self.assertAlmostEqual(c.mantenimiento, p.mantenimiento_dia)

    def test_galones_usan_precio_por_tipo(self):
        p = self.perfiles["OEU846"]
        c = costo_recorrido(p, p.km_dia, self.precios)
        self.assertAlmostEqual(c.galones * D.PRECIO_GASOLINA, p.combustible_dia, places=4)


class ConservacionCostoTests(unittest.TestCase):
    """Lo asignado a traslados + capacidad no utilizada = costo total de la flota."""

    def setUp(self):
        self.precios = precios_estudio()
        self.perfiles = {p.placa: p for p in perfiles_estudio()}
        rnd = random.Random(42)
        sedes = sorted({p.sede for p in self.perfiles.values()})
        placas = sorted(self.perfiles)
        self.traslados = []
        for i in range(400):
            placa = rnd.choice(placas + [None] * 5)
            sede = self.perfiles[placa].sede if placa else rnd.choice(sedes)
            self.traslados.append(TrasladoDriver(id=f"T{i}", sede=sede, placa=placa, km=rnd.choice([60, 90, 118, 202, 388])))
        self.pools = pools_estudio()
        for p in self.pools[:5]:
            p.insumos = 1_000_000
            p.otros = 250_000
        self.total_flota = sum(p.total for p in self.pools)

    def _verificar(self, metodo, indirectos=0.0):
        r = asignar_periodo(self.pools, self.traslados, self.perfiles, self.precios, metodo, indirectos=indirectos)
        self.assertTrue(math.isclose(r.total_pool, self.total_flota + indirectos, rel_tol=1e-12))
        self.assertTrue(
            math.isclose(r.total_asignado + r.total_no_utilizado, self.total_flota + indirectos, rel_tol=1e-9),
            (r.total_asignado, r.total_no_utilizado, self.total_flota),
        )
        for tid, c in r.por_traslado.items():
            componentes = sum(c[a] for a in ("combustible", "mantenimiento", "tripulacion", "insumos", "otros", "indirectos"))
            self.assertAlmostEqual(componentes, c["total"], places=4)
        self.assertAlmostEqual(sum(c["total"] for c in r.costo_parte.values()), r.total_asignado, places=3)
        return r

    def test_metodo_real_absorbe_todo_en_traslados(self):
        r = self._verificar(METODO_REAL)
        con_traslados = {p for pt in r.placa_traslado.values() for p in pt}
        for placa, fila in r.por_ambulancia.items():
            if placa in con_traslados:
                self.assertAlmostEqual(fila["no_utilizado_total"], 0, delta=1e-4)

    def test_metodo_capacidad_conserva_total(self):
        r = self._verificar(METODO_CAPACIDAD)
        self.assertNotAlmostEqual(r.total_no_utilizado, 0, delta=1)

    def test_indirectos_se_asignan_completos(self):
        r = self._verificar(METODO_REAL, indirectos=98_548_329.93)
        self.assertAlmostEqual(sum(c["indirectos"] for c in r.por_traslado.values()), 98_548_329.93, delta=0.01)

    def test_ambulancia_sin_traslados_queda_como_no_utilizada(self):
        pools = [PoolAmbulancia("OKZ364", "Fortul", 100, 200, 300), PoolAmbulancia("OEU846", "Tame", 10, 20, 30)]
        tr = [TrasladoDriver("A", "Tame", 50, "OEU846")]
        r = asignar_periodo(pools, tr, self.perfiles, self.precios)
        self.assertAlmostEqual(r.por_ambulancia["OKZ364"]["no_utilizado_total"], 600)
        self.assertAlmostEqual(r.por_traslado["A"]["total"], 60)
        self.assertAlmostEqual(r.total_asignado + r.total_no_utilizado, 660)

    def test_traslado_sin_placa_se_reparte_en_la_sede_por_km(self):
        pools = [p for p in pools_estudio() if p.sede == "Tame"]
        r = asignar_periodo(pools, [TrasladoDriver("X", "Tame", 100)], self.perfiles, self.precios)
        pesos = r.placa_traslado["X"]
        self.assertAlmostEqual(sum(pesos.values()), 1.0)
        self.assertGreater(pesos["OEU846"], pesos["OYX014"])
        self.assertAlmostEqual(r.total_asignado, sum(p.total for p in pools), delta=1e-4)

    def test_sin_traslados_todo_es_no_utilizado(self):
        r = asignar_periodo(self.pools, [], self.perfiles, self.precios, indirectos=10)
        self.assertEqual(r.total_asignado, 0)
        self.assertAlmostEqual(r.total_no_utilizado, self.total_flota + 10, delta=1e-6)


if __name__ == "__main__":
    unittest.main()

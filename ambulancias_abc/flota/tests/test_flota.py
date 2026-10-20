import datetime as dt
import json
import math
import tempfile
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from abc_engine import datos_estudio as D
from flota.api import crear_clave
from flota.importacion.anexo_tarifas import leer_anexo_traslados
from flota.models import (
    Ambulancia,
    Parametro,
    PerfilCostoEstudio,
    RegistroAuditoria,
    ConflictoVersion,
    Consumo,
    HallazgoCalidad,
    PartidaCostoIndirecto,
    TarifaRuta,
    Traslado,
)
from flota.servicios import importar
from flota.servicios.costeo import calcular_mes, consolidar, matriz_rutas
from flota.servicios.datos_iniciales import cargar
from flota.servicios.respaldo import respaldar

from .utiles import fila_his, xlsx_anexo, xlsx_his

SEP = dt.date(2026, 9, 1)


def filas_basicas():
    return [
        fila_his("HOSPITAL SAN ANTONIO", "S31307", 101, 17000, 9001, "FT1"),  # Tame -> Saravena
        fila_his("HOSPITAL SAN ANTONIO", "601T01", 1, 459300, 9001, "FT1"),  # básica en el mismo ingreso
        fila_his("HOSPITAL SAN ANTONIO", "601T02", 194, 17000, 9002, "FT2", unidad="HOSPITALIZACIÓN"),
        fila_his("HOSPITAL SAN FRANCISCO", "601T02", 45, 17000, 9003, "FF1"),
        fila_his("HOSPITAL SAN FRANCISCO", "601T02", 45, 17000, 9003, ""),  # duplicada, sin factura
        fila_his("HOSPITAL SAN LORENZO", "601T01..", 1, 459300, 9004, "FA1", doc="2000002"),
        fila_his("HOSPITAL SAN JOSE", "601T01", 2, 459300, 9005, "FC1"),  # dos traslados básicos
        fila_his("SEDE DESCONOCIDA", "601T01", 1, 459300, 9006, "FX1"),
    ]


class BaseCargada(TestCase):
    @classmethod
    def setUpTestData(cls):
        cargar()


class DatosInicialesTests(BaseCargada):
    def test_flota_del_estudio(self):
        self.assertEqual(Ambulancia.objects.filter(perfiles__isnull=False).count(), 21)
        self.assertEqual(Ambulancia.objects.filter(estado=Ambulancia.NUEVA).count(), 5)
        self.assertTrue(Ambulancia.objects.get(placa="OKZ364").excluida_promedios)
        self.assertEqual(TarifaRuta.objects.count(), 90)
        # Modelo ABC corregido: las 7 partidas del centro de costos (sin personal) entran al ABC
        self.assertEqual(PartidaCostoIndirecto.objects.filter(incluir_en_abc=True).count(), 7)
        self.assertEqual(Parametro.objects.get(clave="tripulacion_dia").valor, Decimal("457592.6702"))
        self.assertEqual(Parametro.objects.get(clave="tasa_gastos_admin").valor, Decimal("0.062"))
        self.assertEqual(PerfilCostoEstudio.objects.get(ambulancia__placa="OKZ364").horas_dia, Decimal("0.3333"))

    def test_carga_idempotente(self):
        antes = (Ambulancia.objects.count(), TarifaRuta.objects.count(), HallazgoCalidad.objects.count())
        cargar()
        self.assertEqual(antes, (Ambulancia.objects.count(), TarifaRuta.objects.count(), HallazgoCalidad.objects.count()))

    def test_hallazgos_de_fuentes(self):
        tipos = set(HallazgoCalidad.objects.values_list("tipo", flat=True))
        self.assertTrue({"ERROR_FORMULA", "ENCABEZADO", "ESTUDIO", "SUPUESTO", "INCONSISTENCIA"} <= tipos)
        self.assertTrue(HallazgoCalidad.objects.filter(descripcion__icontains="El Tigre").exists())
        self.assertEqual(Parametro.objects.get(clave="ambulancias_reparto_indirectos").valor, Decimal("21"))
        self.assertTrue(HallazgoCalidad.objects.filter(descripcion__icontains="El Tigre").exists())


class LectorAnexoTests(TestCase):
    def test_detecta_ref_encabezado_nota_y_tarifa_incoherente(self):
        r = leer_anexo_traslados(xlsx_anexo())
        self.assertEqual(r["valor_km"], 17000)
        self.assertEqual(r["tarifa_base"]["valor"], 459300)
        self.assertEqual([x["origen"] for x in r["rutas"]], ["TAME", "TAME", "FORTUL"])  # celda combinada
        tipos = {h["tipo"] for h in r["hallazgos"]}
        self.assertTrue({"ERROR_FORMULA", "ENCABEZADO", "INCONSISTENCIA"} <= tipos)
        fortul = r["rutas"][2]
        self.assertEqual(fortul["tarifa"], 45 * 17000 + 1000)  # no se corrige, se marca
        self.assertIn("≠", fortul["observacion"])


class ImportacionFacturacionTests(BaseCargada):
    def setUp(self):
        self.lote = importar.importar_facturacion(xlsx_his(filas_basicas()), nombre="prueba.xlsx")

    def test_resultado_importacion(self):
        self.assertEqual(self.lote.nuevos, 7)
        self.assertEqual(self.lote.omitidos, 1)  # sede desconocida
        self.assertTrue(HallazgoCalidad.objects.filter(ubicacion="SEDE 'SEDE DESCONOCIDA'").exists())

    def test_ruta_inferida_y_km(self):
        t = Traslado.objects.get(codigo_cups="S31307")
        self.assertEqual((t.ruta.origen, t.ruta.destino), ("TAME", "SARAVENA"))
        self.assertTrue(t.ruta_inferida)
        self.assertEqual(t.km_recorridos, Decimal("202.00"))

    def test_linea_basica_del_mismo_ingreso_no_suma_km(self):
        base = Traslado.objects.get(nro_ingreso="9001", codigo_cups="601T01")
        self.assertEqual(base.km_recorridos, 0)
        self.assertIn("tarifa básica y cobro por km", base.observaciones_calidad)

    def test_codigo_normalizado_y_basico_por_unidades(self):
        t = Traslado.objects.get(nro_ingreso="9004")
        self.assertEqual(t.codigo_cups, "601T01")
        self.assertEqual(t.codigo_cups_original, "601T01..")
        self.assertEqual(t.km_recorridos, Decimal("30.00"))
        self.assertEqual(Traslado.objects.get(nro_ingreso="9005").km_recorridos, Decimal("60.00"))

    def test_duplicado_y_sin_factura_marcados(self):
        dup = Traslado.objects.filter(nro_ingreso="9003")
        self.assertEqual(dup.count(), 2)
        self.assertTrue(all("posible duplicado" in t.observaciones_calidad for t in dup))
        self.assertTrue(dup.filter(observaciones_calidad__contains="sin número de factura").exists())

    def test_no_guarda_documento_ni_nombres(self):
        for t in Traslado.objects.all():
            valores = " ".join(str(v) for v in t.__dict__.values())
            self.assertNotIn("1000001", valores)
            self.assertNotIn("APELLIDO", valores)
            self.assertEqual(len(t.documento_hash), 64)

    def test_reimportar_no_duplica(self):
        lote = importar.importar_facturacion(xlsx_his(filas_basicas()), nombre="prueba.xlsx")
        self.assertEqual(lote.nuevos, 0)
        self.assertEqual(lote.actualizados, 7)
        self.assertEqual(Traslado.objects.count(), 7)


class CosteoTests(BaseCargada):
    def setUp(self):
        importar.importar_facturacion(xlsx_his(filas_basicas()), nombre="prueba.xlsx")

    def _pool_estandar(self):
        """Modelo corregido: (combustible + mantenimiento + tripulación TH) × días
        + indirectos del centro de costos ÷ 21, todo con 6,2 % de gastos administrativos."""
        dias = 30
        trip = 457592.6702
        ind_amb = sum(D.INDIRECTOS_MES_MODELO.values()) / 21
        base = sum((c + m + trip) * dias + ind_amb for _p, _s, _c, _k, _h, c, m, *_r in D.ANEXO_A)
        return base * 1.062

    def test_conservacion_costo_total_flota(self):
        for metodo in ("REAL", "CAPACIDAD"):
            r = calcular_mes(SEP, metodo)
            self.assertTrue(math.isclose(r.total_pool, self._pool_estandar(), rel_tol=1e-6), (r.total_pool, self._pool_estandar()))
            self.assertTrue(math.isclose(r.total_asignado + r.total_no_utilizado, r.total_pool, rel_tol=1e-9), metodo)
            self.assertAlmostEqual(sum(f["costo"] for f in r.filas), r.total_asignado, delta=0.01)

    def test_consumo_real_reemplaza_estandar(self):
        amb = Ambulancia.objects.get(placa="OEU846")
        Consumo.objects.create(ambulancia=amb, fecha=dt.date(2026, 9, 3), tipo=Consumo.COMBUSTIBLE, cantidad=10, valor=236500)
        r = calcular_mes(SEP, "REAL")
        self.assertEqual(r.ambulancias["OEU846"]["pool_combustible"], 236500)
        self.assertEqual(r.fuente_pool["OEU846"]["combustible"], "REAL")
        self.assertEqual(r.fuente_pool["OEU846"]["mantenimiento"], "ESTANDAR")
        self.assertTrue(math.isclose(r.total_asignado + r.total_no_utilizado, r.total_pool, rel_tol=1e-9))

    def test_indirectos_y_gastos_administrativos(self):
        r = calcular_mes(SEP, "REAL")
        por_amb = 21717958.27 / 21
        self.assertAlmostEqual(r.ambulancias["OEU846"]["pool_indirectos"], por_amb, delta=0.05)
        self.assertAlmostEqual(r.indirectos, 21 * por_amb, delta=1)
        a = r.ambulancias["OEU846"]
        self.assertAlmostEqual(a["pool_administracion"], (a["pool_total"] - a["pool_administracion"]) * 0.062, delta=0.01)
        self.assertAlmostEqual(sum(f["costo_administracion"] for f in r.filas),
                               sum(x["asignado_administracion"] for x in r.ambulancias.values()), delta=0.01)
        # Si se excluye una partida, baja el costo indirecto
        PartidaCostoIndirecto.objects.filter(concepto="SEGUROS").update(incluir_en_abc=False)
        r2 = calcular_mes(SEP, "REAL")
        self.assertAlmostEqual(r.indirectos - r2.indirectos, 125610.51, delta=1)

    def test_consolidado_filtros(self):
        total = consolidar(SEP, SEP)
        hosp = consolidar(SEP, SEP, filtros={"unidad_funcional": "HOSPITALIZACIÓN"})
        self.assertAlmostEqual(hosp["total"]["linea"], 1, places=9)
        self.assertLess(hosp["total"]["costo"], total["total"]["costo"])
        tame = consolidar(SEP, SEP, filtros={"sede": "tame"})
        self.assertEqual({a["placa"] for a in tame["por_ambulancia"]}, {"OEU846", "OKZ365", "LXS630", "OYX014"})
        self.assertAlmostEqual(tame["total"]["ingreso"], 101 * 17000 + 459300 + 194 * 17000, delta=0.01)

    def test_matriz_rutas_usa_costo_por_km_del_estudio(self):
        filas = matriz_rutas("REAL", sede_codigo="tame", placa="OKZ365")
        arauca = next(f for f in filas if f["ruta"].destino == "ARAUCA")
        base_dia = 206551 + 458376 + 457592.6702 + 21717958.27 / 21 / 30
        self.assertAlmostEqual(arauca["costo"], 194 * 2 * base_dia / 209.6 * 1.062, delta=1)
        self.assertEqual(arauca["tarifa"], 3298000)

    def test_matriz_rutas_estandar_no_carga_tiempo_ocioso(self):
        from flota.servicios.costeo import tasas_vigentes

        t = tasas_vigentes()
        self.assertAlmostEqual(t.tripulacion_hora, 7500000 / 191 + 2754000 / 191 + 3500000 / 191, delta=0.01)
        filas = matriz_rutas(sede_codigo="tame", placa="OKZ365")  # por defecto ESTANDAR
        arauca = next(f for f in filas if f["ruta"].destino == "ARAUCA")
        km = 194 * 2
        horas = km / 34.6
        esperado = (206551 / 209.6 * km + km * t.mantenimiento_km + horas * t.tripulacion_hora + horas * t.indirectos_hora) * 1.062
        self.assertAlmostEqual(arauca["costo"], esperado, delta=1)
        real = next(f for f in matriz_rutas("REAL", sede_codigo="tame", placa="OKZ365") if f["ruta"].destino == "ARAUCA")
        self.assertAlmostEqual(arauca["sobrecosto_unidad"], real["costo"] - arauca["costo"], delta=0.01)
        # Una ambulancia de muy baja utilización (OKZ364: 0,33 h/día) ya no pierde por su tiempo ocioso.
        okz = matriz_rutas(placa="OKZ364")
        self.assertTrue(okz and all(f["margen"] > 0 for f in okz))
        self.assertTrue(all(f["sobrecosto_unidad"] > 0 for f in okz))


class ConcurrenciaTests(BaseCargada):
    def test_bloqueo_optimista(self):
        amb = Ambulancia.objects.get(placa="OYX016")
        c = Consumo.objects.create(ambulancia=amb, fecha=dt.date(2026, 9, 1), tipo=Consumo.INSUMO, valor=1000)
        usuario_a = Consumo.objects.get(pk=c.pk)
        usuario_b = Consumo.objects.get(pk=c.pk)
        usuario_a.valor = 2000
        usuario_a.guardar_con_version(usuario_a.version)
        usuario_b.valor = 3000
        with self.assertRaises(ConflictoVersion):
            usuario_b.guardar_con_version(usuario_b.version)
        c.refresh_from_db()
        self.assertEqual((c.valor, c.version), (2000, 2))


class ApiTests(BaseCargada):
    def setUp(self):
        importar.importar_facturacion(xlsx_his(filas_basicas()), nombre="prueba.xlsx")
        _, self.clave_lectura = crear_clave("auditoria")
        _, self.clave_escritura = crear_clave("rips", escritura=True)

    def test_requiere_clave(self):
        self.assertEqual(self.client.get(reverse("api:traslados"), {"mes": "2026-09"}).status_code, 401)
        r = self.client.get(reverse("api:traslados"), {"mes": "2026-09"}, HTTP_X_API_KEY="abc_invalida")
        self.assertEqual(r.status_code, 401)

    def test_export_traslados_json_y_csv(self):
        r = self.client.get(reverse("api:traslados"), {"mes": "2026-09"}, HTTP_X_API_KEY=self.clave_lectura)
        self.assertEqual(r.status_code, 200)
        datos = r.json()
        self.assertEqual(datos["registros"], 7)
        fila = next(f for f in datos["datos"] if f["codigo_cups"] == "S31307")
        self.assertEqual(fila["ruta_codigo"], "tame-saravena")
        self.assertIn("costo_abc", fila)
        self.assertNotIn("1000001", json.dumps(datos))
        r = self.client.get(reverse("api:traslados"), {"mes": "2026-09", "formato": "csv"}, HTTP_X_API_KEY=self.clave_lectura)
        self.assertIn("text/csv", r["Content-Type"])
        self.assertIn("llave_externa;", r.content.decode("utf-8-sig"))

    def test_export_costos_cuadra_con_flota(self):
        r = self.client.get(reverse("api:costos_ambulancia"), {"mes": "2026-09"}, HTTP_X_API_KEY=self.clave_lectura).json()
        suma = sum(f["costo_total"] for f in r["datos"])
        self.assertAlmostEqual(suma, r["costo_total_flota"], delta=1)
        self.assertEqual(len(r["datos"]), 21)

    def test_rips_concilia_sin_duplicar(self):
        rips = {
            "numFactura": "FT2",
            "usuarios": [
                {
                    "tipoDocumentoIdentificacion": "CC",
                    "numDocumentoIdentificacion": "1000001",
                    "servicios": {
                        "otrosServicios": [
                            {"codTecnologiaSalud": "601T02", "cantidadOS": 194, "vrUnitOS": 17000, "vrServicio": 3298000,
                             "fechaSuministroTecnologia": "2026-09-05 10:00", "consecutivo": 1, "codPrestador": "999"}
                        ]
                    },
                }
            ],
        }
        sin_permiso = self.client.post(reverse("api:importar_rips"), json.dumps(rips), content_type="application/json",
                                       HTTP_X_API_KEY=self.clave_lectura)
        self.assertEqual(sin_permiso.status_code, 401)
        r = self.client.post(reverse("api:importar_rips"), json.dumps(rips), content_type="application/json",
                             HTTP_X_API_KEY=self.clave_escritura).json()
        self.assertEqual((r["conciliados"], r["nuevos"]), (1, 0))
        self.assertEqual(Traslado.objects.count(), 7)
        t = Traslado.objects.get(numero_factura="FT2")
        self.assertTrue(t.conciliado_rips)
        self.assertEqual(t.rips_consecutivo, "1")
        r2 = self.client.post(reverse("api:importar_rips"), json.dumps(rips), content_type="application/json",
                              HTTP_X_API_KEY=self.clave_escritura).json()
        self.assertEqual((r2["conciliados"], r2["nuevos"]), (0, 0))


class VistasTests(BaseCargada):
    def setUp(self):
        importar.importar_facturacion(xlsx_his(filas_basicas()), nombre="prueba.xlsx")
        self.admin = User.objects.create_superuser("admin", "", "Clave-Prueba-1")
        self.client.force_login(self.admin)

    def test_paginas_principales(self):
        t = Traslado.objects.first()
        for nombre, args in [
            ("flota:dashboard", []), ("flota:consolidado", []), ("flota:ambulancias", []), ("flota:ambulancia", ["OEU846"]),
            ("flota:rentabilidad_rutas", []), ("flota:traslados", []), ("flota:consumos", []), ("flota:importar", []),
            ("flota:calidad", []), ("flota:parametros", []), ("flota:bitacora", []), ("flota:traslado_editar", [t.pk]),
            ("flota:consumo_nuevo", []), ("flota:plantilla_consumos", []),
        ]:
            self.assertEqual(self.client.get(reverse(nombre, args=args)).status_code, 200, nombre)
        r = self.client.get(reverse("flota:dashboard"), {"metodo": "CAPACIDAD", "ambito": "Urgencias", "placa": "OEU846"})
        self.assertEqual(r.status_code, 200)

    def test_cambiar_clave(self):
        r = self.client.post(reverse("cambiar_clave"), {
            "old_password": "Clave-Prueba-1", "new_password1": "Nueva-Clave-2026", "new_password2": "Nueva-Clave-2026",
        })
        self.assertRedirects(r, reverse("flota:dashboard"))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("Nueva-Clave-2026"))

    def test_crear_admin_si_no_existe_no_cambia_clave(self):
        from io import StringIO

        from django.core.management import call_command

        salida = StringIO()
        call_command("crear_admin", usuario="admin", clave="Otra-Clave-99", si_no_existe=True, stdout=salida)
        self.assertIn("EXISTE", salida.getvalue())
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("Clave-Prueba-1"))
        call_command("crear_admin", usuario="otro", clave="Otra-Clave-99", si_no_existe=True, stdout=salida)
        self.assertIn("CREADO", salida.getvalue())

    def test_crear_admin_si_nunca_ingreso_sincroniza_clave_del_archivo(self):
        from io import StringIO

        from django.contrib.auth import get_user_model
        from django.core.management import call_command

        otro = get_user_model().objects.create_user("otro", password="Clave-Vieja-1", is_active=False)
        call_command("crear_admin", usuario="otro", clave="Clave-Archivo-9", si_nunca_ingreso=True, stdout=StringIO())
        otro.refresh_from_db()
        self.assertTrue(otro.check_password("Clave-Archivo-9"))
        self.assertTrue(otro.is_active)
        # el admin de la prueba ya ingresó (force_login): no se toca su clave
        self.admin.refresh_from_db()
        self.assertIsNotNone(self.admin.last_login)
        salida = StringIO()
        call_command("crear_admin", usuario="admin", clave="Clave-Archivo-9", si_nunca_ingreso=True, stdout=salida)
        self.assertIn("EXISTE", salida.getvalue())
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("Clave-Prueba-1"))
        salida = StringIO()
        call_command("crear_admin", usuario="admin", clave="Clave-Prueba-1", si_nunca_ingreso=True, stdout=salida)
        self.assertIn("VIGENTE", salida.getvalue())

    def test_requiere_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("flota:dashboard")).status_code, 302)

    def test_editar_con_version_vieja_falla(self):
        t = Traslado.objects.get(codigo_cups="S31307")
        datos = {
            "fecha_servicio": "2026-09-05", "sede": t.sede_id, "ambulancia": Ambulancia.objects.get(placa="OEU846").pk,
            "ruta": t.ruta_id, "codigo_cups": "S31307", "cantidad": "101", "valor_unitario": "17000",
            "valor_facturado": "1717000", "ambito": "Urgencias", "version_leida": t.version,
        }
        r = self.client.post(reverse("flota:traslado_editar", args=[t.pk]), datos)
        self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.ambulancia.placa, "OEU846")
        self.assertEqual(t.km_recorridos, Decimal("202.00"))
        r = self.client.post(reverse("flota:traslado_editar", args=[t.pk]), datos, follow=True)  # versión ya vieja
        self.assertContains(r, "Otro usuario modificó")

    def test_asignar_placa_de_otra_sede_se_omite(self):
        t = Traslado.objects.get(codigo_cups="S31307")
        self.client.post(reverse("flota:traslados_asignar"), {"seleccion": [t.pk], "ambulancia": "OJZ193"})
        t.refresh_from_db()
        self.assertIsNone(t.ambulancia)

    def test_importar_consumos_csv(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        csv = "placa;fecha;tipo;descripcion;cantidad;unidad;valor;odometro;documento_soporte;proveedor\n" \
              "OEU846;2026-09-02;COMBUSTIBLE;Tanqueo;12,5;gal;295625;1500;V-1;EDS\n" \
              "XXX000;2026-09-02;COMBUSTIBLE;;1;gal;1;;V-2;\n"
        archivo = SimpleUploadedFile("consumos.csv", csv.encode("utf-8"))
        r = self.client.post(reverse("flota:importar"), {"tipo": "consumos", "archivo": archivo}, follow=True)
        self.assertContains(r, "1 nuevos")
        self.assertEqual(Consumo.objects.get(documento_soporte="V-1").cantidad, Decimal("12.5"))


class RespaldoTests(TransactionTestCase):
    """Sin transacción envolvente, como corre el comando respaldar_bd."""

    def test_respaldo_sqlite_con_retencion(self):
        with tempfile.TemporaryDirectory() as d:
            for _ in range(3):
                archivo = respaldar(Path(d), retencion=2)
                self.assertTrue(archivo.exists())
            # los nombres llevan segundos: pueden coincidir dentro del mismo segundo
            self.assertLessEqual(len(list(Path(d).glob("*.sqlite3"))), 2)


class ActualizacionModeloCorregidoTests(TestCase):
    """Una instalación con los datos anteriores queda con el modelo corregido al actualizar."""

    def test_actualiza_parametro_no_editado_y_respeta_el_editado(self):
        Parametro.objects.create(clave="tripulacion_dia", valor=Decimal("458467"), descripcion="anterior")
        Parametro.objects.create(clave="tasa_gastos_admin", valor=Decimal("0.05"), descripcion="editado a mano")
        p = Parametro.objects.get(clave="tasa_gastos_admin")
        u = User.objects.create_user("contador", password="Clave-Prueba-1")
        RegistroAuditoria.objects.create(usuario=u, modelo="Parametro", objeto_id=str(p.pk), accion="editar")
        PartidaCostoIndirecto.objects.create(categoria="COSTOS PERSONAL", concepto="CONTRATO DIRECTO (AUXILIAR)", valor_mensual=74256355)
        cargar()
        self.assertEqual(Parametro.objects.get(clave="tripulacion_dia").valor, Decimal("457592.6702"))
        self.assertEqual(Parametro.objects.get(clave="tasa_gastos_admin").valor, Decimal("0.05"))  # se respeta
        self.assertFalse(PartidaCostoIndirecto.objects.filter(categoria="COSTOS PERSONAL").exists())

    def test_hallazgos_de_la_version_anterior_quedan_resueltos(self):
        h = HallazgoCalidad.objects.create(fuente="COSTOS AMBULANCIAS.xlsx", ubicacion="AMBULANCIAS!I43", tipo="ERROR_FORMULA",
                                           descripcion="Celda con error '#DIV/0!'", severidad="MEDIA")
        cargar()
        h.refresh_from_db()
        self.assertTrue(h.resuelto)
        self.assertIn("versión corregida", h.nota_resolucion)

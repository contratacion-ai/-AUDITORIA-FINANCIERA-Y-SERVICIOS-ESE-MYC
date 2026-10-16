import datetime as dt
import io
import json
import zipfile
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from flota.importacion.rips import APH, TRASLADO, clasificar, leer_rips_archivos
from flota.models import Ambulancia, CodigoTraslado, Consumo, LoteImportacion, Sede, Traslado
from flota.servicios import importar
from flota.servicios.costeo import calcular_mes, facturacion_por_ambito, recorridos_por_sede, tabla_consumos
from flota.servicios.datos_iniciales import cargar

from .utiles import fila_his, xlsx_his

SEP = dt.date(2026, 9, 1)
CATALOGO = {"601T01": TRASLADO, "601T02": TRASLADO, "S31307": TRASLADO}

AF = "500000000101,ESE MORENO Y CLAVIJO,NI,900034131,FR100,15/09/2026,01/09/2026,30/09/2026,EPS037,NUEVA EPS,C1,,,0,0,0,5000000\n"
AT = (
    "FR100,500000000101,CC,1000001,AUT1,2,601T02,TRASLADO ASISTENCIAL BASICO TERRESTRE SECUNDARIO,101,17000,1717000\n"
    "FR100,500000000101,CC,1000002,APH,2,999X01,ATENCION PREHOSPITALARIA,1,350000,350000\n"
    "FR100,500000000101,CC,1000003,AUT3,2,888Z01,SERVICIO SIN NOMBRE CONOCIDO,1,100000,100000\n"
    "FR100,500000000101,CC,1000004,AUT4,1,MAT001,GUANTES,10,1000,10000\n"
)
AP = (
    "FR100,500000000101,CC,1000005,20/09/2026,AUT5,601T01,3,1,1,R509,,,,459300\n"
    "FR100,500000000101,CC,1000006,21/10/2026,AUT6,601T01,2,1,1,R509,,,,459300\n"
    "FR100,500000000101,CC,1000007,21/09/2026,AUT7,890201,1,1,1,R509,,,,40000\n"
)


def zip_rips():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("AF000001.txt", AF)
        z.writestr("AT000001.txt", AT.encode("latin-1"))
        z.writestr("AP000001.txt", AP)
        z.writestr("US000001.txt", "CC,1000001,EPS037,2,APELLIDO,,NOMBRE,,30,1,M,81,794,U\n")
    return buf.getvalue()


class ClasificacionRipsTests(TestCase):
    def test_reglas(self):
        self.assertEqual(clasificar("601T02", catalogo=CATALOGO)[0], TRASLADO)
        self.assertEqual(clasificar("X1", "ATENCIÓN PREHOSPITALARIA BÁSICA")[0], APH)
        self.assertEqual(clasificar("X1", "", "APH")[0], APH)
        self.assertEqual(clasificar("X1", "TRASLADO EN AMBULANCIA")[0], TRASLADO)
        self.assertEqual(clasificar("X1", "OTRO", tipo_servicio="2")[0], TRASLADO)
        self.assertIsNone(clasificar("890201", "CONSULTA")[0])

    def test_lee_zip_de_archivos_planos(self):
        r = leer_rips_archivos([("rips_sep.zip", zip_rips())], "sal", CATALOGO)
        self.assertEqual(r["facturas"], 1)
        self.assertEqual(r["lineas_revisadas"], 7)
        cats = sorted((x["codigo_cups"], x["categoria"]) for x in r["registros"])
        self.assertEqual(cats, [("601T01", TRASLADO), ("601T01", TRASLADO), ("601T02", TRASLADO), ("888Z01", TRASLADO), ("999X01", APH)])
        at = next(x for x in r["registros"] if x["codigo_cups"] == "601T02")
        self.assertEqual(at["fecha_servicio"], SEP)  # AT no trae fecha: inicio del periodo del AF
        self.assertIn("fecha tomada del periodo", at["observaciones"][0])
        ap = next(x for x in r["registros"] if x["codigo_cups"] == "601T01" and x["fecha_servicio"].month == 9)
        self.assertEqual(ap["ambito"], "Urgencias")
        self.assertNotIn("1000001", json.dumps([x["documento_hash"] for x in r["registros"]]))


class BaseDatos(TestCase):
    @classmethod
    def setUpTestData(cls):
        cargar()
        cls.tame = Sede.objects.get(codigo="tame")
        cls.tame.codigo_prestador = "500000000101"
        cls.tame.save()


class CargaRipsTests(BaseDatos):
    def test_carga_por_mes_y_hospital(self):
        lote = importar.importar_rips([("rips_sep.zip", zip_rips())], nombre="rips_sep.zip", mes=SEP, sede=self.tame)
        self.assertEqual(lote.nuevos, 5)
        self.assertEqual((lote.mes, lote.sede), (SEP, self.tame))
        self.assertEqual(lote.detalle["aph"]["lineas"], 1)
        self.assertEqual(lote.detalle["traslados"]["lineas"], 4)
        self.assertEqual(lote.detalle["fuera_del_mes"], 1)
        aph = Traslado.objects.get(categoria=APH)
        self.assertEqual((aph.sede, aph.lote_rips), (self.tame, lote))
        ruta = Traslado.objects.get(codigo_cups="601T02").ruta
        self.assertEqual((ruta.origen, ruta.destino), ("TAME", "SARAVENA"))
        # Recargar no duplica
        lote2 = importar.importar_rips([("rips_sep.zip", zip_rips())], nombre="rips_sep.zip", mes=SEP, sede=self.tame)
        self.assertEqual(lote2.nuevos, 0)
        self.assertEqual(Traslado.objects.count(), 5)

    def test_concilia_con_facturacion_his(self):
        importar.importar_facturacion(xlsx_his([fila_his("HOSPITAL SAN ANTONIO", "601T02", 101, 17000, 77, "FR100")]), nombre="f.xlsx")
        lote = importar.importar_rips([("rips_sep.zip", zip_rips())], nombre="rips_sep.zip", mes=SEP, sede=self.tame)
        self.assertEqual(lote.actualizados, 1)
        his = Traslado.objects.get(origen_registro=Traslado.HIS)
        self.assertTrue(his.conciliado_rips)
        self.assertEqual(his.lote_rips, lote)
        self.assertEqual(Traslado.objects.filter(codigo_cups="601T02").count(), 1)

    def test_codigo_aph_en_catalogo(self):
        CodigoTraslado.objects.create(codigo="888Z01", descripcion="APH avanzada", modalidad=CodigoTraslado.BASE, categoria=APH)
        importar.importar_rips([("rips_sep.zip", zip_rips())], nombre="rips_sep.zip", mes=SEP, sede=self.tame)
        self.assertEqual(Traslado.objects.filter(categoria=APH).count(), 2)


class ConsumosTests(BaseDatos):
    def _xlsx(self, filas, encabezado=("placa", "fecha", "descripcion", "cantidad", "unidad", "valor", "meses_prorrateo", "documento_soporte")):
        wb = Workbook()
        ws = wb.active
        ws.append(list(encabezado))
        for f in filas:
            ws.append(f)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    def test_carga_excel_con_tipo_de_la_tabla_y_prorrateo(self):
        arch = self._xlsx([["OEU846", dt.datetime(2026, 3, 15), "SOAT 2026", 1, "und", 1200000, 12, "POL-1"]])
        lote = importar.importar_consumos(arch, nombre="seguros.xlsx", tipo_forzado=Consumo.SEGUROS_IMPUESTOS)
        self.assertEqual(lote.nuevos, 1)
        c = Consumo.objects.get()
        self.assertEqual((c.tipo, c.meses_prorrateo), (Consumo.SEGUROS_IMPUESTOS, 12))
        r = calcular_mes(SEP)
        self.assertAlmostEqual(r.ambulancias["OEU846"]["pool_otros"], 100000)
        self.assertEqual(calcular_mes(dt.date(2027, 3, 1)).ambulancias["OEU846"]["pool_otros"], 0)
        t = tabla_consumos("seguros", dt.date(2026, 2, 1), dt.date(2026, 4, 1))
        self.assertEqual(t["filas"][0]["valores"], [0.0, 100000.0, 100000.0])

    def test_sinonimos_de_tipo(self):
        arch = self._xlsx([["OKZ365", "2026-09-30", "Nómina", 1, "mes", 13754000, 1, "N-9", "PERSONAL"]],
                          ("placa", "fecha", "descripcion", "cantidad", "unidad", "valor", "meses_prorrateo", "documento_soporte", "tipo"))
        importar.importar_consumos(arch, nombre="personal.xlsx")
        self.assertEqual(Consumo.objects.get().tipo, Consumo.TRIPULACION)
        self.assertEqual(calcular_mes(SEP).fuente_pool["OKZ365"]["tripulacion"], "REAL")


class RecorridosSedeTests(BaseDatos):
    def test_totales_cuadran_con_facturacion(self):
        filas = [
            fila_his("HOSPITAL SAN ANTONIO", "601T02", 101, 17000, 1, "A1"),
            fila_his("HOSPITAL SAN ANTONIO", "601T02", 194, 17000, 2, "A2"),
            fila_his("HOSPITAL SAN ANTONIO", "601T02", 71, 17000, 3, "A3"),  # sin ruta en el anexo
            fila_his("HOSPITAL SAN FRANCISCO", "601T02", 45, 17000, 4, "B1"),
        ]
        importar.importar_facturacion(xlsx_his(filas), nombre="f.xlsx", mes=SEP)
        datos = recorridos_por_sede(SEP, SEP)
        total = sum(b["total"]["facturado"] for b in datos)
        self.assertAlmostEqual(total, (101 + 194 + 71 + 45) * 17000, delta=0.01)
        tame = next(b for b in datos if b["sede"].codigo == "tame")
        saravena = next(f for f in tame["filas"] if f["ruta"].destino == "SARAVENA")
        self.assertAlmostEqual(saravena["traslados"], 1, places=9)
        self.assertEqual(saravena["tarifa"], 1717000)
        self.assertGreater(saravena["costo_estandar"], 0)
        self.assertEqual(tame["otras"]["facturado"], 71 * 17000)
        self.assertEqual(tame["n_ambulancias"], 4)
        costo_total = sum(b["total"]["costo_real"] for b in datos)
        self.assertAlmostEqual(costo_total, calcular_mes(SEP).total_asignado, delta=1)

    def test_facturacion_por_ambito(self):
        importar.importar_facturacion(xlsx_his([fila_his("HOSPITAL SAN ANTONIO", "601T02", 101, 17000, 1, "A1", unidad="HOSPITALIZACIÓN")]), nombre="f.xlsx")
        filas = facturacion_por_ambito(SEP, SEP)
        self.assertEqual(filas[0]["unidad_funcional"], "HOSPITALIZACIÓN")
        self.assertEqual(filas[0]["facturado"], Decimal("1717000"))


class VistasCargasTests(BaseDatos):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "", "Clave-Prueba-1"))

    def test_boton_cargar_facturacion_por_hospital(self):
        filas = [fila_his("HOSPITAL SAN ANTONIO", "601T02", 101, 17000, 1, "A1"), fila_his("HOSPITAL SAN FRANCISCO", "601T02", 45, 17000, 2, "B1")]
        arch = SimpleUploadedFile("fact.xlsx", xlsx_his(filas).getvalue())
        r = self.client.post(reverse("flota:cargar_facturacion"), {"mes": "2026-09", "sede": self.tame.pk, "archivo": arch})
        self.assertEqual(r.status_code, 302)
        self.assertIn("facturacion", r["Location"])
        self.assertEqual(Traslado.objects.count(), 1)
        lote = LoteImportacion.objects.get(tipo="FACTURACION_HIS")
        self.assertEqual((lote.mes, lote.sede, lote.omitidos), (SEP, self.tame, 1))

    def test_boton_cargar_rips_varios_archivos(self):
        r = self.client.post(reverse("flota:cargar_rips"), {
            "mes": "2026-09", "sede": self.tame.pk,
            "archivos": [SimpleUploadedFile("AF1.txt", AF.encode()), SimpleUploadedFile("AT1.txt", AT.encode()), SimpleUploadedFile("AP1.txt", AP.encode())],
        })
        lote = LoteImportacion.objects.get(tipo="RIPS")
        self.assertRedirects(r, reverse("flota:rips_detalle", args=[lote.pk]))
        self.assertEqual(lote.nuevos, 5)
        r = self.client.get(reverse("flota:rips_detalle", args=[lote.pk]), {"formato": "xlsx"})
        ws = load_workbook(io.BytesIO(r.content)).active
        self.assertEqual(ws.max_row, 6)
        self.assertContains(self.client.get(reverse("flota:rips_detalle", args=[lote.pk]), {"categoria": "APH"}), "PREHOSPITALARIA")

    def test_paginas_y_descargas(self):
        importar.importar_facturacion(xlsx_his([fila_his("HOSPITAL SAN ANTONIO", "601T02", 101, 17000, 1, "A1")]), nombre="f.xlsx")
        for nombre, args, params in [
            ("flota:cargas", [], {}), ("flota:cargar_facturacion", [], {}), ("flota:cargar_rips", [], {}),
            ("flota:rips_cargas", [], {}), ("flota:recorridos_sede", [], {"desde": "2026-09", "hasta": "2026-09"}),
            ("flota:facturacion_ambito", [], {"desde": "2026-09", "hasta": "2026-09"}),
            ("flota:dashboard", [], {"categoria": "APH"}),
        ] + [("flota:consumos_tabla", [k], {}) for k in Consumo.TABLAS]:
            self.assertEqual(self.client.get(reverse(nombre, args=args), params).status_code, 200, nombre)
        for nombre, args in [("flota:recorridos_sede", []), ("flota:facturacion_ambito", []), ("flota:consumos_tabla", ["combustible"])]:
            r = self.client.get(reverse(nombre, args=args), {"desde": "2026-09", "hasta": "2026-09", "formato": "xlsx"})
            self.assertEqual(r["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", nombre)
        r = self.client.get(reverse("flota:plantilla_consumos_tabla", args=["seguros"]))
        fila = [c.value for c in load_workbook(io.BytesIO(r.content)).active[2]]
        self.assertEqual(fila[2], Consumo.SEGUROS_IMPUESTOS)

    def test_boton_cargar_tabla_combustible(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["placa", "fecha", "cantidad", "valor", "odometro", "documento_soporte"])
        ws.append(["OEU846", "2026-09-02", 10, 236500, 1000, "V1"])
        ws.append(["OEU846", "2026-09-20", 20, 473000, 1250, "V2"])
        buf = io.BytesIO()
        wb.save(buf)
        r = self.client.post(reverse("flota:cargar_consumos", args=["combustible"]), {"archivo": SimpleUploadedFile("comb.xlsx", buf.getvalue())})
        self.assertRedirects(r, reverse("flota:consumos_tabla", args=["combustible"]))
        self.assertEqual(Consumo.objects.filter(tipo=Consumo.COMBUSTIBLE, unidad="gal").count(), 2)
        t = tabla_consumos("combustible", SEP, SEP, "OEU846")
        self.assertEqual(t["filas"][0]["km_odometro"], 250)
        self.assertAlmostEqual(t["filas"][0]["rendimiento"], 12.5)
        self.assertEqual(Ambulancia.objects.get(placa="OEU846").consumos.count(), 2)

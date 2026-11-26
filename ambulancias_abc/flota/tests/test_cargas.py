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


# ---------------------------------------------------------------------------
# RIPS de varios meses / consolidados y auditoría RIPS vs facturación
# ---------------------------------------------------------------------------

PREST_PR = "815910020605"


def _af(factura, inicio, fin):
    return f"{PREST_PR},ESE,NI,900034131,{factura},{fin},{inicio},{fin},EPS037,NUEVA EPS,C1,,,0,0,0,1000000\n"


def _ap(factura, doc, fecha, codigo, valor):
    return f"{factura},{PREST_PR},CC,{doc},{fecha},AUT,{codigo},3,1,1,R509,,,,{valor}\n"


def zip_hospital_varios_meses():
    """ZIP del hospital con carpetas por mes, una factura repetida en dos carpetas,
    una línea AP incompleta, un ZIP anidado con JSON de otro hospital y un XML."""
    ene = "Hospital Juan de Dios /01. Enero/"
    feb = "Hospital Juan de Dios /02. Febrero/"
    interno = io.BytesIO()
    with zipfile.ZipFile(interno, "w") as z:
        z.writestr("FHSL900/FHSL900.json", json.dumps({
            "numFactura": "FHSL900",
            "usuarios": [{"tipoDocumentoIdentificacion": "CC", "numDocumentoIdentificacion": "77",
                          "servicios": {"otrosServicios": [{"codTecnologiaSalud": "601T01", "cantidadOS": 1, "vrUnitOS": 459300,
                                                            "vrServicio": 459300, "fechaSuministroTecnologia": "2026-02-03 08:00",
                                                            "consecutivo": 1, "codPrestador": "810650020602"}]}}],
        }))
        z.writestr("FHSL900/FHSL900.xml", "<Invoice/>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(ene + "AF000001.txt", _af("FHJD100", "01/01/2026", "31/01/2026") + _af("FHJD101", "01/01/2026", "31/01/2026"))
        z.writestr(ene + "AP000001.txt",
                   _ap("FHJD100", "1", "10/01/2026", "601T02", "1700000") + _ap("FHJD101", "2", "20/01/2026", "601T01", "459300")
                   + "FHJD,812200020604,CC,9,07/01/2026,,907009,1,1,Z000,,,,0\n")  # 14 campos
        z.writestr(feb + "AF000002.txt", _af("FHJD101", "01/01/2026", "31/01/2026") + _af("FHJD200", "01/02/2026", "28/02/2026"))
        z.writestr(feb + "AP000002.txt",
                   _ap("FHJD101", "2", "20/01/2026", "601T01", "459300") + _ap("FHJD200", "3", "05/02/2026", "601T02", "3400000"))
        z.writestr("otros/consolidado.zip", interno.getvalue())
    return buf.getvalue()


class RipsVariosMesesTests(BaseDatos):
    def test_lector_meses_carpetas_repetidas_y_zip_anidado(self):
        from flota.importacion.rips import mes_de_carpeta, prefijo_factura

        self.assertEqual(mes_de_carpeta("x.zip/Hospital Juan de Dios /08. Agosto/AP1.txt"), 8)
        self.assertEqual(prefijo_factura("FHJD15037"), "FHJD")
        r = leer_rips_archivos([("hospital.zip", zip_hospital_varios_meses())], "sal", CATALOGO)
        self.assertEqual(sorted((x["numero_factura"], x["fecha_servicio"].month) for x in r["registros"]),
                         [("FHJD100", 1), ("FHJD101", 1), ("FHJD200", 2), ("FHSL900", 2)])
        self.assertEqual(r["lineas_repetidas"], 1)
        self.assertEqual(r["facturas_repetidas"], 1)
        self.assertEqual(r["ignorados"], 1)  # el XML
        incompletas = [h for h in r["hallazgos"] if "14 campos" in h["descripcion"]]
        self.assertEqual(len(incompletas), 1)
        self.assertEqual(r["cobertura"][("FHJD", PREST_PR, "2026-01")], 2)

    def _his(self, factura, fecha, codigo, cantidad, valor, doc):
        from flota.importacion.facturacion_his import hash_documento
        from django.conf import settings

        pr = Sede.objects.get(codigo="pto-rondon")
        return Traslado.objects.create(
            llave_externa=f"HIS|{factura}|{fecha}|{codigo}|{doc}", origen_registro=Traslado.HIS, sede=pr, fecha_servicio=fecha,
            codigo_cups=codigo, cantidad=cantidad, valor_facturado=valor, numero_factura=factura,
            documento_hash=hash_documento("CC", doc, settings.SAL_DOCUMENTO) if doc else "",
        )

    def test_carga_consolidada_detecta_hospital_y_mes_y_audita(self):
        from flota.models import Parametro
        from flota.servicios.auditoria_rips import auditoria_rips

        Parametro.objects.update_or_create(clave="rips_min_facturas_mes", defaults={"valor": 1})

        self._his("FHJD100", dt.date(2026, 1, 10), "601T02", 100, Decimal("1700000"), "1")   # coincide
        self._his("FHJD200", dt.date(2026, 2, 5), "601T02", 200, Decimal("3300000"), "3")    # valor distinto (RIPS 3.400.000)
        self._his("FHJD300", dt.date(2026, 2, 9), "601T01", 1, Decimal("459300"), "4")       # falta en RIPS
        self._his("", dt.date(2026, 2, 11), "601T01", 1, Decimal("459300"), "5")             # prefactura sin número
        lote = importar.importar_rips([("hospital.zip", zip_hospital_varios_meses())], nombre="hospital.zip")
        self.assertIsNone(lote.mes)
        self.assertIsNone(lote.sede)  # dos hospitales: Puerto Rondón (FHJD) y Arauquita (FHSL)
        self.assertEqual(lote.detalle["meses"], ["2026-01", "2026-02"])
        self.assertEqual((lote.actualizados, lote.nuevos), (2, 2))
        self.assertEqual(lote.detalle["con_diferencia_valor"], 1)
        self.assertEqual(Traslado.objects.get(numero_factura="FHSL900").sede.codigo, "arauquita")
        self.assertEqual(Sede.objects.get(codigo="pto-rondon").codigo_prestador, PREST_PR)
        self.assertEqual(Traslado.objects.get(numero_factura="FHJD200").valor_rips, Decimal("3400000"))
        # Recargar no duplica ni vuelve a conciliar
        lote2 = importar.importar_rips([("hospital.zip", zip_hospital_varios_meses())], nombre="hospital.zip")
        self.assertEqual((lote2.nuevos, lote2.actualizados), (0, 0))
        self.assertEqual(Traslado.objects.filter(numero_factura="FHJD101").count(), 1)

        a = auditoria_rips(dt.date(2026, 1, 1), dt.date(2026, 2, 1), "pto-rondon")
        t = a["total"]
        self.assertEqual((t["ambos"], t["diferencia"], t["solo_his"], t["solo_rips"], t["sin_factura"]), (1, 1, 1, 1, 1))
        self.assertAlmostEqual(t["dif_valor"], 100000)
        feb = next(d for d in a["por_mes"] if d["mes"].month == 2)
        self.assertEqual(feb["estado"], "Con diferencias")
        self.assertTrue(feb["cubierto"])
        self.assertEqual(a["n_detalle"], 3)

    def test_mes_sin_rips_cargado_no_es_diferencia(self):
        from flota.servicios.auditoria_rips import auditoria_rips

        self._his("FHJD900", dt.date(2026, 5, 2), "601T01", 1, Decimal("459300"), "8")
        a = auditoria_rips(dt.date(2026, 5, 1), dt.date(2026, 5, 1))
        self.assertEqual((a["total"]["solo_his"], a["total"]["rips_no_cargado"]), (0, 1))
        self.assertEqual(a["por_mes"][0]["estado"], "Sin RIPS cargado")


class AuditoriaRipsVistaTests(BaseDatos):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("aud", "", "Clave-Prueba-1"))

    def test_cargar_consolidado_sin_mes_ni_hospital_y_ver_auditoria(self):
        r = self.client.post(reverse("flota:cargar_rips"), {
            "mes": "", "sede": "",
            "archivos": [SimpleUploadedFile("hospital.zip", zip_hospital_varios_meses(), content_type="application/zip")],
        })
        lote = LoteImportacion.objects.get(tipo="RIPS")
        self.assertRedirects(r, reverse("flota:rips_detalle", args=[lote.pk]))
        self.assertContains(self.client.get(reverse("flota:rips_detalle", args=[lote.pk])), "2026-01 a 2026-02")
        self.assertContains(self.client.get(reverse("flota:rips_cargas")), "Varios hospitales")
        p = self.client.get(reverse("flota:auditoria_rips"), {"desde": "2026-01", "hasta": "2026-02"})
        self.assertContains(p, "Mes a mes por hospital")
        self.assertContains(p, "Hospital San Juan de Dios de Puerto Rondón")
        x = self.client.get(reverse("flota:auditoria_rips"), {"desde": "2026-01", "hasta": "2026-02", "formato": "xlsx"})
        wb = load_workbook(io.BytesIO(x.content))
        self.assertEqual(wb.sheetnames, ["Por mes y hospital", "Por recorrido", "Diferencias"])


# ---------------------------------------------------------------------------
# Informe mensual de mantenimiento y combustible del parque automotor
# ---------------------------------------------------------------------------

ENC_SEDE = ["N°", "Placa", "Vehículo", "Combustible", "Rend. esperado (km/gal)", "Mes", "Mantenimiento ($)", "Viajes",
            "Km estimados", "Galones esperados", "Combustible asignado ($)", "Observación"]


def libro_costos_mensuales(asignado_agosto=5003363):
    wb = Workbook()
    r = wb.active
    r.title = "Resumen"
    r["A4"] = "COSTOS DE OPERACIÓN DE LAS AMBULANCIAS – ENERO A OCTUBRE DE 2026"
    r.append([])
    r.append([])
    r.append(["CÓMO SE REPARTE EL COMBUSTIBLE REAL"])
    r.append(["Mes", "Combustible real reportado – toda la flota ($)", "Consumo esperado – toda la flota ($)",
              "Factor real ÷ esperado", "Asignado a las ambulancias ($)", "Asignado a los demás vehículos ($)", "% ambulancias", "Observación"])
    r.append(["Agosto", 6000000, 1700000, 3.51, asignado_agosto, 996637, 0.83, "Esperado solo del 19 al 31"])
    r.append([])
    r.append(["RESUMEN POR MES – AMBULANCIAS"])
    r.append(["Mes", "Mantenimiento ambulancias ($)", "Combustible real – toda la flota ($)", "Combustible asignado a las ambulancias ($)",
              "Mantenimiento + combustible ambulancias ($)", "Viajes", "Km estimados", "Galones esperados", "Período del reporte de combustible real"])
    r.append(["Julio", 0, 5500000, "—", "—", "sin registro", "sin registro", "sin registro", "Mes completo"])
    r.append(["Agosto", 10229746, 6000000, asignado_agosto, None, 14, 3322, 77.1, "Mes completo"])
    r.append(["Octubre", 265576, "Sin reporte", "Pendiente", "—", 31, 6417, 172.5, "Sin reporte de combustible"])
    t = wb.create_sheet("Tame")
    t["A4"] = "COSTOS DE OPERACIÓN DE LAS AMBULANCIAS – SEDE TAME"
    for _ in range(3):
        t.append([])
    t.append(ENC_SEDE)
    t.append([2, "LXS630", "Ambulancia TAB – Chevrolet Nhr 2023", "ACPM", None, "Julio", 0, "sin registro", "sin registro", "sin registro", "—", None])
    t.append([None, "LXS630", "Ambulancia TAB – Chevrolet Nhr 2023", "ACPM", 43.1, "Agosto", 10229746, 14, 3322, 77.1, 5003363, "Viajes desde el 19/08"])
    t.append([None, None, None, None, None, "TOTAL SEDE", "=SUM(G9:G10)"])
    a = wb.create_sheet("Arauquita")
    a["A4"] = "COSTOS DE OPERACIÓN DE LAS AMBULANCIAS – SEDE ARAUQUITA"
    for _ in range(3):
        a.append([])
    a.append(ENC_SEDE)
    a.append([1, "KSM549", "Ambulancia TAB – Ford Ranger 2027", "ACPM", 37.2, "Octubre", 265576, 31, 6417, 172.5, "Pendiente",
              "Ambulancia nueva; Incluye OT de CDMA pendientes de certificar"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


class CostosMensualesTests(BaseDatos):
    def test_lector(self):
        from flota.importacion.costos_mensuales import leer_costos_mensuales

        d = leer_costos_mensuales(libro_costos_mensuales(), "costos.xlsx")
        self.assertEqual(d["anio"], 2026)
        self.assertEqual(len(d["filas"]), 3)
        ago = next(f for f in d["filas"] if f["mes"] == 8)
        self.assertEqual((ago["mantenimiento"], ago["viajes"], ago["km"], ago["combustible"]), (10229746, 14, 3322, 5003363))
        jul = next(f for f in d["filas"] if f["mes"] == 7)
        self.assertEqual((jul["mantenimiento"], jul["viajes"], jul["combustible"]), (0, None, None))
        oct_ = next(f for f in d["filas"] if f["mes"] == 10)
        self.assertEqual(oct_["combustible_estado"], "Pendiente")
        self.assertEqual(d["flota"][8]["real_flota"], 6000000)
        self.assertEqual(d["flota"][7]["periodo_reporte"], "Mes completo")
        self.assertFalse([h for h in d["hallazgos"] if h["tipo"] == "INCONSISTENCIA"])
        # El Resumen no cuadra con las hojas: se informa
        d2 = leer_costos_mensuales(libro_costos_mensuales(asignado_agosto=9999999), "costos.xlsx")
        self.assertTrue(any("Combustible asignado" in h["descripcion"] for h in d2["hallazgos"]))

    def test_carga_consumos_reales_actividad_y_costeo(self):
        from flota.models import ActividadMensual, Ambulancia, CombustibleFlotaMes

        lote = importar.importar_costos_mensuales(libro_costos_mensuales(), nombre="costos.xlsx")
        ago, jul, oct_ = dt.date(2026, 8, 1), dt.date(2026, 7, 1), dt.date(2026, 10, 1)
        mant = Consumo.objects.get(ambulancia__placa="LXS630", mes=ago, tipo=Consumo.MANTENIMIENTO)
        self.assertEqual(mant.valor, Decimal("10229746"))
        cero = Consumo.objects.get(ambulancia__placa="LXS630", mes=jul, tipo=Consumo.MANTENIMIENTO)
        self.assertEqual(cero.valor, 0)  # mes sin mantenimiento: dato real, reemplaza el estándar
        comb = Consumo.objects.get(ambulancia__placa="LXS630", mes=ago, tipo=Consumo.COMBUSTIBLE)
        self.assertEqual(comb.valor, Decimal("5003363"))
        self.assertAlmostEqual(float(comb.cantidad), 5003363 / 18500, places=2)  # galones a precio de ACPM
        self.assertFalse(Consumo.objects.filter(mes=oct_, tipo=Consumo.COMBUSTIBLE).exists())  # pendiente
        act = ActividadMensual.objects.get(ambulancia__placa="LXS630", mes=ago)
        self.assertEqual((act.viajes, act.km_estimados), (14, Decimal("3322.0")))
        self.assertTrue(ActividadMensual.objects.get(ambulancia__placa="KSM549", mes=oct_).combustible_pendiente)
        self.assertEqual(CombustibleFlotaMes.objects.get(mes=ago).real_flota, Decimal("6000000"))
        self.assertEqual(Ambulancia.objects.get(placa="KSM549").sede.codigo, "arauquita")  # nueva: toma la sede del informe
        self.assertIn("pendientes de certificar", Consumo.objects.get(ambulancia__placa="KSM549", tipo=Consumo.MANTENIMIENTO).descripcion)

        r = calcular_mes(ago)
        self.assertEqual(r.fuente_pool["LXS630"]["mantenimiento"], "REAL")
        self.assertAlmostEqual(r.ambulancias["LXS630"]["pool_mantenimiento"], 10229746, places=2)
        self.assertAlmostEqual(r.ambulancias["LXS630"]["pool_combustible"], 5003363, places=2)
        self.assertEqual(calcular_mes(jul).fuente_pool["LXS630"]["mantenimiento"], "REAL")

        # Recargar actualiza, no duplica
        n = Consumo.objects.count()
        lote2 = importar.importar_costos_mensuales(libro_costos_mensuales(), nombre="costos.xlsx")
        self.assertEqual((lote2.nuevos, Consumo.objects.count()), (0, n))
        self.assertEqual(lote.detalle["mantenimiento_total"], 10229746 + 265576)

    def test_modo_promedio(self):
        importar.importar_costos_mensuales(libro_costos_mensuales(), nombre="costos.xlsx", modo_mantenimiento="PROMEDIO")
        valores = set(Consumo.objects.filter(ambulancia__placa="LXS630", tipo=Consumo.MANTENIMIENTO).values_list("valor", flat=True))
        self.assertEqual(valores, {Decimal("5114873.00")})  # (0 + 10.229.746) / 2

    def test_pantallas(self):
        self.client.force_login(User.objects.create_superuser("cm", "", "Clave-Prueba-1"))
        r = self.client.post(reverse("flota:cargar_costos_mensuales"), {
            "archivo": SimpleUploadedFile("costos.xlsx", libro_costos_mensuales().getvalue()), "modo_mantenimiento": "REAL",
        })
        self.assertRedirects(r, reverse("flota:costos_reales") + "?desde=2026-07&hasta=2026-10")
        p = self.client.get(reverse("flota:costos_reales"), {"desde": "2026-07", "hasta": "2026-10"})
        self.assertContains(p, "LXS630")
        self.assertEqual(p.context["filas"][0]["total"]["km"], 3322)
        lxs = next(f for f in p.context["filas"] if f["ambulancia"].placa == "LXS630")
        self.assertAlmostEqual(lxs["total"]["costo_km_real"], (10229746 + 5003363) / 3322)
        x = self.client.get(reverse("flota:costos_reales"), {"desde": "2026-07", "hasta": "2026-10", "formato": "xlsx"})
        self.assertEqual(load_workbook(io.BytesIO(x.content)).sheetnames, ["Por ambulancia", "Por ambulancia y mes", "Combustible flota"])
        self.assertContains(self.client.get(reverse("flota:cargas")), "Cargar informe mensual de mantenimiento y combustible")
        self.assertContains(self.client.get(reverse("flota:consumos_tabla", args=["combustible"])), "Cargar informe mensual")


# ---------------------------------------------------------------------------
# Referencia y contrarreferencia (RYC-FO-015)
# ---------------------------------------------------------------------------


def libro_referencia_v2(filas):
    """Formato v002 generado por la aplicación con filas diligenciadas (dict clave -> valor)."""
    from flota.importacion.referencia import COL, FILA_ENC
    from flota.servicios.referencia import formato_vacio

    wb = load_workbook(io.BytesIO(formato_vacio()))
    ws = wb["REGISTRO"]
    for i, f in enumerate(filas, start=FILA_ENC + 1):
        for clave, valor in f.items():
            ws.cell(i, COL[clave], valor)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def fila_ref(**kw):
    base = {
        "tipo_registro": "REFERENCIA", "sede": "Hospital San Antonio de Tame", "fecha_reporte": dt.datetime(2026, 9, 10, 8, 0),
        "eps": "NUEVA EPS", "nombre": "PACIENTE DE PRUEBA", "tipo_documento": "CC", "documento": "1000001", "edad": 45,
        "unidad_edad": "AÑOS", "sexo": "F", "especialidad": "MEDICINA INTERNA", "cie10": "J189", "tipo_traslado": "TAB",
        "acompanamiento": "NO", "fecha_llamada": dt.datetime(2026, 9, 10, 9, 0), "fecha_llegada_equipo": dt.datetime(2026, 9, 10, 9, 20),
        "placa": "OEU846", "ips_destino": "HOSPITAL REGIONAL", "municipio_destino": "SARAVENA",
        "fecha_aceptacion": dt.datetime(2026, 9, 10, 14, 0), "fecha_salida": dt.datetime(2026, 9, 10, 15, 0),
        "fecha_llegada": dt.datetime(2026, 9, 10, 19, 0), "estado": "TRASLADADO", "responsable": "JEFE DE TURNO",
    }
    base.update(kw)
    return base


class ReferenciaTests(BaseDatos):
    def _his(self, documento, fecha, codigo="601T02", cantidad=101, valor=1717000, factura="FHSA1"):
        from django.conf import settings

        from flota.importacion.facturacion_his import hash_documento

        return Traslado.objects.create(
            llave_externa=f"HIS|{factura}|{fecha}|{codigo}|{documento}", origen_registro=Traslado.HIS, sede=self.tame,
            fecha_servicio=fecha, codigo_cups=codigo, cantidad=cantidad, valor_facturado=valor, numero_factura=factura,
            documento_hash=hash_documento("CC", documento, settings.SAL_DOCUMENTO),
        )

    def test_formato_v002(self):
        from flota.importacion.referencia import COL, COLUMNAS, FILA_ENC, leer_referencia
        from flota.servicios.referencia import formato_vacio

        wb = load_workbook(io.BytesIO(formato_vacio()))
        self.assertEqual(wb.sheetnames, ["REGISTRO", "INSTRUCCIONES", "INDICADOR", "MEDICION POR EPS", "CONTROL DE CAMBIOS", "LISTAS"])
        self.assertEqual(wb["LISTAS"].sheet_state, "hidden")
        ws = wb["REGISTRO"]
        self.assertTrue(ws.cell(FILA_ENC, COL["placa"]).value.startswith("PLACA DE LA AMBULANCIA"))
        self.assertTrue(str(ws.cell(FILA_ENC + 1, COL["espera_h"]).value).startswith("=IF("))
        self.assertGreaterEqual(len(ws.data_validations.dataValidation), 20)
        placas = [c.value for c in wb["LISTAS"]["K"][1:] if c.value]
        self.assertIn("OEU846", placas)
        self.assertIn("NO APLICA", placas)
        self.assertEqual(len(COLUMNAS), ws.max_column)
        vacio = leer_referencia(io.BytesIO(formato_vacio()), "f.xlsx", "sal")
        self.assertEqual((vacio["version"], vacio["registros"]), ("002", []))

    def test_carga_cruza_con_facturacion_y_asigna_placa_y_ruta(self):
        from flota.models import Remision
        from flota.servicios.referencia import importar_referencia, tablero_referencia

        his = self._his("1000001", dt.date(2026, 9, 10))
        km = self._his("1000001", dt.date(2026, 9, 10), codigo="601T01", cantidad=1, valor=459300)
        filas = [
            fila_ref(),
            fila_ref(documento="2000002", fecha_reporte=dt.datetime(2026, 9, 12, 22, 0), municipio_destino="SARAVENA",
                     fecha_aceptacion=dt.datetime(2026, 9, 14, 2, 0), fecha_salida=dt.datetime(2026, 9, 14, 3, 0),
                     fecha_llegada=dt.datetime(2026, 9, 14, 2, 30)),  # sin facturar; llegada antes de la salida
            fila_ref(documento="3000003", tipo_traslado="AÉREO", placa="NO APLICA", municipio_destino="", ips_destino="BOGOTÁ",
                     eps="SANITAS"),
            fila_ref(documento="4000004", estado="RECHAZADO POR IPS DESTINO"),
        ]
        lote = importar_referencia(libro_referencia_v2(filas), nombre="RYC-FO-015_TAME_2026-09.xlsx")
        self.assertEqual((lote.nuevos, lote.detalle["version_formato"], lote.detalle["meses"]), (4, "002", ["2026-09"]))
        self.assertEqual(lote.sede, self.tame)
        his.refresh_from_db()
        km.refresh_from_db()
        self.assertEqual(his.ambulancia.placa, "OEU846")
        self.assertEqual(km.ambulancia.placa, "OEU846")
        self.assertEqual((his.ruta.origen, his.ruta.destino), ("TAME", "SARAVENA"))
        self.assertIn("RYC-FO-015", his.observaciones_calidad)
        r1 = Remision.objects.get(traslado=his)
        self.assertEqual((r1.lineas_facturadas, r1.espera_h, r1.respuesta_min, r1.duracion_h), (2, 6.0, 20.0, 4.0))
        self.assertNotIn("1000001", r1.documento_hash)
        alertada = Remision.objects.get(alertas__contains="antes de")
        self.assertIn("llegada al destino", alertada.alertas)

        t = tablero_referencia(SEP, SEP)
        tot = t["total"]
        self.assertEqual((tot["total"], tot["trasladadas"], tot["TAB"], tot["AÉREO"], tot["conciliadas"], tot["sin_facturar"]),
                         (4, 3, 3, 1, 1, 1))
        self.assertEqual(tot["valor_sin_facturar"], float(his.ruta.tarifa))
        self.assertEqual(len(t["facturado_sin_remision"]), 0)
        self.assertEqual({d["eps"] for d in t["por_eps"]}, {"NUEVA EPS", "SANITAS"})

        # recargar no duplica
        lote2 = importar_referencia(libro_referencia_v2(filas), nombre="RYC-FO-015_TAME_2026-09.xlsx")
        self.assertEqual((lote2.nuevos, lote2.actualizados, Remision.objects.count()), (0, 4, 4))

    def test_facturado_sin_remision_y_mes_en_hora_colombia(self):
        from flota.models import Remision
        from flota.servicios.referencia import importar_referencia, tablero_referencia

        self._his("9999999", dt.date(2026, 9, 20), factura="FHSA9")
        # 30/09 a las 23:30 en Colombia es 01/10 en UTC: debe quedar en septiembre
        importar_referencia(libro_referencia_v2([fila_ref(fecha_reporte=dt.datetime(2026, 9, 30, 23, 30))]), nombre="r.xlsx")
        self.assertEqual(Remision.objects.get().mes, SEP)
        t = tablero_referencia(SEP, SEP)
        self.assertEqual([x["factura"] for x in t["facturado_sin_remision"]], ["FHSA9"])

    def test_formato_v001_actual(self):
        from flota.models import Remision
        from flota.servicios.referencia import importar_referencia

        wb = Workbook()
        ws = wb.active
        ws["A5"] = "PACIENTES REMITIDOS"
        ws.append(["FECHA DE REPORTE DE REMISION ", "HORA DE REPORTE DE REMISION  (D/M/A)", "EPS", "IPS QUE REPORTA ",
                   "NOMBRE DEL PACIENTE ", "TIPO DE DOCUMENTO ", "NUMERO ", "UNIDAD MEDIA EDAD", "EDAD", "SEXO",
                   "ESPECIALIDAD REQUERIDA", "DIAGNOSTICO", "TIPO TRASLADO ", "ACOMPAÑAMIENTO MEDICO",
                   "HORA DE LLAMADA AL EQUIPO DE REFERENCIA ", "HORA DE LLEGADA DEL EQUIPO DE REFERENCIA ",
                   "No DE PLACA DE LA AMBULANCIA", "DESTINO ", "HORA DE ACEPTACION ", "HORA DE SALIDA DE REMISION",
                   "HORA DE LLEGADA HOSPITAL DESTINO", "OBSERVACIONES ", "RESPONSABLE DEL TURNO"])
        ws.append([dt.datetime(2026, 9, 5), dt.time(22, 0), "COOSALUD", "HOSPITAL SAN ANTONIO", "X", "CC", 5000005, "AÑOS", 30, "M",
                   "ORTOPEDIA", "FRACTURA", "TAB", "NO", dt.time(22, 10), dt.time(22, 30), "OEU846", "HOSPITAL DE SARAVENA",
                   dt.time(23, 0), dt.time(1, 0), dt.time(5, 0), "", "ENFERMERA"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        lote = importar_referencia(buf, nombre="v1.xlsx")
        self.assertEqual(lote.detalle["version_formato"], "001")
        r = Remision.objects.get()
        self.assertEqual((r.sede, r.ambulancia.placa, r.ruta.destino), (self.tame, "OEU846", "SARAVENA"))
        from django.utils import timezone

        self.assertEqual(timezone.localtime(r.fecha_salida).day, 6)  # salida a la 01:00 del día siguiente
        self.assertIn("día siguiente", r.alertas)

    def test_pantallas(self):
        self.client.force_login(User.objects.create_superuser("ref", "", "Clave-Prueba-1"))
        f = self.client.get(reverse("flota:formato_referencia"))
        self.assertEqual(load_workbook(io.BytesIO(f.content)).sheetnames[0], "REGISTRO")
        self.assertIn("RYC-FO-015", f["Content-Disposition"])
        r = self.client.post(reverse("flota:cargar_referencia"), {
            "archivo": SimpleUploadedFile("ref.xlsx", libro_referencia_v2([fila_ref()]).getvalue()), "sede": "",
        })
        self.assertRedirects(r, reverse("flota:referencia") + "?desde=2026-09&hasta=2026-09")
        p = self.client.get(reverse("flota:referencia"), {"desde": "2026-09", "hasta": "2026-09"})
        self.assertContains(p, "Por mes y hospital")
        self.assertContains(p, "NUEVA EPS")
        x = self.client.get(reverse("flota:referencia"), {"desde": "2026-09", "hasta": "2026-09", "formato": "xlsx"})
        self.assertEqual(load_workbook(io.BytesIO(x.content)).sheetnames,
                         ["Por mes y hospital", "Por EPS", "Por destino", "Remisiones", "Facturado sin remisión"])
        self.assertContains(self.client.get(reverse("flota:cargas")), "Cargar referencia y contrarreferencia del mes")

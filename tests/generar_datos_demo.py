"""Genera archivos de DEMOSTRACIÓN (datos ficticios) con el mismo formato de Software 360 y de las plantillas:

    python tests/generar_datos_demo.py [carpeta_salida]

- facturacion_ambito_demo.xlsx  · facturación por ámbito (hojas ENERO 2026, FEBRERO 2026, PARAM CONTRATO, PARAM SERVICIO)
- facturas_generadas_demo.xlsx  · informe de facturas generadas (una fila por factura, títulos arriba como en 360)
- farmacia_demo.xlsx            · dispensación / consumo de farmacia por sede

No contiene datos reales de pacientes: documentos y nombres son inventados."""
import os, random, sys
from datetime import date, timedelta
from openpyxl import Workbook

SEDES = ["HOSPITAL SAN ANTONIO", "HOSPITAL SAN FRANCISCO", "HOSPITAL SAN LORENZO", "CENTRO DE SALUD JESUS CORONEL"]
EPS = [("EPS001", "NUEVA EMPRESA PROMOTORA DE SALUD", "900156264"), ("EPS002", "ENTIDAD PROMOTORA DE SALUD SANITAS S A S", "800251440"),
       ("EPS003", "COOSALUD ENTIDAD PROMOTORA DE SALUD S A", "900226715")]
CONTRATOS = {"NUEVA EMPRESA PROMOTORA DE SALUD": [("NUEVA EPS EVENTO SUBSIDIADO", "EVENTO"), ("NUEVA EPS SUBSIDIADO PYM GLOBAL", "CAPITA")],
             "ENTIDAD PROMOTORA DE SALUD SANITAS S A S": [("SANITAS REGIMEN SUBSIDIADO EVENTO", "EVENTO")],
             "COOSALUD ENTIDAD PROMOTORA DE SALUD S A": [("COOSALUD CAPITA SUBSIDIADO", "CAPITA")]}
PROC = [("CONSULTAS", "890201", "CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL", 32200), ("LABORATORIO CLINICO", "903841", "GLUCOSA EN SUERO", 9800),
        ("ODONTOLOGIA", "997310", "CONTROL DE PLACA DENTAL", 21400), ("IMAGENOLOGIA", "871121", "RADIOGRAFIA DE TORAX", 46860)]
MEDS = [("MEDICAMENTOS", "G-0052", "LACTATO DE RINGER SOLUCION INYECTABLE 500 ML", 6900), ("MEDICAMENTOS", "G-0101", "ACETAMINOFEN 500 MG TABLETA", 120),
        ("MEDICAMENTOS", "G-0233", "DICLOFENACO 75 MG/3 ML AMPOLLA", 1450), ("MEDICAMENTOS", "G-0410", "AMOXICILINA 500 MG CAPSULA", 380),
        ("MATERIALES E INSUMOS MEDICOS", "D-0015", "JERINGA DESECHABLE 5 ML", 650), ("MATERIALES E INSUMOS MEDICOS", "D-0077", "GUANTES DE EXAMEN TALLA M (PAR)", 900),
        ("MATERIALES E INSUMOS MEDICOS", "D-0120", "CATETER INTRAVENOSO No 20", 4200), ("SUPLEMENTOS DIETARIOS", "S-0003", "SUPLEMENTO NUTRICIONAL EN POLVO 400 G", 38000)]
HDR = ["CONTRATO", "SEDE", "SERVICIO", "AMBITO", "REGIMEN DEL PACIENTE", "COD. PROCEDIMIENTO", "COD. MEDICAMENTO", "CANTIDAD", "VALOR ACTIVIDAD",
       "VALOR TOTAL", "FACTURA NRO", "FECHA DE FACTURACION", "CODIGO EPS", "NOMBRE EPS", "NUMERO IDE.", "ESPECIALIDAD", "AGENDA", "PROFESIONAL",
       "REGULADO", "NOMBRE DIAGNÓSTICO", "ACTIVIDAD", "TIPO DE PACIENTE"]

def facturacion(path, facturas_por_mes=120, seed=7):
    rnd = random.Random(seed); wb = Workbook(write_only=True); fact = []
    pc = wb.create_sheet("PARAM CONTRATO"); pc.append([]); pc.append([]); pc.append([None, "CONTRATO", "MODALIDAD"])
    for cs in CONTRATOS.values():
        for c, m in cs: pc.append([None, c, m])
    ps = wb.create_sheet("PARAM SERVICIO"); ps.append([]); ps.append([]); ps.append([None, "SERVICIO"])
    for s in sorted({p[0] for p in PROC + MEDS}): ps.append([None, s])
    for mes, nombre in ((1, "ENERO 2026"), (2, "FEBRERO 2026")):
        ws = wb.create_sheet(nombre); ws.append(HDR)
        for n in range(facturas_por_mes):
            cod, eps_n, _ = rnd.choice(EPS); contrato, modal = rnd.choice(CONTRATOS[eps_n]); sede = rnd.choice(SEDES)
            fn = f"FE{mes}{n:05d}"; dia = min(28, int(rnd.triangular(1, 28, 27))); f = date(2026, mes, dia)
            amb = rnd.choice(["AMBULATORIO", "URGENCIAS", "HOSPITALIZACION"]); ide = str(10_000_000 + rnd.randint(1, 400)); total = 0.0
            items = [rnd.choice(PROC)] + rnd.sample(MEDS, rnd.randint(0, 3))
            for serv, code, act, precio in items:
                med = serv != PROC[0][0] and code[0] in "GDS"
                pr = precio * (1.25 if (med and sede == "HOSPITAL SAN LORENZO" and code == "G-0233") else 1)   # precio no homogéneo a propósito
                cant = rnd.randint(1, 3) if not med else rnd.randint(1, 10)
                vt = 0 if modal == "CAPITA" and med and rnd.random() < .3 else pr * cant; total += vt
                ws.append([contrato, sede, serv, amb, "SUBSIDIADO", "" if med else code, code if med else "", cant, pr, vt, fn, f.strftime("%d/%m/%Y"),
                           cod, eps_n, ide, "MEDICINA GENERAL", "CONSULTA EXTERNA", "PROFESIONAL DEMO", "NO", "DIAGNOSTICO DEMO", act, "SUBSIDIADO"])
            fact.append((mes, fn, f, sede, eps_n, contrato, modal, amb, total, ide))
    wb.save(path); return fact

def facturas_generadas(path, fact, seed=11):
    rnd = random.Random(seed); wb = Workbook(write_only=True); ws = wb.create_sheet("FACTU GENERADA ENERO")
    for t in ("ESE DEPARTAMENTAL DE PRIMER NIVEL MORENO Y CLAVIJO", "INFORME DE FACTURAS GENERADAS", "FECHA DE CONSULTA DESDE: 01/01/2026,HASTA 31/01/2026",
              "SEDE: Todas", "TIPO FACTURA: Factura", None): ws.append([t])
    ws.append(["NRO. INGRESO", "GENERADA EN", "NUMERO FACTURA", "ESTADO FACTURA", "FECHA DE FACTURA", "SUBTOTAL", "VALOR CUOTA", "VALOR COPAGO", "VALOR BONO", "IVA",
               "TOTAL", "VALOR DESCUENTO", "PARTICULAR", "REGISTRADA POR", "ESTADO AUDITORIA", "FECHA DE ANULACIÓN", "TIPO IDE.", "NUMERO IDE.", "PACIENTE",
               "FECHA INGRESO", "FECHA EGRESO", "RECIBO CAJA NRO", "VALOR", "TIPO CONTRATO", "TERCERO", "NIT", "CONTRATO", "REGIMEN DEL CONTRATO",
               "FECHA RADICADO", "NRO. RADICADO EPS", "RADICADO ACTUAL", "VALOR EXCENDENTE", "SEDE", "ESTADO ENVIO DIAN", "CUFE", "CLASE CONTRATO", "CLASE DE ATENCIÓN"])
    nit = {e[1]: e[2] for e in EPS}
    for i, (mes, fn, f, sede, eps_n, contrato, modal, amb, total, ide) in enumerate(x for x in fact if x[0] == 1):
        est = rnd.choices(["Factura Radicada", "Glosada", "Pendiente Por Radicar"], [80, 12, 8])[0]
        rad = "" if est.startswith("Pendiente") else f + timedelta(days=rnd.choice([5, 10, 17, 24, 40]))
        cuota = rnd.choice([0, 0, 0, 4700]); ing = f - timedelta(days=rnd.choice([0, 1, 3, 45]))
        valor = round(total) if i % 37 else round(total) + 1000                                   # encabezado ≠ detalle a propósito cada 37
        ws.append([str(2_000_000 + i), amb, fn, est, f, valor + cuota + (500 if i % 41 == 0 else 0), cuota, 0, 0, 0, valor,   # F06 sembrado cada 41
                   0, "NO", rnd.choice(["FACTURADOR UNO", "FACTURADOR DOS", "FACTURADOR TRES"]),
                   "RADICADA" if rad else "LISTA PARA RADICAR", None, "CC", ide, "PACIENTE DEMO", ing, ing + timedelta(days=1) if amb != "AMBULATORIO" else None,
                   "0", "0.00", "EVENTO AGRUPADO" if modal == "EVENTO" else "CAPITADA", eps_n, nit[eps_n], contrato, "SUBSIDIADO", rad or None,
                   "LOTE DEMO" if rad else None, "1" if rad else "0", "0.00", sede, "ENVIADA" if i % 50 else "ENVIADA Y NO RECIBIDA", "" if i % 50 == 0 else f"cufe{i:08d}",
                   "RÉGIMEN SUBSIDIADO", "REGIMEN SUBSIDIADO"])
    wb.save(path)

def farmacia(path, seed=13):
    rnd = random.Random(seed); wb = Workbook(write_only=True); ws = wb.create_sheet("DISPENSACION")
    ws.append(["MES", "SEDE", "CODIGO", "DESCRIPCION", "TIPO", "CONCEPTO", "CANTIDAD", "COSTO_UNITARIO", "VALOR_COSTO"])
    for mes in ("2026-01", "2026-02"):
        for sede in SEDES:
            for serv, code, desc, precio in MEDS:
                tipo = "MEDICAMENTO" if serv == "MEDICAMENTOS" else "INSUMO" if "INSUMO" in serv else "SUPLEMENTO"
                ws.append([mes, sede, code, desc, tipo, "DISPENSACION", rnd.randint(5, 120), round(precio * rnd.uniform(.55, 1.05), 2), None])
    wb.save(path)

# Medicamentos como los exporta 360 filtrando SERVICIO = MEDICAMENTOS: hoja «ENERO» (sin año), COD. PROCEDIMIENTO = código interno
# (G-xxxx) y COD. MEDICAMENTO = código ATC. Los CUM del RIPS se relacionan con el código 360 mediante la tabla de homologación.
MEDS_360 = [("G-0052", "B05CB01", "CLORURO DE SODIO 0.9% SOLUCION INYECTABLE 500ML", 4180, "19942561-6"),
            ("G-0067", "M01AB05", "DICLOFENACO SODICO SOLUCION INYECTABLE 75MG/3ML", 1450, "20012345-1"),
            ("G-0105", "B05XA30", "LACTATO DE RINGER SOLUCION INYECTABLE 500ML", 3410, "19911111-3"),
            ("G-0002", "N02BE01", "ACETAMINOFEN TABLETA 500MG", 66, "19900001-2")]       # G-0002: su CUM queda SIN homologar a propósito
HDR_MED = ["UNIDAD FUNCIONAL", "CONTRATO", "TIPO IDE.", "NUMERO IDE.", "PRIMER APELLIDO", "SEDE", "SERVICIO", "AGENDA", "COD. PROCEDIMIENTO",
           "COD. MEDICAMENTO", "ACTIVIDAD", "CANTIDAD", "COD. DIAGNÓSTICO", "NOMBRE DIAGNÓSTICO", "FECHA DE FACTURACION", "FACTURA NRO",
           "VALOR ACTIVIDAD", "VALOR TOTAL", "REGIMEN DEL PACIENTE", "AMBITO", "NOMBRE EPS", "CÓDIGO EPS"]

def medicamentos_360(path, facturas=40, seed=17):
    """Devuelve las líneas generadas [(factura, codigo_360, cum, cantidad, valor)] para construir el RIPS de prueba."""
    rnd = random.Random(seed); wb = Workbook(write_only=True); ws = wb.create_sheet("ENERO"); ws.append(HDR_MED); out = []
    for n in range(facturas):
        cod, eps_n, _ = rnd.choice(EPS); contrato, modal = rnd.choice(CONTRATOS[eps_n]); sede = rnd.choice(SEDES)
        fn = f"FM{n:05d}"; f = date(2026, 1, rnd.randint(1, 28)); ide = str(20_000_000 + rnd.randint(1, 25))
        uf = rnd.choice(["URGENCIAS", "HOSPITALIZACIÓN", "CONSULTA EXTERNA"])
        for g, atc, desc, pr, cum in rnd.sample(MEDS_360, rnd.randint(1, 3)):
            cant = rnd.randint(1, 4); vt = pr * cant
            ws.append([uf, contrato, "CC", ide, "DEMO", sede, "MEDICAMENTOS", "MEDICAMENTOS", g, atc, desc, cant, "R509", "FIEBRE, NO ESPECIFICADA",
                       f, fn, pr, vt, "SUBSIDIADO", "Urgencias" if uf != "CONSULTA EXTERNA" else "Ambulatorio", eps_n, cod])
            out.append((fn, g, cum, cant, vt, eps_n, contrato, modal, sede))
    wb.save(path); return out

def rips_medicamentos(path, lineas, seed=19):
    """RIPS (plantilla homologada) con CUM: omite algunas líneas y cambia cantidades para que el cruce tenga hallazgos."""
    rnd = random.Random(seed); wb = Workbook(write_only=True); ws = wb.create_sheet("RIPS")
    ws.append(["MES", "EPS", "CONTRATO", "MODALIDAD", "SEDE", "FACTURA_FEV", "TIPO_SERVICIO", "CODIGO_CUPS_CUMS", "CANTIDAD", "VALOR_SERVICIO", "VALOR_FACTURA_FEV"])
    tot = {}
    for fn, g, cum, cant, vt, *_ in lineas: tot[fn] = tot.get(fn, 0) + vt
    for i, (fn, g, cum, cant, vt, eps_n, contrato, modal, sede) in enumerate(lineas):
        if i % 11 == 5: continue                                        # facturado sin RIPS
        c = cant + 1 if i % 13 == 7 else cant                           # cantidad difiere
        ws.append(["2026-01", eps_n, contrato, modal, sede, fn, "Medicamentos", cum, c, vt, tot[fn]])
    wb.save(path)

def codigos(path):
    wb = Workbook(write_only=True); ws = wb.create_sheet("PLANTILLA"); ws.append(["CODIGO_RIPS", "CODIGO_SOFTWARE360", "DESCRIPCION"])
    for g, atc, desc, pr, cum in MEDS_360[:3]: ws.append([cum, g, desc])
    wb.save(path)

# ───── Facturación generada POR ACTIVIDAD (360) + RIPS JSON (Res. 2275/2023) y TXT (Res. 3374/2000) del mismo mes ─────
HDR_ACT = ["UNIDAD FUNCIONAL", "CONTRATO", "TIPO IDE.", "NUMERO IDE.", "SEDE", "SERVICIO", "COD. PROCEDIMIENTO", "COD. MEDICAMENTO", "ACTIVIDAD",
           "CANTIDAD", "CODIGO DX", "NOMBRE DX", "FECHA DE FACTURACION", "VALOR ACTIVIDAD", "VALOR TOTAL", "FACTURA NRO", "VALOR FACTURA",
           "REGIMEN DEL PACIENTE", "AMBITO", "FECHA RADICADO", "NRO. RADICADO EPS"]
ACT_SERV = [("CONSULTAS", "890201", "", "CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL", 32200, "Consultas"),
            ("LABORATORIO CLINICO", "903841", "", "GLUCOSA EN SUERO", 9800, "Procedimientos"),
            ("MEDICAMENTOS", "G-0052", "B05CB01", "CLORURO DE SODIO 0.9% SOLUCION INYECTABLE 500ML", 4180, "Medicamentos"),
            ("MATERIALES E INSUMOS MEDICOS", "D-0082", "", "JERINGA DESECHABLE 10 ML", 350, "Otros servicios")]
CUM = {"G-0052": "19942561-6"}

def actividad_y_rips(carpeta, facturas=30, seed=23):
    """Devuelve (rutas, esperado): esperado documenta los hallazgos sembrados para las pruebas."""
    import json, zipfile
    rnd = random.Random(seed); os.makedirs(carpeta, exist_ok=True)
    rutas = {k: os.path.join(carpeta, k) for k in ("ENERO_actividad.xlsx", "rips_json.zip", "rips_txt.zip", "una_factura.json")}
    wb = Workbook(write_only=True); ws = wb.create_sheet("_var_lib_tomcat8_webapps_salud3"); ws.append(HDR_ACT)
    facs = []
    for n in range(facturas):
        fn = f"FA{n:05d}"; f = date(2026, 1, rnd.randint(2, 27)); ide = str(30_000_000 + n); contrato = rnd.choice(["NUEVA EPS EVENTO SUBSIDIADO", "SANITAS CONTRIBUTIVO EVENTO"])
        items = [(sv, rnd.randint(1, 3) if sv[0] in ("MEDICAMENTOS", "MATERIALES E INSUMOS MEDICOS") else 1) for sv in ACT_SERV[:1] + rnd.sample(ACT_SERV[1:], 2)]
        total = sum(sv[4] * c for sv, c in items); vfac = total + (5000 if n == 7 else 0)          # R10 sembrado en FA00007
        for sv, cant in items:
            ws.append(["URGENCIAS", contrato, "CC", ide, "HOSPITAL SAN ANTONIO", sv[0], sv[1], sv[2], sv[3], f"{cant:10.2f}", "R509", "FIEBRE",
                       f, sv[4], sv[4] * cant, fn, f"{vfac:16.2f}", "SUBSIDIADO", "Urgencias", f + timedelta(days=10), "LOTE 1"])
        facs.append((fn, f, ide, contrato, items, total))
    wb.save(rutas["ENERO_actividad.xlsx"])
    esperado = dict(sin_rips="FA00003", cantidad="FA00005", paciente="FA00009", sin_dx="FA00011", cero="FA00013", r10="FA00007")
    def rips_json(fn, f, ide, items):
        serv = {"consultas": [], "procedimientos": [], "medicamentos": [], "otrosServicios": []}
        for sv, cant in items:
            if fn == esperado["cantidad"] and sv[5] in ("Medicamentos", "Otros servicios"): cant += 1
            val = 0 if (fn == esperado["cero"] and sv[5] == "Consultas") else sv[4] * cant
            fecha = f"{f.isoformat()} 08:00"; dx = "" if fn == esperado["sin_dx"] else "R509"
            if sv[5] == "Consultas": serv["consultas"].append({"codConsulta": sv[1], "fechaInicioAtencion": fecha, "codDiagnosticoPrincipal": dx, "vrServicio": val})
            elif sv[5] == "Procedimientos": serv["procedimientos"].append({"codProcedimiento": sv[1], "fechaInicioAtencion": fecha, "codDiagnosticoPrincipal": dx, "vrServicio": val})
            elif sv[5] == "Medicamentos": serv["medicamentos"].append({"codTecnologiaSalud": CUM[sv[1]], "nomTecnologiaSalud": sv[3], "cantidadMedicamento": cant,
                                                                       "vrUnitMedicamento": sv[4], "vrServicio": val, "fechaDispensAdmon": fecha})
            else: serv["otrosServicios"].append({"codTecnologiaSalud": sv[1], "nomTecnologiaSalud": sv[3], "cantidadOS": cant, "vrUnitOS": sv[4], "vrServicio": val,
                                                 "fechaSuministroTecnologia": fecha})
        doc = "99999999" if fn == esperado["paciente"] else ide
        return {"numDocumentoIdObligado": "900034131", "numFactura": fn, "tipoNota": None, "numNota": None,
                "usuarios": [{"tipoDocumentoIdentificacion": "CC", "numDocumentoIdentificacion": doc, "tipoUsuario": "04", "codSexo": "F", "servicios": serv}]}
    with zipfile.ZipFile(rutas["rips_json.zip"], "w") as z:
        for fn, f, ide, contrato, items, total in facs:
            if fn != esperado["sin_rips"]: z.writestr(f"FEV_{fn}/{fn}.json", json.dumps(rips_json(fn, f, ide, items), ensure_ascii=False))
    fn, f, ide, contrato, items, total = facs[0]
    with open(rutas["una_factura.json"], "w", encoding="utf-8") as o: json.dump(rips_json(fn, f, ide, items), o)
    af, us, ac, ap, am, at = [], [], [], [], [], []
    for fn, f, ide, contrato, items, total in facs:
        if fn == esperado["sin_rips"]: continue
        d = f.strftime("%d/%m/%Y"); doc = "99999999" if fn == esperado["paciente"] else ide
        af.append(f"500000000001,ESE MORENO Y CLAVIJO,NI,900034131,{fn},{d},{d},{d},EPS037,NUEVA EMPRESA PROMOTORA DE SALUD,{contrato},,,0,0,0,{total}")
        us.append(f"CC,{doc},EPS037,2,DEMO,,PACIENTE,,40,1,F,81,794,U")
        for sv, cant in items:
            if fn == esperado["cantidad"] and sv[5] in ("Medicamentos", "Otros servicios"): cant += 1
            val = 0 if (fn == esperado["cero"] and sv[5] == "Consultas") else sv[4] * cant; dx = "" if fn == esperado["sin_dx"] else "R509"
            if sv[5] == "Consultas": ac.append(f"{fn},500000000001,CC,{doc},{d},,{sv[1]},10,13,{dx},,,,1,{val},0,{val}")
            elif sv[5] == "Procedimientos": ap.append(f"{fn},500000000001,CC,{doc},{d},,{sv[1]},1,1,,{dx},,,1,{val}")
            elif sv[5] == "Medicamentos": am.append(f"{fn},500000000001,CC,{doc},,{CUM[sv[1]]},1,{sv[3]},SOLUCION,0.9%,ML,{cant},{sv[4]},{val}")
            else: at.append(f"{fn},500000000001,CC,{doc},,1,{sv[1]},{sv[3]},{cant},{sv[4]},{val}")
    with zipfile.ZipFile(rutas["rips_txt.zip"], "w") as z:
        for pref, lineas in (("AF", af), ("US", us), ("AC", ac), ("AP", ap), ("AM", am), ("AT", at)):
            z.writestr(f"{pref}000123.txt", "\r\n".join(lineas).encode("cp1252"))
        z.writestr("CT000123.txt", f"500000000001,{date(2026, 1, 31).strftime('%d/%m/%Y')},AF000123,{len(af)}")
    return rutas, esperado

def generar(carpeta):
    os.makedirs(carpeta, exist_ok=True)
    rutas = {k: os.path.join(carpeta, k) for k in ("facturacion_ambito_demo.xlsx", "facturas_generadas_demo.xlsx", "farmacia_demo.xlsx")}
    fact = facturacion(rutas["facturacion_ambito_demo.xlsx"]); facturas_generadas(rutas["facturas_generadas_demo.xlsx"], fact); farmacia(rutas["farmacia_demo.xlsx"])
    for k in ("medicamentos_360_demo.xlsx", "rips_medicamentos_demo.xlsx", "homologacion_codigos_demo.xlsx"): rutas[k] = os.path.join(carpeta, k)
    lin = medicamentos_360(rutas["medicamentos_360_demo.xlsx"]); rips_medicamentos(rutas["rips_medicamentos_demo.xlsx"], lin); codigos(rutas["homologacion_codigos_demo.xlsx"])
    return rutas

if __name__ == "__main__":
    for r in generar(sys.argv[1] if len(sys.argv) > 1 else "datos_demo").values(): print("✔", r)

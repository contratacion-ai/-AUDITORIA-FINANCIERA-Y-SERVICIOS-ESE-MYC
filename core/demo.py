"""Datos SINTÉTICOS de demostración (no corresponden a personas ni a una institución real).

Genera archivos Excel con la plantilla estándar y los procesa con el mismo flujo de carga que usa el
personal financiero, luego ejecuta el motor. Sirve para capacitación y como prueba de integración.
"""
import io
import random
from decimal import Decimal

from django.db import transaction
from openpyxl import Workbook

from cargas.esquemas import esquema
from cargas.models import PlantillaMapeo
from cargas.procesador import procesar_carga
from modelo.catalogo_base import cargar_catalogo_base
from modelo.models import Ambito, AmbitoHomologacion, CentroCosto, ConfiguracionInforme, CuentaRecurso, Recurso, Sede
from motor.motor import ejecutar

from .models import Institucion, Periodo

ACTIVOS = {
    # código: sede
    "A10": "S01", "A18": "S01", "B01": "S01", "B02": "S01", "E01": "S02", "I01": "S01",
    "L07": "S01", "L02": "S01", "M05": "S01", "N08": "S01",
    "O02": "S01", "P01": "S01", "P03": "S01", "Q01": "S01", "Q03": "S01",
    "R01": "S01", "R04": "S01", "R05": "S01", "S01": "S01", "S04": "S01", "T01": "S01", "T07": "S01", "U01": "S01",
}
M2 = {"A10": 120, "A18": 30, "B01": 90, "B02": 140, "E01": 60, "I01": 300, "L07": 80, "L02": 50, "M05": 70, "N08": 40,
      "O02": 25, "P01": 60, "P03": 45, "Q01": 35, "Q03": 40, "R01": 30, "R04": 15, "R05": 20, "S01": 30, "S04": 35,
      "T01": 40, "T07": 25, "U01": 20}
EQUIPOS = {"A10": 6, "A18": 2, "B01": 4, "B02": 3, "E01": 3, "I01": 6, "L07": 4, "L02": 2, "M05": 2, "N08": 1, "O02": 2,
           "Q01": 4, "Q03": 3, "R01": 3, "R05": 2, "S01": 4, "S04": 6, "T01": 3, "T07": 5, "U01": 2}
PERSONAS = {"A10": 14, "A18": 3, "B01": 9, "B02": 8, "E01": 5, "I01": 16, "L07": 6, "L02": 3, "M05": 4, "N08": 4, "O02": 3,
            "P01": 5, "P03": 3, "Q01": 5, "Q03": 3, "R01": 3, "R04": 6, "R05": 2, "S01": 4, "S04": 7, "T01": 3, "T07": 3, "U01": 2}
CUPS = [
    ("890201", "CONSULTA DE PRIMERA VEZ POR MEDICINA GENERAL", "A10", 1, None),
    ("890301", "CONSULTA DE CONTROL O DE SEGUIMIENTO POR MEDICINA GENERAL", "A10", Decimal("0.8"), None),
    ("890208", "CONSULTA DE PRIMERA VEZ POR PSICOLOGIA", "A18", 1, None),
    ("890701", "CONSULTA DE URGENCIAS POR MEDICINA GENERAL", "B01", 1, None),
    ("890601", "CUIDADO (MANEJO) INTRAHOSPITALARIO POR MEDICINA GENERAL", "B02", 1, "3"),
    ("890601", "CUIDADO (MANEJO) INTRAHOSPITALARIO POR MEDICINA GENERAL", "I01", 1, "2"),
    ("890203", "CONSULTA DE PRIMERA VEZ POR ODONTOLOGIA GENERAL", "E01", 1, None),
    ("902210", "HEMOGRAMA (DEMO: SERVICIO INTERMEDIO FACTURADO)", "L07", 1, None),
]
EPS = [("EPSD01", "EPS DEMO CONTRIBUTIVO"), ("EPSD02", "EPS DEMO SUBSIDIADO"), ("EPSD03", "ASEGURADORA DEMO EVENTO")]
TARIFAS = {"890201": 42000, "890301": 36000, "890208": 38000, "890701": 52000, "890601": 95000, "890203": 30000, "902210": 9800}
CUENTAS_DEMO = [("51111701", "R14"), ("51111702", "R15"), ("51111703", "R16"), ("511149", "R21"), ("51118001", "R4"),
                ("51118002", "R5"), ("51118003", "R6"), ("51117901", "R3")]


def libro(tipo, filas):
    esq = esquema(tipo)
    wb = Workbook()
    ws = wb.active
    ws.title = tipo
    ws.append([c.etiqueta for c in esq.campos])
    for f in filas:
        ws.append([f.get(c.nombre) for c in esq.campos])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cargar(inst, usuario, tipo, filas, periodo=None):
    plantilla = PlantillaMapeo.objects.de(inst).filter(tipo_archivo=tipo, es_estandar=True).first()
    res = procesar_carga(institucion=inst, usuario=usuario, tipo_archivo=tipo, contenido=libro(tipo, filas),
                         nombre=f"DEMO_{tipo}_{periodo.etiqueta if periodo else 'catalogo'}.xlsx", periodo=periodo,
                         plantilla=plantilla)
    if res.errores:
        raise RuntimeError(f"Errores en datos demo {tipo}: {res.errores[:5]}")
    return res


def _periodo(inst, usuario, anio, mes, factor, rnd):
    p, _ = Periodo.objects.get_or_create(institucion=inst, anio=anio, mes=mes)
    f = Decimal(str(factor))

    def v(base):
        return int(Decimal(base) * f * Decimal(str(rnd.uniform(0.92, 1.08))))

    # Nómina (soporte de área de R1) por persona sintética
    nomina, total_nomina, n = [], 0, 0
    for cc, personas in PERSONAS.items():
        for _ in range(max(1, personas // 3)):
            n += 1
            valor = v(4200000)
            total_nomina += valor
            nomina.append({"documento_tercero": str(1000000000 + n), "nombre_tercero": f"EMPLEADO SINTETICO {n:03d}",
                           "centro_costo": cc, "recurso": "R1", "valor": valor})
    # Contratistas OPS sin centro → % de mano de obra
    ops = [("900000001", {"A10": 50, "B01": 50}), ("900000002", {"I01": 70, "B02": 30}), ("900000003", {"L07": 100}),
           ("900000004", {"E01": 60, "A18": 40}), ("900000005", {"S04": 100})]
    mano_obra = [{"tipo": "OPS", "documento": d, "nombre": f"CONTRATISTA SINTETICO {i}", "centro_costo": cc, "porcentaje": pct}
                 for i, (d, reparto) in enumerate(ops, 1) for cc, pct in reparto.items()]
    ef = [{"cuenta": "510101", "nombre_cuenta": "SUELDOS DEL PERSONAL", "valor": int(total_nomina * Decimal("1.03"))}]
    ef += [{"cuenta": "510106", "nombre_cuenta": "CONTRATO DIRECTO", "centro_costo": cc, "valor": v(base)}
           for cc, base in (("A10", 18000000), ("B01", 15000000), ("N08", 9000000), ("Q01", 6000000))]
    ef += [{"cuenta": "51117901", "nombre_cuenta": "HONORARIOS CONTRATO CON TERCEROS", "documento_tercero": d,
            "nombre_tercero": f"CONTRATISTA SINTETICO {i}", "valor": v(base)}
           for i, (d, base) in enumerate(zip([o[0] for o in ops], (32000000, 41000000, 22000000, 18000000, 12000000)), 1)]
    inventario = [{"centro_costo": cc, "recurso": r, "descripcion": desc, "valor": v(base)} for cc, r, desc, base in (
        ("M05", "R4", "MEDICAMENTOS DISPENSADOS", 7000000), ("I01", "R4", "MEDICAMENTOS HOSPITALIZACION", 9000000),
        ("B02", "R4", "MEDICAMENTOS OBSERVACION", 6000000), ("B01", "R5", "DISPOSITIVOS URGENCIAS", 4000000),
        ("E01", "R5", "INSUMOS ODONTOLOGIA", 3000000), ("L07", "R6", "REACTIVOS LABORATORIO", 11000000),
        ("L02", "R6", "PELICULAS Y REVELADO", 2500000))]
    for x in inventario:
        cuenta = {"R4": "51118001", "R5": "51118002", "R6": "51118003"}[x["recurso"]]
        ef.append({"cuenta": cuenta, "nombre_cuenta": x["descripcion"], "centro_costo": x["centro_costo"], "valor": x["valor"]})
    ef += [{"cuenta": "51111701", "nombre_cuenta": "ENERGIA", "sede": "S01", "valor": v(9500000)},
           {"cuenta": "51111701", "nombre_cuenta": "ENERGIA", "sede": "S02", "valor": v(2100000)},
           {"cuenta": "51111702", "nombre_cuenta": "ACUEDUCTO Y ALCANTARILLADO", "sede": "S01", "valor": v(1800000)},
           {"cuenta": "51111702", "nombre_cuenta": "ACUEDUCTO Y ALCANTARILLADO", "sede": "S02", "valor": v(400000)},
           {"cuenta": "51111703", "nombre_cuenta": "RESIDUOS HOSPITALARIOS", "sede": "S01", "valor": v(2600000)},
           {"cuenta": "511149", "nombre_cuenta": "ASEO Y CAFETERIA", "sede": "S01", "valor": v(4800000)},
           {"cuenta": "511115", "nombre_cuenta": "MANTENIMIENTO", "valor": v(14000000 if factor < 1 else 26000000)},
           {"cuenta": "511123", "nombre_cuenta": "COMUNICACIONES Y TRANSPORTE", "valor": v(7000000)},
           {"cuenta": "511114", "nombre_cuenta": "MATERIALES Y SUMINISTROS DE OFICINA", "valor": v(2200000)},
           {"cuenta": "511125", "nombre_cuenta": "SEGUROS GENERALES", "valor": v(3000000)},
           {"cuenta": "511113", "nombre_cuenta": "VIGILANCIA Y SEGURIDAD", "valor": v(5200000)},
           {"cuenta": "5120", "nombre_cuenta": "IMPUESTOS CONTRIBUCIONES Y TASAS", "valor": v(1300000)},
           {"cuenta": "5330", "nombre_cuenta": "DEPRECIACION EDIFICACIONES Y EQUIPO", "valor": v(8000000)},
           {"cuenta": "5344", "nombre_cuenta": "AMORTIZACION LICENCIAS", "valor": v(900000)},
           {"cuenta": "511190", "nombre_cuenta": "OTROS GASTOS GENERALES", "valor": v(3500000)}]
    if factor >= 1:
        ef.append({"cuenta": "580812", "nombre_cuenta": "SENTENCIAS", "centro_costo": "T01", "valor": v(9000000)})
    valores = [{"inductor": "PERSONAS", "centro_costo": cc, "cantidad": n} for cc, n in PERSONAS.items()]
    valores += [{"inductor": "RACIONES", "centro_costo": cc, "cantidad": int(n * factor)} for cc, n in (("I01", 900), ("B02", 260))]
    valores += [{"inductor": "KILOS_ROPA", "centro_costo": cc, "cantidad": int(n * factor)} for cc, n in (("I01", 700), ("B02", 220), ("B01", 90))]
    for origen, reparto in {"L07": {"A10": 900, "B01": 600, "B02": 300, "I01": 700}, "L02": {"B01": 120, "I01": 90, "A10": 60},
                            "M05": {"A10": 1300, "B02": 300, "I01": 500, "B01": 400}, "N08": {"B01": 40, "I01": 25}}.items():
        valores += [{"inductor": "SOLICITUDES", "centro_origen": origen, "centro_costo": cc, "cantidad": int(n * factor)}
                    for cc, n in reparto.items()]
    # Facturación y RIPS coherentes, con inconsistencias intencionales
    fac, rips, nfac = [], [], 0
    for cups, *_ in {c[0]: c for c in CUPS}.values():
        for eps, _nom in EPS:
            cantidad_total = int({"890201": 3400, "890301": 2600, "890208": 420, "890701": 2300, "890601": 1100,
                                  "890203": 1500, "902210": 5000}[cups] * factor * rnd.uniform(0.25, 0.4))
            for lote in range(3):
                nfac += 1
                cant = max(1, cantidad_total // 3)
                tarifa = TARIFAS[cups]
                ambito = "3" if cups in ("890701",) else "2" if cups == "890601" and lote else "3" if cups == "890601" else "1"
                fila_f = {"numero_factura": f"FE{anio}{mes:02d}{nfac:05d}", "fecha": f"15/{mes:02d}/{anio}", "eps": eps, "cups": cups,
                          "cantidad": cant, "valor_unitario": tarifa, "valor_total": tarifa * cant, "ambito": ambito,
                          "tipo_documento_paciente": "CC", "documento_paciente": str(2000000 + nfac)}
                fac.append(fila_f)
                rips.append({"tipo_registro": "AC" if cups.startswith("8902") or cups.startswith("8903") else "AP",
                             "numero_factura": fila_f["numero_factura"], "fecha_atencion": fila_f["fecha"], "eps": eps,
                             "tipo_documento_paciente": "CC", "documento_paciente": fila_f["documento_paciente"], "cups": cups,
                             "ambito": ambito, "cantidad": cant, "valor": tarifa * cant})
    fac[1]["valor_unitario"] = TARIFAS[fac[1]["cups"]] + 5000  # EPSD01 (evento): facturado distinto a la tarifa
    fac[1]["valor_total"] = fac[1]["valor_unitario"] * fac[1]["cantidad"]
    rips[5]["cantidad"] += 2
    fac.append({"numero_factura": f"FE{anio}{mes:02d}99999", "fecha": f"20/{mes:02d}/{anio}", "eps": "EPSD03", "cups": "890201",
                "cantidad": 1, "valor_unitario": 42000, "valor_total": 42000, "ambito": "DOMICILIARIA"})
    rips.append({"tipo_registro": "AP", "numero_factura": f"FE{anio}{mes:02d}88888", "eps": "EPSD01", "cups": "999999",
                 "ambito": "1", "cantidad": 1})
    _cargar(inst, usuario, "MANO_OBRA", mano_obra, p)
    _cargar(inst, usuario, "ESTADO_FINANCIERO", ef, p)
    _cargar(inst, usuario, "NOMINA", nomina, p)
    _cargar(inst, usuario, "VALORES_INDUCTOR", valores, p)
    _cargar(inst, usuario, "FACTURACION", fac, p)
    _cargar(inst, usuario, "RIPS", rips, p)
    return p


@transaction.atomic
def crear_demo(usuario=None, codigo="DEMO-001"):
    rnd = random.Random(2025)
    inst, _ = Institucion.objects.get_or_create(codigo=codigo, defaults={
        "nombre": "IPS DEMO PRIMER NIVEL (datos sintéticos)", "nit": "000000000-0", "departamento": "Demostración",
        "municipio": "Municipio Demo"})
    cargar_catalogo_base(inst, usuario=usuario)
    sedes = {"S01": Sede.objects.get_or_create(institucion=inst, codigo="S01", defaults={"nombre": "SEDE PRINCIPAL"})[0],
             "S02": Sede.objects.get_or_create(institucion=inst, codigo="S02", defaults={"nombre": "PUESTO DE SALUD RURAL"})[0]}
    for c in CentroCosto.objects.de(inst):
        c.activo = c.codigo in ACTIVOS
        c.sede = sedes.get(ACTIVOS.get(c.codigo))
        c.save()
    for cuenta, rec in CUENTAS_DEMO:
        CuentaRecurso.objects.update_or_create(institucion=inst, prefijo=cuenta, defaults={
            "recurso": Recurso.objects.get(institucion=inst, codigo=rec), "descripcion": "Cuenta auxiliar de demostración"})
    amb = Ambito.objects.get(institucion=inst, codigo="1")
    AmbitoHomologacion.objects.get_or_create(institucion=inst, valor_origen="AMBULATORIA", defaults={"ambito": amb})
    _cargar(inst, usuario, "CUPS", [{"codigo": c, "descripcion": d, "centro_costo": cc, "peso_relativo": peso, "ambito": a}
                                    for c, d, cc, peso, a in CUPS])
    valores = [{"inductor": "M2", "centro_costo": cc, "cantidad": m, "permanente": "SI"} for cc, m in M2.items()]
    valores += [{"inductor": "EQUIPOS", "centro_costo": cc, "cantidad": n, "permanente": "SI"} for cc, n in EQUIPOS.items()]
    notas = []
    for cups, tarifa in TARIFAS.items():
        notas.append({"eps": "EPSD01", "nombre_eps": EPS[0][1], "contrato": "DEMO-EV-2025", "modalidad": "EVENTO",
                      "vigencia_desde": "01/01/2024", "vigencia_hasta": "31/12/2025", "cups": cups, "tarifa": tarifa,
                      "manual_tarifario": "PROPIO"})
        notas.append({"eps": "EPSD03", "nombre_eps": EPS[2][1], "contrato": "DEMO-EV-03", "modalidad": "EVENTO",
                      "vigencia_desde": "01/01/2024", "vigencia_hasta": "31/12/2025", "cups": cups,
                      "tarifa": int(tarifa * 0.55), "manual_tarifario": "SOAT -45%"})
        notas.append({"eps": "EPSD02", "nombre_eps": EPS[1][1], "contrato": "DEMO-CAP-2025", "modalidad": "CAPITA",
                      "vigencia_desde": "01/01/2024", "vigencia_hasta": "31/12/2025", "cups": cups, "tarifa": 0,
                      "frecuencia_esperada": 150, "valor_mensual": 85000000, "poblacion": 6200})
    _cargar(inst, usuario, "NOTA_TECNICA", notas)
    p_ant = _periodo(inst, usuario, 2024, 9, 0.8, rnd)
    _cargar(inst, usuario, "VALORES_INDUCTOR", valores, p_ant)
    p_act = _periodo(inst, usuario, 2025, 9, 1.0, rnd)
    cfg, _ = ConfiguracionInforme.objects.get_or_create(institucion=inst)
    cfg.elaborado_por = "Área de Costos (demostración)"
    cfg.recursos_por_sede = "R14,R15,R16,R21"
    cfg.recomendaciones = ("P1 - INMEDIATO | Contratación | Revisar tarifas por debajo del costo con la aseguradora de evento | "
                           "Cubrir el costo unitario | 3 meses | Gerencia / Contratos")
    cfg.save()
    corridas = [ejecutar(p_ant, usuario), ejecutar(p_act, usuario)]
    return inst, corridas

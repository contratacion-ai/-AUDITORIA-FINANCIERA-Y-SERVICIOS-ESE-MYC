"""Carga de catálogos y datos del estudio. Idempotente: se puede repetir."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.models import Group, Permission
from django.db import transaction

from abc_engine import datos_estudio as D
from abc_engine.estudio import verificar_estudio

from ..models import Ambulancia, CodigoTraslado, Parametro, PerfilCostoEstudio, Sede
from .comunes import CORRECCIONES_PARAMETROS, PARAMETROS_DEFECTO, guardar_hallazgos
from .importar import guardar_costos_ceco, guardar_tarifas

DIR = Path(__file__).resolve().parent.parent / "datos_iniciales"

# codigo, nombre, municipio, origen en el Anexo 3, alias (estudio, facturación HIS, tabla 2)
SEDES = [
    ("tame", "Hospital San Antonio de Tame", "Tame", "TAME",
     ["Tame", "HOSPITAL SAN ANTONIO", "Hospital San Antonio de Tame"]),
    ("arauquita", "Hospital San Lorenzo de Arauquita", "Arauquita", "ARAUQUITA",
     ["Arauquita", "HOSPITAL SAN LORENZO", "Hospital San Lorenzo de Arauquita"]),
    ("pto-rondon", "Hospital San Juan de Dios de Puerto Rondón", "Puerto Rondón", "PUERTO RONDON",
     ["Pto Rondon", "Puerto Rondon", "HOSPITAL SAN JUAN DE DIOS", "Hospital San Juan de Dios de Pto Rondón"]),
    ("fortul", "Hospital San Francisco de Fortul", "Fortul", "FORTUL",
     ["Fortul", "HOSPITAL SAN FRANCISCO", "Hospital San Francisco de Fortul"]),
    ("la-esmeralda", "Hospital San Ricardo Pampurí (La Esmeralda)", "La Esmeralda (Arauquita)", "HOSPITAL SAN RICARDO PAMPURI",
     ["La Esmeralda", "HOSPITAL SAN RICARDO PAMPURI", "Hospital San Ricardo Pampuri"]),
    ("cravo-norte", "Hospital San José de Cravo Norte", "Cravo Norte", "CRAVO NORTE",
     ["Cravo Norte", "HOSPITAL SAN JOSE", "Hospital San José de Cravo Norte"]),
    ("pto-jordan", "C. S. Juan de Jesús Coronel (Puerto Jordán)", "Puerto Jordán (Arauquita)", "PUERTO JORDAN",
     ["Puerto Jordan", "Pto Jordan", "CENTRO DE SALUD JESUS CORONEL", "C. S. Juan de Jesús Coronel (Pto Jordán)"]),
    ("panama", "Centro de Salud Panamá de Arauca", "Panamá de Arauca (Arauquita)", "PANAMA DE ARAUCA",
     ["Panama", "CENTRO DE SALUD PANAMA DE ARAUCA", "Centro de Salud Panamá"]),
]

# Prefijo del número de factura de cada hospital (facturación HIS y RIPS de la ESE, 2026).
# Permite reconocer el hospital de cada factura en un RIPS consolidado.
PREFIJOS_FACTURA = {
    "tame": ["FHSA"], "arauquita": ["FHSL"], "pto-rondon": ["FHJD"], "fortul": ["FHSF"],
    "la-esmeralda": ["FHSR"], "cravo-norte": ["FHCN"], "pto-jordan": ["FCSJ"], "panama": ["FCSP"],
}

CODIGOS = [
    ("601T01", "Traslado asistencial básico terrestre primario", CodigoTraslado.BASE),
    ("601T02", "Traslado asistencial básico terrestre secundario", CodigoTraslado.POR_KM),
    ("S31307", "Traslado básico servicio intermunicipal mayor de 25 km hasta 300 km (cada kilómetro)", CodigoTraslado.POR_KM),
]

GRUPOS = {
    "Administrador ABC": ["add", "change", "delete", "view"],
    "Operador": ["add", "change", "view"],
    "Consulta": ["view"],
}


@transaction.atomic
def cargar(incluir_tarifas=True, incluir_ceco=True) -> dict:
    resumen = {}
    for codigo, nombre, municipio, origen, alias in SEDES:
        Sede.objects.update_or_create(
            codigo=codigo,
            defaults={"nombre": nombre, "municipio": municipio, "origen_tarifa": origen, "alias": "\n".join(alias)},
        )
    for codigo, prefijos in PREFIJOS_FACTURA.items():
        # solo si el usuario no los ha definido
        Sede.objects.filter(codigo=codigo, prefijos_factura="").update(prefijos_factura="\n".join(prefijos))
    sedes = {}
    for s in Sede.objects.all():
        for n in s.nombres():
            sedes[n.upper()] = s
    resumen["sedes"] = Sede.objects.count()

    for clave, (valor, unidad, desc, fuente, supuesto) in PARAMETROS_DEFECTO.items():
        p, creado = Parametro.objects.get_or_create(
            clave=clave,
            defaults={"valor": Decimal(str(valor)), "unidad": unidad, "descripcion": desc, "fuente": fuente, "es_supuesto": supuesto},
        )
        # Actualización del programa: aplica la corrección del modelo ABC solo si el
        # parámetro conserva el valor anterior por defecto (nadie lo cambió a mano).
        anterior_nuevo = CORRECCIONES_PARAMETROS.get(clave)
        if not creado and anterior_nuevo and p.valor == anterior_nuevo[0]:
            p.valor, p.descripcion, p.fuente = anterior_nuevo[1], desc, fuente
            p.version += 1
            p.save()
    # Parámetro de la versión anterior que el modelo corregido reemplazó.
    Parametro.objects.filter(clave="tripulacion_dia_tabla_th").delete()
    resumen["parametros"] = Parametro.objects.count()

    for codigo, desc, modalidad in CODIGOS:
        CodigoTraslado.objects.get_or_create(codigo=codigo, defaults={"descripcion": desc, "modalidad": modalidad})

    fecha = dt.date(2026, 10, 14)
    for placa, sede, comb, km, h, c, m, total, ckm, ch, excl in D.ANEXO_A:
        h = D.CORRECCIONES_ANEXO_A.get(placa, {}).get("horas_dia", h)  # corrección del modelo ABC
        amb, _ = Ambulancia.objects.update_or_create(
            placa=placa,
            defaults={
                "sede": sedes[sede.upper()],
                "tipo_combustible": comb,
                "excluida_promedios": excl,
                "motivo_exclusion": "Solo 3 desplazamientos y 416 km en 55 días (num. 5.6 del informe)" if excl else "",
            },
        )
        PerfilCostoEstudio.objects.update_or_create(
            ambulancia=amb,
            estudio=D.FUENTE,
            defaults={
                "fecha_estudio": fecha,
                "km_dia": Decimal(str(km)),
                "horas_dia": Decimal(str(round(h, 4))),
                "combustible_dia": c,
                "mantenimiento_dia": m,
                "tripulacion_dia": round(D.TRIPULACION_DIA_MODELO, 2),
                "total_dia_reportado": total,
                "costo_km_reportado": ckm,
                "costo_hora_reportado": ch,
            },
        )
    for placa in D.AMBULANCIAS_NUEVAS_SIN_HISTORIAL:
        Ambulancia.objects.get_or_create(
            placa=placa,
            defaults={
                "sede": sedes["TAME"],
                "tipo_combustible": Ambulancia.ACPM,
                "descripcion": "Ford Ranger recibida en octubre de 2026",
                "estado": Ambulancia.NUEVA,
                "incluir_en_costeo": False,
                "excluida_promedios": True,
                "motivo_exclusion": "Sin historial de operación (num. 2 del informe). Sede y combustible por confirmar.",
            },
        )
    resumen["ambulancias"] = Ambulancia.objects.count()

    hallazgos = [
        {
            "fuente": D.FUENTE,
            "ubicacion": h["ubicacion"],
            "tipo": "ESTUDIO",
            "descripcion": f"{h['indicador']}: reportado {h['reportado']:,}, recalculado con el Anexo A {h['calculado']:,} "
            f"(diferencia {h['diferencia']:,}).",
            "severidad": "MEDIA" if "Rendimiento" in h["indicador"] else "BAJA",
        }
        for h in verificar_estudio()
    ]
    hallazgos += [
        {
            "fuente": D.FUENTE,
            "ubicacion": "Anexo KSM548-KSM552",
            "tipo": "SUPUESTO",
            "descripcion": "Las cinco Ford Ranger nuevas se registran en Tame con ACPM y sin costeo hasta que se "
            "confirme su sede, combustible y perfil de costos.",
            "severidad": "MEDIA",
        },
        {
            "fuente": D.FUENTE,
            "ubicacion": "Num. 4.1 / Anexo 3",
            "tipo": "SUPUESTO",
            "descripcion": "Los km del Anexo 3 se interpretan como un solo sentido; para el costeo se multiplican por "
            "el parámetro factor_ida_vuelta (2). Confirmar con facturación.",
            "severidad": "ALTA",
        },
        {
            "fuente": D.FUENTE,
            "ubicacion": "Figura 2 vs Tabla 2",
            "tipo": "INCONSISTENCIA",
            "descripcion": "La figura 2 muestra un factor de ×3,5 para Puerto Jordán; la tabla 2 reporta 3,15 (ACPM).",
            "severidad": "BAJA",
        },
    ]
    resumen["hallazgos_estudio"] = guardar_hallazgos(hallazgos)

    if incluir_tarifas:
        datos = json.loads((DIR / "tarifas_anexo3_2026.json").read_text(encoding="utf-8"))
        lote = guardar_tarifas(datos, archivo="ANEXOS DE TARIFAS 2026.xlsx (datos iniciales)")
        resumen["tarifas"] = lote.nuevos + lote.actualizados
    if incluir_ceco:
        datos = json.loads((DIR / "costos_ambulancias_ceco.json").read_text(encoding="utf-8"))
        lote = guardar_costos_ceco(datos, archivo="COSTOS AMBULANCIAS.xlsx (modelo ABC corregido, datos iniciales)",
                                   forzar_parametros=False)
        resumen["partidas_ceco"] = lote.nuevos + lote.actualizados

    for nombre, acciones in GRUPOS.items():
        grupo, _ = Group.objects.get_or_create(name=nombre)
        modelos = ["traslado", "consumo"] if nombre == "Operador" else None
        perms = Permission.objects.filter(content_type__app_label="flota", codename__regex=r"^(%s)_" % "|".join(acciones))
        if modelos:
            perms = perms.filter(content_type__model__in=modelos) | Permission.objects.filter(
                content_type__app_label="flota", codename__startswith="view_"
            )
        grupo.permissions.set(perms)
    return resumen

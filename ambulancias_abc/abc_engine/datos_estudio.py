"""Datos transcritos del Informe Técnico de costo de operación de ambulancias.

Fuente: "INFORME TÉCNICO - COSTO DIARIO DE OPERACIÓN DE LA FLOTA DE
AMBULANCIAS", Ing. Edwin Seudiel Romero Angarita, Tame, 14/10/2026.

* ANEXO_A: Tabla 5 (Anexo A, pág. 11), valores tal como aparecen.
* COMBUSTIBLE_SEDE: Tabla 2 (págs. 4-5).
* REPORTADO: valores de las tablas 3 y 4 y del texto, para validar que el
  motor reproduce el estudio.
"""

FUENTE = "Informe Técnico Costo Operación Ambulancias (14/10/2026)"

PRECIO_GASOLINA = 23650
PRECIO_ACPM = 18500
TRIPULACION_DIA = 458467  # 13.754.000 / 30 (num. 4.4)
TRIPULACION_MES = 13754000
SALARIOS_TRIPULACION = {"CONDUCTOR": 3500000, "AUXILIAR DE ENFERMERIA": 2754000, "MEDICO": 7500000}
DIAS_RECORRIDO = 55  # 19/08 - 12/10/2026
DIAS_COMBUSTIBLE = 27  # 04 - 30/09/2026
DIAS_MANTENIMIENTO = 283  # 01/01 - 10/10/2026
VELOCIDAD_FLOTA_KMH = 34.6
KM_VEREDA_IDA_VUELTA = 30  # num. 4.1: traslados a veredas valorados en 30 km ida y vuelta

# placa, sede, combustible, km/día, h/día, combustible/día, mantenimiento/día,
# total/día, $/km, $/hora oper., excluida de mínimos y promedios
ANEXO_A = [
    ("OEU846", "Tame", "GASOLINA", 281.3, 7.1, 700215, 294137, 1452819, 5165, 206002, False),
    ("OYX013", "Pto Rondon", "GASOLINA", 205.4, 4.7, 470337, 340059, 1268863, 6177, 269634, False),
    ("ODS688", "Arauquita", "GASOLINA", 179.8, 5.6, 480445, 227692, 1166604, 6489, 208889, False),
    ("OYX018", "Cravo Norte", "GASOLINA", 39.4, 1.1, 116943, 585978, 1161387, 29490, 1019489, False),
    ("OKZ365", "Tame", "ACPM", 209.6, 6.1, 206551, 458376, 1123393, 5360, 182813, False),
    ("LXS630", "Tame", "ACPM", 202.8, 4.9, 197118, 399350, 1054934, 5203, 216124, False),
    ("OYX015", "Arauquita", "ACPM", 180.1, 5.4, 346369, 168857, 973693, 5406, 178869, False),
    ("OLM779", "Pto Rondon", "ACPM", 115.2, 3.2, 235985, 222914, 917365, 7962, 290210, False),
    ("OJZ193", "Fortul", "GASOLINA", 121.8, 5.1, 325261, 72845, 856573, 7034, 167668, False),
    ("OEU843", "La Esmeralda", "GASOLINA", 109.8, 4.4, 271274, 66002, 795743, 7246, 182886, False),
    ("OLM788", "La Esmeralda", "ACPM", 131.8, 4.4, 193647, 97757, 749871, 5689, 169964, False),
    ("OKZ367", "Puerto Jordan", "ACPM", 17.5, 0.5, 43490, 186557, 688513, 39360, 1360680, False),
    ("OEU845", "Cravo Norte", "GASOLINA", 74.3, 2.3, 79091, 132778, 670336, 9019, 293485, False),
    ("OKZ368", "Panama", "ACPM", 136.3, 4.0, 132583, 78223, 669273, 4909, 166520, False),
    ("OLM775", "Puerto Jordan", "ACPM", 114.5, 2.4, 119584, 86920, 664971, 5806, 279536, False),
    ("OYX014", "Tame", "ACPM", 164.5, 4.8, 125360, 79262, 663088, 4031, 139165, False),
    ("KSM545", "Cravo Norte", "GASOLINA", 37.7, 1.1, 52008, 140275, 650751, 17257, 596582, False),
    ("OEU828", "Cravo Norte", "GASOLINA", 48.6, 1.4, 44517, 134291, 637275, 13113, 453306, False),
    ("OKZ362", "Arauquita", "ACPM", 19.8, 0.6, 7275, 141588, 607330, 30707, 1061547, False),
    ("OYX016", "Fortul", "ACPM", 61.5, 3.0, 75179, 15111, 548756, 8919, 183198, False),
    ("OKZ364", "Fortul", "ACPM", 7.6, 0.2, 5653, 30392, 494512, 65380, 2260201, True),
]

# Ford Ranger recibidas en octubre de 2026: sin historial, excluidas del estudio (num. 2).
AMBULANCIAS_NUEVAS_SIN_HISTORIAL = ["KSM548", "KSM549", "KSM550", "KSM551", "KSM552"]

# sede, galones gasolina, galones ACPM, valor, factor gasolina, factor ACPM
COMBUSTIBLE_SEDE = [
    ("Hospital San Antonio de Tame", 799.40, 944.26, 36374620, 1.95, 1.82),
    ("Hospital San Lorenzo de Arauquita", 548.50, 688.50, 25709275, 2.05, 2.82),
    ("Hospital San Juan de Dios de Pto Rondón", 536.96, 344.41, 19070689, 1.66, 2.71),
    ("Hospital San Francisco de Fortul", 410.69, 223.97, 13856264, 2.47, 2.35),
    ("Hospital San Ricardo Pampuri", 309.70, 282.62, 12552875, 1.77, 2.36),
    ("Hospital San José de Cravo Norte", 334.00, 0.00, 7899100, 1.42, None),
    ("C. S. Juan de Jesús Coronel (Pto Jordán)", 28.00, 238.00, 5065200, None, 3.15),
    ("Sede administrativa (Subgerencia)", 12.00, 246.00, 4834800, None, None),
    ("Centro de Salud Panamá", 19.00, 193.50, 4029100, None, 1.62),
]
COMBUSTIBLE_TOTAL = {"gasolina": 2998.25, "acpm": 3161.26, "valor": 129391923, "valor_dia": 4792293}

# Valores reportados en el texto y en las tablas 3 y 4 (sin OKZ364).
REPORTADO = {
    "promedio_km_dia": 122.6,
    "promedio_gal_dia": 9.9,
    "promedio_rendimiento": 14.9,
    "promedio_combustible_dia": 211162,
    "promedio_mantenimiento_dia": 196449,
    "promedio_total_dia": 866077,
    "promedio_costo_km": 11217,
    "promedio_horas_dia": 3.6,
    "promedio_costo_hora_operacion": 381328,
    "promedio_costo_hora_disponibilidad": 36087,
    "costo_hora_ponderado": 240571,
    "flota_total_dia": 17816048,
    "flota_total_mes": 534500000,
    "gasolina_total_dia": 962261,
    "acpm_total_dia": 787381,
    "gasolina_combustible_km": 2313,
    "acpm_combustible_km": 1243,
    "gasolina_costo_hora_ponderado": 264658,
    "acpm_costo_hora_ponderado": 220504,
    "rendimiento_min": (8.4, "OJZ193"),
    "rendimiento_max": (24.3, "OKZ365"),
    "combustible_asignado_ambulancias_mes": 114200000,
}

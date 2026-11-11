"""Rutas, constantes y parámetros por defecto.

Todo lo que el área administrativa puede necesitar ajustar sin tocar código vive en
la tabla ``parametros`` de la base de datos (página "Configuración" de la aplicación);
aquí solo están los valores iniciales.
"""
from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
# La carpeta de datos se puede mover (p. ej. a una unidad de red con copia de seguridad)
# definiendo la variable de entorno ESE_CONTROL_DATOS.
DIR_DATOS = Path(os.environ.get("ESE_CONTROL_DATOS", RAIZ / "datos"))
RUTA_BD = DIR_DATOS / "control_ese.db"
DIR_ARCHIVOS = DIR_DATOS / "archivos_cargados"
DIR_CATALOGO_INICIAL = RAIZ / "catalogo_inicial"

ENTIDAD = "ESE Moreno y Clavijo"

# Ítems controlados (alcance acotado: no hay nómina, facturación ni otros procesos).
SERVICIOS = "servicios_publicos"
COMBUSTIBLE = "combustible"
MANTENIMIENTO = "mantenimiento"
ITEMS = {
    SERVICIOS: "Servicios públicos",
    COMBUSTIBLE: "Combustible",
    MANTENIMIENTO: "Mantenimientos",
}
# Fuente complementaria del ítem combustible (no es gasto: son kilómetros recorridos).
DESPLAZAMIENTOS = "desplazamientos"

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
MESES_ABREV = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

GALON_EN_LITROS = 3.785

# Parámetros de validación y de alertas (clave, valor inicial, descripción).
PARAMETROS_DEFECTO = [
    ("precio_galon_min", 6000, "Precio por galón mínimo plausible ($). Por debajo se marca el registro."),
    ("precio_galon_max", 32000, "Precio por galón máximo plausible ($). Por encima se marca el registro."),
    ("galones_max_tanqueo", 150, "Galones máximos plausibles en un solo tanqueo o suministro."),
    ("km_max_desplazamiento", 1500, "Kilómetros máximos plausibles en un solo desplazamiento."),
    ("alerta_variacion_pct", 30, "Variación % frente al mes anterior que genera alerta."),
    ("alerta_variacion_min_valor", 300000, "Cambio mínimo en pesos para que una variación genere alerta."),
    ("alerta_z_robusto", 3.5, "Umbral de puntuación z robusta (mediana/MAD) para valor atípico."),
    ("alerta_meses_historia", 4, "Meses previos mínimos para evaluar valores atípicos contra el historial."),
    ("alerta_mora_factor", 1.5, "Total a pagar ÷ valor facturado del mes a partir del cual se alerta saldo en mora."),
    ("alerta_rendimiento_pct", 70, "% del rendimiento esperado (km/gal) por debajo del cual se alerta."),
    ("alerta_costo_unitario_pct", 40, "Desviación % del costo unitario frente a la mediana que genera alerta."),
    ("dias_cobertura_minima", 25, "Días mínimos con registros para considerar completo un periodo de combustible."),
]

# Paleta de gráficos (validada para daltonismo en orden fijo; no reordenar).
COLOR_SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
COLOR_ITEM = {SERVICIOS: "#2a78d6", COMBUSTIBLE: "#eb6834", MANTENIMIENTO: "#1baf7a"}
COLOR_SEVERIDAD = {"Alta": "#c62f2f", "Media": "#b86e00", "Baja": "#5b6470"}
ICONO_SEVERIDAD = {"Alta": "▲", "Media": "◆", "Baja": "●"}

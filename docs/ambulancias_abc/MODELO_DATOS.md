# Modelo de costeo ABC y modelo de datos

Fuente principal: *Informe Técnico — Costo diario de operación de la flota de ambulancias* (Ing. Edwin Seudiel Romero Angarita, Tame, 14/10/2026). Fuentes complementarias: `ANEXOS DE TARIFAS 2026.xlsx` (hoja *TRASLADOS Anexo 3*), `COSTOS AMBULANCIAS.xlsx` y el reporte de facturación de traslados del HIS.

## 1. Lo que se extrajo del estudio

### Entidades

| Entidad del estudio | Dónde aparece | Entidad en la aplicación |
|---|---|---|
| Ambulancia (placa, sede, combustible) | Anexo A, figuras 1-4 | `Ambulancia` |
| Sede / hospital | Tabla 2, Anexo A | `Sede` (con alias para cruzar nombres de archivos) |
| Perfil diario (km/día, h/día, combustible/día, mantenimiento/día) | Anexo A (tabla 5) | `PerfilCostoEstudio` |
| Tripulación (conductor, auxiliar, médico) | Num. 4.4 | `Parametro tripulacion_dia` |
| Precio de combustible | Num. 4.2 | `Parametro precio_gasolina`, `precio_acpm` |
| Velocidad efectiva de la flota | Num. 4.5 | `Parametro velocidad_flota_kmh` |
| Unidades excluidas (OKZ364; Ford Ranger KSM548-552) | Num. 2 y 5.6 | `Ambulancia.excluida_promedios`, `estado=NUEVA_SIN_HISTORIAL` |
| Combustible certificado por sede | Tabla 2 | Validación del estudio (`abc_engine/estudio.py`) |

### Recursos y su valor

| Recurso | Valor | Base |
|---|---|---|
| Combustible | $/día por placa (Anexo A) | Galones certificados ajustados por factor de sede × precio de contrato ($23.650 gasolina, $18.500 ACPM) |
| Mantenimiento | $/día por placa (Anexo A) | Repuestos y mano de obra 01/01–10/10/2026 ÷ 283 días |
| Tripulación | $458.467 por ambulancia/día | (3.500.000 + 2.754.000 + 7.500.000) ÷ 30 |

`C_total/día = C_combustible + C_mantenimiento + C_tripulación` (num. 4).

### Actividades y drivers (costeo ABC)

| Actividad | Recurso | Driver | Cómo se mide el driver de un traslado |
|---|---|---|---|
| Desplazamiento | Combustible | **Galones** | km conducidos ÷ rendimiento de la ambulancia (km/día ÷ galones/día) |
| Desgaste del vehículo | Mantenimiento | **Kilómetros** | km conducidos |
| Atención en misión | Tripulación | **Horas en misión** | km conducidos ÷ velocidad efectiva (km/día ÷ h/día; 34,6 km/h si no hay perfil) |
| Atención clínica | Insumos (consumos reales) | Número de traslados | 1 por traslado |
| Soporte / otros | Otros consumos y partidas indirectas incluidas | Número de traslados | 1 por traslado |

**Km conducidos de una línea facturada** (`flota/servicios/importar.py`):
- Si se factura por kilómetro (valor unitario = $17.000 o código por km): `cantidad × factor_ida_vuelta`.
- Si es tarifa básica ($459.300): `km_traslado_basico × cantidad`.
- Si en el mismo ingreso hay tarifa básica y cobro por km, la línea básica queda con 0 km (el recorrido ya está en la línea por km) y se marca para revisión.

### Métodos de asignación

1. **Absorción real** (`REAL`): por ambulancia y mes, `tasa = costo del recurso ÷ suma del driver de sus traslados`. Todo el costo del mes queda en los traslados; una ambulancia sin traslados deja su costo como *capacidad no utilizada*.
2. **Capacidad** (`CAPACIDAD`, tipo *time-driven ABC*): tasas estándar del estudio: combustible a precio por galón, mantenimiento a $/km, tripulación a costo de **disponibilidad** (C_tripulación ÷ 24 h) por hora en misión. Lo no absorbido es capacidad ociosa (o sobreuso si sale negativo).

En ambos se cumple, y lo verifican las pruebas:

```
Σ costo asignado a traslados + Σ capacidad no utilizada = costo total de la flota (+ indirectos incluidos)
```

### Costo del periodo por ambulancia (pool)

Para cada ambulancia y mes, y para cada recurso:
- Si hay **consumos registrados** de ese tipo en el mes → se usa el valor real (`fuente = REAL`).
- Si no → estándar del estudio: `costo/día × días calendario del mes` (`fuente = ESTANDAR`).
- Tripulación: `tripulacion_dia × días` (o consumos tipo TRIPULACION si se registran ajustes).
- Ambulancias con `incluir_en_costeo = False` solo aportan sus consumos reales.

### Traslados sin placa

La facturación del HIS no trae la placa. Hasta que se asigne (pantalla *Traslados*), el traslado se reparte entre las ambulancias de su sede en proporción a su km/día del estudio. El reparto queda registrado (`reparto_placas` en la API).

### Costo estándar por recorrido (matriz de rentabilidad)

Para una ruta del Anexo 3 de `km` kilómetros y una ambulancia:

```
km conducidos = km × factor_ida_vuelta
galones       = km conducidos ÷ rendimiento
horas         = km conducidos ÷ velocidad efectiva
combustible   = galones × precio
mantenimiento = km conducidos × mantenimiento/día ÷ km/día
tripulación   = horas × tripulación/día ÷ horas/día        (REAL)
              = horas × tripulación/día ÷ 24               (CAPACIDAD)
margen        = tarifa 2026 − costo ;  rentabilidad = margen ÷ tarifa
```

Con `REAL`, `costo ÷ km conducidos` es exactamente el $/km del Anexo A.

## 2. Modelo de datos

```
Sede 1───* Ambulancia 1───* PerfilCostoEstudio
  │            │  1
  │            └──────* Consumo  (combustible, mantenimiento, lubricantes, llantas, insumos, tripulación, otros)
  └──* Traslado *──1 TarifaRuta (Anexo 3)          CodigoTraslado (601T01 base, 601T02 / S31307 por km)
          *──1 LoteImportacion 1──* HallazgoCalidad
Parametro · PartidaCostoIndirecto · RegistroAuditoria · ClaveAPI
```

| Modelo | Llave natural / única | Notas |
|---|---|---|
| `Sede` | `codigo` | `origen_tarifa` enlaza con el ORIGEN del Anexo 3; `alias` con los nombres del HIS y del estudio; `codigo_prestador` con RIPS |
| `Ambulancia` | `placa` | estado, combustible, exclusión de promedios |
| `PerfilCostoEstudio` | (`ambulancia`, `estudio`) | permite cargar estudios futuros sin borrar el anterior (`vigente`) |
| `TarifaRuta` | (`vigencia`, `codigo` = `origen-destino`) | km, valor km, tarifa, fila de origen en el Excel |
| `Traslado` | `llave_externa` | HIS: `HIS|prefactura|ingreso|cups|fecha|cantidad|valor|n`; RIPS: `RIPS|factura|hash|cups|tipo|consecutivo`; manual: `MANUAL|uuid` |
| `Consumo` | — (CSV: placa+fecha+tipo+documento) | odómetro, documento soporte, proveedor, usuario que registró |
| `Parametro` | `clave` | `es_supuesto` marca valores que el estudio no define |
| `PartidaCostoIndirecto` | (`categoria`, `concepto`) | `incluir_en_abc` en falso por defecto |
| `HallazgoCalidad` | (`fuente`, `ubicacion`, `tipo`, `descripcion`) | celda o fila exacta del archivo fuente |
| `RegistroAuditoria` | — | quién, cuándo y qué cambió |

Los modelos editables (`Ambulancia`, `Parametro`, `Traslado`, `Consumo`) llevan `version`, `creado_por`, `actualizado_por` y fechas (bloqueo optimista y trazabilidad).

## 3. Parámetros y supuestos

| Clave | Valor inicial | Fuente | ¿Supuesto? |
|---|---|---|---|
| `precio_gasolina` | 23.650 | Informe num. 4.2 | No |
| `precio_acpm` | 18.500 | Informe num. 4.2 | No |
| `tripulacion_dia` | 458.467 | Informe num. 4.4 | No |
| `tripulacion_dia_tabla_th` | 504.073,30 | COSTOS AMBULANCIAS, tabla TH (solo contraste) | No |
| `velocidad_flota_kmh` | 34,6 | Informe num. 4.5 | No |
| `valor_km_tarifa` | 17.000 | Anexo 3, P8 | No |
| `tarifa_traslado_basico` | 459.300 | Anexo 3, P9 | No |
| `factor_ida_vuelta` | 2 | El anexo factura un sentido; la ambulancia regresa | **Sí** |
| `km_traslado_basico` | 30 | Analogía con num. 4.1 (veredas: 30 km ida y vuelta) | **Sí** |
| `tolerancia_km_ruta` | 0 | Configuración | **Sí** |

Los traslados mensuales, la facturación real y los consumos **no** vienen del estudio: se importan (HIS, RIPS, CSV) o se registran en la aplicación.

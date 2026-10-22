# Caso de prueba pequeño (validación de cálculos)

Implementado en `tests/conftest.py` y verificado en `tests/test_motor.py::test_caso_pequeno_cuatro_distribuciones`.

## Datos

Centros: **E1** Gerencia (estructura) · **S1** Aseo (servicios) · **I1** Laboratorio (intermedio) · **F1** Consulta, **F2** Urgencias (finales).

| Movimiento | Recurso | Centro | Valor |
|---|---|---|---|
| Sueldos | R1 | F1 | 1.000 |
| Sueldos | R1 | E1 | 600 |
| Energía | R14 | — (regla: m²) | 400 |
| Reactivos | R6 | I1 | 300 |
| Aseo | R21 | S1 | 200 |
| **Total** | | | **2.500** |

Inductores: m² E1 10, S1 10, I1 20, F1 30, F2 30 · personas S1 2, I1 2, F1 4, F2 2 · solicitudes al laboratorio F1 1, F2 2.

## Cálculo a mano

| Paso | Cálculo | E1 | S1 | I1 | F1 | F2 |
|---|---|---|---|---|---|---|
| 1ª directo | | 600 | 200 | 300 | 1.000 | 0 |
| 1ª energía (tasa 400/100 m² = 4) | 4 × m² | 40 | 40 | 80 | 120 | 120 |
| Acumulado 1ª | | 640 | 240 | 380 | 1.120 | 120 |
| 2ª E1 por personas (tasa 640/10 = 64) | 64 × personas | −640 | 128 | 128 | 256 | 128 |
| 3ª S1 (368) por m² (tasa 368/80 = 4,6) | 4,6 × m² | | −368 | 92 | 138 | 138 |
| 4ª I1 (600) por solicitudes (tasa 200) | 200 × solicitudes | | | −600 | 200 | 400 |
| **Costo final** | | 0 | 0 | 0 | **1.714** | **786** |

Conservación: 1.714 + 786 = 2.500 ✔

## Costeo CUPS

F1 produce 890201 × 100 (peso 1) y 890301 × 50 (peso 2) → 200 unidades ponderadas, 8,57 por unidad:
890201 = 857 (unitario **8,57**), 890301 = 857 (unitario **17,14**). F2 produce 890701 × 10 → 786 (unitario **78,60**).

Pruebas adicionales: conciliación nómina (900) vs. estado financiero (1.000) → F1 666,67 y F2 333,33;
contratista OPS 60/40; regla de excepción del periodo (N4) que prevalece sobre la general; pendiente cuando falta la regla
de la 4ª distribución (600 quedan en I1, la corrida sigue cuadrando).

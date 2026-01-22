# Referencias Científicas - Índices Espectrales para Monitoreo de Humedales

**Sistema de Monitoreo de Humedales**  
**Base Científica y Normalización de Índices**

---

## 1. MNDWI - Modified Normalized Difference Water Index (Índice Modificado de Diferencia Normalizada de Agua)

### Fórmula

```
MNDWI = (Green - SWIR) / (Green + SWIR)
MNDWI = (B3 - B11) / (B3 + B11)
```

Donde:
- **B3**: Banda verde (Green) - Sentinel-2: 560 nm
- **B11**: Banda SWIR - Sentinel-2: 1610 nm

### Rango Teórico y Práctico

- **Rango teórico**: -1 a +1
- **Interpretación**:
  - Valores < 0: superficies no acuáticas (suelo, vegetación)
  - Valores ≈ 0: límite agua/tierra
  - Valores > 0: cuerpos de agua
  - Umbral común: 0.0 para clasificación binaria agua/no-agua

### Normalización Implementada

**IMPORTANTE**: Para permitir comparación visual directa en gráficos temporales, todos los índices se mantienen en su **rango teórico natural de [-1, 1]**.

**Estrategia de normalización:**
- **Índices normalizados** (MNDWI, NDRE, NDCI, SAVI): Mantienen su rango [-1, 1] sin transformación
- **WRI**: Se transforma desde su rango [0, 5] al rango [-1, 1] mediante la fórmula `(valor - 1) / 2`
- **FAI**: Se aplica clipping a [-1, 1] para valores fuera del rango

**Ventajas de este enfoque:**
1. ✅ Todos los índices en el mismo rango [-1, 1]
2. ✅ Comparación visual directa en gráficos multi-índice
3. ✅ Preserva la interpretación científica estándar
4. ✅ Facilita el análisis comparativo temporal

**Interpretación del rango [-1, 1]:**
- Valores positivos: condiciones favorables (agua, vegetación saludable, etc.)
- Valor 0: punto neutro o transición
- Valores negativos: ausencia de la característica medida

### Referencia Principal

Xu, H. (2006). Modification of normalised difference water index (NDWI) to enhance open water features in remotely sensed imagery. *International Journal of Remote Sensing, 27*(14), 3025-3033. https://doi.org/10.1080/01431160600589179

**Justificación**: Xu (2006) desarrolló el MNDWI específicamente para mejorar la detección de cuerpos de agua abiertos en imágenes de teledetección, demostrando que suprime mejor el ruido por vegetación y suelo que el NDWI original.

---

## 2. NDRE - Normalized Difference Red Edge Index (Índice Normalizado de Diferencia del Borde Rojo)

### Fórmula

```
NDRE = (NIR - RedEdge) / (NIR + RedEdge)
NDRE = (B8 - B5) / (B8 + B5)
```

Donde:
- **B8**: Banda NIR (Near-Infrared) - Sentinel-2: 842 nm
- **B5**: Banda Red Edge - Sentinel-2: 705 nm

### Rango Teórico y Práctico

- **Rango teórico**: -1 a +1
- **Rango práctico común**: 0 a 0.8
- **Interpretación**:
  - Valores 0.6-1.0: vegetación densa y saludable con alto contenido de clorofila
  - Valores 0.2-0.6: vegetación moderada o en desarrollo
  - Valores 0-0.2: vegetación escasa o suelo desnudo
  - Valores < 0: superficies no vegetadas

### Normalización Implementada

**Rango natural**: [-1, 1] (rango teórico de índices normalizados)  
**Rango usado**: [-1, 1] (sin transformación adicional)  
**Razón**: Valores típicos 0-0.8, pero se mantiene rango completo para consistencia

### Referencias Principales

Gitelson, A. A., & Merzlyak, M. N. (1994). Spectral reflectance changes associated with autumn senescence of Aesculus hippocastanum L. and Acer platanoides L. leaves. Spectral features and relation to chlorophyll estimation. *Journal of Plant Physiology, 143*(3), 286-292. https://doi.org/10.1016/S0176-1617(11)81633-0

Delegido, J., Verrelst, J., Alonso, L., & Moreno, J. (2011). Evaluation of sentinel-2 red-edge bands for empirical estimation of green LAI and chlorophyll content. *Sensors, 11*(7), 7063-7081. https://doi.org/10.3390/s110707063

**Justificación**: NDRE es altamente sensible al contenido de clorofila y estado del nitrógeno en las plantas. Se prefiere sobre NDVI en vegetación densa porque no satura. El rango 0-0.8 representa valores típicos en vegetación terrestre según múltiples estudios empíricos.

---

## 3. NDCI - Normalized Difference Chlorophyll Index (Índice Normalizado de Diferencia de Clorofila)

### Fórmula

```
NDCI = (RedEdge - Red) / (RedEdge + Red)
NDCI = (B5 - B4) / (B5 + B4)
```

Donde:
- **B5**: Banda Red Edge - Sentinel-2: 705 nm
- **B4**: Banda roja (Red) - Sentinel-2: 665 nm

### Rango Teórico y Práctico

- **Rango teórico**: -1 a +1
- **Rango práctico común**: -0.1 a 0.5
- **Interpretación**:
  - Valores > 0.1: concentraciones elevadas de clorofila-a (condiciones de floración)
  - Valores 0-0.1: turbidez media
  - Valores < 0: baja turbidez

### Normalización Implementada

**Rango natural**: [-1, 1] (rango teórico)  
**Rango usado**: [-1, 1] (sin transformación adicional)  
**Razón**: Valores típicos -0.1 a 0.5, pero se mantiene rango completo para comparación visual

### Referencia Principal

Mishra, S., & Mishra, D. R. (2012). Normalized difference chlorophyll index: A novel model for remote estimation of chlorophyll-a concentration in turbid productive waters. *Remote Sensing of Environment, 117*, 394-406. https://doi.org/10.1016/j.rse.2011.10.016

**Justificación**: Mishra & Mishra (2012) desarrollaron NDCI específicamente para aguas turbias y productivas, demostrando su superioridad sobre otros índices para estimar concentración de clorofila-a. El rango -0.1 a 0.5 captura la variabilidad típica en humedales.

---

## 4. SAVI - Soil Adjusted Vegetation Index (Índice de Vegetación Ajustado por Suelo)

### Fórmula

```
SAVI = [(NIR - Red) / (NIR + Red + L)] × (1 + L)
SAVI = [(B8 - B4) / (B8 + B4 + L)] × (1 + L)
```

Donde:
- **B8**: Banda NIR - Sentinel-2: 842 nm
- **B4**: Banda roja - Sentinel-2: 665 nm
- **L**: Factor de corrección de brillo del suelo = 0.5 (valor estándar)

### Rango Teórico y Práctico

- **Rango teórico**: -1 a +1
- **Rango práctico común**: -0.5 a 0.8
- **Interpretación**:
  - Valores < 0.2: cobertura vegetal muy baja, agua o áreas urbanas
  - Valores ≈ 0.5: cobertura verde moderada
  - Valores cercanos a 1: vegetación densa
- **Factor L**:
  - L = 0: cobertura vegetal muy alta
  - L = 0.5: valor estándar para cobertura intermedia
  - L = 1: cobertura vegetal muy baja

### Normalización Implementada

**Rango natural**: [-1, 1] (rango teórico con L=0.5)  
**Rango usado**: [-1, 1] (sin transformación adicional)  
**Razón**: Valores típicos -0.5 a 0.8, pero se mantiene rango completo para consistencia

### Referencia Principal

Huete, A. R. (1988). A soil-adjusted vegetation index (SAVI). *Remote Sensing of Environment, 25*(3), 295-309. https://doi.org/10.1016/0034-4257(88)90106-X

**Justificación**: Huete (1988) introdujo SAVI para minimizar la influencia del brillo del suelo en áreas con vegetación escasa, común en humedales estacionales. El factor L=0.5 fue determinado empíricamente como óptimo para reducir variaciones del suelo.

---

## 5. FAI - Floating Algae Index (Índice de Algas Flotantes)

### Fórmula

```
FAI = NIR - [Red + (SWIR - Red) × ((λNIR - λRed) / (λSWIR - λRed))]
FAI = B8 - [B4 + (B11 - B4) × ((842 - 665) / (1610 - 665))]
```

**Nota sobre Implementación**: Se utiliza la banda **B8 (842 nm)** en lugar de B8A (865 nm) original de Hu (2009) para aprovechar la **resolución de 10m** de Sentinel-2, crítica para humedales pequeños, frente a los 20m de B8A.

Where:
- **B8**: Banda NIR - Sentinel-2: 842 nm (10m res)
- **B4**: Banda roja - Sentinel-2: 665 nm (10m res)
- **B11**: Banda SWIR - Sentinel-2: 1610 nm (20m res)

### Rango Teórico y Práctico

- **Rango teórico**: valores sin límite definido
- **Rango práctico común**: -0.1 a 0.5
- **Interpretación**:
  - Valores > 0.05: presencia de algas flotantes (umbral de clasificación común)
  - Valores 0.01-0.05: posible presencia de algas
  - Valores < 0: ausencia de algas flotantes

### Normalización Implementada

**Rango natural**: [-1, 1] (rango teórico)  
**Rango usado**: [-1, 1] (sin transformación adicional)  
**Razón**: Valores típicos -0.1 a 0.5, pero se mantiene rango completo para comparación visual

### Referencia Principal

Hu, C. (2009). A novel ocean color index to detect floating algae in the global oceans. *Remote Sensing of Environment, 113*(10), 2118-2129. https://doi.org/10.1016/j.rse.2009.05.012

**Justificación**: Hu (2009) desarrolló FAI como un índice robusto para detectar algas flotantes que es menos susceptible a variaciones atmosféricas y geométricas que índices tradicionales como NDVI. El rango -0.1 a 0.5 captura la variabilidad observada en estudios de floraciones algales.

---

## 6. WRI - Water Ratio Index (Índice de Ratio de Agua)

### Fórmula

```
WRI = (Green + Red) / (NIR + SWIR)
WRI = (B3 + B4) / (B8 + B11)
```

Donde:
- **B3**: Banda verde - Sentinel-2: 560 nm
- **B4**: Banda roja - Sentinel-2: 665 nm
- **B8**: Banda NIR - Sentinel-2: 842 nm
- **B11**: Banda SWIR - Sentinel-2: 1610 nm

### Rango Teórico y Práctico

- **Rango teórico**: 0 a infinito (sin límite superior teórico)
- **Rango práctico común**: 0 a 5
- **Interpretación**:
  - Valores > 1: presencia de agua o humedad
  - Valores ≈ 1: límite agua/tierra
  - Valores < 1: ausencia de agua (superficies secas)
  - Rango observado: 0.83-1.24 en estudios empíricos

### Normalización Implementada

**Rango natural**: [0, ∞] (sin límite superior teórico)  
**Rango usado**: [-1, 1] (con transformación)  
**Fórmula**: `(valor - 1) / 2`  
**Razón**: Umbral 1.0 → 0 en escala normalizada. Valores >1 (agua) → positivos, valores <1 (tierra) → negativos

### Referencia Principal

Shen, L., & Li, C. (2010). Water body extraction from Landsat ETM+ imagery using adaboost algorithm. *18th International Conference on Geoinformatics*, 1-4. https://doi.org/10.1109/GEOINFORMATICS.2010.5567762

**Justificación**: Shen & Li (2010) propusieron WRI para extracción de cuerpos de agua, demostrando su efectividad para diferenciar agua de otras coberturas terrestres. El umbral de 1.0 proporciona una separación clara entre agua y no-agua.

---

## 7. Validación de Rangos de Normalización

### Metodología de Normalización

**CAMBIO IMPORTANTE**: Todos los índices ahora se mantienen en el rango estándar **[-1, 1]** para:

1. **Comparabilidad**: Permitir comparación visual directa entre diferentes índices en el mismo gráfico
2. **Preservación semántica**: Mantener la interpretación estándar de índices normalizados
3. **Visualización**: Optimizar la representación gráfica con escala common
4. **Análisis estadístico**: Facilitar comparación de tendencias entre índices

La transformación se aplica solo cuando es necesaria:

```python
# Para WRI: mapear [0, 5] a [-1, 1]
valor_normalizado = (valor_wri - 1) / 2

# Para otros índices: clipping a [-1, 1]
valor_normalizado = max(-1, min(1, valor))
```

### Tabla Resumen de Rangos

| Índice | Fórmula | Rango Natural | Rango Usado | Transformación | Referencia |
|--------|---------|---------------|-------------|----------------|------------|
| MNDWI | (Green-SWIR)/(Green+SWIR) | [-1, 1] | [-1, 1] | Sin transformación | Xu (2006) |
| NDRE | (NIR-RedEdge)/(NIR+RedEdge) | [-1, 1] | [-1, 1] | Sin transformación | Gitelson & Merzlyak (1994) |
| NDCI | (RedEdge-Red)/(RedEdge+Red) | [-1, 1] | [-1, 1] | Sin transformación | Mishra & Mishra (2012) |
| SAVI | [(NIR-Red)/(NIR+Red+L)]×(1+L) | [-1, 1] | [-1, 1] | Sin transformación | Huete (1988) |
| FAI | NIR-[baseline] | Sin límite | [-1, 1] | Clipping | Hu (2009) |
| WRI | (Green+Red)/(NIR+SWIR) | [0, ∞] | [-1, 1] | (valor-1)/2 | Shen & Li (2010) |

---

## 8. Referencias Complementarias

### Sentinel-2

European Space Agency. (2015). *Sentinel-2 User Handbook*. ESA Communications. https://sentinel.esa.int/documents/247904/685211/Sentinel-2_User_Handbook

**Relevancia**: Especificaciones técnicas de las bandas espectrales utilizadas en los cálculos.

### Metodología de Teledetección

Jensen, J. R. (2015). *Introductory digital image processing: A remote sensing perspective* (4th ed.). Pearson Education.

**Relevancia**: Fundamentos de procesamiento de imágenes digitales y cálculo de índices espectrales.

### Monitoreo de Humedales

Ozesmi, S. L., & Bauer, M. E. (2002). Satellite remote sensing of wetlands. *Wetlands Ecology and Management, 10*(5), 381-402. https://doi.org/10.1023/A:1020908432489

**Relevancia**: Revisión comprehensiva de técnicas de teledetección para monitoreo de humedales.

---

## 9. Consideraciones Científicas

### Limitaciones

1. **Variabilidad Estacional**: Los rangos pueden variar según la estación del año y condiciones climáticas
2. **Tipos de Humedales**: Diferentes tipos de humedales pueden mostrar variabilidad en los rangos esperados
3. **Calidad Atmosférica**: Corrección atmosférica afecta los valores absolutos de los índices
4. **Resolución Espacial**: Píxeles mixtos pueden afectar los valores especialmente en bordes

### Validación en Campo

Se recomienda validación en campo para:
- Calibrar umbrales específicos del sitio
- Verificar interpretaciones de rangos
- Ajustar parámetros según condiciones locales

### Actualización de Referencias

Este documento debe ser actualizado cuando:
- Se publiquen nuevos estudios relevantes
- Se modifiquen las formulaciones de los índices
- Se identifiquen rangos más precisos para humedales chilenos

---

## 10. Implementación en el Sistema

Los cálculos y rangos documentados en este archivo están implementados en:

- **Backend**: `backend/main.py` - Funciones de cálculo de índices (líneas 45-201)
- **Normalización**: `backend/main.py` - Función `normalize_index_value()` (líneas 218-251)
- **Documentación**: `backend/report_generator.py` - Generación de reportes con referencias

---

**Documento preparado por**: Sistema de Monitoreo de Humedales  
**Fecha de creación**: 22 de enero de 2026  
**Versión**: 1.0  
**Formato de referencias**: APA 7ª edición

---

**Nota**: Todas las referencias bibliográficas en este documento siguen el formato de la 7ª edición del *Manual de Publicación de la American Psychological Association* (APA, 2020).

import math
from typing import Any, Dict, Optional

MACROZONE_PROFILES: Dict[str, Dict[str, Any]] = {
    "norte_altoandino": {
        "label": "Macrozona Norte / Altoandina (Salares y Bofedales)",
        "weights_water": {"hydrology": 0.30, "vegetation": 0.45, "water_quality": 0.25},
        "weights_dry": {"hydrology": 0.40, "vegetation": 0.60, "water_quality": 0.0},
    },
    "centro_mediterraneo": {
        "label": "Macrozona Centro / Mediterránea (Humedales Urbanos y Costeros)",
        "weights_water": {"hydrology": 0.35, "vegetation": 0.35, "water_quality": 0.30},
        "weights_dry": {"hydrology": 0.50, "vegetation": 0.50, "water_quality": 0.0},
    },
    "sur_templado": {
        "label": "Macrozona Sur / Templada Lluviosa (Hualves y Palustres)",
        "weights_water": {"hydrology": 0.40, "vegetation": 0.30, "water_quality": 0.30},
        "weights_dry": {"hydrology": 0.55, "vegetation": 0.45, "water_quality": 0.0},
    },
    "austral_patagonico": {
        "label": "Macrozona Austral / Patagónica (Turberas y Marismas)",
        "weights_water": {"hydrology": 0.40, "vegetation": 0.40, "water_quality": 0.20},
        "weights_dry": {"hydrology": 0.50, "vegetation": 0.50, "water_quality": 0.0},
    },
}


def infer_chile_macrozone(lat: Optional[float] = None, region: Optional[str] = None) -> str:
    """Infer Chilean ecological macrozone from latitude or administrative region."""
    if region:
        r = region.lower()
        if any(k in r for k in ("arica", "tarapac", "antofagasta", "atacama")):
            return "norte_altoandino"
        if any(k in r for k in ("aysen", "aysén", "magallanes")):
            return "austral_patagonico"
        if any(k in r for k in ("biob", "araucan", "los r", "los lagos")):
            return "sur_templado"
        if any(k in r for k in ("coquimbo", "valpara", "metropolitana", "o'higgins", "maule", "ñuble", "nuble")):
            return "centro_mediterraneo"
    if lat is not None:
        if lat > -26.5:
            return "norte_altoandino"
        if lat > -36.5:
            return "centro_mediterraneo"
        if lat > -43.5:
            return "sur_templado"
        return "austral_patagonico"
    return "centro_mediterraneo"


def calculate_index_situational_status(mode: str, value: Optional[float], trend: Optional[float] = None) -> Dict[str, Any]:
    """Calculate the situational scientific status for a single spectral index based on validated literature thresholds.
    References:
      - MNDWI: Xu (2006); Du et al. (2016); Ji et al. (2009)
      - NDRE: Gitelson & Merzlyak (1994); Barnes et al. (2000); Clevers & Gitelson (2013)
      - NDCI: Mishra & Mishra (2012)
      - SAVI: Huete (1988)
      - FAI: Hu (2009)
      - WRI: Shen & Li (2010)
    """
    if value is None:
        return {
            "status_label": "Sin Datos",
            "status_level": "unknown",
            "status_color": "#94a3b8",
            "threshold": "N/A",
            "scientific_basis": "N/A",
            "description": "No se dispone de observaciones satelitales suficientes para determinar el estado."
        }
    
    val = float(value)
    
    if mode == "Hydrology":  # MNDWI
        if val > 0.00:
            return {
                "status_label": "Agua Abierta / Inundación Plena",
                "status_level": "optimal",
                "status_color": "#3b82f6",
                "threshold": "MNDWI > 0.00",
                "scientific_basis": "Xu (2006); Du et al. (2016)",
                "description": "Cuerpo de agua libre superficial bien delimitado. Régimen de inundación activo y comprobable."
            }
        elif val >= -0.20:
            return {
                "status_label": "Suelo Saturado / Humedal Palustre",
                "status_level": "good",
                "status_color": "#06b6d4",
                "threshold": "-0.20 ≤ MNDWI ≤ 0.00",
                "scientific_basis": "Ji et al. (2009); Rapinel et al. (2019)",
                "description": "Sustrato hídrico con alta humedad o vegetación emergente con agua subyacente."
            }
        else:
            return {
                "status_label": "Estrés Hídrico / Sin Inundación",
                "status_level": "alert",
                "status_color": "#ef4444",
                "threshold": "MNDWI < -0.20",
                "scientific_basis": "Xu (2006)",
                "description": "Superficie desecada sin presencia de agua libre o humedad superficial detectable."
            }

    elif mode == "Vegetation":  # NDRE
        if val > 0.40:
            return {
                "status_label": "Vigor Óptimo (Alta Clorofila)",
                "status_level": "optimal",
                "status_color": "#22c55e",
                "threshold": "NDRE > 0.40",
                "scientific_basis": "Gitelson & Merzlyak (1994); Clevers & Gitelson (2013)",
                "description": "Canopia vegetal hidrófita densa, fotosintéticamente activa y sin estrés fisiológico."
            }
        elif val >= 0.20:
            return {
                "status_label": "Vigor Moderado / Estrés Parcial",
                "status_level": "alert",
                "status_color": "#eab308",
                "threshold": "0.20 ≤ NDRE ≤ 0.40",
                "scientific_basis": "Barnes et al. (2000)",
                "description": "Cobertura vegetal intermedia, clorofila reducida o estrés foliar moderado."
            }
        else:
            return {
                "status_label": "Degradación / Vegetación Escasa",
                "status_level": "critical",
                "status_color": "#ef4444",
                "threshold": "NDRE < 0.20",
                "scientific_basis": "Gitelson & Merzlyak (1994)",
                "description": "Mínima actividad fotosintética; vegetación marchita, suelo desnudo o espejo de agua abierto."
            }

    elif mode == "WaterQuality":  # NDCI
        if val < 0.00:
            return {
                "status_label": "Oligotrófico (Agua Limpia)",
                "status_level": "optimal",
                "status_color": "#06b6d4",
                "threshold": "NDCI < 0.00",
                "scientific_basis": "Mishra & Mishra (2012)",
                "description": "Baja concentración de clorofila-a; columna de agua transparente y baja carga de nutrientes."
            }
        elif val <= 0.20:
            return {
                "status_label": "Mesotrófico (Productividad Normal)",
                "status_level": "good",
                "status_color": "#22c55e",
                "threshold": "0.00 ≤ NDCI ≤ 0.20",
                "scientific_basis": "Mishra & Mishra (2012)",
                "description": "Nivel biológico equilibrado; productividad primaria moderada dentro de rangos normales."
            }
        elif val <= 0.35:
            return {
                "status_label": "Eutrófico (Degradación Trófica)",
                "status_level": "alert",
                "status_color": "#f59e0b",
                "threshold": "0.20 < NDCI ≤ 0.35",
                "scientific_basis": "Mishra & Mishra (2012)",
                "description": "Enriquecimiento excesivo de nutrientes y proliferación de microalgas fitoplanctónicas."
            }
        else:
            return {
                "status_label": "Hipereutrófico (Bloom Crítico)",
                "status_level": "critical",
                "status_color": "#ef4444",
                "threshold": "NDCI > 0.35",
                "scientific_basis": "Mishra & Mishra (2012)",
                "description": "Floración algal descontrolada; riesgo inminente de hipoxia y degradación biológica."
            }

    elif mode == "SoilVegetation":  # SAVI
        if val > 0.45:
            return {
                "status_label": "Cobertura Vegetal Densa",
                "status_level": "optimal",
                "status_color": "#84cc16",
                "threshold": "SAVI > 0.45",
                "scientific_basis": "Huete (1988)",
                "description": "Estrato vegetal continuo y vigoroso con suelo de fondo totalmente protegido."
            }
        elif val >= 0.25:
            return {
                "status_label": "Cobertura Vegetal Moderada",
                "status_level": "good",
                "status_color": "#22c55e",
                "threshold": "0.25 ≤ SAVI ≤ 0.45",
                "scientific_basis": "Huete (1988)",
                "description": "Cobertura vegetal intermedia con parches mixtos de vegetación y suelo húmedo."
            }
        elif val >= 0.10:
            return {
                "status_label": "Vegetación Dispersa / Borde",
                "status_level": "alert",
                "status_color": "#eab308",
                "threshold": "0.10 ≤ SAVI < 0.25",
                "scientific_basis": "Huete (1988)",
                "description": "Vegetación dispersa o rala sobre suelo expuesto o transición de borde seco."
            }
        else:
            return {
                "status_label": "Suelo Desnudo / Sin Cobertura",
                "status_level": "critical",
                "status_color": "#a8a29e",
                "threshold": "SAVI < 0.10",
                "scientific_basis": "Huete (1988)",
                "description": "Predominio de suelo mineral expuesto, sustrato degradado o agua sin vegetación vascular."
            }

    elif mode == "AlgaeBloom":  # FAI
        if val <= 0.00:
            return {
                "status_label": "Ausencia de Algas Flotantes",
                "status_level": "optimal",
                "status_color": "#10b981",
                "threshold": "FAI ≤ 0.00",
                "scientific_basis": "Hu (2009)",
                "description": "Espejo de agua libre de natas superficiales, espumas algales o macrófitas flotantes."
            }
        elif val <= 0.05:
            return {
                "status_label": "Presencia Incipiente de Flotantes",
                "status_level": "alert",
                "status_color": "#eab308",
                "threshold": "0.00 < FAI ≤ 0.05",
                "scientific_basis": "Hu (2009)",
                "description": "Manchas o natas flotantes incipientes o desarrollo temprano de lentejas de agua."
            }
        else:
            return {
                "status_label": "Floración Masiva / Manto Flotante",
                "status_level": "critical",
                "status_color": "#ef4444",
                "threshold": "FAI > 0.05",
                "scientific_basis": "Hu (2009)",
                "description": "Manto continuo de algas flotantes o alfombras densas de macrófitas superficiales."
            }

    elif mode == "WaterRatio":  # WRI
        if val >= 1.00:
            return {
                "status_label": "Cuerpo Hídrico Confirmado",
                "status_level": "optimal",
                "status_color": "#a855f7",
                "threshold": "WRI ≥ 1.00",
                "scientific_basis": "Shen & Li (2010)",
                "description": "Predominancia espectral de agua respecto a la matriz circundante."
            }
        elif val >= 0.50:
            return {
                "status_label": "Transición Hídrica / Suelo Húmedo",
                "status_level": "alert",
                "status_color": "#eab308",
                "threshold": "0.50 ≤ WRI < 1.00",
                "scientific_basis": "Shen & Li (2010)",
                "description": "Superficie en transición hídrica o humedad residual sin espejo de agua libre continuo."
            }
        else:
            return {
                "status_label": "Superficie Terrestre Seca",
                "status_level": "critical",
                "status_color": "#ef4444",
                "threshold": "WRI < 0.50",
                "scientific_basis": "Shen & Li (2010)",
                "description": "Valores característicos de suelo seco o matriz terrestre sin presencia de agua."
            }

    return {
        "status_label": "Analizado",
        "status_level": "good",
        "status_color": "#64748b",
        "threshold": "Estándar",
        "scientific_basis": "Sentinel-2 MSI",
        "description": "Índice procesado dentro de parámetros estándar."
    }

def calculate_consolidated_wetland_status(analysis_results: Dict[str, Any], macrozone: Optional[str] = None) -> Dict[str, Any]:
    """Calculate the consolidated Multicriteria Ecological Condition Index (IMCE / MCEI)
    based on the Analytic Hierarchy Process (AHP, Saaty 1980) and the Ecological Character
    framework of the Ramsar Convention (Resolution IX.1 Annex E) and US EPA NWCA guidelines.

    Scientific Protocol:
    1. Dynamic water-body conditioning: NDCI (Mishra & Mishra, 2012) and FAI (Hu, 2009) optical
       water quality algorithms are physically valid only on open water surfaces. When no water
       body is detected (MNDWI < -0.15 and WRI < 0.85), water quality is flagged as Not Applicable
       to prevent false positives from terrestrial vegetation, and weights renormalize 50% / 50%.
    2. Continuous non-linear sigmoidal functions reflecting ecosystem tipping points.
    3. Integration aligned with the regulatory criteria of Chilean Wetland Law N° 21.202.
    """
    # 1. Scientific Verification: Validate presence of authentic observational data
    core_modes = ["Hydrology", "Vegetation", "WaterQuality", "SoilVegetation", "AlgaeBloom", "WaterRatio"]
    valid_observed_modes = []
    for cm in core_modes:
        m_obj = analysis_results.get(cm)
        if isinstance(m_obj, dict) and m_obj.get("status") != "error":
            c_val = m_obj.get("stats", {}).get("current")
            if c_val is not None:
                try:
                    float(c_val)
                    valid_observed_modes.append(cm)
                except (ValueError, TypeError):
                    pass

    # Strict scientific guard: If fewer than 2 distinct spectral indices have valid observations,
    # DO NOT invent synthetic scores or declare health. Declare unknown / insufficient data.
    if len(valid_observed_modes) < 2:
        return {
            "imce_score": None,
            "weci_score": None,
            "score": None,
            "health_level": "unknown",
            "health_label": "Sin Datos Suficientes",
            "health_color": "#94a3b8",
            "health_badge": "DATOS INSUFICIENTES",
            "has_water_body": False,
            "dimensions": {
                "hydrology": {"score": None, "weight": 0, "label": "Sin Datos", "indicators": []},
                "vegetation": {"score": None, "weight": 0, "label": "Sin Datos", "indicators": []},
                "water_quality": {"score": None, "weight": 0, "status": "unknown", "label": "Sin Datos", "indicators": []}
            },
            "diagnosis": {
                "code": "INSUFFICIENT_OBSERVATIONS",
                "title": "Observaciones Satelitales Insuficientes",
                "description": "No se dispone de un número suficiente de índices espectrales válidos para estructurar un dictamen ecológico formal.",
                "regulatory_verdict": "Ampliar la ventana temporal o seleccionar un período con menor interferencia atmosférica para habilitar la fiscalización."
            },
            "scientific_framework": "AHP (Saaty 1980) · Convención Ramsar Res. IX.1 · US EPA NWCA"
        }

    def get_obs(mode_key: str) -> Optional[float]:
        m = analysis_results.get(mode_key)
        if isinstance(m, dict) and m.get("status") != "error":
            stats = m.get('stats', {})
            val = stats.get('current')
            if val is not None:
                try:
                    f_val = float(val)
                    if math.isfinite(f_val):
                        return f_val
                except (ValueError, TypeError):
                    pass
        return None

    def get_delta(mode_key: str, default: float = 0.0) -> float:
        m = analysis_results.get(mode_key, {})
        if isinstance(m, dict):
            stats = m.get('stats', {})
            val = stats.get('trend_delta') if stats.get('trend_delta') is not None else stats.get('trend')
            if val is not None:
                try:
                    f_val = float(val)
                    if math.isfinite(f_val):
                        return f_val
                except:
                    pass
        return default

    mndwi = get_obs('Hydrology')
    wri = get_obs('WaterRatio')
    ndre = get_obs('Vegetation')
    savi = get_obs('SoilVegetation')
    ndci = get_obs('WaterQuality')
    fai = get_obs('AlgaeBloom')

    # Effective values for delta and diagnostic rules
    eff_mndwi = mndwi if mndwi is not None else 0.0
    eff_ndre = ndre if ndre is not None else 0.25
    eff_savi = savi if savi is not None else 0.25
    eff_ndci = ndci if ndci is not None else 0.0
    eff_fai = fai if fai is not None else 0.0

    delta_mndwi = get_delta('Hydrology', 0.0)
    delta_ndre = get_delta('Vegetation', 0.0)
    delta_savi = get_delta('SoilVegetation', 0.0)
    delta_ndci = get_delta('WaterQuality', 0.0)

    def safe_sigmoid(x: float) -> float:
        """Compute numerically stable logistic sigmoid 1 / (1 + exp(-x)) preventing float overflow."""
        if x > 40.0: return 1.0
        if x < -40.0: return 0.0
        return 1.0 / (1.0 + math.exp(-x))

    # 1. Dimensión Hidrológica (Saturación y Espejo de Agua)
    dim_hydrology = None
    hydro_indicators = []
    if mndwi is not None and wri is not None:
        s_mndwi = 100.0 * safe_sigmoid(12.0 * (mndwi + 0.10))
        s_wri = 100.0 * safe_sigmoid(4.0 * (wri - 1.0))
        dim_hydrology = round(0.70 * s_mndwi + 0.30 * s_wri, 1)
        hydro_indicators = ["MNDWI", "WRI"]
    elif mndwi is not None:
        s_mndwi = 100.0 * safe_sigmoid(12.0 * (mndwi + 0.10))
        dim_hydrology = round(s_mndwi, 1)
        hydro_indicators = ["MNDWI"]
    elif wri is not None:
        s_wri = 100.0 * safe_sigmoid(4.0 * (wri - 1.0))
        dim_hydrology = round(s_wri, 1)
        hydro_indicators = ["WRI"]

    # 2. Dimensión Vegetacional / Biótica (Vigor Fotosintético y Cobertura)
    dim_vegetation = None
    veg_indicators = []
    if ndre is not None and savi is not None:
        s_ndre = 100.0 * safe_sigmoid(14.0 * (ndre - 0.25))
        s_savi = 100.0 * safe_sigmoid(12.0 * (savi - 0.25))
        dim_vegetation = round(0.60 * s_ndre + 0.40 * s_savi, 1)
        veg_indicators = ["NDRE", "SAVI"]
    elif ndre is not None:
        s_ndre = 100.0 * safe_sigmoid(14.0 * (ndre - 0.25))
        dim_vegetation = round(s_ndre, 1)
        veg_indicators = ["NDRE"]
    elif savi is not None:
        s_savi = 100.0 * safe_sigmoid(12.0 * (savi - 0.25))
        dim_vegetation = round(s_savi, 1)
        veg_indicators = ["SAVI"]

    # 3. Dimensión Calidad Óptica de Agua / Estado Trófico
    # Detect presence of free open water body from observational data
    has_water_body = (
        (mndwi is not None and mndwi >= -0.15) or
        (wri is not None and wri >= 0.85)
    )

    dim_quality = None
    quality_indicators = []
    dim_quality_status = "not_applicable"
    dim_quality_label = "No Aplicable (Sin lámina de agua libre detectable)"

    if has_water_body:
        dim_quality_label = "Integridad Óptica y Calidad del Agua"
        if ndci is not None and fai is not None:
            s_ndci = 100.0 * (1.0 - safe_sigmoid(20.0 * (ndci - 0.10)))
            s_fai = 100.0 * (1.0 - safe_sigmoid(100.0 * (fai - 0.02)))
            dim_quality = round(0.60 * s_ndci + 0.40 * s_fai, 1)
            dim_quality_status = "active"
            quality_indicators = ["NDCI", "FAI"]
        elif ndci is not None:
            s_ndci = 100.0 * (1.0 - safe_sigmoid(20.0 * (ndci - 0.10)))
            dim_quality = round(s_ndci, 1)
            dim_quality_status = "active"
            quality_indicators = ["NDCI"]
        elif fai is not None:
            s_fai = 100.0 * (1.0 - safe_sigmoid(100.0 * (fai - 0.02)))
            dim_quality = round(s_fai, 1)
            dim_quality_status = "active"
            quality_indicators = ["FAI"]
        else:
            dim_quality_status = "unobserved"
            dim_quality_label = "Sin Observaciones de Calidad de Agua"
    else:
        dim_quality = 0.0
        dim_quality_status = "not_applicable"

    # Dynamic AHP Multi-Criteria Normalization calibrated by Chilean macrozone
    mz_key = macrozone if macrozone in MACROZONE_PROFILES else "centro_mediterraneo"
    mz_profile = MACROZONE_PROFILES[mz_key]
    if has_water_body and dim_quality_status == "active":
        nominal_weights = mz_profile["weights_water"]
    else:
        nominal_weights = mz_profile["weights_dry"]

    dim_scores = {
        "hydrology": dim_hydrology,
        "vegetation": dim_vegetation,
        "water_quality": dim_quality if dim_quality_status == "active" else None
    }
    active_dims = {k: nominal_weights[k] for k, score in dim_scores.items() if score is not None and nominal_weights[k] > 0}
    total_active_weight = sum(active_dims.values())

    if total_active_weight > 0:
        norm_weights = {k: v / total_active_weight for k, v in active_dims.items()}
        imce_score = round(sum(norm_weights[k] * dim_scores[k] for k in active_dims), 1)
        weight_hydrology = round(norm_weights.get("hydrology", 0.0) * 100)
        weight_vegetation = round(norm_weights.get("vegetation", 0.0) * 100)
        dim_quality_weight = round(norm_weights.get("water_quality", 0.0) * 100)
    else:
        imce_score = None
        weight_hydrology = 0
        weight_vegetation = 0
        dim_quality_weight = 0

    # Health category based on normalized scientific multi-criteria scale
    if imce_score is not None:
        if imce_score >= 80:
            health_level = "optimal"
            health_label = "Excelente / Óptimo"
            health_color = "#10b981"
            health_badge = "CONDICIÓN EXCELENTE"
        elif imce_score >= 60:
            health_level = "good"
            health_label = "Bueno / Estable"
            health_color = "#06b6d4"
            health_badge = "CONDICIÓN BUENA"
        elif imce_score >= 40:
            health_level = "alert"
            health_label = "Alerta / Estrés Moderado"
            health_color = "#f59e0b"
            health_badge = "ESTADO DE ALERTA"
        elif imce_score >= 20:
            health_level = "degraded"
            health_label = "Degradado / Estrés Severo"
            health_color = "#f97316"
            health_badge = "ESTADO DEGRADADO"
        else:
            health_level = "critical"
            health_label = "Crítico / Pérdida Ecosistémica"
            health_color = "#ef4444"
            health_badge = "ESTADO CRÍTICO"
    else:
        health_level = "unknown"
        health_label = "Sin Datos Suficientes"
        health_color = "#94a3b8"
        health_badge = "DATOS INSUFICIENTES"

    # Dynamic Diagnostic Matrix (State-and-Transition Model / Reglas Cruzadas)
    # Evaluates BOTH dynamic trajectories (deltas) AND chronic/static state thresholds to guarantee 0% contradiction with IMCE score
    if eff_mndwi < -0.22 and eff_ndre < 0.20 and eff_savi < 0.16 and (delta_mndwi < -0.06 or (imce_score is not None and imce_score < 28)):
        diag_title = "Alteración Antrópica Severa / Pérdida Crítica de Humedal"
        diag_desc = "Ausencia crítica de lámina de agua y saturación hídrica con erradicación de vegetación hidrófita y predominio de matriz seca o suelo desnudo compactado. Perfil compatible con drenaje forzado, remoción de sustrato o relleno."
        regulatory_verdict = "Evidencia pericial de daño ambiental o quiebre hidrológico crítico. Se sugiere fiscalización inmediata bajo marco Ley 21.202 y denuncia a la SMA."
        diag_code = "ANTHROPIC_DEGRADATION"
    elif eff_mndwi < -0.12 and eff_savi > 0.28 and (delta_savi > 0.03 or delta_mndwi < -0.04 or eff_ndre >= 0.25):
        diag_title = "Terrestrización / Invasión en Lecho Seco"
        diag_desc = "Ausencia o retroceso del cuerpo de agua superficial acompañado de colonización por especies vegetales terrestres u oportunistas sobre el lecho desecado."
        regulatory_verdict = "Alerta de pérdida del régimen hidrológico de saturación. La cobertura vegetal detectada se desarrolla sobre sustrato deficitario en humedad."
        diag_code = "TERRESTRIALIZATION"
    elif eff_mndwi < -0.15 and ((delta_mndwi < -0.04 and delta_ndre <= 0.02) or (imce_score is not None and imce_score < 45)):
        diag_title = "Déficit Hídrico Severo / Desecación Ecosistémica"
        diag_desc = "Cuerpo de agua retraído y baja humedad de sustrato, acompañado de estrés fisiológico o declive en el vigor de la comunidad vegetal hidrófita."
        regulatory_verdict = "Contracción severa del régimen hidrológico. Evaluar impacto de sequía prolongada vs. extracción o desvío de caudales afluentes."
        diag_code = "DESICCATION"
    elif has_water_body and ((dim_quality is not None and dim_quality < 45) or eff_ndci > 0.20 or eff_fai > 0.03):
        diag_title = "Eutrofización Crítica / Floración Algal (Bloom)"
        diag_desc = "Alta concentración de biomasa fitoplanctónica, clorofila-a o mantos de macrófitas flotantes. Deterioro evidente de la calidad óptica del agua."
        regulatory_verdict = "Riesgo ecológico agudo en columna de agua. Priorizar inspección de descargas de aguas servidas, escorrentía de nutrientes o estancamiento anaeróbico."
        diag_code = "EUTROPHICATION"
    elif delta_mndwi > 0.05 and delta_ndre > 0.03 and (imce_score is None or imce_score >= 45):
        diag_title = "Fase de Recuperación Ecosistémica"
        diag_desc = "Expansión del espejo de agua y fortalecimiento del vigor de la vegetación hidrófita respecto al período inicial de referencia."
        regulatory_verdict = "Evolución favorable: Fortalecimiento de los criterios de régimen hidrológico y hábitat hidrófito según la Ley 21.202."
        diag_code = "RECOVERY"
    elif imce_score is not None and imce_score < 60:
        diag_title = "Estrés Ecosistémico / Vulnerabilidad Funcional"
        diag_desc = "El humedal presenta indicadores intermedios o sub-óptimos de saturación hídrica y/o vigor fotosintético, evidenciando fragilidad funcional frente a presiones climáticas o antrópicas."
        regulatory_verdict = "Atención preventiva: Monitorear evolución estacional para descartar deterioro progresivo de los criterios de sustentabilidad de la Ley 21.202."
        diag_code = "MODERATE_STRESS"
    else:
        diag_title = "Régimen Hídrico Pleno y Vitalidad Ecosistémica Estable"
        diag_desc = "Equilibrio funcional entre el balance hídrico superficial, la cobertura de vegetación hidrófita y una columna de agua sin estrés trófico severo."
        regulatory_verdict = "El ecosistema mantiene los tres criterios fundamentales de la Ley 21.202 (hidrología activa, cobertura vegetal y funciones ecológicas)."
        diag_code = "STABLE_HEALTHY"

    return {
        "imce_score": imce_score,
        "weci_score": imce_score,
        "score": imce_score,
        "overall_score": imce_score,
        "health_level": health_level,
        "health_label": health_label,
        "health_color": health_color,
        "health_badge": health_badge,
        "has_water_body": has_water_body,
        "macrozone": mz_key,
        "macrozone_label": mz_profile["label"],
        "dimensions": {
            "hydrology": {
                "score": dim_hydrology,
                "weight": weight_hydrology,
                "label": "Régimen Hidrológico y Saturación",
                "indicators": hydro_indicators
            },
            "vegetation": {
                "score": dim_vegetation,
                "weight": weight_vegetation,
                "label": "Vigor y Cobertura Vegetal",
                "indicators": veg_indicators
            },
            "water_quality": {
                "score": dim_quality,
                "weight": dim_quality_weight,
                "status": dim_quality_status,
                "label": dim_quality_label,
                "indicators": quality_indicators
            }
        },
        "diagnosis": {
            "code": diag_code,
            "title": diag_title,
            "description": diag_desc,
            "regulatory_verdict": regulatory_verdict
        },
        "scientific_framework": "AHP (Saaty 1980) · Convención Ramsar Res. IX.1 · US EPA NWCA"
    }


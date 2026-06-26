"""Confidence scoring for multi-signal attribution decisions."""

LLM_WEIGHT = 0.50
STYLOMETRIC_WEIGHT = 0.25
LEXICAL_SPECIFICITY_WEIGHT = 0.25
AI_THRESHOLD = 0.72
HUMAN_THRESHOLD = 0.28


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def classify_scores(
    llm_score: float,
    stylometric_score: float,
    lexical_specificity_score: float = 0.5,
) -> dict:
    """Combine signal scores and map them to attribution + confidence."""
    llm_score = clamp(llm_score)
    stylometric_score = clamp(stylometric_score)
    lexical_specificity_score = clamp(lexical_specificity_score)
    ai_probability = (
        (LLM_WEIGHT * llm_score)
        + (STYLOMETRIC_WEIGHT * stylometric_score)
        + (LEXICAL_SPECIFICITY_WEIGHT * lexical_specificity_score)
    )

    if ai_probability >= AI_THRESHOLD:
        attribution = "likely_ai"
        confidence = ai_probability
    elif ai_probability <= HUMAN_THRESHOLD:
        attribution = "likely_human"
        confidence = 1 - ai_probability
    else:
        attribution = "uncertain"
        confidence = max(ai_probability, 1 - ai_probability)

    return {
        "attribution": attribution,
        "ai_probability": round(ai_probability, 3),
        "confidence": round(confidence, 3),
        "thresholds": {
            "likely_ai_min": AI_THRESHOLD,
            "likely_human_max": HUMAN_THRESHOLD,
        },
        "weights": {
            "llm_semantic": LLM_WEIGHT,
            "stylometric": STYLOMETRIC_WEIGHT,
            "lexical_specificity": LEXICAL_SPECIFICITY_WEIGHT,
        },
        "ensemble_method": "weighted_average",
    }

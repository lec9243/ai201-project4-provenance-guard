"""Reader-facing transparency labels."""

LABEL_TEMPLATES = {
    "likely_ai": (
        "Provenance Guard: Likely AI-generated. Our detection signals found "
        "strong AI-generation patterns (confidence: {confidence_percent}). "
        "The creator can appeal this label if it is wrong."
    ),
    "likely_human": (
        "Provenance Guard: Likely human-written. Our detection signals found "
        "strong human authorship patterns (confidence: {confidence_percent}). "
        "This label provides context, not a guarantee."
    ),
    "uncertain": (
        "Provenance Guard: Authorship uncertain. Our detection signals were "
        "mixed or low-confidence (confidence: {confidence_percent}). This work "
        "should not be treated as clearly AI-generated or clearly human-written."
    ),
}


def render_label(attribution: str, confidence: float) -> str:
    """Return the exact transparency label text for a classification."""
    template = LABEL_TEMPLATES.get(attribution, LABEL_TEMPLATES["uncertain"])
    confidence_percent = f"{round(confidence * 100)}%"
    return template.format(confidence_percent=confidence_percent)

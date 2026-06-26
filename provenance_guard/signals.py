"""Detection signals used by Provenance Guard."""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any

from .scoring import clamp

WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

AI_FORMULAIC_PHRASES = (
    "it is important to note",
    "it is essential",
    "furthermore",
    "moreover",
    "in conclusion",
    "transformative",
    "paradigm shift",
    "ethical implications",
    "stakeholders",
    "various sectors",
    "responsible deployment",
    "multifaceted",
    "delve",
    "underscore",
    "plays a crucial role",
)

HUMAN_MARKERS = (
    "i",
    "me",
    "my",
    "we",
    "honestly",
    "ok",
    "okay",
    "kinda",
    "sorta",
    "really",
    "probably",
    "friend",
    "downtown",
    "won't",
    "can't",
    "didn't",
    "i've",
    "i'm",
)

ABSTRACT_AI_TERMS = (
    "artificial",
    "intelligence",
    "transformative",
    "paradigm",
    "society",
    "benefits",
    "numerous",
    "essential",
    "ethical",
    "implications",
    "stakeholders",
    "sectors",
    "collaborate",
    "responsible",
    "deployment",
    "relationship",
    "monetary",
    "policy",
    "inflation",
    "literature",
    "mandate",
    "stability",
    "consequences",
    "valuations",
    "framework",
    "innovation",
    "efficiency",
)

CONCRETE_DETAIL_TERMS = (
    "ramen",
    "downtown",
    "broth",
    "sodium",
    "thirsty",
    "friend",
    "spicy",
    "porch",
    "coffee",
    "neighborhood",
    "kitchen",
    "street",
    "bus",
    "train",
    "draft",
    "notebook",
    "photo",
    "camera",
    "studio",
    "paint",
    "canvas",
    "guitar",
    "song",
    "voice",
    "hands",
    "morning",
    "yesterday",
    "downtown",
)


def tokenize(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def split_sentences(text: str) -> list[str]:
    candidates = [part.strip() for part in SENTENCE_RE.split(text.strip()) if part.strip()]
    if candidates:
        return candidates
    return [line.strip() for line in text.splitlines() if line.strip()] or [text.strip()]


def llm_semantic_signal(text: str) -> dict[str, Any]:
    """Use Groq when configured, otherwise use a local semantic proxy."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return _local_semantic_fallback(text, reason="GROQ_API_KEY not configured")

    try:
        from groq import Groq

        client = Groq(api_key=api_key)
        completion = client.chat.completions.create(
            model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            temperature=0,
            max_tokens=220,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You classify authorship signals for a writing platform. "
                        "Return JSON only with keys ai_probability and rationale. "
                        "ai_probability must be a number from 0.0 (strongly human) "
                        "to 1.0 (strongly AI-generated). Be conservative: when "
                        "evidence is mixed, stay near 0.5."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Text to assess:\n\n{text[:6000]}",
                },
            ],
        )
        raw_content = completion.choices[0].message.content or "{}"
        parsed = _parse_json_object(raw_content)
        score = clamp(float(parsed.get("ai_probability", 0.5)))
        rationale = str(parsed.get("rationale", "Groq returned no rationale."))[:500]
        return {
            "name": "llm_semantic",
            "score": round(score, 3),
            "source": "groq",
            "rationale": rationale,
        }
    except Exception as exc:  # pragma: no cover - depends on external service
        return _local_semantic_fallback(text, reason=f"Groq unavailable: {exc}")


def stylometric_signal(text: str) -> dict[str, Any]:
    """Compute structural writing metrics and convert them to an AI-likeness score."""
    words = tokenize(text)
    sentences = split_sentences(text)
    sentence_lengths = [max(1, len(tokenize(sentence))) for sentence in sentences]

    word_count = len(words)
    unique_words = len(set(words))
    avg_sentence_length = sum(sentence_lengths) / len(sentence_lengths)
    variance = (
        sum((length - avg_sentence_length) ** 2 for length in sentence_lengths)
        / len(sentence_lengths)
    )
    burstiness = math.sqrt(variance) / max(avg_sentence_length, 1)
    type_token_ratio = unique_words / max(word_count, 1)
    punctuation_count = sum(1 for char in text if char in ".,;:!?")
    punctuation_density = punctuation_count / max(len(text), 1)
    informal_density = _human_marker_count(words, text.lower()) / max(word_count, 1)

    uniformity_score = 1 - clamp(burstiness / 0.75)
    low_diversity_score = clamp((0.72 - type_token_ratio) / 0.42)
    long_sentence_score = clamp((avg_sentence_length - 11) / 18)
    regular_punctuation_score = 1 - clamp(abs(punctuation_density - 0.035) / 0.06)
    informal_penalty = clamp(informal_density * 4.5)

    score = (
        (0.36 * uniformity_score)
        + (0.24 * low_diversity_score)
        + (0.22 * long_sentence_score)
        + (0.18 * regular_punctuation_score)
        - (0.30 * informal_penalty)
    )

    return {
        "name": "stylometric",
        "score": round(clamp(score), 3),
        "metrics": {
            "word_count": word_count,
            "sentence_count": len(sentences),
            "avg_sentence_length": round(avg_sentence_length, 2),
            "sentence_length_variance": round(variance, 2),
            "burstiness": round(burstiness, 3),
            "type_token_ratio": round(type_token_ratio, 3),
            "punctuation_density": round(punctuation_density, 3),
            "informal_density": round(informal_density, 3),
        },
    }


def lexical_specificity_signal(text: str) -> dict[str, Any]:
    """Score lexical genericity versus concrete author-specific detail."""
    lower_text = text.lower()
    words = tokenize(text)
    word_count = max(len(words), 1)

    formulaic_hits = sum(1 for phrase in AI_FORMULAIC_PHRASES if phrase in lower_text)
    abstract_hits = sum(1 for word in words if word in ABSTRACT_AI_TERMS)
    concrete_hits = sum(1 for word in words if word in CONCRETE_DETAIL_TERMS)
    human_hits = _human_marker_count(words, lower_text)
    number_hits = len(re.findall(r"\b\d+(?:\.\d+)?\b", text))
    tag_like_hits = lower_text.count("#")

    genericity = clamp((abstract_hits / word_count) * 7.0)
    formulaic_density = clamp((formulaic_hits / word_count) * 18.0)
    concrete_density = clamp((concrete_hits / word_count) * 7.0)
    personal_density = clamp((human_hits / word_count) * 5.5)
    metadata_specificity = clamp(((number_hits + tag_like_hits) / word_count) * 4.0)

    score = (
        0.48
        + (0.30 * genericity)
        + (0.28 * formulaic_density)
        - (0.22 * concrete_density)
        - (0.26 * personal_density)
        - (0.12 * metadata_specificity)
    )

    return {
        "name": "lexical_specificity",
        "score": round(clamp(score), 3),
        "metrics": {
            "word_count": len(words),
            "formulaic_phrase_hits": formulaic_hits,
            "abstract_term_hits": abstract_hits,
            "concrete_detail_hits": concrete_hits,
            "human_marker_hits": human_hits,
            "number_hits": number_hits,
            "tag_like_hits": tag_like_hits,
            "genericity": round(genericity, 3),
            "concrete_density": round(concrete_density, 3),
            "personal_density": round(personal_density, 3),
        },
    }


def _local_semantic_fallback(text: str, reason: str) -> dict[str, Any]:
    lower_text = text.lower()
    words = tokenize(text)
    word_count = max(len(words), 1)

    formulaic_hits = sum(1 for phrase in AI_FORMULAIC_PHRASES if phrase in lower_text)
    transition_hits = sum(1 for word in words if word in {"furthermore", "moreover", "therefore"})
    human_hits = _human_marker_count(words, lower_text)
    question_or_emphasis = text.count("?") + text.count("!") + len(re.findall(r"\b[A-Z]{2,}\b", text))
    avg_word_length = sum(len(word) for word in words) / word_count
    abstract_hits = sum(
        1
        for word in words
        if word
        in {
            "society",
            "essential",
            "implications",
            "stakeholders",
            "deployment",
            "framework",
            "relationship",
            "policy",
            "valuation",
        }
    )

    score = (
        0.46
        + (0.075 * formulaic_hits)
        + (0.04 * transition_hits)
        + (0.015 * abstract_hits)
        + (0.035 if avg_word_length >= 6.0 else 0)
        - (0.045 * human_hits)
        - (0.025 * question_or_emphasis)
    )

    return {
        "name": "llm_semantic",
        "score": round(clamp(score), 3),
        "source": "local_fallback",
        "rationale": (
            "Local fallback used because {reason}. It checks semantic markers "
            "such as formulaic AI phrasing, abstract generality, personal context, "
            "and informal language."
        ).format(reason=reason),
    }


def _human_marker_count(words: list[str], lower_text: str) -> int:
    word_hits = sum(1 for word in words if word in HUMAN_MARKERS)
    phrase_hits = sum(1 for phrase in ("way too", "my friend", "i was", "i wrote") if phrase in lower_text)
    return word_hits + phrase_hits


def _parse_json_object(raw_content: str) -> dict[str, Any]:
    try:
        return json.loads(raw_content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw_content, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}

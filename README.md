# Provenance Guard

Provenance Guard is a Flask backend for creative writing platforms. It accepts text and structured metadata submissions, runs a multi-signal authorship analysis, returns a confidence-aware transparency label, records every decision in a structured audit log, and lets creators appeal classifications.

This project is intentionally conservative. A false positive against a human writer is worse than missing some AI-generated text, so mixed evidence produces an `uncertain` label instead of forcing a binary verdict.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a local `.env` file for the Groq key. `.env` is already listed in `.gitignore` and should never be committed.

```bash
GROQ_API_KEY=your_key_here
```

Run the API:

```bash
flask --app app run
```

If no Groq key is configured, the app still runs with a deterministic local semantic fallback so the project can be tested offline.

## API Endpoints

### `POST /submit`

Classifies a text submission. The endpoint is rate limited.

```bash
curl -s -X POST http://localhost:5000/submit \
  -H "Content-Type: application/json" \
  -d '{
    "text": "The sun dipped below the horizon, painting the sky in hues of amber and rose.",
    "creator_id": "test-user-1"
  }' | python3 -m json.tool
```

Response fields include:

```json
{
  "content_id": "uuid",
  "creator_id": "test-user-1",
  "content_type": "text",
  "status": "classified",
  "attribution": "likely_ai | likely_human | uncertain",
  "confidence": 0.835,
  "ai_probability": 0.835,
  "label": "reader-facing transparency label",
  "provenance_certificate": null,
  "signals": {
    "llm_semantic": { "score": 1.0, "source": "local_fallback" },
    "stylometric": { "score": 0.341, "metrics": {} },
    "lexical_specificity": { "score": 1.0, "metrics": {} }
  }
}
```

Metadata submissions use the same endpoint with `content_type: "metadata"`:

```bash
curl -s -X POST http://localhost:5000/submit \
  -H "Content-Type: application/json" \
  -d '{
    "creator_id": "metadata-user",
    "content_type": "metadata",
    "metadata": {
      "title": "Downtown mural study",
      "description": "Photo set documenting a painted wall near the train station.",
      "alt_text": "A blue and yellow mural with chipped paint and a bus stop nearby.",
      "tags": ["mural", "street art", "downtown"],
      "creator_process": "I photographed this after work and selected the frame manually."
    }
  }' | python3 -m json.tool
```

### `POST /appeal`

Queues a creator appeal and updates the content status to `under_review`.

```bash
curl -s -X POST http://localhost:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{
    "content_id": "PASTE-CONTENT-ID-HERE",
    "creator_reasoning": "I wrote this myself and can provide drafts showing my editing process."
  }' | python3 -m json.tool
```

### `GET /log`

Returns recent structured audit log entries.

```bash
curl -s http://localhost:5000/log?limit=5 | python3 -m json.tool
```

### `GET /appeals`

Returns content currently marked `under_review`, which acts as the human-review queue for this project.

### `POST /verify-human`

Creates a verified-human provenance certificate for a creator after they provide a process statement and draft excerpt.

```bash
curl -s -X POST http://localhost:5000/verify-human \
  -H "Content-Type: application/json" \
  -d '{
    "creator_id": "verified-demo",
    "process_statement": "I draft my posts in a notebook, revise them twice, and keep dated draft excerpts before publishing.",
    "draft_excerpt": "First rough draft from my notebook: I wanted to describe the porch coffee scene before revising the ending."
  }' | python3 -m json.tool
```

Future submissions from that creator include a separate `provenance_certificate` object with the display label:

```text
Verified Human Creator: This creator completed Provenance Guard's process-based human verification step.
```

### `GET /analytics` and `GET /dashboard`

`GET /analytics` returns dashboard metrics as JSON. `GET /dashboard` renders the same metrics as a simple HTML dashboard.

## Architecture Overview

A submission enters through `POST /submit` with either `text` or structured `metadata`, plus `creator_id`. The Flask API validates the request, generates a `content_id`, runs the LLM semantic signal, stylometric signal, and lexical specificity signal, combines them into an `ai_probability`, maps that score to an attribution and confidence value, renders the transparency label, attaches any verified-human certificate for that creator, stores the content decision, writes a structured audit entry, and returns the JSON response.

An appeal enters through `POST /appeal` with `content_id` and `creator_reasoning`. The API looks up the original content record, changes the status to `under_review`, logs the appeal alongside the original decision, and returns a confirmation. The full diagram is in `planning.md` under `## Architecture`.

## Detection Signals

### Signal 1: LLM Semantic Assessment

This signal asks Groq's `llama-3.3-70b-versatile` model to judge whether the text reads as AI-generated or human-written. It captures holistic writing patterns: generic framing, overly balanced structure, safe summary language, lack of lived specificity, and formulaic transitions. It returns a score from `0.0` to `1.0`, where `1.0` means stronger AI evidence.

Why I chose it: semantic and stylistic coherence are hard to capture with pure statistics.

What it misses: polished human writing can look generic, and edited AI text can include personal details. If the Groq API is unavailable, the code uses a local fallback that checks semantic markers like formulaic phrases and personal context.

### Signal 2: Stylometric Heuristics

This signal computes structural metrics in pure Python: sentence length variance, burstiness, type-token ratio, punctuation density, average sentence length, and informal marker density. It also returns a score from `0.0` to `1.0`.

Why I chose it: it is independent from the LLM-style signal and gives a measurable check on whether the writing structure is unusually uniform.

What it misses: short text, poetry, formal essays, and non-native English writing can produce noisy or misleading stylometric patterns.

### Signal 3: Lexical Specificity

This signal measures word-level evidence: formulaic AI phrases, abstract generic terms, concrete detail words, first-person/context markers, numbers, and tag-like metadata details. It returns a score from `0.0` to `1.0`, where higher scores mean more AI-like genericity and lower scores mean more human-specific concrete context.

Why I chose it: it is distinct from stylometrics because it does not measure sentence structure. It is also more deterministic than the LLM signal because it exposes exactly which lexical markers affected the score.

What it misses: a human writer can use abstract vocabulary, and AI-generated text can be prompted to include concrete details. It is a supporting signal, not a standalone detector.

## Confidence Scoring

The system combines three signal scores into an ensemble AI probability:

```text
ai_probability =
  (0.50 * llm_semantic_score)
+ (0.25 * stylometric_score)
+ (0.25 * lexical_specificity_score)
```

Conflict handling: the LLM signal receives the largest weight because it captures context, but the two deterministic signals together can pull a borderline case into the uncertain range when they disagree with the LLM. The final label still uses the same conservative thresholds.

Thresholds:

```text
ai_probability >= 0.72  -> likely_ai
ai_probability <= 0.28  -> likely_human
otherwise               -> uncertain
```

The wide uncertain band is deliberate. A score near `0.51` means the system is not confident enough to label the work as AI or human. A score near `0.95` means the evidence strongly supports the selected attribution.

Example validation results using the local fallback:

| Example | LLM semantic | Stylometric | Lexical specificity | AI probability | Confidence | Attribution |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| AI-like policy paragraph | 1.000 | 0.341 | 1.000 | 0.835 | 0.835 | `likely_ai` |
| Casual ramen review | 0.000 | 0.019 | 0.020 | 0.010 | 0.990 | `likely_human` |
| Formal monetary-policy paragraph | 0.490 | 0.461 | 0.780 | 0.555 | 0.555 | `uncertain` |
| Lightly edited remote-work paragraph | 0.415 | 0.290 | 0.443 | 0.391 | 0.609 | `uncertain` |

These examples show that the score is not constant and that borderline content lands in the uncertain range.

## Transparency Labels

The app implements all three label variants as exact templates. `{confidence_percent}` is replaced with the runtime confidence value.

| Variant | Exact label text |
| --- | --- |
| High-confidence AI | "Provenance Guard: Likely AI-generated. Our detection signals found strong AI-generation patterns (confidence: {confidence_percent}). The creator can appeal this label if it is wrong." |
| High-confidence human | "Provenance Guard: Likely human-written. Our detection signals found strong human authorship patterns (confidence: {confidence_percent}). This label provides context, not a guarantee." |
| Uncertain | "Provenance Guard: Authorship uncertain. Our detection signals were mixed or low-confidence (confidence: {confidence_percent}). This work should not be treated as clearly AI-generated or clearly human-written." |

## Appeals Workflow

Creators appeal with `POST /appeal`, providing the `content_id` and `creator_reasoning`. The system updates the content status to `under_review`, stores the creator's reasoning with the content record, and writes a separate audit entry with the original attribution and confidence. `GET /appeals` returns the review queue a human moderator would inspect.

Automated re-classification is not included. That is intentional: an appeal should be a human review path, especially because false positives can harm creators.

## Rate Limiting

`POST /submit` is limited to:

```text
10 per minute;100 per day
```

Reasoning: a normal creator might submit a few drafts or posts in a short period, so 10 per minute is enough for real use. A script trying to flood the classifier would hit the minute limit quickly. The 100 per day cap still allows active testing or a prolific creator while limiting abuse in this class-project backend.

Rate-limit test output from 12 rapid submissions:

```text
200
200
200
200
200
200
200
200
200
200
429
429
```

## Audit Log

The audit log is structured JSON stored at `data/audit_log.json` when the app runs locally. It records submission decisions and appeals. Runtime log files are ignored by Git, but the format is visible through `GET /log` and in this sample.

Sample with three visible entries:

```json
[
  {
    "timestamp": "2026-06-25T23:39:00.000Z",
    "event_type": "submission",
    "content_id": "demo-ai-001",
    "creator_id": "ai_like",
    "content_type": "text",
    "status": "classified",
    "attribution": "likely_ai",
    "confidence": 0.835,
    "ai_probability": 0.835,
    "signals": {
      "llm_semantic": {
        "score": 1.0,
        "source": "local_fallback",
        "rationale": "Local fallback used because GROQ_API_KEY not configured."
      },
      "stylometric": {
        "score": 0.341,
        "metrics": {
          "word_count": 41,
          "type_token_ratio": 0.902,
          "sentence_length_variance": 14.89
        }
      },
      "lexical_specificity": {
        "score": 1.0,
        "metrics": {
          "formulaic_phrase_hits": 4,
          "abstract_term_hits": 18,
          "concrete_detail_hits": 0
        }
      }
    }
  },
  {
    "timestamp": "2026-06-25T23:39:01.000Z",
    "event_type": "submission",
    "content_id": "demo-human-001",
    "creator_id": "human_casual",
    "content_type": "text",
    "status": "classified",
    "attribution": "likely_human",
    "confidence": 0.99,
    "ai_probability": 0.01,
    "signals": {
      "llm_semantic": { "score": 0.0, "source": "local_fallback" },
      "stylometric": { "score": 0.019, "metrics": { "word_count": 52 } },
      "lexical_specificity": { "score": 0.02, "metrics": { "concrete_detail_hits": 6 } }
    }
  },
  {
    "timestamp": "2026-06-25T23:39:02.000Z",
    "event_type": "appeal",
    "content_id": "demo-ai-001",
    "creator_id": "ai_like",
    "status": "under_review",
    "appeal_reasoning": "I wrote this myself and can provide drafts showing the editing process.",
    "original_attribution": "likely_ai",
    "original_confidence": 0.769,
    "original_ai_probability": 0.769
  }
]
```

## Stretch Features

### Ensemble Detection

The pipeline now uses three distinct signals:

| Signal | What it measures | Weight |
| --- | --- | ---: |
| `llm_semantic` | Holistic semantic and stylistic authorship patterns | 0.50 |
| `stylometric` | Sentence structure, burstiness, punctuation density, vocabulary diversity | 0.25 |
| `lexical_specificity` | Concrete detail, personal context, abstract generic vocabulary, formulaic phrases | 0.25 |

Conflicts are resolved by weighted averaging plus the conservative uncertain band. For example, the formal monetary-policy paragraph scored `0.490` on the LLM signal, `0.461` on stylometrics, and `0.780` on lexical specificity. The ensemble result was `0.555`, so the label remained `uncertain` instead of forcing an AI verdict.

### Provenance Certificate

A creator earns a verified-human credential through `POST /verify-human`. In this project version, the verification step requires:

1. `creator_id`
2. `process_statement`
3. `draft_excerpt`

The returned certificate contains a `certificate_id`, verification timestamp, method, and this display label:

```text
Verified Human Creator: This creator completed Provenance Guard's process-based human verification step.
```

After verification, future submissions by that creator include:

```json
{
  "provenance_certificate": {
    "certificate_id": "vh-abc123def456",
    "creator_id": "verified-demo",
    "status": "verified_human",
    "verification_method": "process_statement_plus_draft_excerpt",
    "display_label": "Verified Human Creator: This creator completed Provenance Guard's process-based human verification step."
  }
}
```

This certificate is separate from the standard transparency label. A verified creator can still receive an uncertain or AI-like detection result; the certificate only says the creator completed the extra process-based verification step.

### Analytics Dashboard

`GET /analytics` returns JSON metrics, and `GET /dashboard` renders a simple HTML dashboard. The dashboard includes the required three metrics:

| Metric | Meaning |
| --- | --- |
| Detection pattern | Counts of `likely_ai`, `likely_human`, and `uncertain` verdicts |
| Appeal rate | `total_appeals / total_submissions` |
| Average confidence | Mean confidence score across submissions |

It also shows content-type counts and verified-creator submission counts.

Example `/analytics` output:

```json
{
  "appeal_rate": 0.0,
  "average_confidence": 0.747,
  "content_type_counts": { "metadata": 1, "text": 4 },
  "total_appeals": 0,
  "total_submissions": 5,
  "verdict_counts": { "likely_ai": 1, "likely_human": 2, "uncertain": 2 },
  "verified_creator_submissions": 1
}
```

### Multi-Modal Support

The second supported content type is structured metadata. This models a platform analyzing non-body-text context for an uploaded image or creative object. The metadata fields are normalized into analysis text, then passed through the same three-signal ensemble.

Supported metadata fields include:

```text
title, description, alt_text, caption, tags, creator_process, software, camera, location
```

Example result from a metadata submission:

```text
content_type: metadata
attribution: likely_human
confidence: 0.745
provenance_certificate: present
```

## Known Limitations

Poetry with repeated words or intentionally uniform line lengths may be over-scored by the stylometric signal because low vocabulary diversity and controlled rhythm can look AI-like.

Formal human essays may land in the uncertain range because academic prose often uses generic transitions, abstract vocabulary, and balanced sentence structure.

Very short submissions are unstable because stylometric metrics need enough text to estimate variation. The app accepts short submissions over 20 characters, but a production system should ask for more context before showing strong labels.

## Spec Reflection

The planning spec helped most with the confidence thresholds. Defining the uncertain band before coding made the implementation conservative instead of accidentally treating every score above 0.5 as AI.

The implementation diverged from the original Groq-only first signal by adding a local fallback. I added it so the grader and local tests can run without network access or a live API key, while still using Groq automatically when `GROQ_API_KEY` is configured.

## AI Usage

1. I used AI assistance while drafting `planning.md` to organize my architecture, API contract, signal definitions, thresholds, label text, appeal handling, and implementation plan. I revised the thresholds to make the uncertain band wider because false positives are more harmful in this project.

2. I used AI assistance to generate first drafts of the Flask backend modules: API routes, signal functions, scoring logic, label rendering, JSON audit storage, and tests. I reviewed the code against my spec and corrected the implementation when testing exposed a Flask-Limiter object lifetime bug by keeping the limiter attached to the Flask app instance.

3. I used AI assistance to help summarize test evidence for scoring variation, rate limiting, and audit logs. I edited the final README so it documents my design choices, limitations, and verification results clearly instead of only listing features.

## Tests

Run the automated tests:

```bash
python3 -m unittest discover -s tests
```

Run a syntax check:

```bash
python3 -m compileall app.py provenance_guard tests
```

## Walkthrough Video

For the course submission, record a short walkthrough showing:

1. `POST /submit` returning a classification, confidence score, label, and signals.
2. `GET /log` showing structured audit entries.
3. `POST /appeal` changing a submission to `under_review`.
4. The rate-limit test returning 429 after the 10th rapid request.

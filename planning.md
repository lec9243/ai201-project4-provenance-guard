# Provenance Guard Planning

## Problem Summary

Provenance Guard is a backend service for creative writing platforms. It accepts a text submission, runs multiple attribution signals, combines those signals into an uncertainty-aware score, returns a reader-facing transparency label, logs the decision, and gives creators a way to appeal decisions they believe are wrong.

The system is intentionally conservative because a false positive against a human writer is more harmful than a missed AI-generated post. Borderline cases should be labeled as uncertain instead of forcing an AI or human label.

## Architecture

```text
Submission flow

Client
  |
  | POST /submit
  | { text, creator_id }
  v
Flask API
  |
  | raw text
  v
Signal 1: LLM semantic assessment
  |
  | llm_score, rationale
  v
Signal 2: Stylometric heuristics
  |
  | stylometric_score, metrics
  v
Signal 3: Lexical specificity heuristics
  |
  | lexical_specificity_score, metrics
  v
Confidence scorer
  |
  | ai_probability, attribution, confidence
  v
Transparency label generator
  |
  | label text
  v
Audit log + content store
  |
  | structured decision record
  v
JSON response
  { content_id, attribution, confidence, ai_probability, label, signal details, certificate if present }


Appeal flow

Creator
  |
  | POST /appeal
  | { content_id, creator_reasoning }
  v
Flask API
  |
  | content_id lookup
  v
Content store status update
  |
  | status = under_review
  v
Audit log
  |
  | appeal entry with original decision + creator reasoning
  v
JSON response
  { content_id, status, message }
```

The submission flow starts at `POST /submit`, where Flask validates the text or metadata and creator ID, runs the LLM-style semantic signal, the stylometric signal, and the lexical specificity signal, combines their outputs into one AI probability, maps that probability into an attribution and confidence score, generates the matching transparency label, attaches a verified-human certificate if the creator has one, stores the content decision, writes an audit entry, and returns the response. The appeal flow starts at `POST /appeal`, looks up the original content decision, changes its status to `under_review`, writes an appeal audit entry, and returns a confirmation.

## API Contract

### `POST /submit`

Request:

```json
{
  "text": "A poem, story excerpt, blog post, or other text submission.",
  "creator_id": "creator-123"
}
```

Response:

```json
{
  "content_id": "uuid",
  "creator_id": "creator-123",
  "status": "classified",
  "attribution": "likely_ai | likely_human | uncertain",
  "confidence": 0.91,
  "ai_probability": 0.91,
  "label": "reader-facing label text",
  "signals": {
    "llm_semantic": { "score": 0.88, "rationale": "..." },
    "stylometric": { "score": 0.96, "metrics": {} },
    "lexical_specificity": { "score": 0.74, "metrics": {} }
  }
}
```

### `POST /appeal`

Request:

```json
{
  "content_id": "uuid",
  "creator_reasoning": "I wrote this myself and can explain my process."
}
```

Response:

```json
{
  "content_id": "uuid",
  "status": "under_review",
  "message": "Appeal received and queued for human review."
}
```

### `GET /log`

Returns recent structured audit entries. This is public in the class project for grader visibility. A real production version would require authentication.

### `GET /appeals`

Returns content records currently marked `under_review`, which is the basic human-review queue.

## Detection Signals

### Signal 1: LLM Semantic Assessment

What it measures: The first signal asks a Groq-hosted LLM to assess whether the text reads like AI-generated writing or human writing. It looks for holistic semantic and stylistic patterns such as generic framing, overly balanced structure, formulaic transitions, unusually polished neutrality, and lack of lived specificity.

Output: A float from `0.0` to `1.0`, where `0.0` means strongly human-written and `1.0` means strongly AI-generated. The signal also returns a short rationale and the source used (`groq` or `local_fallback`).

Why this differs between human and AI writing: LLM-generated writing often has broad coverage, smooth transitions, and safe generalizations. Human writing more often includes uneven emphasis, concrete personal context, idiosyncratic phrasing, or abrupt changes in focus.

Blind spots: A human can write polished generic prose, especially in academic or professional contexts. A lightly edited AI draft can include personal details. The signal can also inherit bias from the model, so it should not be used alone.

### Signal 2: Stylometric Heuristics

What it measures: The second signal computes measurable text features: sentence length variation, type-token ratio, punctuation density, average sentence length, and informal marker density. It estimates whether the structure is unusually uniform or unusually human-like.

Output: A float from `0.0` to `1.0`, where `0.0` means the text has stronger human-style variation and `1.0` means it has stronger AI-style uniformity. It also returns the raw metrics used to calculate the score.

Why this differs between human and AI writing: AI-generated text often has more even sentence structure and a polished rhythm. Human writing often has more burstiness, slang, contractions, punctuation irregularity, and variable sentence length.

Blind spots: Short texts produce noisy statistics. Poetry, lyrics, formal essays, and non-native English writing can look statistically unusual in ways that do not map cleanly to authorship.

### Signal 3: Lexical Specificity Heuristics

What it measures: The third signal counts concrete details, first-person/context markers, abstract generic vocabulary, formulaic AI phrases, numbers, and tag-like metadata markers. It estimates whether the text relies on broad generic language or contains grounded details that often appear in human process-based writing.

Output: A float from `0.0` to `1.0`, where `0.0` means stronger concrete human-context evidence and `1.0` means stronger abstract or formulaic AI-like evidence. It also returns the lexical counts used to calculate the score.

Why this differs between human and AI writing: AI-generated content often defaults to abstract and broadly applicable wording unless prompted otherwise. Human creative submissions often include specific objects, places, process notes, or personal context.

Blind spots: This signal can be gamed by prompting AI to include concrete details, and human academic writing can legitimately use abstract terms. It is only one part of the ensemble.

## Confidence Scoring

The system combines all three signal outputs as an AI probability:

```text
ai_probability =
  (0.50 * llm_semantic_score)
+ (0.25 * stylometric_score)
+ (0.25 * lexical_specificity_score)
```

The LLM semantic score has more weight because it captures meaning and context, while the stylometric and lexical specificity scores act as independent deterministic checks. The two deterministic signals together can disagree with the LLM and push borderline cases into the uncertain range.

Thresholds:

```text
ai_probability >= 0.72  -> attribution = likely_ai
ai_probability <= 0.28  -> attribution = likely_human
otherwise               -> attribution = uncertain
```

Confidence is the strength of the selected classification. For `likely_ai`, confidence equals `ai_probability`. For `likely_human`, confidence equals `1 - ai_probability`. For `uncertain`, confidence equals the stronger side of the mixed decision, but the label remains uncertain because neither side reached the decision threshold.

A confidence score of `0.60` means the system leans in one direction but not enough to present a firm attribution to readers. A confidence score of `0.95` means both the selected classification and the underlying score are strong. This design avoids treating `0.51` as a meaningful verdict.

## Transparency Label Design

The exact label text is implemented as templates because the displayed confidence percentage changes per submission.

| Variant | Exact label template |
| --- | --- |
| High-confidence AI | "Provenance Guard: Likely AI-generated. Our detection signals found strong AI-generation patterns (confidence: {confidence_percent}). The creator can appeal this label if it is wrong." |
| High-confidence human | "Provenance Guard: Likely human-written. Our detection signals found strong human authorship patterns (confidence: {confidence_percent}). This label provides context, not a guarantee." |
| Uncertain | "Provenance Guard: Authorship uncertain. Our detection signals were mixed or low-confidence (confidence: {confidence_percent}). This work should not be treated as clearly AI-generated or clearly human-written." |

## Appeals Workflow

Who can appeal: Any creator with the `content_id` for a classified submission can file an appeal in this project version. In production, this would be restricted to the authenticated creator who submitted the content.

Information provided: The creator submits `content_id` and `creator_reasoning`. The reasoning should explain why the creator believes the classification is wrong, such as personal context, drafting history, language background, or evidence of authorship.

System behavior:

1. Look up the original content record by `content_id`.
2. Reject the appeal if the content ID does not exist or the reasoning is empty.
3. Update the content status from `classified` to `under_review`.
4. Write an audit entry with the appeal reasoning, original attribution, original confidence, and current status.
5. Return a confirmation response.

Human reviewer view: `GET /appeals` returns the under-review queue with `content_id`, creator ID, original attribution, confidence, label, text excerpt, appeal reasoning, and appeal timestamp.

## Anticipated Edge Cases

Poetry with repetition and controlled rhythm: A human poem may have short repeated lines and low vocabulary diversity. The stylometric signal could mistake this for AI-like uniformity, so the combined system should avoid a high-confidence AI label unless the semantic signal also agrees.

Formal academic prose written by a human: Academic writing often uses balanced sentences, abstract vocabulary, and neutral transitions. The system may score it higher than casual writing, which is why the uncertain band is wide.

Very short submissions: A two-sentence post does not provide enough evidence for stable stylometric metrics. The API accepts short text, but the confidence score should be interpreted cautiously.

Non-native English writing: Some human writers use formal phrasing, repetition, or unusual sentence structure. That can look AI-like to multiple signals, so the appeal path is important.

## AI Tool Plan

### M3: Submission Endpoint + First Signal

Spec sections to provide: Architecture, API Contract, Detection Signals.

Request to AI tool: Generate a Flask app skeleton with `POST /submit`, validation for `text` and `creator_id`, UUID content IDs, and the LLM semantic signal function with a deterministic fallback for local testing.

Verification: Run the route with Flask's test client and a curl command. Confirm the response contains `content_id`, `attribution`, `confidence`, and `label`, and confirm `GET /log` shows a structured audit entry with the LLM score.

### M4: Second Signal + Confidence Scoring

Spec sections to provide: Detection Signals, Confidence Scoring, Architecture.

Request to AI tool: Generate a stylometric signal function and scoring logic that combines `llm_semantic_score` and `stylometric_score` using the planned weights and thresholds.

Verification: Test clearly AI-like text, clearly human casual text, formal human text, and lightly edited AI-like text. Confirm the scores vary meaningfully and the audit log records all individual signal scores.

### M5: Production Layer

Spec sections to provide: Transparency Label Design, Appeals Workflow, Architecture, API Contract.

Request to AI tool: Generate label rendering logic, the `POST /appeal` endpoint, rate limiting for `POST /submit`, and structured audit logging.

Verification: Confirm all three label variants are reachable, submit an appeal and check that the content status changes to `under_review`, verify `GET /log` includes the appeal entry, and trigger rate limiting with more than 10 rapid submissions.

## Stretch Feature Plan

### Stretch 1: Ensemble Detection

Before implementation, I will extend the detector from two signals to three signals:

1. LLM semantic assessment: holistic semantic/stylistic authorship evidence.
2. Stylometric heuristics: structural uniformity, sentence variation, punctuation, and vocabulary metrics.
3. Lexical specificity signal: concrete detail, first-person/context markers, abstract generic vocabulary, and formulaic AI phrasing.

The ensemble will use weighted averaging:

```text
ai_probability =
  (0.50 * llm_semantic_score)
+ (0.25 * stylometric_score)
+ (0.25 * lexical_specificity_score)
```

Conflict handling: the LLM signal receives the largest weight because it captures context, but the two deterministic signals together can pull a borderline text into the uncertain range when they disagree. The same thresholds remain in place: `>= 0.72` likely AI, `<= 0.28` likely human, otherwise uncertain.

### Stretch 2: Provenance Certificate

Before implementation, I will add a simple verified-human credential. A creator earns it by calling `POST /verify-human` with a creator ID, a process statement, and a draft excerpt. This is not a real identity system; it models the extra verification step a platform could require before displaying a certificate.

Verified creators will receive a certificate record with a `certificate_id`, timestamp, verification method, and display label. Future submissions from that creator will include a separate `provenance_certificate` object so the verified label is distinguishable from the normal AI-detection transparency label.

### Stretch 3: Analytics Dashboard

Before implementation, I will add an analytics view for moderators. `GET /analytics` will return JSON metrics, and `GET /dashboard` will render a simple HTML dashboard. The dashboard will show at least three metrics:

1. Detection pattern: counts of likely AI, likely human, and uncertain verdicts.
2. Appeal rate: appeals divided by total submissions.
3. Average confidence across submissions.

Additional metrics may include content-type counts and verified-creator submissions.

### Stretch 4: Multi-Modal Support

Before implementation, I will extend `POST /submit` beyond plain text by accepting `content_type: "metadata"` with a structured `metadata` object. This represents image/post metadata such as title, alt text, description, tags, and creator process notes.

The metadata pipeline will normalize those structured fields into analysis text, then run the same three-signal ensemble. The response will include `content_type`, the analyzed metadata excerpt, signal scores, attribution, confidence, and label.

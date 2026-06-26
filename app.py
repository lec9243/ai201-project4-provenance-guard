from __future__ import annotations

import os
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()

from provenance_guard.labels import render_label
from provenance_guard.scoring import classify_scores
from provenance_guard.signals import (
    lexical_specificity_signal,
    llm_semantic_signal,
    stylometric_signal,
)
from provenance_guard.storage import (
    append_audit_entry,
    analytics_summary,
    appeal_queue,
    get_certificate_for_creator,
    get_content,
    recent_audit_entries,
    save_content,
    save_certificate,
    update_content_for_appeal,
    utc_now_iso,
)

SUBMIT_RATE_LIMIT = os.getenv("SUBMIT_RATE_LIMIT", "10 per minute;100 per day")


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["RATELIMIT_ENABLED"] = os.getenv("RATELIMIT_ENABLED", "true").lower() == "true"

    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[],
        storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
    )
    app.extensions["provenance_guard_limiter"] = limiter

    @app.errorhandler(429)
    def rate_limit_handler(error):
        return (
            jsonify(
                {
                    "error": "rate_limit_exceeded",
                    "message": "Submission rate limit exceeded. Try again later.",
                    "limit": SUBMIT_RATE_LIMIT,
                }
            ),
            429,
        )

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/")
    def index():
        return render_template_string(
            """
            <!doctype html>
            <html lang="en">
              <head>
                <meta charset="utf-8">
                <title>Provenance Guard</title>
                <style>
                  body { font-family: Arial, sans-serif; margin: 2rem; color: #1f2937; }
                  h1 { font-size: 1.7rem; margin-bottom: 0.5rem; }
                  p { max-width: 680px; line-height: 1.5; }
                  ul { line-height: 1.9; }
                  code { background: #f3f4f6; padding: 0.15rem 0.35rem; border-radius: 4px; }
                </style>
              </head>
              <body>
                <h1>Provenance Guard</h1>
                <p>
                  Flask backend for attribution analysis, transparency labels,
                  creator appeals, provenance certificates, analytics, and
                  metadata submissions.
                </p>
                <ul>
                  <li><a href="/health"><code>/health</code></a> - service status</li>
                  <li><a href="/dashboard"><code>/dashboard</code></a> - analytics dashboard</li>
                  <li><a href="/analytics"><code>/analytics</code></a> - analytics JSON</li>
                  <li><a href="/log"><code>/log</code></a> - structured audit log</li>
                  <li><a href="/appeals"><code>/appeals</code></a> - appeal review queue</li>
                </ul>
                <p>
                  Use <code>POST /submit</code>, <code>POST /appeal</code>, and
                  <code>POST /verify-human</code> from curl for the walkthrough.
                </p>
              </body>
            </html>
            """
        )

    @app.post("/submit")
    @limiter.limit(SUBMIT_RATE_LIMIT)
    def submit():
        payload = request.get_json(silent=True) or {}
        creator_id = str(payload.get("creator_id", "")).strip()

        if not creator_id:
            return jsonify({"error": "missing_creator_id", "message": "`creator_id` is required."}), 400

        parsed_submission, error_response = _parse_submission_payload(payload)
        if error_response:
            return error_response

        analysis_text = parsed_submission["analysis_text"]
        content_type = parsed_submission["content_type"]

        content_id = str(uuid4())
        llm_signal = llm_semantic_signal(analysis_text)
        style_signal = stylometric_signal(analysis_text)
        lexical_signal = lexical_specificity_signal(analysis_text)
        decision = classify_scores(
            llm_signal["score"],
            style_signal["score"],
            lexical_signal["score"],
        )
        label = render_label(decision["attribution"], decision["confidence"])
        certificate = get_certificate_for_creator(creator_id)
        public_certificate = _certificate_public_view(certificate)

        content_record = {
            "content_id": content_id,
            "creator_id": creator_id,
            "content_type": content_type,
            "created_at": utc_now_iso(),
            "status": "classified",
            "text_excerpt": _excerpt(analysis_text),
            "attribution": decision["attribution"],
            "confidence": decision["confidence"],
            "ai_probability": decision["ai_probability"],
            "label": label,
            "provenance_certificate": public_certificate,
            "signals": {
                "llm_semantic": llm_signal,
                "stylometric": style_signal,
                "lexical_specificity": lexical_signal,
            },
        }
        if content_type == "metadata":
            content_record["metadata"] = parsed_submission["metadata"]
        save_content(content_record)

        append_audit_entry(
            {
                "event_type": "submission",
                "content_id": content_id,
                "creator_id": creator_id,
                "content_type": content_type,
                "status": "classified",
                "attribution": decision["attribution"],
                "confidence": decision["confidence"],
                "ai_probability": decision["ai_probability"],
                "provenance_certificate": public_certificate,
                "signals": {
                    "llm_semantic": {
                        "score": llm_signal["score"],
                        "source": llm_signal.get("source"),
                        "rationale": llm_signal.get("rationale"),
                    },
                    "stylometric": {
                        "score": style_signal["score"],
                        "metrics": style_signal.get("metrics", {}),
                    },
                    "lexical_specificity": {
                        "score": lexical_signal["score"],
                        "metrics": lexical_signal.get("metrics", {}),
                    },
                },
            }
        )

        return jsonify(content_record), 200

    @app.post("/verify-human")
    def verify_human():
        payload = request.get_json(silent=True) or {}
        creator_id = str(payload.get("creator_id", "")).strip()
        process_statement = str(payload.get("process_statement", "")).strip()
        draft_excerpt = str(payload.get("draft_excerpt", "")).strip()

        if not creator_id:
            return jsonify({"error": "missing_creator_id", "message": "`creator_id` is required."}), 400
        if len(process_statement) < 40:
            return (
                jsonify(
                    {
                        "error": "process_statement_too_short",
                        "message": "`process_statement` must explain the creator's process.",
                    }
                ),
                400,
            )
        if len(draft_excerpt) < 40:
            return (
                jsonify(
                    {
                        "error": "draft_excerpt_too_short",
                        "message": "`draft_excerpt` must include a short sample of drafting evidence.",
                    }
                ),
                400,
            )

        certificate = {
            "certificate_id": f"vh-{uuid4().hex[:12]}",
            "creator_id": creator_id,
            "status": "verified_human",
            "verified_at": utc_now_iso(),
            "verification_method": "process_statement_plus_draft_excerpt",
            "display_label": (
                "Verified Human Creator: This creator completed Provenance Guard's "
                "process-based human verification step."
            ),
            "process_statement_excerpt": _excerpt(process_statement, length=180),
            "draft_excerpt": _excerpt(draft_excerpt, length=180),
        }
        save_certificate(certificate)
        public_certificate = _certificate_public_view(certificate)

        append_audit_entry(
            {
                "event_type": "verification",
                "creator_id": creator_id,
                "status": "verified_human",
                "certificate_id": certificate["certificate_id"],
                "verification_method": certificate["verification_method"],
            }
        )

        return jsonify({"certificate": public_certificate}), 200

    @app.post("/appeal")
    def appeal():
        payload = request.get_json(silent=True) or {}
        content_id = str(payload.get("content_id", "")).strip()
        creator_reasoning = str(payload.get("creator_reasoning", "")).strip()

        if not content_id:
            return jsonify({"error": "missing_content_id", "message": "`content_id` is required."}), 400
        if not creator_reasoning:
            return (
                jsonify(
                    {
                        "error": "missing_creator_reasoning",
                        "message": "`creator_reasoning` is required.",
                    }
                ),
                400,
            )

        original_record = get_content(content_id)
        if not original_record:
            return jsonify({"error": "not_found", "message": "No content found for that content_id."}), 404

        updated_record = update_content_for_appeal(content_id, creator_reasoning)
        append_audit_entry(
            {
                "event_type": "appeal",
                "content_id": content_id,
                "creator_id": original_record.get("creator_id"),
                "status": "under_review",
                "appeal_reasoning": creator_reasoning,
                "original_attribution": original_record.get("attribution"),
                "original_confidence": original_record.get("confidence"),
                "original_ai_probability": original_record.get("ai_probability"),
            }
        )

        return (
            jsonify(
                {
                    "content_id": content_id,
                    "status": updated_record["status"],
                    "message": "Appeal received and queued for human review.",
                }
            ),
            200,
        )

    @app.get("/log")
    def log():
        limit = request.args.get("limit", default=20, type=int)
        return jsonify({"entries": recent_audit_entries(limit=limit)})

    @app.get("/appeals")
    def appeals():
        return jsonify({"entries": appeal_queue()})

    @app.get("/analytics")
    def analytics():
        return jsonify(analytics_summary())

    @app.get("/dashboard")
    def dashboard():
        summary = analytics_summary()
        return render_template_string(_dashboard_html(), summary=summary)

    return app


def _parse_submission_payload(payload: dict):
    content_type = str(payload.get("content_type", "text")).strip().lower() or "text"

    if content_type == "text":
        text = str(payload.get("text", "")).strip()
        if not text:
            return None, (jsonify({"error": "missing_text", "message": "`text` is required."}), 400)
        if len(text) < 20:
            return (
                None,
                (
                    jsonify(
                        {
                            "error": "text_too_short",
                            "message": "Please submit at least 20 characters for attribution analysis.",
                        }
                    ),
                    400,
                ),
            )
        return {"content_type": "text", "analysis_text": text}, None

    if content_type == "metadata":
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            return (
                None,
                (
                    jsonify(
                        {
                            "error": "missing_metadata",
                            "message": "`metadata` must be an object when content_type is metadata.",
                        }
                    ),
                    400,
                ),
            )
        analysis_text = _metadata_to_analysis_text(metadata)
        if len(analysis_text) < 20:
            return (
                None,
                (
                    jsonify(
                        {
                            "error": "metadata_too_sparse",
                            "message": "Metadata must contain enough descriptive fields to analyze.",
                        }
                    ),
                    400,
                ),
            )
        return {
            "content_type": "metadata",
            "analysis_text": analysis_text,
            "metadata": metadata,
        }, None

    return (
        None,
        (
            jsonify(
                {
                    "error": "unsupported_content_type",
                    "message": "`content_type` must be either `text` or `metadata`.",
                }
            ),
            400,
        ),
    )


def _metadata_to_analysis_text(metadata: dict) -> str:
    parts = []
    for key in (
        "title",
        "description",
        "alt_text",
        "caption",
        "tags",
        "creator_process",
        "software",
        "camera",
        "location",
    ):
        if key in metadata:
            value = _flatten_metadata_value(metadata[key])
            if value:
                parts.append(f"{key.replace('_', ' ')}: {value}")
    if not parts:
        for key, value in metadata.items():
            flattened = _flatten_metadata_value(value)
            if flattened:
                parts.append(f"{key}: {flattened}")
    return ". ".join(parts)


def _flatten_metadata_value(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        flattened_items = [_flatten_metadata_value(item) for item in value]
        return ", ".join(item for item in flattened_items if item)
    if isinstance(value, dict):
        flattened_pairs = []
        for key, item in value.items():
            flattened = _flatten_metadata_value(item)
            if flattened:
                flattened_pairs.append(f"{key}: {flattened}")
        return ", ".join(flattened_pairs)
    return ""


def _certificate_public_view(certificate: dict | None) -> dict | None:
    if not certificate:
        return None
    return {
        "certificate_id": certificate["certificate_id"],
        "creator_id": certificate["creator_id"],
        "status": certificate["status"],
        "verified_at": certificate["verified_at"],
        "verification_method": certificate["verification_method"],
        "display_label": certificate["display_label"],
    }


def _excerpt(text: str, length: int = 220) -> str:
    compact = " ".join(text.split())
    if len(compact) <= length:
        return compact
    return compact[: length - 3] + "..."


def _dashboard_html() -> str:
    return """
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <title>Provenance Guard Analytics</title>
        <style>
          body { font-family: Arial, sans-serif; margin: 2rem; color: #1f2937; }
          h1 { font-size: 1.6rem; margin-bottom: 1rem; }
          .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; }
          .metric { border: 1px solid #d1d5db; border-radius: 8px; padding: 1rem; }
          .label { color: #4b5563; font-size: 0.9rem; }
          .value { font-size: 1.8rem; font-weight: 700; margin-top: 0.35rem; }
          table { border-collapse: collapse; margin-top: 1.5rem; width: 100%; max-width: 720px; }
          th, td { border: 1px solid #d1d5db; padding: 0.65rem; text-align: left; }
          th { background: #f3f4f6; }
        </style>
      </head>
      <body>
        <h1>Provenance Guard Analytics</h1>
        <div class="grid">
          <div class="metric">
            <div class="label">Total submissions</div>
            <div class="value">{{ summary.total_submissions }}</div>
          </div>
          <div class="metric">
            <div class="label">Appeal rate</div>
            <div class="value">{{ "%.1f"|format(summary.appeal_rate * 100) }}%</div>
          </div>
          <div class="metric">
            <div class="label">Average confidence</div>
            <div class="value">{{ "%.1f"|format(summary.average_confidence * 100) }}%</div>
          </div>
          <div class="metric">
            <div class="label">Verified creator submissions</div>
            <div class="value">{{ summary.verified_creator_submissions }}</div>
          </div>
        </div>

        <table>
          <thead>
            <tr><th>Verdict</th><th>Count</th></tr>
          </thead>
          <tbody>
            {% for verdict, count in summary.verdict_counts.items() %}
              <tr><td>{{ verdict }}</td><td>{{ count }}</td></tr>
            {% endfor %}
          </tbody>
        </table>

        <table>
          <thead>
            <tr><th>Content type</th><th>Count</th></tr>
          </thead>
          <tbody>
            {% for content_type, count in summary.content_type_counts.items() %}
              <tr><td>{{ content_type }}</td><td>{{ count }}</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </body>
    </html>
    """


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)

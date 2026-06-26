"""Small JSON-backed content store and audit log."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def data_dir() -> Path:
    return Path(os.getenv("PROVENANCE_DATA_DIR", "data"))


def audit_log_path() -> Path:
    return data_dir() / "audit_log.json"


def content_store_path() -> Path:
    return data_dir() / "contents.json"


def certificate_store_path() -> Path:
    return data_dir() / "certificates.json"


def append_audit_entry(entry: dict[str, Any]) -> dict[str, Any]:
    entry = {"timestamp": utc_now_iso(), **entry}
    with _LOCK:
        entries = _read_json(audit_log_path(), [])
        entries.append(entry)
        _write_json(audit_log_path(), entries)
    return entry


def recent_audit_entries(limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    entries = _read_json(audit_log_path(), [])
    return entries[-limit:]


def save_content(record: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        records = _read_json(content_store_path(), {})
        records[record["content_id"]] = record
        _write_json(content_store_path(), records)
    return record


def get_content(content_id: str) -> dict[str, Any] | None:
    records = _read_json(content_store_path(), {})
    return records.get(content_id)


def all_content_records() -> list[dict[str, Any]]:
    records = _read_json(content_store_path(), {})
    return list(records.values())


def update_content_for_appeal(content_id: str, creator_reasoning: str) -> dict[str, Any] | None:
    with _LOCK:
        records = _read_json(content_store_path(), {})
        record = records.get(content_id)
        if not record:
            return None

        record["status"] = "under_review"
        record["appeal_reasoning"] = creator_reasoning
        record["appealed_at"] = utc_now_iso()
        records[content_id] = record
        _write_json(content_store_path(), records)
        return record


def appeal_queue() -> list[dict[str, Any]]:
    records = _read_json(content_store_path(), {})
    return [
        record
        for record in records.values()
        if record.get("status") == "under_review"
    ]


def save_certificate(certificate: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        certificates = _read_json(certificate_store_path(), {})
        certificates[certificate["creator_id"]] = certificate
        _write_json(certificate_store_path(), certificates)
    return certificate


def get_certificate_for_creator(creator_id: str) -> dict[str, Any] | None:
    certificates = _read_json(certificate_store_path(), {})
    certificate = certificates.get(creator_id)
    if not certificate:
        return None
    if certificate.get("status") != "verified_human":
        return None
    return certificate


def analytics_summary() -> dict[str, Any]:
    entries = _read_json(audit_log_path(), [])
    contents = all_content_records()
    submission_entries = [entry for entry in entries if entry.get("event_type") == "submission"]
    appeal_entries = [entry for entry in entries if entry.get("event_type") == "appeal"]

    verdict_counts = {"likely_ai": 0, "likely_human": 0, "uncertain": 0}
    content_type_counts: dict[str, int] = {}
    confidence_total = 0.0
    confidence_count = 0

    for entry in submission_entries:
        attribution = entry.get("attribution")
        if attribution in verdict_counts:
            verdict_counts[attribution] += 1
        confidence = entry.get("confidence")
        if isinstance(confidence, (int, float)):
            confidence_total += float(confidence)
            confidence_count += 1

    for record in contents:
        content_type = record.get("content_type", "text")
        content_type_counts[content_type] = content_type_counts.get(content_type, 0) + 1

    total_submissions = len(submission_entries)
    appeal_rate = len(appeal_entries) / total_submissions if total_submissions else 0.0
    average_confidence = confidence_total / confidence_count if confidence_count else 0.0
    verified_creator_submissions = sum(
        1 for record in contents if record.get("provenance_certificate")
    )

    return {
        "total_submissions": total_submissions,
        "total_appeals": len(appeal_entries),
        "appeal_rate": round(appeal_rate, 3),
        "average_confidence": round(average_confidence, 3),
        "verdict_counts": verdict_counts,
        "content_type_counts": content_type_counts,
        "verified_creator_submissions": verified_creator_submissions,
    }


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as file:
        try:
            return json.load(file)
        except json.JSONDecodeError:
            return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, sort_keys=True)
        file.write("\n")
    tmp_path.replace(path)

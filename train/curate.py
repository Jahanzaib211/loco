"""Stage 1 — curate execution traces into candidate training examples.

Reads the local export (already redacted on capture and again on export) and
applies the gates that keep the flywheel honest:

  * feedback gate — traces the operator marked down/unhelpful never become
    training signal; `--feedback-only` keeps only positively marked traces;
  * completeness gate — a record needs messages and a usable assistant turn
    (text or tool calls), never a truncated placeholder;
  * size gate — oversized turns are rejected outright, never silently cut;
  * dedup gate — identical conversations collapse to one example;
  * redaction re-check — defense in depth before anything reaches disk.

Deterministic given the same database. No model runs here.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from loco import exporter
from loco.redact import redact_value

MAX_RECORD_BYTES = 64_000
NEGATIVE_VERDICTS = {"down", "unhelpful", "rejected", "0", "false"}
POSITIVE_VERDICTS = {"up", "helpful", "accepted", "1", "true"}


def load_sft(limit_traces: int = 500, start=None, end=None) -> list[dict]:
    """Raw SFT-shaped records straight from the local exporter."""
    raw = exporter.export_jsonl(start, end, limit_traces, fmt="sft")
    records = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def load_feedback() -> dict[str, str]:
    """trace_id -> verdict from operator feedback events."""
    from loco import memory
    verdicts: dict[str, str] = {}
    with memory.conn() as connection:
        rows = connection.execute(
            "SELECT trace_id, payload FROM trace_events "
            "WHERE event = 'user_feedback' AND trace_id IS NOT NULL "
            "ORDER BY ts"
        ).fetchall()
    for trace_id, payload in rows:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        verdict = str(data.get("feedback") or "").strip().lower()
        if verdict:
            verdicts[str(trace_id)] = verdict
    return verdicts


def _assistant_turn(response) -> tuple[str, list[dict]]:
    """Extract assistant text and tool calls from a recorded API response."""
    if not isinstance(response, dict):
        return "", []
    if response.get("truncated"):
        return "", []
    choices = response.get("choices") or []
    if not choices:
        return "", []
    message = (choices[0] or {}).get("message") or {}
    text = str(message.get("content") or "").strip()
    tool_calls = message.get("tool_calls") or []
    return text, tool_calls if isinstance(tool_calls, list) else []


def example_from(record: dict, verdict: str | None) -> tuple[dict | None, str]:
    """Return (example, 'kept') or (None, reason) for one SFT record."""
    messages = record.get("messages")
    if not isinstance(messages, list) or not messages:
        # Single-turn requests are valid: the recorded response becomes the
        # assistant target in dataset.to_row.
        return None, "no_messages"
    if verdict and verdict.lower() in NEGATIVE_VERDICTS:
        return None, "feedback_down"

    text, tool_calls = _assistant_turn(record.get("response"))
    if not text and not tool_calls:
        return None, "no_assistant_turn"

    example = {
        "trace_id": str(record.get("trace_id") or ""),
        "model": record.get("model"),
        "messages": messages,
        "assistant": text,
        "tool_calls": tool_calls,
        "verdict": verdict or "unmarked",
        "usage": record.get("usage") or {},
    }
    blob = json.dumps(example, sort_keys=True, ensure_ascii=False)
    if len(blob.encode()) > MAX_RECORD_BYTES:
        return None, "oversized"
    # Re-redact on the way in: the export already redacted twice, this pass
    # guarantees a clean record even if a future exporter path forgets.
    example = redact_value(example)
    example["_sha"] = hashlib.sha256(
        json.dumps({"m": example["messages"], "a": example["assistant"],
                    "t": example["tool_calls"]},
                   sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return example, "kept"


def curate(limit_traces: int = 500, feedback_only: bool = False,
           start=None, end=None) -> tuple[list[dict], dict]:
    """Curate all available traces; returns (examples, stats)."""
    records = load_sft(limit_traces, start, end)
    verdicts = load_feedback()
    stats: Counter = Counter()
    examples: list[dict] = []
    seen: set[str] = set()

    for record in records:
        verdict = verdicts.get(str(record.get("trace_id") or ""))
        example, reason = example_from(record, verdict)
        stats[reason] += 1
        if example is None:
            continue
        if feedback_only and example["verdict"] not in POSITIVE_VERDICTS:
            stats["dropped_unmarked"] += 1
            continue
        if example["_sha"] in seen:
            stats["duplicate"] += 1
            continue
        seen.add(example["_sha"])
        examples.append(example)

    stats["records_in"] = len(records)
    stats["examples_out"] = len(examples)
    stats["feedback_traces"] = len(verdicts)
    return examples, dict(stats)

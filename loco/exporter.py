"""Local JSONL export for the Loco data flywheel.

Explicit export is the only path off disk. It applies a second redaction pass,
groups events into stable trace records, and never opens a network connection.
"""

from __future__ import annotations

import json
import time
import urllib.parse

from . import memory
from .redact import redact_value, safe_json


def decode_query(query: str) -> dict:
    return {k: v[-1] for k, v in urllib.parse.parse_qs(query).items()}


def _trace_records(start=None, end=None, limit_traces=100):
    limit_traces = max(1, min(int(limit_traces), 1_000))
    with memory.conn() as c:
        ids = [
            row[0] for row in c.execute(
                """SELECT trace_id FROM trace_events
                   WHERE trace_id IS NOT NULL
                     AND (? IS NULL OR ts >= ?)
                     AND (? IS NULL OR ts <= ?)
                   GROUP BY trace_id
                   ORDER BY max(ts) DESC LIMIT ?""",
                (start, start, end, end, limit_traces),
            ).fetchall()
        ]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = c.execute(
            f"SELECT trace_id, ts, event, payload FROM trace_events "
            f"WHERE trace_id IN ({placeholders}) "
            f"ORDER BY ts, id",
            ids,
        ).fetchall()
    grouped = {tid: [] for tid in ids}
    for trace_id, timestamp, event, payload in rows:
        try:
            body = json.loads(payload)
        except json.JSONDecodeError:
            body = {"unparsed_payload_bytes": len(payload or "")}
        grouped[trace_id].append({"ts": timestamp, "event": event,
                                  "data": redact_value(body)})
    return [{"trace_id": tid, "events": grouped[tid]} for tid in ids]


def _sft_record(trace: dict) -> dict | None:
    events = trace["events"]
    request = next((e["data"] for e in events if e["event"] == "request_received"), None)
    complete = next((e["data"] for e in reversed(events)
                     if e["event"] == "chat_completed"), None)
    if not request or not complete:
        return None
    messages = request.get("messages")
    response = complete.get("response")
    if not messages or not response:
        return None
    model = complete.get("model")
    return {
        "trace_id": trace["trace_id"],
        "model": model,
        "messages": messages,
        "response": response,
        "usage": {
            "prompt_tokens": complete.get("prompt_tokens"),
            "completion_tokens": complete.get("completion_tokens"),
        },
        "finish_reason": complete.get("finish_reason"),
        "duration_ms": complete.get("duration_ms"),
    }


def export_jsonl(start=None, end=None, limit_traces=100, fmt="events"):
    records = _trace_records(start, end, limit_traces)
    fmt = (fmt or "events").lower()
    lines = []
    if fmt in {"jsonl", "events"}:
        lines = [safe_json(record) for record in records]
    elif fmt == "sft":
        for trace in records:
            record = _sft_record(trace)
            if record is not None:
                lines.append(safe_json(record))
    else:
        raise ValueError("format must be one of: events, sft")
    return "\n".join(lines) + ("\n" if lines else "")


def export_filename(fmt: str) -> str:
    safe = "events" if (fmt or "").lower() not in {"sft"} else "sft"
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return f"loco-{safe}-{stamp}.jsonl"

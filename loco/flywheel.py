"""Structured, redacted execution traces for future model improvement.

The module is intentionally boring: every event is append-only local JSONL
metadata in SQLite, redacted on capture, and available for explicit export.
It never reaches the network.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from . import memory
from .redact import (prompt_hash, redact_messages, redact_string, redact_value,
                     safe_json, trace_mode)

MAX_TRACE_BYTES = 64_000


class Trace:
    def __init__(self, payload: dict, headers: dict | None = None):
        self.mode = trace_mode(headers or {}, payload)
        self.trace_id = None if self.mode == "off" else str(uuid.uuid4())
        self.started = time.time()
        self.payload = payload

    @property
    def enabled(self) -> bool:
        return self.trace_id is not None

    def record(self, event: str, data: dict | None = None):
        if not self.enabled:
            return
        payload = redact_value(data or {})
        try:
            memory.record_trace(self.trace_id, event, payload)
        except Exception:
            # Observability must never reduce availability of inference.
            pass

    def start(self, route_candidates: list[str] | None = None):
        if not self.enabled:
            return
        messages = self.payload.get("messages", [])
        base = {
            "message_count": len(messages) if isinstance(messages, list) else 0,
            "roles": [m.get("role") for m in messages if isinstance(m, dict)],
            "prompt_sha256": prompt_hash(messages),
            "stream": bool(self.payload.get("stream")),
            "temperature": self.payload.get("temperature"),
            "tools": [t.get("function", {}).get("name")
                      for t in self.payload.get("tools", []) or []
                      if isinstance(t, dict)],
            "mode": self.mode,
            "route_candidates": route_candidates or [],
        }
        if self.mode == "full":
            base["messages"] = redact_messages(messages)
        self.record("request_received", base)

    def route(self, model: str, backend: str, task: str, action: str, *,
              confidence: float | None = None, reason: str | None = None,
              explicit: bool = False):
        self.record("model_routed", {
            "model": model, "backend": backend, "task": task,
            "action": action, "confidence": confidence,
            "reason": reason, "explicit": explicit,
        })

    def cache_hit(self, model: str, backend: str, task: str):
        self.record("cache_hit", {"model": model, "backend": backend,
                                  "task": task})

    def response_json(self, raw: bytes, *, model: str, backend: str,
                      cached: bool = False):
        if not self.enabled:
            return
        data: dict[str, Any] = {
            "model": model,
            "backend": backend,
            "cached": cached,
            "duration_ms": round((time.time() - self.started) * 1000, 1),
            "bytes": len(raw),
        }
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None

        if isinstance(parsed, dict):
            usage = parsed.get("usage") or {}
            data.update({
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "finish_reason": _finish_reason(parsed),
                "tool_calls": _tool_calls(parsed),
            })
            if self.mode == "full":
                data["response"] = _cap(redact_value(parsed), MAX_TRACE_BYTES)
        self.record("chat_completed", data)

    def response_stream(self, *, model: str, backend: str, raw_buffer: bytes,
                        chunks: int, tool_calls: list[dict],
                        captured_bytes: int):
        if not self.enabled:
            return
        redacted_calls = redact_value(tool_calls)
        data = {
            "model": model,
            "backend": backend,
            "duration_ms": round((time.time() - self.started) * 1000, 1),
            "chunks": chunks,
            "bytes": captured_bytes,
            "captured_bytes": len(raw_buffer),
            "truncated": captured_bytes > MAX_TRACE_BYTES,
            "tool_calls": redacted_calls,
        }
        if self.mode == "full" and raw_buffer:
            text = raw_buffer[:MAX_TRACE_BYTES].decode("utf-8", errors="replace")
            data["stream_text"] = redact_string(text)
        self.record("chat_completed", data)

    def fail(self, event: str, detail: str, *, model: str | None = None):
        self.record(event, {"model": model,
                            "error": redact_string(str(detail))[:1000],
                            "duration_ms": round((time.time() - self.started) * 1000, 1)})


def _cap(value: Any, max_bytes: int) -> Any:
    try:
        raw = safe_json(value).encode()
    except Exception:
        return REDACTED
    if len(raw) <= max_bytes:
        return value
    text = raw[:max_bytes].decode("utf-8", errors="replace")
    return {"truncated": True, "bytes": len(raw), "preview": text}


REDACTED = "[REDACTED]"


def _finish_reason(parsed: dict) -> str | None:
    choices = parsed.get("choices") or []
    if choices and isinstance(choices, list):
        return choices[0].get("finish_reason")
    return None


def _tool_calls(parsed: dict) -> list[str]:
    names = []
    for choice in parsed.get("choices") or []:
        message = choice.get("message") or {}
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            name = function.get("name")
            if name:
                names.append(str(name))
    return names

"""Local-first secret and PII redaction for Loco's data flywheel.

Redaction is lossy by design. It replaces recognisable credentials with a
stable placeholder but never replaces a model's reasoning with fabricated
content. `trace=off` is the caller's privacy lever; `metadata` is the narrow
fallback.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

REDACTED = "[REDACTED]"

_SECRET_PATTERNS = [
    (re.compile(r"(?i)(?:sk-|sk-ant-)[A-Za-z0-9_-]{8,}"), REDACTED),
    (re.compile(r"(?i)(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{8,}"), REDACTED),
    (re.compile(r"(?i)(?:github_pat_)[A-Za-z0-9_]{8,}"), REDACTED),
    (re.compile(r"(?i)(?:xox[baprs]-)[A-Za-z0-9-]{8,}"), REDACTED),
    (re.compile(r"(?i)(?:AKIA|ASIA)[A-Z0-9]{16}"), REDACTED),
    (re.compile(r"(?i)(?:password|passwd|pwd)\s*[=:]\s*['\"]?[^\s'\",]{6,}"),
     lambda m: m.group(0).split('=', 1)[0].split(':', 1)[0] + "=" + REDACTED),
    (re.compile(r"(?i)(?:private[- _]?key|api[- _]?key|secret|token)\s*[=:]\s*['\"]?[^\s'\",]{8,}"),
     lambda m: m.group(0).split('=', 1)[0].split(':', 1)[0] + ": " + REDACTED),
    (re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----",
                re.DOTALL), REDACTED),
    (re.compile(r"(?i)(?:authorization|bearer)\s*[:=]?\s*['\"]?[A-Za-z0-9._~+/-]{12,}"),
     lambda m: m.group(0).split()[0] + " " + REDACTED),
    (re.compile(r"https?://[^\s/@:]+:[^\s/@]+@"), "https://" + REDACTED + "@"),
]

_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)\+?\d{1,4}[\s.\-]?\(?\d{2,4}\)?[\s.\-]?\d{3,4}[\s.\-]?\d{3,4}(?!\w)")
_SSHTHREAD = re.compile(r"(?m)^(.*\.ssh\/authorized_keys.*$)")


def redact_string(value: str) -> str:
    """Redact recognizable secret values while retaining useful structure."""
    out = value or ""
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    out = _EMAIL.sub(REDACTED + "@email.local", out)
    out = _PHONE.sub(REDACTED + "-phone", out)
    out = _SSHTHREAD.sub(REDACTED + " (ssh authorized_keys line)", out)
    return out


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, dict):
        return {str(key): redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    if isinstance(value, float) and (value == float("inf") or value != value):
        return str(value)
    return value


def prompt_hash(messages: Any) -> str:
    """Stable one-way ID for analytics without retaining content."""
    raw = json.dumps(redact_value(messages), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def redact_messages(messages: list[dict]) -> list[dict]:
    return [
        {
            **{k: redact_value(v) for k, v in message.items() if k != "content"},
            "content": redact_value(message.get("content")),
        }
        for message in messages if isinstance(message, dict)
    ]


def safe_json(value: Any) -> str:
    return json.dumps(redact_value(value), ensure_ascii=False, separators=(",", ":"))


def trace_mode(headers: dict, payload: dict | None = None) -> str:
    """Header wins, body may pick; `LOCO_TRACE_DEFAULT` is the final fallback."""
    headers = headers or {}
    raw = headers.get("X-Loco-Trace") or headers.get("x-loco-trace")
    if not raw and isinstance(payload, dict):
        loco = payload.get("loco")
        if isinstance(loco, dict):
            raw = loco.get("trace")
    mode = str(raw or os.environ.get("LOCO_TRACE_DEFAULT", "full")).lower()
    return mode if mode in {"full", "metadata", "off"} else "full"

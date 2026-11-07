"""Stage 2 — deterministic dataset assembly with a held-out split.

Turns curated examples into chat-format JSONL rows and splits them by a
content hash, so the same database always produces the same train/eval files.
The eval split is never trained on; it exists to measure a candidate honestly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ALLOWED_ROLES = {"system", "user", "assistant", "tool"}
DEFAULT_EVAL_PERCENT = 10


def to_row(example: dict) -> dict | None:
    """One curated example -> one chat-format training row."""
    messages = []
    for message in example.get("messages") or []:
        if not isinstance(message, dict):
            return None
        role = str(message.get("role") or "")
        if role not in ALLOWED_ROLES:
            return None
        content = message.get("content")
        if content is None or content == "":
            # Tool-call turns legitimately carry arguments instead of content.
            if role == "assistant" and example.get("tool_calls"):
                content = ""
            else:
                return None
        messages.append({"role": role, "content": content})

    assistant_text = str(example.get("assistant") or "")
    tool_calls = example.get("tool_calls") or []
    if not assistant_text and not tool_calls:
        return None
    if messages and messages[-1]["role"] != "assistant":
        # The recorded assistant turn is the training target; append it when
        # the request messages do not already end with it.
        tail = {"role": "assistant", "content": assistant_text}
        if tool_calls:
            tail["tool_calls"] = tool_calls
        messages.append(tail)

    return {
        "messages": messages,
        "trace_id": example.get("trace_id"),
        "model": example.get("model"),
        "verdict": example.get("verdict"),
        "has_tools": bool(tool_calls),
        "_sha": example.get("_sha") or "",
    }


def split(rows: list[dict], eval_percent: int = DEFAULT_EVAL_PERCENT
          ) -> tuple[list[dict], list[dict]]:
    """Hash-based split: stable across runs, independent of insertion order."""
    eval_percent = max(0, min(int(eval_percent), 50))
    train_rows, eval_rows = [], []
    for row in rows:
        digest = hashlib.sha256(
            (row.get("_sha") or row.get("trace_id") or "").encode()
        ).hexdigest()
        bucket = int(digest[:8], 16) % 100
        (eval_rows if bucket < eval_percent else train_rows).append(
            {k: v for k, v in row.items() if k != "_sha"})
    return train_rows, eval_rows


def write_jsonl(path: str | Path, rows: list[dict]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return destination


def dataset_hash(*paths: str | Path) -> str:
    """Content hash over the written files — provenance for the registry."""
    digest = hashlib.sha256()
    for path in sorted(str(p) for p in paths):
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def build(examples: list[dict], out_dir: str | Path,
          eval_percent: int = DEFAULT_EVAL_PERCENT) -> dict:
    """Write train/eval JSONL and return a manifest with hashes."""
    rows = [row for row in (to_row(example) for example in examples) if row]
    train_rows, eval_rows = split(rows, eval_percent)
    out = Path(out_dir)
    train_path = write_jsonl(out / "train.jsonl", train_rows)
    eval_path = write_jsonl(out / "eval.jsonl", eval_rows)
    return {
        "train_path": str(train_path),
        "eval_path": str(eval_path),
        "train_rows": len(train_rows),
        "eval_rows": len(eval_rows),
        "rows_with_tools": sum(1 for row in rows if row["has_tools"]),
        "dataset_hash": dataset_hash(train_path, eval_path),
    }

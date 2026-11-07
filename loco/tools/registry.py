"""Explicit local-tool registry for the optional Loco tool pack.

The default inference mode passes tools through untouched. These are available
only to callers that send requests to /v1/tools/call. Every entry declares
whether it is safe to run unattended. Unknown names are denied by policy.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .. import memory, semantic
from .calculator import calculate as calculator_calculate
from .search import search as notes_search
from .summarizer import summarize as _summarize


def _tool_summarize(params: dict):
    text = str(params.get("text") or "").strip()
    if not text:
        raise ValueError("text required")
    return {"summary": _summarize(text)}


def _tool_calculate(params: dict):
    expression = str(params.get("expression") or params.get("expr") or "").strip()
    if not expression:
        raise ValueError("expression required")
    return {"result": calculator_calculate(expression)}


def _tool_notes_search(params: dict):
    query = str(params.get("query") or "").strip()
    if not query:
        raise ValueError("query required")
    limit = max(1, min(int(params.get("limit", 5)), 20))
    return {"results": notes_search(query, limit=limit)}


def _tool_memory_search(params: dict):
    query = str(params.get("query") or "").strip()
    if not query:
        raise ValueError("query required")
    k = max(1, min(int(params.get("k", 5)), 16))
    try:
        embedding = semantic.embed_text(query, timeout=10)
        mode = "hybrid"
    except Exception:
        embedding = [0.0] * 384
        mode = "fts_degraded"
    return {"results": memory.hybrid_search(query, embedding, k=k),
            "memory_mode": mode}


def _tool_memory_propose(params: dict):
    content = str(params.get("content") or "").strip()
    if not content:
        raise ValueError("content required")
    try:
        embedding = semantic.embed_text(content)
    except Exception:
        embedding = [0.0] * 384
    proposal_id = memory.propose_note(
        content,
        embedding,
        importance=float(params.get("importance", 0.5)),
        source="tool",
        memory_type=str(params.get("memory_type", "general")),
        reason=params.get("reason") or "Tool-requested local memory",
        metadata={"tool": "memory.propose"},
    )
    return {"proposal_id": proposal_id, "status": "pending", "stored": False}


def _tool_lab_status(params: dict):
    from .. import telemetry
    from ..models import MODEL_SPECS
    gpu = telemetry.snapshot()
    return {
        "gpu": gpu,
        "memory": memory.stats(),
        "models": [spec.id for spec in MODEL_SPECS.values()],
    }


TOOL_REGISTRY = {
    "summarize": {
        "fn": _tool_summarize,
        "description": "Return the first sentences of a local document.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "write": False,
        "approval": False,
    },
    "calculate": {
        "fn": _tool_calculate,
        "description": "Evaluate a bounded arithmetic expression.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string"},
                "expr": {"type": "string"},
            },
            "anyOf": [{"required": ["expression"]}, {"required": ["expr"]}],
            "additionalProperties": False,
        },
        "write": False,
        "approval": False,
    },
    "notes.search": {
        "fn": _tool_notes_search,
        "description": "Keyword-search the local permanent-memory SQLite index.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "write": False,
        "approval": False,
    },
    "memory.search": {
        "fn": _tool_memory_search,
        "description": (
            "Search local memories using semantic and lexical match; "
            "returns only clearly relevant stored items."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "minimum": 1, "maximum": 16},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "write": False,
        "approval": False,
    },
    "memory.propose": {
        "fn": _tool_memory_propose,
        "description": (
            "Create a pending memory proposal for human review; never writes "
            "to permanent memory directly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "importance": {"type": "number", "minimum": 0, "maximum": 1},
                "memory_type": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["content"],
            "additionalProperties": False,
        },
        "write": True,
        "approval": False,
    },
    "lab.status": {
        "fn": _tool_lab_status,
        "description": "Return local GPU, model and memory measurements.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "write": False,
        "approval": False,
    },
}


def list_tool_schemas() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": spec["description"],
                "parameters": spec["input_schema"],
            },
        }
        for name, spec in sorted(TOOL_REGISTRY.items())
    ]


def call_tool(name: str, params: dict | None) -> dict:
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        raise KeyError(f"unknown local tool: {name}")
    started = time.time()
    result = spec["fn"](dict(params or {}))
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
    return {
        "tool": name,
        "status": "completed",
        "result": result,
        "duration_ms": round((time.time() - started) * 1000, 1),
        "bytes": len(encoded),
    }

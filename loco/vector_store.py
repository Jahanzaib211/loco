"""Optional local Qdrant adapter for Loco memory.

Qdrant is a localhost-only vector index, never the source of truth.
SQLite owns note text, proposal state, audit chains and provenance; Qdrant
stores point IDs and vectors only.  If Qdrant is absent, Loco continues with
FTS5 + cosine over SQLite exactly as before.

Configuration is explicit and bounded.  No telemetry, no API keys, no network
fallback: the adapter talks only to the configured URL, defaulting to the
loopback service.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

COLLECTION = "loco_memory"
DEFAULT_URL = "http://127.0.0.1:6333"
TIMEOUT_S = 3.0

# Notes below this count are served by the complete SQLite cosine pass; the
# ANN index only earns its keep once a full scan stops being cheap.
VECTOR_INDEX_MIN_NOTES = 5000

_ensured: dict[int, bool] = {}
_ready_cache: tuple[float, bool] = (0.0, False)
READY_TTL_S = 15.0


def url() -> str:
    return os.environ.get("LOCO_QDRANT_URL", DEFAULT_URL).rstrip("/")


def disabled() -> bool:
    return os.environ.get("LOCO_QDRANT_DISABLED", "").lower() in {"1", "true", "yes"}


def _request(method: str, path: str, payload: dict | None = None,
             timeout: float = TIMEOUT_S):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url() + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def ready() -> bool:
    """Probe readiness, cached briefly so a dead service never stalls callers."""
    global _ready_cache
    if disabled():
        return False
    stamp, cached = _ready_cache
    if time.time() - stamp < READY_TTL_S:
        return cached
    try:
        _request("GET", "/healthz", timeout=1.5)
        result = True
    except Exception:
        # Older releases use /readyz or lack the endpoint; fall back to collections.
        try:
            _request("GET", "/collections", timeout=1.5)
            result = True
        except Exception:
            result = False
    _ready_cache = (time.time(), result)
    return result


def ensure_collection(dim: int, distance: str = "Cosine") -> bool:
    if disabled():
        return False
    dim = int(dim)
    if _ensured.get(dim):
        return True
    try:
        _request("PUT", f"/collections/{COLLECTION}",
                 {"vectors": {"size": int(dim), "distance": distance}})
        _request("PUT", f"/collections/{COLLECTION}/index",
                 {"field_name": "note_id", "field_schema": "keyword"})
        _ensured[dim] = True
        return True
    except urllib.error.HTTPError as exc:
        # Existing collection is fine.
        try:
            body = exc.read().decode(errors="replace")
        except Exception:
            body = ""
        if exc.code == 409 or "already exists" in body.lower():
            _ensured[dim] = True
            return True
        return False
    except Exception:
        return False


def count() -> int:
    """Points currently indexed, or 0 when the index is unavailable."""
    if disabled():
        return 0
    try:
        data = _request("GET", f"/collections/{COLLECTION}")
        return int(data.get("result", {}).get("points_count", 0))
    except Exception:
        return 0


def status() -> dict:
    """Honest status block for health/stats reporting."""
    if disabled():
        return {"configured": False, "enabled": False, "ready": False}
    ready_now = ready()
    return {
        "configured": True,
        "enabled": True,
        "ready": ready_now,
        "url": url(),
        "points": count() if ready_now else 0,
        "min_notes_for_ann": VECTOR_INDEX_MIN_NOTES,
    }


def upsert(note_id: str, vector: list[float]) -> bool:
    if disabled() or not vector:
        return False
    if not ensure_collection(len(vector)):
        return False
    payload = {
        "points": [
            {"id": note_id, "vector": vector, "payload": {"note_id": note_id}}
        ]
    }
    try:
        _request("PUT", f"/collections/{COLLECTION}/points", payload)
        return True
    except Exception:
        return False


def search(vector: list[float], limit: int = 24) -> list[dict]:
    if disabled() or not vector:
        return []
    limit = max(1, min(int(limit), 64))
    payload = {"vector": vector, "limit": limit, "with_payload": True,
               "with_vector": False}
    try:
        data = _request("POST", f"/collections/{COLLECTION}/points/search", payload)
        return [
            {"id": result.get("payload", {}).get("note_id", result.get("id")),
             "score": float(result.get("score", 0.0))}
            for result in data.get("result", [])
        ]
    except Exception:
        return []


def delete(note_id: str) -> bool:
    if disabled():
        return False
    try:
        _request("POST", f"/collections/{COLLECTION}/points/delete",
                 {"points": [note_id]})
        return True
    except Exception:
        return False

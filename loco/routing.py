"""Deterministic router: exact grammar → explicit model → ranked local fallback.

No LLM participates in default routing. The functions in this module return
either a route decision or raise a typed error that the HTTP layer translates to
`503 overloaded`. It never silently substitutes a weaker model for an
explicit request.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import config as C
from .capacity import DEFAULT_HEADROOM_MIB, can_load, query_vram
from .models import (
    AUTO_MODEL,
    MODEL_MIB,                    # re-exported for compatibility
    MODEL_SPECS,
    EmbeddingModelNotChatCapable,
    ModelSpec,
    canonical_model_id,
    require_chat_model,
)

_EVICT_LOCK = threading.Lock()


class UnsupportedCapabilityError(RuntimeError):
    """Raised for loco:auto requests whose capability is not configured."""


class ModelFitError(RuntimeError):
    """Raised when no local model in the candidate chain can safely load."""

    def __init__(self, message: str, *, model: str, free_mib: int | None,
                 required_mib: int | None, fallback_candidates: list[str]):
        super().__init__(message)
        self.model = model
        self.free_mib = free_mib
        self.required_mib = required_mib
        self.fallback_candidates = fallback_candidates


@dataclass(frozen=True)
class RouteDecision:
    model: str
    backend: str
    task: str
    explicit: bool
    confidence: float
    action: str
    reason: str
    vram: dict
    fallback_candidates: tuple[str, ...] = ()

    @property
    def degraded(self) -> bool:
        return bool(self.fallback_candidates)


_RULES = [
    ("vision", re.compile(
        r"image|photo|picture|screenshot|diagram|vision|llava|attached.*\.(png|jpe?g)", re.I)),
    ("math", re.compile(
        r"\d+\s*[+\-*/^]\s*\d+|sqrt|prime|integral|derivative|solve for|equation|modulo"
        r"|factorial|gcd|lcm|probability|calculus|proof", re.I)),
    ("code", re.compile(
        r"```|def |class |function|implement|compile|stack ?trace|segfault|null pointer"
        r"|segv|syntax error|refactor|pull request|debug|fix this|bug report", re.I)),
    ("compact", re.compile(
        r"summari[sz]e|compact|tl;dr|condense|outline|write.*brief", re.I)),
]


def classify(messages: list[dict] | None) -> str:
    """Classify a chat request; unknown/ambiguous input becomes chat."""
    parts = []
    for message in messages or []:
        if isinstance(message, dict):
            parts.append(str(message.get("content", "")))
    text = "\n".join(parts)
    for task, pattern in _RULES:
        if pattern.search(text):
            return task
    if len(text) > 12_000:
        return "longdoc"
    return "chat"


# Hermes 4 14B promoted to chain leader (2026-11-07, measured): agentic tools
# 8/10 (best), role 6/8 (ties Nemotron). Tradeoff: 9.0GB/37t/s/16k ctx vs
# Nemotron 4.3GB/71t/s/32k — higher VRAM, slower, smaller ctx; unguided math
# and safe-002 (forbidden tool) misses observed. Fallbacks preserve prior order.
_CHAINS = {
    "vision": [
        "nemotron-3.5-bolt-e4b",
        "ornith-1.5-9b",
        "lfm3-1.7b",
    ],
    "math": [
        "hermes-4-14b-iq4_xs",
        "nemotron-3.5-bolt-e4b",
        "ornith-1.5-9b",
        "lfm3-1.7b",
    ],
    "code": [
        "hermes-4-14b-iq4_xs",
        "nemotron-3.5-bolt-e4b",
        "ornith-1.5-9b",
        "lfm3-1.7b",
    ],
    "compact": [
        "hermes-4-14b-iq4_xs",
        "ornith-1.5-9b",
        "nemotron-3.5-bolt-e4b",
        "lfm3-1.7b",
    ],
    "longdoc": [
        "hermes-4-14b-iq4_xs",
        "nemotron-3.5-bolt-e4b",
        "ornith-1.5-9b",
        "lfm3-1.7b",
    ],
    "chat": [
        "hermes-4-14b-iq4_xs",
        "nemotron-3.5-bolt-e4b",
        "ornith-1.5-9b",
        "lfm3-1.7b",
    ],
}


def candidate_chain(task: str) -> list[str]:
    return list(_CHAINS.get(task, _CHAINS["chat"]))


def backend_for(model: str) -> str:
    spec = MODEL_SPECS.get(canonical_model_id(model))
    if spec is not None:
        return spec.backend
    if model == "bge-small-en-v1.5":
        return "embed"
    if "gemma" in model.lower():
        return "ollama"
    return "llama-cpp"


def _llama_url() -> str:
    return C.endpoints()["llama-cpp"].rstrip("/")


def _ollama_url() -> str:
    return C.endpoints()["ollama"].rstrip("/")


def is_ollama_resident(model: str) -> bool:
    """Return whether the Ollama service currently has this exact model in VRAM."""
    try:
        with urllib.request.urlopen(
                f"{_ollama_url().removesuffix('/v1')}/api/ps", timeout=5) as response:
            data = json.loads(response.read().decode())
    except Exception:
        return False
    return any(
        (item.get("model") == model or item.get("name") == model)
        for item in data.get("models", [])
    )


def is_llama_resident(model: str) -> bool:
    """Return whether the llama.cpp router currently reports this model loaded.

    The router spawns one child process per model; a resident child already
    occupies VRAM, so admission must not charge it a fresh-load cost again.
    """
    try:
        with urllib.request.urlopen(f"{_llama_url()}/models", timeout=5) as response:
            data = json.loads(response.read().decode())
    except Exception:
        return False
    for entry in data.get("data", []):
        if str(entry.get("id")) != model:
            continue
        status = entry.get("status") or {}
        return status.get("value") == "loaded" and not status.get("failed")
    return False


def try_unload_llama(base: str | None = None) -> int:
    """Unload every non-evicted llama.cpp router child; return estimated MiB freed.

    The llama-server router also performs its own LRU eviction.  This call is
    only used before admitting a new model and is serialized to avoid racing two
    capacity checks.
    """
    base = (base or _llama_url()).removesuffix("/v1")
    try:
        with urllib.request.urlopen(f"{base}/v1/models", timeout=5) as response:
            data = json.loads(response.read().decode())
    except Exception:
        return 0

    freed = 0
    for model in data.get("data", []):
        status = model.get("status") or {}
        if status.get("value") != "loaded" or status.get("failed"):
            continue
        model_id = str(model.get("id"))
        try:
            payload = json.dumps({"model": model_id}).encode()
            request = urllib.request.Request(
                f"{base}/models/unload",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(request, timeout=15).read()
            freed += MODEL_MIB.get(model_id, 0)
        except Exception:
            continue
    return freed


def try_unload_ollama(model: str = "huihui_ai/gemma-4-abliterated:e4b") -> int:
    """Ask Ollama to release a model; return a constant-size retirement estimate."""
    try:
        payload = json.dumps(
            {"model": model, "prompt": "", "stream": False, "keep_alive": 0}
        ).encode()
        request = urllib.request.Request(
            f"{_ollama_url().removesuffix('/v1')}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=30).read()
        return MODEL_MIB.get(model, 9_900)
    except Exception:
        return 0


def _free_capacity(target: ModelSpec, current: dict) -> dict:
    """Make room for one model, then return fresh measurements.

    A failed VRAM query is never laundered into success.  When measurements
    are unavailable the limiter fails closed and the caller gets an honest 503.
    """
    if not current.get("available"):
        return current

    with _EVICT_LOCK:
        # Always prefer releasing the incumbent controlled by this subsystem.
        try_unload_llama()
        time.sleep(1.0)
        fresh = query_vram()

        allowed, _, _ = can_load(
            target, fresh, headroom_mib=DEFAULT_HEADROOM_MIB)
        if allowed:
            return fresh

        # If the target is a llama.cpp model, Ollama may still hold the VRAM.
        if target.backend == "llama-cpp":
            try_unload_ollama()
            time.sleep(1.0)
            fresh = query_vram()
        return fresh


def _raise_overloaded(spec: ModelSpec, current: dict, tried: list[str]) -> None:
    free = current.get("free_mib") if isinstance(current, dict) else None
    raise ModelFitError(
        f"system overloaded: cannot admit {spec.id} "
        f"({spec.vram_mib_est} MiB estimated; free={free} MiB)",
        model=spec.id,
        free_mib=free,
        required_mib=spec.vram_mib_est,
        fallback_candidates=tried,
    )


def _choose(chain: list[str], task: str, explicit: bool) -> RouteDecision:
    if not chain:
        raise ModelFitError(
            "no chat models are configured", model="none",
            free_mib=None, required_mib=None, fallback_candidates=[])

    current = query_vram()
    admitted: list[str] = []

    for model_id in chain:
        spec = MODEL_SPECS[model_id]
        if spec.backend == "ollama" and is_ollama_resident(spec.id):
            return RouteDecision(
                spec.id, spec.backend, task, explicit,
                confidence=1.0 if explicit else 0.85,
                action="ok-resident",
                reason="model is already resident in controlled backend; admission does not recheck fresh-load cost",
                vram=current)
        if spec.backend == "llama-cpp" and is_llama_resident(spec.id):
            return RouteDecision(
                spec.id, spec.backend, task, explicit,
                confidence=1.0 if explicit else 0.85,
                action="ok-resident",
                reason="model is already loaded in the llama router; admission does not recheck fresh-load cost",
                vram=current)
        allowed, reason, measurement = can_load(
            spec, current, headroom_mib=DEFAULT_HEADROOM_MIB)
        if allowed:
            return RouteDecision(
                spec.id, spec.backend, task, explicit,
                confidence=1.0 if explicit else 0.85,
                action="ok", reason=reason, vram=measurement)

        current = _free_capacity(spec, current)
        allowed, reason, fresh_measurement = can_load(
            spec, current, headroom_mib=DEFAULT_HEADROOM_MIB)
        if allowed:
            return RouteDecision(
                spec.id, spec.backend, task, explicit,
                confidence=1.0 if explicit else 0.80,
                action="admitted-after-eviction", reason=reason,
                vram=fresh_measurement, fallback_candidates=tuple(admitted))

        admitted.append(spec.id)
        current = fresh_measurement

    fallback = MODEL_SPECS["lfm3-1.7b"]
    allowed, reason, fresh = can_load(
        fallback, current, headroom_mib=DEFAULT_HEADROOM_MIB)
    if allowed:
        if explicit:
            # An explicit request never silently degrades to another model.
            _raise_overloaded(MODEL_SPECS[chain[0]], current, admitted)
        return RouteDecision(
            fallback.id, fallback.backend, task, explicit,
            confidence=0.65,
            action="degraded-overloaded", reason=reason, vram=fresh,
            fallback_candidates=tuple(admitted))

    _raise_overloaded(MODEL_SPECS[chain[0]], fresh or current, admitted)


def select_model(payload: dict) -> RouteDecision:
    """Resolve `model`, honoring an explicit request above all routing rules."""
    messages = payload.get("messages", [])
    requested = payload.get("model")
    if requested is None or requested == AUTO_MODEL or requested == "auto":
        task = classify(messages)
        if task == "vision":
            raise UnsupportedCapabilityError(
                "loco:auto has no multimodal model configured; "
                "an explicit image-capable local model is required")
        return _choose(candidate_chain(task), task, explicit=False)

    spec = require_chat_model(str(requested))
    # Explicit selection may still need an eviction to admit it, but it never
    # falls back to a different model without the caller asking for `auto`.
    return _choose([spec.id], "explicit", explicit=True)


def resolve_model(payload: dict, cuda_free_mib: int | None = None):
    """Backwards-compatible API returning (model, backend, task).

    cuda_free_mib is ignored: admission always uses a live measurement.  This
    parameter remains so older call sites fail loudly rather than silently
    accepting stale/forged capacity data.
    """
    if cuda_free_mib is not None:
        raise TypeError("resolve_model() always measures VRAM; pass no cuda_free_mib")
    decision = select_model(payload)
    return decision.model, decision.backend, decision.task


def plan(model: str, task: str, gpu_free_mib: int | None = None,
         headroom_mib: int = DEFAULT_HEADROOM_MIB):
    """Single-model admission check used by explicit and compatibility flows."""
    if gpu_free_mib is not None:
        # A supplied number cannot carry availability, name, utilisation or the
        # time the value was measured, so the capacity path ignores it and
        # measures again.  Callers requiring no GPU may use select_model only.
        gpu_free_mib = None
    spec = require_chat_model(model)
    current = query_vram()
    allowed, reason, state = can_load(
        spec, current, headroom_mib=headroom_mib)
    if allowed:
        return "ok", reason
    fresh = _free_capacity(spec, state)
    allowed, reason, _ = can_load(
        spec, fresh, headroom_mib=headroom_mib)
    return ("unload-then-ok", reason) if allowed else ("busy", reason)


def _model_name_for_id(model_id: str) -> str:
    spec = MODEL_SPECS.get(model_id)
    return spec.id if spec else model_id.split("/")[-1].replace(":", " ")


__all__ = [
    "classify", "candidate_chain", "select_model", "resolve_model",
    "backend_for", "plan", "MODEL_MIB", "try_unload_llama",
    "try_unload_ollama", "is_ollama_resident", "is_llama_resident",
    "RouteDecision", "ModelFitError",
    "UnsupportedCapabilityError",
]

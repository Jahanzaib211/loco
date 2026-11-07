"""Canonical local chat-model registry.

Values are measured on this lab (2026-11-05), never inferred from parameter
count alone.  The estimates are the amount of VRAM a resident model consumes,
not the size of the file on disk.  Llama.cpp measurements are the measured GPU
delta at the recommended context/KV setting; Gemma is Ollama-managed and is
conservatively sized from its Ollama-listed 9.6 GB weight payload.

The registry is intentionally data-only.  No network calls, no GPU probes, and
no mutable module-level state live here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class ModelSpec:
    """Static, measured facts about a locally available chat model."""

    id: str
    backend: str
    role: str
    context_tokens: int
    vram_mib_est: int
    tokens_per_second: int | None
    kv_cache: str | None
    aliases: tuple[str, ...] = ()


MODEL_SPECS: dict[str, ModelSpec] = {
    "lfm3-1.7b": ModelSpec(
        "lfm3-1.7b", "llama-cpp", "routing · extraction · fastest",
        131_072, 1_877, 155, "f16", ("lfm3:1.7b",)),
    "phi4-mini-3.8b": ModelSpec(
        "phi4-mini-3.8b", "llama-cpp", "small general · balanced",
        65_536, 7_045, 94, "q8_0", ("phi4-mini:3.8b", "phi4-mini")),
    "bonsai-2-9b": ModelSpec(
        "bonsai-2-9b", "llama-cpp", "mid general · high throughput",
        65_536, 4_129, 92, "f16", ("bonsai-2:9b", "bonsai")),
    "ornith-1.5-9b": ModelSpec(
        "ornith-1.5-9b", "llama-cpp", "mid reasoning · careful",
        65_536, 6_723, 54, "f16", ("ornith-1.5:9b", "ornith")),
    "nemotron-3.5-bolt-e4b": ModelSpec(
        "nemotron-3.5-bolt-e4b", "llama-cpp", "mid general · long context",
        32_768, 4_362, 71, "f16", ("nemotron-3.5-bolt:e4b", "nemotron")),
    "deepseek-r1-14b": ModelSpec(
        "deepseek-r1-14b", "llama-cpp", "hard reasoning · math · code",
        16_384, 10_167, 33, "q8_0", ("deepseek-r1:14b", "deepseek")),
    "huihui_ai/gemma-4-abliterated:e4b": ModelSpec(
        "huihui_ai/gemma-4-abliterated:e4b", "ollama",
        "flagship chat · vision · 131k", 131_072, 9_900, 77,
        None, ("gemma", "gemma-4-abliterated")),
    # Benchmark candidate: measured 8,921 MiB resident delta on this rig
    # (459 -> 9,380 MiB with nothing else loaded) at the 16k/q8_0-KV preset,
    # 23 tok/s generation, 43-104 tok/s prompt. Explicit-selection only and
    # in no routing chain until evals say otherwise.
    "Underdog-Saluki-27B-1.0-IQ2-mix": ModelSpec(
        "Underdog-Saluki-27B-1.0-IQ2-mix", "llama-cpp",
        "benchmark candidate · explicit only",
        16_384, 8_921, 23, "q8_0", ("saluki",)),
    # Hermes 4 14B (NousResearch, qwen3 arch, IQ4_XS file_type 30, 8.11 GB).
    # GGUF header: 40 layers, 5120 emb, 40/8 heads GQA, key 128, 40960 native
    # ctx, text-only (no vision keys). 16k @ q8_0 KV fits 11088 MiB usable
    # (~8995 MiB measured resident delta). Promoted to auto-chain leader
    # (2026-11-07, measured): agentic tools 8/10 best, role 6/8 ties Nemotron;
    # tradeoffs: 9.0GB/37t/s/16k vs Nemotron 4.3GB/71t/s/32k, unguided math +
    # safe-002 (forbidden-tool) misses. Explicit selection always honored.
    "hermes-4-14b-iq4_xs": ModelSpec(
        "hermes-4-14b-iq4_xs", "llama-cpp",
        "chain leader · agentic tools 8/10 · role 6/8 (measured)",
        16_384, 8_995, 37, "q8_0",
        ("hermes4", "hermes-4-14b", "hermes-4", "hermes")),
    # Lab smoke model, CPU only (the test container has no GPU): SmolLM2-135M-
    # Instruct Q4_K_M, 100 MiB, measured 147 tok/s generation on 8 CPU threads
    # with llama.cpp CPU-only. It is a path check, not a quality model: it is in
    # no routing chain and is served only when it is asked for by name.
    "smollm2-135m-instruct": ModelSpec(
        "smollm2-135m-instruct", "llama-cpp",
        "lab smoke · CPU only · explicit only",
        4_096, 0, 147, "f16", ("smollm2",)),
}

# Embedding model is deliberately not a chat-completions target.
EMBEDDING_MODELS = frozenset({"bge-small-en-v1.5"})

AUTO_MODEL = "loco:auto"

# Back-compat constant for the existing VRAM gate and UI.  It uses the same
# measured values as MODEL_SPECS.
MODEL_MIB = {model.id: model.vram_mib_est for model in MODEL_SPECS.values()}

_ALIASES = {
    alias: spec.id
    for spec in MODEL_SPECS.values()
    for alias in spec.aliases
}


class ModelNotFoundError(ValueError):
    """Raised when an explicit, known-by-Loco model identifier is requested."""


class EmbeddingModelNotChatCapable(ValueError):
    """Raised when the embedding model is supplied to the chat endpoint."""


def canonical_model_id(model_id: str) -> str:
    """Resolve a friendly alias to its canonical local model identifier."""
    candidate = model_id.strip()
    return _ALIASES.get(candidate, candidate)


def known_model(model_id: str) -> ModelSpec | None:
    return MODEL_SPECS.get(canonical_model_id(model_id))


def require_chat_model(model_id: str) -> ModelSpec:
    canonical = canonical_model_id(model_id)
    if canonical in EMBEDDING_MODELS or "embed" in model_id.lower():
        raise EmbeddingModelNotChatCapable(
            f"{model_id!r} is an embedding model; use POST /v1/embeddings")
    spec = MODEL_SPECS.get(canonical)
    if spec is None:
        valid = ", ".join(sorted(MODEL_SPECS))
        raise ModelNotFoundError(
            f"unknown model {model_id!r}; valid chat models: {valid}")
    return spec


def chat_model_payload() -> list[dict]:
    """Return the OpenAI-compatible model list for the chat API.

    Includes measured display facts so the UI can be server-driven and never
    drift from the registry when a model is added or re-measured.
    """
    return [
        {
            "id": model.id,
            "object": "model",
            "owned_by": "loco-recipe",
            "permission": [],
            "context_length": model.context_tokens,
            "role": model.role,
            "backend": model.backend,
            "vram_mib_est": model.vram_mib_est,
            "tokens_per_second": model.tokens_per_second,
            "kv_cache": model.kv_cache,
            "aliases": list(model.aliases),
        }
        for model in MODEL_SPECS.values()
    ]


def candidates(backend: str | None = None) -> list[ModelSpec]:
    """Stable iteration order, optionally filtered by backend."""
    return [
        spec for spec in MODEL_SPECS.values()
        if backend is None or spec.backend == backend
    ]


def model_ids() -> list[str]:
    return list(MODEL_SPECS)

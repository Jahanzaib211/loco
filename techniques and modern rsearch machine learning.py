#!/usr/bin/env python3
"""Techniques & modern research (ML/serving) — Loco pipeline field notes.

This is a living, runnable notebook-file for the Loco local-inference lab. It
documents memory/efficiency techniques that matter on *this* rig, records
measured benchmarks (no guesses), and sketches an agentic "self-focus" loop
the pipeline can grow into.

Ground truth sources (factual):
- Local hardware/serve state measured on 2026-11-07 (RTX 3060 12 GB, 30 GiB RAM,
  swap 8 GiB; llama.cpp 0.9.0-dev build 12110, commit cd959bae9).
- llama-server --help for build 12110 (authoritative for available flags).
- Empirical runs against the live router (:8080) / Loco (:8200).
- Web research tool was unavailable (HTTP 402) during this session, so
  technique descriptions are grounded in llama.cpp semantics + measurements;
  items that vary by build/model are labeled "verify on build 12110".

Design goal: help edge/AI-lab-style pipelines be efficient *without* relying on
another LLM — use measurements, headroom, and deterministic policies.

Run:  python3 "techniques and modern rsearch machine learning.py" report
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

RIG = {
    "date": "2026-11-07",
    "gpu": "NVIDIA GeForce RTX 3060 12 GiB (12288 MiB)",
    "ram_gib": 30,
    "swap_gib": 8,
    "llama_cpp": "0.9.0-dev build 12110 (cd959bae9)",
    "stack": "llama-server router :8080 (presets.ini) -> Loco :8200 -> bge embedder :8210",
    "core_strategy": "one resident chat model + VRAM-gated admission/evict (Loco/router)",
}

# --- technique catalog (scoped to this rig / build 12110) --------------------
# Each entry: what it really does, relevant flags (build 12110), fit/risk, and
# how to benchmark it. "Not a VRAM mount" is called out explicitly where people
# expect magic disk<->VRAM tiering.
TECHNIQUES: list[dict[str, Any]] = [
    {
        "id": "kv_quant",
        "name": "KV cache quantization",
        "flags": ["-ctk/--cache-type-k TYPE", "-ctv/--cache-type-v TYPE", "(values like f16,q8_0,q4_0,...)"],
        "reality": "Shrinks KV cache (dominant VRAM growth with ctx). Frees room for larger --ctx-size. q4_0 < q8_0 < f16 VRAM; possible quality/speed tradeoff (verify per model).",
        "fit": "BEST lever for longer context on 12 GB; already used (q8_0) on several models.",
        "risks": "Quality regression on some tasks; measure, don't assume.",
        "benchmark": "Same model+ctx+prompt set: record VRAM delta (nvidia-smi), prompt/gen t/s, and task quality (tool/JSON/math) across f16 vs q8_0 vs q4_0.",
    },
    {
        "id": "ctx_size",
        "name": "Right-size --ctx-size",
        "flags": ["-c/--ctx-size N"],
        "reality": "KV VRAM scales roughly linearly with context. Hermes 40k-native served at 16k/q8_0 measured ~9.0 GB resident; 32k/40k likely exceeds 12 GB on-GPU.",
        "fit": "Use the largest ctx that MEASURES safe (watcher + VRAM delta).",
        "risks": "Over-allocating ctx OOMs or forces offload/thrash.",
        "benchmark": "Sweep ctx (e.g. 8k/16k/24k/32k) at fixed KV type; plot VRAM delta + gen t/s + no-OOM.",
    },
    {
        "id": "ngl_offload",
        "name": "GPU layer split / CPU offload (-ngl)",
        "flags": ["-ngl/--gpu-layers N", "-cmoe/--cpu-moe", "-ncmoe", "-ncffn"],
        "reality": "-ngl 99 = full GPU (fast). Lower -ngl moves layers to RAM/CPU (fits larger KV/model) but throughput drops a lot. MoE-specific offload flags exist (build 12110). NOT disk cache.",
        "fit": "Last resort to fit; prefer KV quant/ctx tuning first.",
        "risks": "Severe slowdown; CPU becomes bottleneck.",
        "benchmark": "At fixed ctx/KV, compare -ngl 99 vs reduced; record VRAM, gen t/s, latency.",
    },
    {
        "id": "mmap_mlock",
        "name": "mmap / mlock paging behavior",
        "flags": ["--mmap / --no-mmap / --mlock", "memory ops (default: mmap unless unsupported)"],
        "reality": "mmap memory-maps weights so the OS can page; helps load flexibility. NOT a live VRAM<->disk tier for active GPU KV. With swap already pressured (5.2 GiB used observed), extra paging risks thrash.",
        "fit": "Keep defaults; mlock can reduce paging if RAM allows, but costs resident RAM.",
        "risks": "no-mmap/mlock can increase RAM pressure; swap thrash hurts everything.",
        "benchmark": "Measure load time, steady-state RAM/swap, and gen t/s with default vs toggles (only if RAM headroom is clear).",
    },
    {
        "id": "prompt_cache_reuse",
        "name": "Prompt/KV reuse (--keep, prefix cache)",
        "flags": ["--keep N", "--flash-attn [on|off|auto]", "slot/cache reuse (server behavior)"],
        "reality": "Reuses cached prefix/KV so repeated/shared prefixes cost far fewer prompt tokens. Cache lives in RAM/VRAM, not disk spillover. Flash Attention (auto) is an efficiency win where supported.",
        "fit": "Already in use (keep 1, q8_0 KV, fa auto). High value for agentic/multi-turn workloads.",
        "risks": "Cache invalidation/context shifts; measure cache hit behavior.",
        "benchmark": "Same long prefix sent twice: compare prompt_n/prompt eval ms/wall time and reported cached_tokens. MEASURED OK on Hermes (506 cached tokens, 428ms->72ms).",
        "measured": {"model": "hermes-4-14b-iq4_xs@16k/q8_0", "reuse1": {"prompt_n": 495, "ms": 428}, "reuse2": {"prompt_n": 6, "cached_tokens": 506, "ms": 72}},
    },
    {
        "id": "one_resident_evict",
        "name": "One-resident admission + LRU/evict",
        "flags": ["Loco control/load/unload, VRAM-gated admission", "router max-instances/models autoload"],
        "reality": "Keep only one big chat model resident; evict before loading another. Prevents multi-model VRAM pileup on 12 GB.",
        "fit": "Core Loco strategy — already implemented. Essential.",
        "risks": "Load latency on switches; mitigated by router presets/keep/warmth where possible.",
        "benchmark": "Switch A->B: measure unload/load time, VRAM peak, correctness.",
    },
    {
        "id": "speculative_decoding",
        "name": "Speculative decoding (draft model)",
        "flags": ["--spec-draft-* family (draft model, threads, kv types) — build 12110 exposes spec-draft params"],
        "reality": "A small draft model proposes tokens a larger target verifies; can raise effective t/s. Costs extra resources (draft model footprint/compute). Does NOT add VRAM for target KV.",
        "fit": "Optional throughput experiment; needs a compatible small draft + VRAM/RAM budget. Tight on 12 GB with a 9 GB target loaded.",
        "risks": "Draft+target may not both fit; acceptance rate varies by task/model pair.",
        "benchmark": "Target-only vs target+draft: gen t/s, latency, VRAM/RAM, output parity (verify on build 12110).",
    },
    {
        "id": "rope_yarn",
        "name": "RoPE scaling / YaRN (context extension)",
        "flags": ["--rope-scaling {none,linear,yarn}", "--rope-scale N", "--yarn-orig-ctx N",
                  "--yarn-ext-factor N", "--yarn-attn-factor N", "--yarn-beta-slow N", "--yarn-beta-fast N"],
        "reality": "Extends usable context beyond (or reshapes) training context. Hermes native 40k (no YaRN needed to reach 40k, but VRAM-limited here); Bonsai native 262k. Quality depends on model/params; verify on build 12110 per architecture (qwen3/qwen35).",
        "fit": "Per-model, only with measured VRAM headroom; pair with KV quant. Not a blanket flip.",
        "risks": "Quality degradation, OOM if ctx scaled up without KV/VRAM budget.",
        "benchmark": "Needle/long-context recall + task quality at target ctx vs baseline; VRAM delta; stability. Requires careful per-model presets.",
    },
    {
        "id": "swap_unified_misconception",
        "name": "RAM/swap 'overflow' & unified memory (misconception check)",
        "flags": ["(OS behavior; not a llama-server VRAM tier)"],
        "reality": "OS swap relieves RAM pressure, NOT live GPU KV capacity. CUDA unified/managed memory exists but is typically slow and not how llama-server is tuned. On this rig swap is already 5.2/8 GiB used — leaning on it risks thrash.",
        "fit": "Do not rely on swap/unified memory as a VRAM extension; use KV quant/ctx/ngl deliberately instead.",
        "risks": "Severe slowdown, instability.",
        "benchmark": "Observe swap growth + gen t/s collapse under pressure (watcher).",
    },
    {
        "id": "headroom_compression_misc",
        "name": "Headroom & non-LLM compression/efficiency (SOTA hygiene)",
        "flags": ["(policy) VRAM headroom floor, bounded max_tokens, streaming, deterministic routing, slot mgmt"],
        "reality": "Keep VRAM headroom (e.g. >=1.2-1.8 GiB free) to avoid OOM spikes; bound max_tokens (reasoning models burn budget); stream; route deterministically; defrag/slot behavior is server-managed (build 12110 marks --defrag-thold deprecated). Weight quant is in the GGUF (e.g. IQ4_XS) — chosen at model selection time.",
        "fit": "Always-on operational discipline; complements all above.",
        "risks": "Too little headroom = OOM on long prompts; too aggressive caps = truncated answers.",
        "benchmark": "Track OOM events=0, p95 latency, truncation rate vs max_tokens/headroom policy (watcher JSONL).",
    },
]

# --- measured benchmark records (append-only in spirit; edit to add runs) -----
@dataclass
class Bench:
    ts: str
    technique: str
    model: str
    config: dict[str, Any]
    vram: dict[str, Any] = field(default_factory=dict)
    perf: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    restored: bool = True


BENCHMARKS: list[Bench] = [
    Bench(
        ts="2026-11-07T12:xxZ",
        technique="baseline+prompt_cache_reuse",
        model="hermes-4-14b-iq4_xs",
        config={"ctx": 16384, "cache_kv": "q8_0", "ngl": 99, "fa": "auto", "keep": 1, "rope_scaling": "default"},
        vram={"baseline_mib": 1004, "loaded_mib": 9999, "delta_mib": 8995, "free_mib": 1945},
        perf={"gen_tps_avg": 37.2, "prompt_tps_avg_sample": 107.6, "reuse1": {"prompt_n": 495, "ms": 428, "cached": 17}, "reuse2": {"prompt_n": 6, "ms": 72, "cached": 506}},
        quality={"text_hello": "pass", "tool_calculate": "pass", "json": "pass", "math_unguided": "miss(14vs15)", "agentic_tool_suite": "8/10"},
        notes="Authoritative baseline on this rig. Prompt/KV reuse confirmed effective.",
    ),
    Bench(
        ts="2026-11-07T13:05Z",
        technique="kv_quant (q8_0 vs q4_0 KV) @16k — ATTEMPT, NOT A VALID A/B",
        model="hermes-4-14b-iq4_xs",
        config={"ctx": 16384, "cache_kv_target": "q4_0", "ngl": 99, "fa": "auto", "keep": 1},
        vram={"q8_0_loaded_mib_measured": 9999, "q8_0_delta_mib": 8995, "q4_0_measured": "NOT OBTAINED"},
        perf={},
        quality={},
        notes=(
            "Patched presets.ini to q4_0, unloaded, reloaded via Loco — but the spawned child "
            "STILL had --cache-type-k/v q8_0 (verified via /proc cmdline). The router "
            "(started with --models-preset) did not apply the edited preset flags on respawn "
            "in this observed behavior. Preset restored to q8_0 and Hermes reloaded to known-good. "
            "FINDING: to A/B KV/ctx/YaRN flag changes, restart the router or use a mechanism "
            "build 12110 honors; do not assume control/load re-reads edited presets. q4_0 KV "
            "savings remain UNMEASURED on this model (expected meaningful, must confirm)."
        ),
        restored=True,
    ),
]


def agentic_self_focus_policy(vram_used_mib: int, vram_total_mib: int = 12288,
                              swap_used_pct: float | None = None) -> dict[str, Any]:
    """Deterministic (non-LLM) policy the pipeline can use to 'focus' itself.

    Given live measurements, choose safe serving knobs. This is the seed of an
    agentic loop: watcher -> policy -> (suggest/apply-with-approval) -> remeasure.
    """
    free = vram_total_mib - vram_used_mib
    used_pct = 100 * vram_used_mib / vram_total_mib
    if used_pct >= 93 or free < 700:
        return {"level": "critical", "action": "evict/unload or refuse new load; do NOT raise ctx",
                "ctx_hint": "hold/decrease", "kv_hint": "prefer q4_0 if reloading"}
    if used_pct >= 85 or free < 1400:
        return {"level": "warn", "action": "no ctx increases; prefer q8_0/q4_0 KV; keep one resident",
                "ctx_hint": "hold", "kv_hint": "q8_0 or q4_0"}
    return {"level": "ok", "action": "may test +1 ctx step WITH measurement; keep headroom >= ~1.2 GiB",
            "ctx_hint": "careful step-up", "kv_hint": "q8_0 baseline; q4_0 if chasing ctx"}


def report() -> str:
    lines = []
    lines.append("# Techniques & modern research — Loco (measured)\n")
    lines.append(f"Rig: {json.dumps(RIG, indent=2)}\n")
    lines.append(f"Techniques catalogued: {len(TECHNIQUES)} | Benchmarks recorded: {len(BENCHMARKS)}\n")
    for t in TECHNIQUES:
        m = " [MEASURED]" if t.get("measured") else ""
        lines.append(f"- **{t['id']}**{m}: {t['name']} — {t['fit']}")
    lines.append("\n## Latest benchmarks (factual)")
    for b in BENCHMARKS:
        lines.append(f"- {b.technique} / {b.model} / {b.config} => vram {b.vram} perf {b.perf} quality {b.quality} | restored={b.restored} | {b.notes}")
    lines.append("\n## Agentic self-focus (deterministic, no LLM)")
    lines.append(json.dumps(agentic_self_focus_policy(9944), indent=2))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "report"
    if cmd == "report":
        print(report())
        return 0
    if cmd == "json":
        print(json.dumps({"rig": RIG, "techniques": TECHNIQUES, "benchmarks": [asdict(b) for b in BENCHMARKS]}, indent=2))
        return 0
    if cmd == "policy":
        used = int(argv[2]) if len(argv) > 2 else 9944
        print(json.dumps(agentic_self_focus_policy(used), indent=2))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

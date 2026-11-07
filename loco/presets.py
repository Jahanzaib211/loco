"""Per-model config: /home/jahanzaib/models/presets.ini (llama.cpp router presets)

Read/write without external deps. The router honors it only when the service
was installed with --models-preset (see scripts/install-preset-unit.sh).
"""
import re
from pathlib import Path

PRESETS = Path("/home/jahanzaib/models/presets.ini")

_FLAG_DOCS = {
    "n-gpu-layers": "GPU layers to offload (0–99, 99 = all)",
    "ctx-size": "Context size in tokens (per-model VRAM cost)",
    "cache-type-k": "KV cache type for K (f16/q8_0/q4_0)",
    "cache-type-v": "KV cache type for V (f16/q8_0/q4_0)",
    "flash-attn": "Flash Attention (on/off/auto)",
    "threads": "CPU threads (default: auto)",
    "batch-size": "Logical batch size",
    "ubatch-size": "Physical ubatch size",
    "keep": "Tokens to keep from system prompt (attention sink)",
    "mlock": "Lock model in RAM (1 = on)",
    "rope-scaling": "RoPE scaling (none/linear/yarn)",
    "parallel": "Parallel slots",
    "no-warmup": "Skip warmup batch",
}

def flag_docs():
    return _FLAG_DOCS

def read() -> dict:
    """Return {section: {key: value}}."""
    out = {}
    if not PRESETS.exists():
        return out
    section = None
    for raw in PRESETS.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[(.+)\]$", line)
        if m:
            section = m.group(1)
            out.setdefault(section, {})
            continue
        if section and "=" in line:
            k, v = line.split("=", 1)
            out[section][k.strip()] = v.strip()
    return out

def write_model(model: str, flags: dict) -> dict:
    """Merge-set flags for a model section; value '__delete__' removes the key."""
    data = read()
    section = data.setdefault(model, {})
    for k, v in flags.items():
        if str(v) == "__delete__":
            section.pop(k, None)
        else:
            section[k] = str(v)
    lines = ["# Loco per-model presets for llama-server router (autodiscovery)",
             "# Managed by Loco — edit in the UI or by hand. Values = measured on this rig.", ""]
    for sec, kv in data.items():
        lines.append(f"[{sec}]")
        for k, v in kv.items():
            lines.append(f"{k} = {v}")
        lines.append("")
    PRESETS.write_text("\n".join(lines))
    return data

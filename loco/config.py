"""Loco configuration: reads ~/models/loco.yaml (PyYAML)."""
import os
from pathlib import Path
import yaml

LOCO_YAML = Path(os.environ.get("LOCO_YAML", str(Path.home() / "models" / "loco.yaml")))

_DEFAULT = {
    "gpu": {"name": "unknown", "vram_mib": 12288, "headroom_mib": 1200},
    "router": {
        "llama_cpp": {"endpoint": "http://127.0.0.1:8080/v1"},
        "ollama": {"endpoint": "http://127.0.0.1:11434/v1"},
    },
    "models": {},
}

def load():
    cfg = dict(_DEFAULT)
    if LOCO_YAML.exists():
        with open(LOCO_YAML) as f:
            raw = yaml.safe_load(f) or {}
        for k, v in raw.items():
            cfg[k] = v if not (isinstance(v, dict) and isinstance(cfg.get(k), dict)) else {**cfg[k], **v}
    return cfg

CFG = load()

def endpoints():
    r = CFG.get("router", {})
    return {
        "llama-cpp": r.get("llama_cpp", {}).get("endpoint", "http://127.0.0.1:8080/v1"),
        "ollama": r.get("ollama", {}).get("endpoint", "http://127.0.0.1:11434/v1"),
    }

def model_table():
    """Flat map of ollama model id -> backend; llama.cpp file stem -> backend."""
    return list(CFG.get("models", {}).keys())

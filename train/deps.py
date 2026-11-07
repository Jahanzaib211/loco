"""Stage 4 (part 1) — dependency check for offline adapter training.

Training needs a GPU stack (torch + transformers + peft) that this project
deliberately does not ship and never auto-installs.  This module only tells
the truth about what is present so `train.py` can fail fast instead of
half-starting.
"""

from __future__ import annotations

import importlib.util

REQUIRED = ("torch", "transformers", "peft", "datasets")
OPTIONAL = ("trl", "bitsandbytes")


def status() -> dict:
    present = {}
    for name in REQUIRED + OPTIONAL:
        try:
            present[name] = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            present[name] = False
    missing_required = [name for name in REQUIRED if not present[name]]
    return {
        "present": present,
        "missing_required": missing_required,
        "ready": not missing_required,
    }


def install_hint(missing: list[str]) -> str:
    return (
        "training dependencies missing: " + ", ".join(missing) + "\n"
        "install them yourself into a dedicated environment, for example:\n"
        "  python3 -m venv .venv-train && . .venv-train/bin/activate\n"
        "  pip install torch transformers peft datasets\n"
        "Loco never installs packages or fetches weights on your behalf."
    )

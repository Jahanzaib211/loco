"""Per-model system prompts — markdown, editable via UI, injected at request time.

The directory is configurable with LOCO_SYSTEM_PROMPTS_DIR so Loco is not pinned
to one user's home directory; the default keeps the original location.
"""
import os
import re
from pathlib import Path

DIR = Path(os.environ.get("LOCO_SYSTEM_PROMPTS_DIR",
                           "/home/jahanzaib/models/system-prompts"))

def _file(model: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", model)
    return DIR / f"{safe}.md"

def get(model: str) -> str | None:
    f = _file(model)
    return f.read_text() if f.exists() else None

def put(model: str, content: str) -> Path:
    DIR.mkdir(parents=True, exist_ok=True)
    f = _file(model)
    f.write_text(content)
    return f

def delete(model: str) -> bool:
    f = _file(model)
    if f.exists():
        f.unlink(); return True
    return False

def list_all() -> list:
    if not DIR.exists():
        return []
    out = []
    for f in sorted(DIR.glob("*.md")):
        out.append({"name": f.stem, "bytes": f.stat().st_size})
    return out

def inject(messages: list, model: str) -> list:
    """If the client sent no system message, prepend the model's md prompt."""
    if not isinstance(messages, list) or not messages:
        return messages
    if any((m.get("role") == "system") for m in messages if isinstance(m, dict)):
        return messages
    prompt = get(model)
    if not prompt or not prompt.strip():
        return messages
    return [{"role": "system", "content": prompt.strip()}, *messages]

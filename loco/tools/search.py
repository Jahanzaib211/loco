"""Search the local notes database (SQLite) for a keyword.

The database is Loco's own memory store (memory/loco.db). The path can be
overridden with LOCO_MEMORY_DB; the default is the repo-local store, the same
file loco/memory.py uses.
"""
import os
import sqlite3
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = os.environ.get("LOCO_MEMORY_DB") or str(_ROOT / "memory" / "loco.db")


def search(query: str, limit: int = 5) -> list:
    """Return up to *limit* notes whose content contains *query* (case‑insensitive)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT id, content FROM notes WHERE lower(content) LIKE ? LIMIT ?",
        (f"%{query.lower()}%", limit),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows
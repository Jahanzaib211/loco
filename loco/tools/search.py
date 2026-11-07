"""Search the local notes database (SQLite) for a keyword."""
import sqlite3

DB_PATH = "/home/jahanzaib/Desktop/Loco/memory/loco.db"


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
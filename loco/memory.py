"""Memory store: pure-local SQLite foundation.

SQLite remains the system of record: notes, exact-response cache, route log,
FTS5 index, supersede metadata.  A stdlib cosine pass is used for semantic
ranking; a deterministic FTS5/BM25 pass provides lexical recall.

An optional loopback vector index (see `vector_store`) may mirror note
vectors for approximate candidate generation once the corpus outgrows a
complete scan.  It is never required: when it is absent or disabled every
read falls back to the SQLite pass above, and no note text ever leaves the
local database.

The write path deliberately distinguishes proposals from committed notes.
Callers may create proposals without mutating durable knowledge; approval
copies the proposed payload into notes atomically and leaves an audit trace.
"""

import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from pathlib import Path

from . import events, vector_store

DB = Path(__file__).resolve().parent.parent / "memory" / "loco.db"
DECAY_HALF_LIFE_HOURS = 24 * 30
DEFAULT_MIN_COSINE = 0.28
MAX_TOP_K = 32

_FTS_TOKEN = re.compile(r"[A-Za-z0-9_]{2,}")


def conn():
    """Open the local DB and ensure forward-compatible schema/migrations."""
    DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB, timeout=30)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS notes(
          id TEXT PRIMARY KEY, content TEXT NOT NULL, ts REAL NOT NULL,
          importance REAL DEFAULT 0.5, pinned INTEGER DEFAULT 0,
          access_count INTEGER DEFAULT 0, last_access REAL,
          embedding BLOB, source TEXT DEFAULT 'chat',
          memory_type TEXT NOT NULL DEFAULT 'general',
          superseded INTEGER NOT NULL DEFAULT 0,
          expires_at REAL,
          metadata TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS kv_cache(
          id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL,
          req_hash TEXT UNIQUE NOT NULL, response TEXT NOT NULL,
          hits INTEGER DEFAULT 0, ts REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS route_log(
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
          task TEXT, model TEXT, backend TEXT, action TEXT,
          prompt_tokens INTEGER, completion_tokens INTEGER,
          latency_ms REAL, cache_hit INTEGER DEFAULT 0, gpu_mib_before INTEGER,
          trace_id TEXT, confidence REAL, memory_mode TEXT
        );
        CREATE TABLE IF NOT EXISTS audit_log(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts REAL NOT NULL,
          event TEXT NOT NULL,
          data_json TEXT NOT NULL,
          prev_hash TEXT,
          hash TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS memory_links(
          source_id TEXT NOT NULL,
          target_id TEXT NOT NULL,
          relation TEXT NOT NULL DEFAULT 'related',
          confidence REAL NOT NULL DEFAULT 0.6,
          PRIMARY KEY(source_id, target_id, relation),
          FOREIGN KEY(source_id) REFERENCES notes(id) ON DELETE CASCADE,
          FOREIGN KEY(target_id) REFERENCES notes(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS memory_proposals(
          id TEXT PRIMARY KEY,
          created_at REAL NOT NULL,
          content TEXT NOT NULL,
          embedding BLOB,
          importance REAL NOT NULL DEFAULT 0.5,
          source TEXT NOT NULL DEFAULT 'api',
          memory_type TEXT NOT NULL DEFAULT 'general',
          reason TEXT,
          metadata TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'pending',
          approved_at REAL,
          rejected_at REAL,
          resolved_by TEXT
        );
        CREATE TABLE IF NOT EXISTS trace_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          trace_id TEXT,
          ts REAL NOT NULL,
          event TEXT NOT NULL,
          payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS schema_meta(
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS notes_pinned_last_access_idx
          ON notes(pinned, last_access DESC);
        CREATE INDEX IF NOT EXISTS proposals_status_created_at_idx
          ON memory_proposals(status, created_at DESC);
        CREATE INDEX IF NOT EXISTS route_log_ts_idx ON route_log(ts DESC);
        CREATE INDEX IF NOT EXISTS trace_events_trace_id_idx ON trace_events(trace_id);
    """)

    _ensure_column(connection, "notes", "memory_type", "TEXT NOT NULL DEFAULT 'general'")
    _ensure_column(connection, "notes", "superseded", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "notes", "expires_at", "REAL")
    _ensure_column(connection, "notes", "metadata", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(connection, "route_log", "trace_id", "TEXT")
    _ensure_column(connection, "route_log", "confidence", "REAL")
    _ensure_column(connection, "route_log", "memory_mode", "TEXT")
    _ensure_fts(connection)
    return connection


def _ensure_column(connection, table, column, declaration):
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _ensure_fts(connection):
    # Standalone FTS keeps the index robust if the content table is migrated.
    exists = connection.execute(
        "SELECT value FROM schema_meta WHERE key=?", ("notes_fts_schema",)
    ).fetchone()
    if not exists:
        connection.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts
            USING fts5(notes_id UNINDEXED, content)
        """)
        connection.execute(
            "INSERT INTO notes_fts(notes_id, content) SELECT id, content FROM notes"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('notes_fts_schema', '1') "
            "ON CONFLICT(key) DO UPDATE SET value='1'"
        )
    connection.executescript("""
        CREATE TRIGGER IF NOT EXISTS notes_fts_ai AFTER INSERT ON notes
        BEGIN
          INSERT INTO notes_fts(notes_id, content) VALUES (new.id, new.content);
        END;
        CREATE TRIGGER IF NOT EXISTS notes_fts_au AFTER UPDATE OF id, content
        ON notes
        BEGIN
          DELETE FROM notes_fts WHERE notes_id = old.id;
          INSERT INTO notes_fts(notes_id, content) VALUES (new.id, new.content);
        END;
        CREATE TRIGGER IF NOT EXISTS notes_fts_ad AFTER DELETE ON notes
        BEGIN
          DELETE FROM notes_fts WHERE notes_id = old.id;
        END;
    """)


def vec_to_blob(v):
    return sqlite3.Binary(json.dumps([float(x) for x in v]).encode())


def blob_to_vec(b):
    return json.loads(bytes(b).decode())


def cosine(a, b):
    dot = 0.0; na = 0.0; nb = 0.0
    for x, y in zip(a, b):
        dot += x * y; na += x * x; nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _json_load(raw, default=None):
    try:
        return json.loads(raw) if raw is not None else default
    except (TypeError, json.JSONDecodeError):
        return default


def _audit_payload_from(conn, data):
    row = conn.execute(
        "SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prev_hash = row[0] if row else "GENESIS"
    payload = {"ts": time.time(), "event": data["event"], "data": data["data"]}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256((prev_hash + "|" + encoded.decode()).encode()).hexdigest()
    return prev_hash, digest, json.dumps(payload["data"], sort_keys=True)


def audit(event, data, connection=None):
    """Append a tamper-evident audit entry; caller may pass an open transaction."""
    payload = {"event": event, "data": data or {}}
    if connection is not None:
        prev_hash, digest, data_json = _audit_payload_from(connection, payload)
        cursor = connection.execute(
            "INSERT INTO audit_log(ts, event, data_json, prev_hash, hash) "
            "VALUES (?,?,?,?,?)",
            (time.time(), event, data_json, prev_hash, digest),
        )
        return cursor.lastrowid

    with conn() as connection:
        return audit(event, data, connection)


def _touch_note(connection, note_id):
    connection.execute(
        "UPDATE notes SET access_count = access_count + 1, last_access = ? WHERE id = ?",
        (time.time(), note_id),
    )


def _memory_row(row):
    (nid, content, ts, importance, access_count, last_access, emb, pinned,
     memory_type, superseded) = row
    return {
        "id": nid,
        "content": content,
        "ts": ts,
        "importance": float(importance),
        "access_count": access_count,
        "last_access": last_access,
        "pinned": bool(pinned),
        "memory_type": memory_type,
        "superseded": bool(superseded),
        "embedding": blob_to_vec(emb) if emb else [],
    }


_MEMORY_COLUMNS = (
    "id, content, ts, importance, access_count, last_access, embedding, pinned, "
    "memory_type, superseded")


def _effective_importance(row, now=None):
    now = now or time.time()
    last = row.get("last_access") or row["ts"]
    age_hours = max(0.0, (now - last) / 3600.0)
    return row["importance"] * (0.5 ** (age_hours / DECAY_HALF_LIFE_HOURS))


def _bm25_ranked_ids(connection, query, limit):
    tokens = list(dict.fromkeys(_FTS_TOKEN.findall(query.lower())))[:12]
    if not tokens:
        return []
    match = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)
    try:
        rows = connection.execute(
            "SELECT notes_id, bm25(notes_fts) AS rank"
            " FROM notes_fts WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?",
            (match, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return rows


def _keyword_scores(connection, query, limit=12):
    """Return normalized lexical scores in [0, 1], with 1 = strongest."""
    ranked = _bm25_ranked_ids(connection, query, limit)
    if not ranked:
        return {}
    ranks = [float(rank) for _, rank in ranked]
    # BM25 lower is better. Normalize by worst/ best positions, avoiding ties.
    low, high = min(ranks), max(ranks)
    if high == low:
        return {note_id: 1.0 for note_id, _ in ranked}
    return {
        note_id: (high - float(rank)) / (high - low)
        for note_id, rank in ranked
    }


def _as_requested_pinned(include_pinned, row):
    # include_pinned=False restricts results to non-pinned notes.
    return include_pinned or not row["pinned"]


def _index_vector(note_id, embedding):
    """Best-effort mirror of a committed note vector into the optional ANN index.

    Failure here never fails the write: SQLite already owns the truth.
    """
    try:
        vector = [float(x) for x in (embedding or [])]
    except (TypeError, ValueError):
        return False
    if not vector:
        return False
    try:
        return bool(vector_store.upsert(str(note_id), vector))
    except Exception:
        return False


_ann_probe = {"ts": 0.0, "total": -1}
_ANN_COUNT_TTL_S = 60.0


def _ann_candidate_ids(vector, k):
    """Candidate ids from the optional ANN index, or None for a full scan.

    The index is only consulted once the corpus is large enough that a
    complete SQLite cosine pass stops being the cheaper option.
    """
    if not vector or not vector_store.ready():
        return None
    now = time.time()
    if _ann_probe["total"] < 0 or now - _ann_probe["ts"] > _ANN_COUNT_TTL_S:
        try:
            with conn() as c:
                total = c.execute(
                    "SELECT count(*) FROM notes WHERE superseded = 0"
                ).fetchone()[0]
        except sqlite3.Error:
            return None
        _ann_probe["ts"] = now
        _ann_probe["total"] = int(total)
    if _ann_probe["total"] < vector_store.VECTOR_INDEX_MIN_NOTES:
        return None
    try:
        hits = vector_store.search(vector, limit=max(int(k) * 3, 24))
    except Exception:
        return None
    ids = [str(hit["id"]) for hit in hits if hit.get("id")]
    return ids or None


def search(embedding, k=8, min_cos=DEFAULT_MIN_COSINE, include_pinned=True):
    """Semantic fallback used by the sidecar path and legacy call sites."""
    k = max(1, min(int(k), MAX_TOP_K))
    q = [float(x) for x in embedding]
    with conn() as c:
        rows = c.execute(
            f"SELECT {_MEMORY_COLUMNS} FROM notes WHERE superseded = 0"
        ).fetchall()
    now = time.time()
    scored = []
    for row in map(_memory_row, rows):
        if not _as_requested_pinned(include_pinned, row):
            continue
        vector = row["embedding"]
        cos = cosine(q, vector) if vector else 0.0
        if cos < min_cos and not row["pinned"]:
            continue
        importance = _effective_importance(row, now)
        relevance = 0.75 * max(cos, 0.0) + 0.25 * importance
        if row["pinned"]:
            relevance += 0.3
        scored.append({**{k2: v for k2, v in row.items() if k2 != "embedding"},
                       "cosine": round(cos, 4),
                       "keyword": 0.0,
                       "score": round(relevance, 4)})
    scored.sort(key=lambda r: r["score"], reverse=True)
    cleaned = []
    with conn() as c:
        for item in scored[:k]:
            _touch_note(c, item["id"])
            cleaned.append(item)
    return cleaned


def hybrid_search(query, embedding, k=8, min_score=0.15, include_pinned=True):
    """FTS5 + semantic ranking with pinned and supersede handling."""
    k = max(1, min(int(k), MAX_TOP_K))
    vector = [float(x) for x in embedding]
    now = time.time()
    ann_ids = _ann_candidate_ids(vector, k)
    with conn() as c:
        lexical = _keyword_scores(c, str(query), limit=max(12, k * 2))
        column_set = {
            "id", "content", "ts", "importance", "access_count",
            "last_access", "embedding", "pinned", "memory_type", "superseded",
        }
        if ann_ids:
            # ANN supplies candidates; pinned notes stay eligible even if the
            # approximate index missed them.
            placeholders = ",".join("?" * len(ann_ids))
            rows = c.execute(
                f"SELECT {_MEMORY_COLUMNS} FROM notes WHERE superseded = 0 "
                f"AND (id IN ({placeholders}) OR pinned = 1)",
                list(ann_ids),
            ).fetchall()
        else:
            rows = c.execute(
                f"SELECT {_MEMORY_COLUMNS} FROM notes WHERE superseded = 0"
            ).fetchall()

    candidates = []
    for row in map(_memory_row, rows):
        if not _as_requested_pinned(include_pinned, row):
            continue
        emb = row["embedding"]
        semantic_score = max(cosine(vector, emb), 0.0) if emb else 0.0
        keyword_score = lexical.get(row["id"], 0.0)
        effective = _effective_importance(row, now)
        # Pinned documents are eligible even below the minimum threshold.
        score = (0.50 * semantic_score + 0.25 * keyword_score +
                 0.15 * effective + 0.10 * min(row["access_count"] / 10.0, 1.0))
        if row["pinned"]:
            score += 0.3
        if score < min_score and not row["pinned"]:
            continue
        if semantic_score < DEFAULT_MIN_COSINE and keyword_score <= 0.0 \
                and not row["pinned"]:
            continue
        candidates.append({**{key: value for key, value in row.items()
                             if key != "embedding"},
                          "cosine": round(semantic_score, 4),
                          "keyword": round(keyword_score, 4),
                          "score": round(score, 4)})

    candidates.sort(key=lambda r: r["score"], reverse=True)
    top = candidates[:k]
    with conn() as c:
        for item in top:
            _touch_note(c, item["id"])
    return top


def add_note(content, embedding, importance=0.5, pinned=0, source="chat",
             memory_type="general", audit_entry=True, connection=None):
    note_id = str(uuid.uuid4())
    now = time.time()
    metadata = json.dumps({}, sort_keys=True)
    values = (
        note_id, content, now, float(importance), bool(pinned), 0, now,
        vec_to_blob(embedding), source, str(memory_type), 0, metadata)

    def _insert(c):
        c.execute(
            "INSERT INTO notes(id, content, ts, importance, pinned, access_count, "
            "last_access, embedding, source, memory_type, superseded, metadata) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            values,
        )
        if audit_entry:
            audit("memory_note_added",
                  {"id": note_id, "source": source, "memory_type": memory_type,
                   "importance": float(importance), "pinned": bool(pinned)},
                  c)
        return note_id

    if connection is not None:
        return _insert(connection)
    with conn() as c:
        inserted = _insert(c)
    _index_vector(note_id, embedding)
    events.publish("memory", action="add", id=note_id, source=source)
    return inserted


def propose_note(content, embedding, importance=0.5, source="api",
                 memory_type="general", reason=None, metadata=None):
    proposal_id = str(uuid.uuid4())
    with conn() as c:
        c.execute(
            "INSERT INTO memory_proposals(id, created_at, content, embedding, "
            "importance, source, memory_type, reason, metadata, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (proposal_id, time.time(), content, vec_to_blob(embedding),
             float(importance), source, memory_type, reason,
             json.dumps(metadata or {}, sort_keys=True), "pending"),
        )
        audit("memory_proposal_created",
              {"id": proposal_id, "source": source, "reason": reason,
               "memory_type": memory_type}, c)
    events.publish("memory", action="proposal", id=proposal_id, source=source)
    return proposal_id


def list_proposals(status="pending", limit=25):
    limit = max(1, min(int(limit), 100))
    with conn() as c:
        rows = c.execute(
            "SELECT id, created_at, content, importance, source, memory_type, "
            "reason, metadata, status FROM memory_proposals "
            "WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    return [
        {"id": row[0], "created_at": row[1], "content": row[2],
         "importance": round(row[3], 4), "source": row[4], "memory_type": row[5],
         "reason": row[6], "metadata": _json_load(row[7], {}), "status": row[8]}
        for row in rows
    ]


def approve_proposal(proposal_id, resolved_by="user", content_override=None):
    """Convert a proposal to a committed note exactly once.

    When `content_override` is provided the vector is rebuilt so the committed
    retrieval index remains consistent with the approved text.
    """
    with conn() as c:
        row = c.execute(
            "SELECT id, content, embedding, importance, source, memory_type "
            "FROM memory_proposals WHERE id = ? AND status = 'pending'",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return None
        _, content, embedding, importance, source, memory_type = row
        if content_override is not None and str(content_override).strip():
            content = str(content_override).strip()
            try:
                from . import semantic
                embedding = vec_to_blob(semantic.embed_text(content))
            except Exception as exc:
                raise RuntimeError(
                    f"embedding backend unavailable while approving edited proposal: {exc}") from exc
        note_id = add_note(
            content,
            blob_to_vec(embedding) if embedding else [],
            importance=importance,
            source=source,
            memory_type=memory_type,
            audit_entry=False,
            connection=c,
        )
        c.execute(
            "UPDATE memory_proposals SET status = 'approved', approved_at = ?, "
            "resolved_by = ? WHERE id = ?",
            (time.time(), resolved_by, proposal_id),
        )
        audit("memory_proposal_approved",
              {"proposal_id": proposal_id, "note_id": note_id,
               "resolved_by": resolved_by}, c)
    _index_vector(note_id, blob_to_vec(embedding) if embedding else [])
    events.publish("memory", action="proposal-approved", id=proposal_id)
    return note_id


def reject_proposal(proposal_id, resolved_by="user"):
    with conn() as c:
        updated = c.execute(
            "UPDATE memory_proposals SET status = 'rejected', rejected_at = ?, "
            "resolved_by = ? WHERE id = ? AND status = 'pending'",
            (time.time(), resolved_by, proposal_id),
        ).rowcount
        if not updated:
            return False
        audit("memory_proposal_rejected",
              {"proposal_id": proposal_id, "resolved_by": resolved_by}, c)
    events.publish("memory", action="proposal-rejected", id=proposal_id)
    return True


def reinforce(note_ids):
    ids = [str(i) for i in note_ids]
    if not ids:
        return
    with conn() as c:
        c.execute(
            "UPDATE notes SET access_count = access_count + 1, last_access = ? "
            "WHERE id IN (%s)" % ",".join("?" * len(ids)),
            [time.time(), *ids],
        )
        audit("memory_reinforced", {"ids": ids, "count": len(ids)}, c)
    events.publish("memory", action="reinforce", count=len(ids))


def stats():
    with conn() as c:
        notes = c.execute("SELECT count(*) FROM notes WHERE superseded = 0").fetchone()[0]
        superseded = c.execute("SELECT count(*) FROM notes WHERE superseded = 1").fetchone()[0]
        pinned = c.execute("SELECT count(*) FROM notes WHERE pinned = 1 AND superseded = 0").fetchone()[0]
        proposals = c.execute("SELECT count(*) FROM memory_proposals WHERE status = 'pending'").fetchone()[0]
        cache_rows = c.execute("SELECT count(*), coalesce(sum(hits),0) FROM kv_cache").fetchone()
        route_rows = c.execute("SELECT count(*) FROM route_log").fetchone()[0]
        audit_rows = c.execute("SELECT count(*) FROM audit_log").fetchone()[0]
    return {"notes": notes, "superseded": superseded, "pinned": pinned,
            "pending_proposals": proposals,
            "cache_entries": cache_rows[0], "cache_hits": cache_rows[1],
            "route_log_rows": route_rows, "audit_entries": audit_rows,
            "vector_index": vector_store.status()}


def rebuild_vector_index():
    """Mirror every live note vector into the optional ANN index.

    Used for first-time enablement and after restoring a database: SQLite
    remains authoritative, this only brings the mirror up to date.
    """
    status = vector_store.status()
    if not status.get("ready"):
        return {"indexed": 0, "total": 0, "ready": False, "reason": "index unavailable"}
    with conn() as c:
        rows = c.execute(
            f"SELECT {_MEMORY_COLUMNS} FROM notes WHERE superseded = 0"
        ).fetchall()
    indexed = 0
    for row in map(_memory_row, rows):
        if _index_vector(row["id"], row["embedding"]):
            indexed += 1
    return {"indexed": indexed, "total": len(rows), "ready": True}


def req_hash(model, messages):
    blob = json.dumps({"model": model, "m": messages}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def cache_get(model, messages):
    h = req_hash(model, messages)
    with conn() as c:
        row = c.execute(
            "SELECT response FROM kv_cache WHERE model = ? AND req_hash = ?", (model, h),
        ).fetchone()
        if row:
            c.execute(
                "UPDATE kv_cache SET hits = hits + 1 WHERE model = ? AND req_hash = ?",
                (model, h),
            )
            return row[0]
    return None


def cache_put(model, messages, response):
    h = req_hash(model, messages)
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO kv_cache(model, req_hash, response, ts) VALUES (?,?,?,?)",
            (model, h, response, time.time()),
        )


def log_route(task, model, backend, action, prompt_tokens, completion_tokens,
              latency_ms, cache_hit=0, gpu_mib_before=None, trace_id=None,
              confidence=None, memory_mode=None):
    with conn() as c:
        c.execute(
            "INSERT INTO route_log(ts, task, model, backend, action, prompt_tokens,"
            " completion_tokens, latency_ms, cache_hit, gpu_mib_before,"
            " trace_id, confidence, memory_mode)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), task, model, backend, action, prompt_tokens,
             completion_tokens, latency_ms, cache_hit, gpu_mib_before,
             trace_id, confidence, memory_mode),
        )
    events.publish("route", task=task, model=model, backend=backend, action=action,
                   latency_ms=round(latency_ms, 1) if latency_ms else None, cache_hit=cache_hit)


def record_trace(trace_id, event, payload, connection=None):
    from .redact import redact_value
    encoded = json.dumps(redact_value(payload or {}), sort_keys=True,
                         separators=(",", ":"))
    def _insert(c):
        c.execute(
            "INSERT INTO trace_events(trace_id, ts, event, payload) VALUES (?,?,?,?)",
            (trace_id, time.time(), event, encoded),
        )
    if connection is not None:
        _insert(connection)
        return
    with conn() as c:
        _insert(c)

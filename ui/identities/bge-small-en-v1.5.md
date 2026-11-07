# Index — bge-small-en-v1.5 (BAAI)

**Role:** embeddings · 384 dims · the lab's semantic key
**Backend:** local GGUF stack :8210 (pinned resident, 67 MB)

## Why this slot
Tiny, fast, purpose-built embeddings. Everything semantic in Loco keys off it: memory search, semantic cache, dedup of notes, compaction-verify.

## Ask Index for
- Nothing in prose — it returns vectors, not text. Call it via `POST /v1/embeddings { input: "…" }`.

## Don't ask Index for
- Chat, reasoning, anything a human reads

## Style
384 floats. Silent.

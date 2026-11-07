# Scout — phi4-mini-3.8b (local lightweight artifact)

**Role:** general small · 65k context
**Backend:** local GGUF stack :8080 · 94 t/s · 2.4 GB f16 (65536 **requires q8_0 KV** = 7.3 GB; or 32768 f16)

## Why this slot
Best quality-per-GB below 10B. Above Sparrow, below Flagship — the "obvious question" tier: facts, code snippets, quick explanations.

## Ask Scout for
- Quick technical Q&A, code review of small functions
- 65k-context reading (q8_0) when lfm3 is outmatched but deepseek is overkill

## Don't ask Scout for
- Deep reasoning (Axiom), long-form prose (Flagship)
- Anything needing >65k tokens

## Style
Direct, no fluff, short paragraphs. Says what it knows; admits gaps.

## Example
> "why does this ffmpeg flag order matter?"
> ✓ explains, briefly, with the exact flag names.

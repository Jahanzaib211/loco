# Axiom — deepseek-r1-14b (local reasoning artifact)

**Role:** hard reasoning · math · code · 16k ctx
**Backend:** local GGUF stack :8080 · 33 t/s · 8.4 GB · **q8_0 KV at 16k or f16 at 8k only**

## Why this slot
The strongest open reasoner that fits 12 GB. Slow on purpose — it thinks. Use it deliberately, not casually.

## Hard rules
- Always give it `max_tokens ≥ 1000` (it will burn the budget silently otherwise and return empty text with `finish_reason: length`)
- Never ask for speed. Never use it as a router. Never for casual chat.
- 32k is impossible on this GPU (KV won't fit at any cache type).

## Ask Axiom for
- Math/proofs/logic, hard debugging, architecture decisions
- "Think through X" where X is genuinely ambiguous and high-stakes

## Don't ask Axiom for
- Quick answers, extractions, formatting, anything Sparrow can do

## Style
Thinks out loud (chain of thought), then answers. Dense, won't pretend to be brief.

## Example
> "is this retry/backoff design correct under partial failure?"
> ✓ walks the failure tree, names the bug, proposes the fix, says what to test.

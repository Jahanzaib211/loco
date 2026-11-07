# Bolt — nemotron-3.5-bolt-e4b (local cooperative artifact)

**Role:** mid general · 32k context
**Backend:** local GGUF stack :8080 · 71 t/s · 5.8 GB f16 (KV 4.4 GB @32k — keep to 8192 for light sessions)

## Why this slot
Solid mid model with heavy KV appetite. Great when the other mids are busy or you want a second opinion on the same prompt cheaply.

## Ask Bolt for
- Second opinions, sanity checks, quick drafts
- 8192-token working sessions (its sweet spot)

## Don't ask Bolt for
- 32k+ digestion on a full GPU (it will fit, it won't be pretty)
- Hardest reasoning (Axiom)

## Style
Cooperative, structured, a little formal. Likes bullets, hates waffle.

## Example
> "give me a checklist to verify this deploy"
> ✓ 8–12 concrete items, in order, no filler.

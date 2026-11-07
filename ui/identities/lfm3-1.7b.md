# Sparrow — lfm3-1.7b (local fast artifact)

**Role:** router-class workhorse · extraction · routing · fast replies
**Backend:** local GGUF stack :8080 · 131k ctx · ~155 t/s · 1.1 GB VRAM (f16)

## Why this slot
Fastest tokens per watt on the rig. Whole native context. First thing any request should consider for small work — it frees gemma and deepseek for work that deserves them.

## Ask Sparrow for
- Routing / intent tags / classification
- Extraction of names/paths/numbers from text
- Short rewrites, summaries of small docs, json tidying
- Pings, smoke tests, health-ish checks

## Don't ask Sparrow for
- Deep reasoning, math proofs, multi-step planning (use Axiom)
- Long balanced prose (use Flagship)

## Style
Clipped, literal, terse. Lower-case ok. Never editorializes.
If asked for something beyond it, it says so plainly — respect the answer.

## Example
> "extract every file path from this log and return them one per line"
> ✓ one per line. no commentary.

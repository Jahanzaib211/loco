# Contributing to Loco

Thanks for helping. Loco is a local-first inference lab: one endpoint, no
network egress, deterministic routing, and a memory system that proposes
rather than writes.

## Ground rules

1. **Local only.** No code path may make an outbound network call at runtime.
   The server binds `127.0.0.1` and serves from disk.
2. **Measured, not guessed.** Every claim in the UI or docs must come from a
   recorded run in `results/`. If a number isn't there, it didn't happen.
3. **Deterministic routing.** Explicit model selection always wins. The
   router never silently substitutes a different artifact.
4. **Never write durable memory silently.** Facts go to the proposal queue
   for approval.
5. **Refuse rather than OOM.** If VRAM headroom cannot be secured, return
   `503` with measured numbers.

## Getting set up

```bash
python3 -m loco                 # server on 127.0.0.1:8200
python3 test_all.py             # end-to-end suite (30 checks)
node scripts/build-ui           # rebuild ui/*.js from ui/ts/*.ts
```

The UI is authored in TypeScript and compiled with Node's built-in type
stripper — no npm install, no bundler. See `ui/ts/`.

## Before you open a pull request

- `python3 test_all.py` passes (all 30 checks).
- `node scripts/build-ui` succeeds and `git diff` shows no stray `ui/*.js`
  drift — the compiled files are committed alongside their sources.
- New server routes are documented in `scripts/build-ui`'s `META` table. The
  build **fails** if a route exists without docs, or docs exist without a
  route. This keeps the API catalog honest.
- User-visible copy avoids vendor or product names in prose. Artifact
  identifiers may appear in code and configuration.

## Commit style

Short, imperative subject lines. Explain *why* in the body when the change
is not obvious.

## Reporting bugs

Include the output of `curl -s 127.0.0.1:8200/health` and
`curl -s 127.0.0.1:8200/v1/control/status`, plus the exact request that
misbehaved.

## License

By contributing you agree that your contributions are licensed under the
Apache License, Version 2.0.

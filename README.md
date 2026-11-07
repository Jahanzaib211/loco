# Loco — local inference lab, one endpoint

<p align="left">
  <img src="docs/wordmark.svg" width="460" alt="Loco — local inference lab" />
</p>

Single local inference API for the lab. Local artifacts are selected by you—or
measured to fit by Loco—never by hidden substitution. A local memory system
proposes facts for you to approve; it never writes to long-term context on its
own.

```text
POST 127.0.0.1:8200/v1/chat/completions   standard chat/SSE route
POST 127.0.0.1:8200/v1/control/load|unload model lifecycle (UI or curl)
GET  /v1/control/status                   loaded state + GPU
POST 127.0.0.1:8200/v1/embeddings          local embedding service
GET  /ui                                   lab UI
WS   /ws                                  GPU/route/memory updates
POST 127.0.0.1:8200/v1/memory/add|search|reinforce|stats
POST 127.0.0.1:8200/v1/memory/proposals    pending durable-memory review
GET  /health                               real probe status
GET  /metrics                             Prometheus text
POST 127.0.0.1:8200/v1/compact             bounded summarization
```

## The lab UI

Open `http://127.0.0.1:8200/ui/` — a zero-dependency, local-only console.

- **Lab overview** — a live 3D pipeline: requests become packets that travel
  from your apps through the router, the VRAM gate, the chosen brain and into
  the memory drum. A heart-monitor strip charts route latency in real time.
- **API catalog** — every route the server dispatches, generated from
  `loco/server.py` itself, with a live try-it console. It cannot drift.
- **Chat, memory, compactor, embeddings, tools** — each page carries its own
  scoped 3D view of exactly what that subsystem is doing, fed by the same
  `/ws` event stream.

No CDNs, no web fonts, no telemetry. The UI is TypeScript compiled by
`node scripts/build-ui` using Node's built-in type stripper — no npm install,
no bundler.

## License

Apache License 2.0 — see [`LICENSE.txt`](LICENSE.txt) and [`NOTICE`](NOTICE).

Model artifacts are **not** bundled. Loco references local model files you
download yourself; any artifact remains under its own terms, which apply
independently of this project's license.


## Why it refuses rather than OOM

Before admitting any local artifact, the router:

1. reads a live GPU sample and requires a reserved headroom;
2. unloads incumbent controlled-backends only for an admitted target;
3. refuses the request with measured numbers when nothing fits;
4. returns cache-only results only for an exact temperature-zero prompt.

## Layout

```text
loco/             Python package
loco/server.py    routes · admission · trace · memory hand-off
loco/routing.py   deterministic classifier + admission
loco/capacity.py  live GPU query; fails closed
loco/models.py    local artifact registry and cost model
loco/memory.py    notes · hybrid retrieval · proposals · audit
loco/tools/       reviewed local tool pack
models/           local artifact files (user-selected)
results/          bench and failure records
memory/           SQLite database
scripts/          service launch helpers and export tools
docs/             architecture · implementation · research boundary
```

Artifact identifiers remain in configuration for compatibility and reproducibility;
the project avoids endorsing or privileging their vendors.

## Deterministic safety rules

- Explicit target cannot be silently replaced.
- If current GPU cannot be measured, no model is admitted.
- Cached responses bypass model loading but are identified as cached.
- Empty local model answers are marked; memory is not written from uncertain
  output automatically.
- Every action item is tagged accepted, rejected, or approved after review.

## Start

```sh
scripts/embed-server.sh &
scripts/start-loco.sh
```

See `docs/AGENTIC_API.md` for the full contract and `docs/RESEARCH.md` for the
first-principles build scope.

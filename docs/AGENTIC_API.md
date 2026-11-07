# Loco — Agentic Inference API and Data Flywheel

**Status:** implementation plan derived from source audits and measured local tests. It is the design contract for Loco, not a claim that everything is shipped.

## Product statement

Loco is the single local inference and agentic interaction plane for the lab. It:

1. speaks the standard chat/embeddings interface most local clients expect;
2. routes deterministically to local models;
3. optionally retrieves its own local memory;
4. emits tool calls for any harness to consume;
5. never executes a host action without an explicit local toolpack and approval gate;
6. records useful, redacted execution traces so future models can learn from successful agent behavior.

Loco is not a copy of any third-party agent project, and it is not an opaque “memory before every request” service. It is a transparent endpoint with opt-in enhancement.

## Non-goals

- No network/vector service. Locality and reliability beat unproven sophistication.
- No secondary identity payload injected into every request unless configured by the user.
- No LAN or remote-bind path by default. Bind `127.0.0.1` unless explicitly changed.
- No import of private development snapshots or operator files without explicit review.
- No automatic host shell. Harness owns host execution. Loco’s local toolpack is allowlisted and gated.
- No fake metrics, no fabricated “healthy” responses.
- No silent partial model load.
- No hidden fallbacks. Degradation is explicit, never a silent swap.

## Core invariant

> **An API call must either do what it claims, or tell the caller exactly what it did, what it could not do, and why.**

This means:

- every response carries a routed model, never a misleading “whatever answered”;
- 503 is used for overload and for memory-dependent feature failure;
- headers may describe route/memory/trace mode but do not change the request result silently;
- unavailable measurements are marked `unavailable` rather than replaced with plausible constants;
- persisted memory is never written from an unapproved or confidence-poor model response.

## Planes

### 1. Inference plane

```text
POST /v1/chat/completions
POST /v1/embeddings
GET  /v1/models
```

Standard chat-completions payloads and streams remain the primary API. A harness that knows nothing about Loco should work without special code.

`loco` is an optional namespaced extension in the request body for extra behavior:

```json
{
  "model": "loco:auto",
  "messages": [...],
  "stream": true,
  "loco": {
    "route": "auto",
    "memory": "search|inject|off|propose",
    "trace": "full|metadata|off",
    "tools": "passthrough|local"
  }
}
```

Default behavior remains explicit and inspectable:

- `route=auto` only when the requested model is omitted or is `loco:auto`;
- `memory=search` may return retrieval metadata but must not inject unrequested facts;
- `memory=inject` is required for memory to enter the next model prompt;
- `trace=full` is the lab default because traces are the product moat and never leave the machine; callers may opt out;
- `tools=passthrough` is the default; Loco streams tool calls and applies routing/VRAM controls only;
- `tools=local` activates Loco’s allowlisted local toolpack and approval queue.

Interoperability metadata is carried in headers, for example:

```text
X-Loco-Route: <model-id>
X-Loco-Backend: <backend>
X-Loco-Pipeline-ms: 412
X-Loco-Memory: sqlite-hybrid
X-Loco-Trace: full
X-Loco-Version: 0.2.0
```

### 1b. Control plane (model lifecycle)

```text
POST /v1/control/load        warm a model into VRAM (secures headroom first)
POST /v1/control/unload      release a model (idempotent: already-unloaded = 200)
GET  /v1/control/status      loaded state per model + GPU + presets-active flag
GET  /v1/control/config      per-model runtime flags (read)
POST /v1/control/config      per-model runtime flags (merge-set, applied on next load)
GET  /v1/control/prompts     system-prompt inventory
GET  /v1/control/prompt      one model's stored system prompt
POST /v1/control/prompt      store a system prompt (injected only when the
                             request carries no system message)
```

Load semantics are measured, not optimistic: before spawning a new model the
control plane checks live VRAM headroom against the model's measured footprint
and, if needed, evicts resident siblings first. If the model still cannot fit
with the required headroom, the response is `503 overloaded` with
`free_mib`/`required_mib` — never an opaque backend error. Unload is
idempotent by design: releasing an already-released model reports success.

### 2. Memory plane (pure local)

SQLite is the canonical system of record. The current `memory/loco.db` migrates forward in place; there is no external vector daemon and no network dependency.

```text
memory/loco.db
├── notes               canonical content + vector blob + metadata
├── notes_fts           SQLite FTS5 virtual table + triggers
├── memory_links        backlinks between related memories
├── memory_proposals    high-value writes requiring approve/reject
├── route_log           latency/route/quality observations
├── audit_log           append-only hash-linked audit events
├── trace_events        optional per-turn telemetry for evaluation
└── kv_cache            temperature=0 exact-response reuse
```

Initial implementation uses the installed bge-small-en-v1.5 embedder. SQLite FTS5 catches exact terms; a local cosine pass searches semantic vectors. The two streams re-rank under one score:

```text
score =
    0.50 * semantic
  + 0.25 * normalized_bm25
  + 0.15 * importance
  + 0.10 * access_reinforcement
  - decay_penalty
```

An optional loopback ANN index may mirror note vectors once the corpus
outgrows a complete scan (threshold: 5,000 live notes). It is never required:
SQLite stays the system of truth, reads fall back to the full cosine pass
whenever the index is absent or disabled, and no note text ever leaves the
local database. `POST /v1/memory/index/rebuild` re-mirrors every live note
(first-time enablement or after a restore); `GET /health` reports
`components.vector_index` as `on`/`off`, and `/metrics` exposes
`loco_vector_index_ready` / `loco_vector_index_points`.

Filters:

- `superseded=1` is never returned;
- pinned rows bypass decay;
- caller may constrain `source`, `type`, `time_after`, `pinned`, and `min_score`;
- top-k is capped (`k <= 32`);
- fallback to FTS5 when the embedder sidecar is down;
- if neither FTS5 nor embeddings can serve a result, the response reports `memory_mode: "unavailable"`.

The write gate is role-aware rather than eager:

| Grade | Action |
|---|---|
| no durable value / unverifiable | drop |
| transient context / task state | session-only, short TTL |
| ordinary durable fact with evidence | store with importance estimate |
| unusual or high-impact fact | proposal → human approve/edit/reject |
| confirmed fact / user explicit "remember" | approved store permitted immediately |
| contradiction with old item | mark old `superseded`, keep provenance |
| low confidence flagged by quality gate | never store |

Approval is a first-class API:

```text
GET  /v1/memory/proposals
GET  /v1/memory/proposals/{id}
POST /v1/memory/proposals/{id}/approve
POST /v1/memory/proposals/{id}/reject
POST /v1/memory/add
POST /v1/memory/search
POST /v1/memory/reinforce
POST /v1/memory/stats
POST /v1/memory/index/rebuild
```

No page in the lab UI may imply that a proposal is stored just because it was extracted by the model. The user must be able to inspect, correct, approve, or reject it.

### 3. Tool plane

Tools are **data, descriptions, and policy**, not arbitrary code. The default is passthrough: a harness-owned agent can receive standard chat-completions tool calls and execute them itself.

When a caller requests `tools=local`, Loco can use its allowlisted pack:

```text
lfsys.*       bounded read/inspect operations
memory.*      search/propose/reject
lab.*         status/health/metrics
web.*         only when explicitly enabled (never implicit network egress)
```

There is deliberately no raw shell tool. Host commands must be issued by the harness or exposed one-by-one through explicit tools with approvals.

Policy levels:

```text
deny        blocked always
read        bounded local inspection; audit logged
write       backup → staged write → user approve → atomic replace → audit
privileged  never offered by default
```

For write tools the state machine is:

```text
draft -> pending_approval -> approved -> atomic_replace -> audit
                │
                └-> rejected / expired / never_written
```

The approval queue is persisted as an SQLite table, not a best-effort JSON file or Telegram text. The UI shows diff-like details from the tool adapter; approval/reject is delivered over `/ws` and HTTP. Tool results are injected to the caller in an explicit, bounded envelope:

```text
[TOOL RESULT]
tool: write_file
status: completed
duration_ms: 13
backup: ...
<path>
```

Unknown tools are not executed. They produce an explicit refusal that names the missing permission or missing allowlisted name. Approval parsing must use word-boundary matching; `YESTERDAY` never means `YES`.

### 4. Real-time and lab plane

`GET /ui[/...]` serves the lab without starting a second web server. The single WebSocket `/ws` emits:

```text
gpu
route
model_loaded / model_unloaded
memory_proposal
memory_committed
memory_rejected
tool_pending / tool_approved / tool_rejected
cache_hit
quality_flag
```

The UI displays measured values and clear “unavailable” states. It never substitutes invented GPU or SLA numbers.

### 5. Data flywheel — local first

The moat is not chatting more; it is structured, verified task traces that later can become SFT/DPO material. Loco records:

```text
trace_id                  stable UUID
task_id                   caller/workspace correlation when supplied
timestamp                 RFC3339 UTC + local offset
user_input                redacted/toggled by trace mode
tools_schema              names + schemas, not hidden allowlist bodies
memory_hits               IDs, scores, mode; never raw private logs with trace=metadata
routing                   model, backend, confidence, reason, explicit|auto
tool_calls                name, redacted args, durations, outcome
memory_decision           none | proposal | committed | rejected
response_summary          finish_reason, tokens, tps, latency
user_feedback             optional thumbs/approve/reject signals
gate_outputs              empty/loop/unverified flags (no raw reasoning chain)
```

PII and secrets are redacted before persistence by default. Captures live under `results/traces/` locally. Nothing leaves the machine. Export only happens through an explicit export command, with redaction rechecked and output hash recorded.

Training set families:

1. `route_policy` — exact task → route preference and fallback;
2. `tool_use_trace` — argument/tool grammar used successfully;
3. `memory_write_policy` — keep/drop/propose labels;
4. `approval_feedback` — approve/reject/edit preference pairs;
5. `failure_recovery` — error/fallback/recovery;
6. `compaction_quality` — accepted vs rejected dense briefs.

The first quality targets are measurable: route acceptance, unsupported-tool refusal accuracy, memory commit precision, approval rejection rate, and p50/p95/p99 latency. Finetune only after these are stable; the data is not a reward to train on while we are still guessing.

## Routing contract

`loco:auto` and other auto requests resolve in this order:

1. explicit model understood and accepted;
2. exact STOP/control commands;
3. deterministic prefix/table rules;
4. capability/VRAM feasibility;
5. fallback policy uses the configured lightest admitted model, only with explicit `degraded: true`;
6. otherwise 503, with measured budget and an actionable alternative;
7. log decision and confidence everywhere.

No LLM or network service decides routing in the default path. Local model scores may be used later only as an optional signal with an explicit low-confidence report to the caller.

`POST /v1/chat/completions` with a known model name never reroutes silently. If the explicit target cannot fit at that moment, the response is 503 with headroom, not a hidden substitute.

## HTTP and error semantics

| Situation | Status / body |
|---|---|
| explicit model absent / unknown | `400 model_not_found`, list valid IDs |
| requested model too large | `503 overloaded`, `vram_free_mib`, `required_mib`, fallback candidates |
| control load cannot fit after eviction | `503 overloaded`, evicted/free/required MiB |
| control unload of an unloaded model | `200` (idempotent — desired state already true) |
| embedding model sent to chat | `400 embedding_model_not_chat_capable` |
| auto route asked for an unconfigured capability | `400 unsupported_capability` (never a silent substitute) |
| unknown local tool | `403 unknown_tool` |
| memory unavailable | request proceeds if memory is optional; 503 if caller required it |
| local tool denied | `403 tool_denied`, reason, `policy: deny|permission|approval_pending` |
| malformed body | `400`, bounded message, no stack trace |
| backend dead | `502 backend_unreachable`, configured target name only |
| internal failure | `500 loco_internal`, `trace_id`; detail only local logs |

`GET /health` reports current GPU readiness, backends, memory mode, and uptime. `/metrics` exposes counters usable by Prometheus without private prompt content.

## Implementation order

Do not defer layers or bolt them on after “the core”. Each lands in dependency order with a test beside it.

1. **Design contract and inventory** — this document; `docs/ARCHITECTURE_MAP.md` generated from repo + service checks.
2. **Core fixes** — single source-of-truth model table, 503 contract, no `NameError`, explicit model control, VRAM estimator in `loco/capacity.py`.
3. **Memory storage rebuild** — migration, FTS5 triggers, supersede/pinned, proposals table, atomic audit chain.
4. **Pipeline** — `loco/pipeline.py` orchestrates parse → route → memory → backend → gates → persist. `/v1/chat/completions` becomes thin.
5. **Measured eval harness** — seeded prompts, explicit tools/no-tools, ground-truth checks, deterministic route acceptance. This is the gate before changing tools or adding memories.
6. **Agentic tool passthrough** — tool chunk streaming, no server-side execution by default, `tools=local` policy pack, approval queue/UI.
7. **Data flywheel** — trace schema migration, redaction, JSONL export, SFT/DPO dataset curation, `scripts/loco-eval` report.
8. **Lab UI** — memory approval, route timeline, approval cards, warnings, measured KPIs; every page resilient or visibly degraded.
9. **systemd/deploy** — `loco.service`, read-only source tree option, constrained state paths, restart success checks.
10. **Release gate** — e2e suite, memory-recovery test, backend failure test, VRAM-insufficient test, export redaction audit, changelog.

## Explicit decisions carried from the mining

Keep from prior design review:

- 4-tier memory gate and pending amendments;
- confidence/fallback reporting;
- FTS5 + decay + deduplication/supersede;
- prefix routing primitives;
- stable prefix prompt assembly;
- trace/audit discipline;
- default-deny tool posture.

Keep from prior UI/ops review:

- diff → approve → backup → atomic rename for approved writes;
- delete-completely from dashboard patterns that decorate rather than control;
- "unavailable" rather than hidden failure;
- service awareness/inventory listing.

Do not port:

- A network vector service and remote embeddings as hard dependencies;
- unauthenticated 0.0.0.0 endpoints;
- chat-app approvals as an authorization mechanism;
- Flask/FastAPI for local UI unless added for a specific reason;
- shell=True, substring blacklists, unverified sudo;
- auto-injection of private identity/SOUL into every prompt;
- continuously accumulating "always context" that makes local models slower and fatter;
- fabricated/empty fallback monitoring;
- unfiltered prompt/PII uploads.

## Definition of done

Loco is “agentic API ready” only when all of the following are true:

- `POST /v1/chat/completions` handles happy path, VRAM-insufficient path, missing backend, memory-up/down, explicit and auto routing, and stream/non-stream;
- a harness can see and consume tool calls without Loco hijacking execution;
- local tools do not run without their policy gate and durable approval record;
- memory search is hybrid and corrections are visible to the user;
- every failed response gives reasons and no fabricated metric;
- trace export is opt-in/redacted or full-local;
- benchmarks and tests pass from a reboot and from cold state.

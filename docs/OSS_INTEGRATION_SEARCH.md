# Loco — first-principles developer tool boundary (2026-11-07)

**Scope:** small OSS tooling that makes Loco better at its own job:
schema, contracts, tests, memory, traces and release safety.

**Not scope:** adopting another application as Loco's runtime.

## The problem hierarchy

Loco's failures cluster into:

1. **Schema/drift** — code and durable state disagree.
2. **Contract/drift** — HTTP shape and implementation diverge.
3. **Route/drift** — selection logic and measured VRAM disagree.
4. **Memory/drift** — duplicate or incorrect content leaks into future context.
5. **Action/drift** — tool requests become execution without evidence.
6. **Trace/drift** — data saved no longer helps evaluation.

Each category needs an independent check. No tool fixes many categories by
itself.

## Tier 1 — schema and migration control

| Job | Required property | Loco status |
|---|---|---|
| SQL migration | ordered, rollback-free, repeatable | currently Python-embedded migration only |
| `locale` database runtime check | file path known, transactions explicit | needed for install/update workflow |
| Schema lint | static SQL and column review | optional after migrations exist |
| Snapshot fixture | reproduce test DB | worth not yet productionized |

Any migration tool accepted must:

- run without network;
- produce deterministic schema output;
- commit every migration file under VCS;
- support safe bring-forward from an older database;
- verify integrity before changing anything.

## Tier 2 — contract and API verification

| Job | Method |
|---|---|
| API artifact | single hand/tool-generated contract in `docs/` |
| Contract validity | schema for all routes/error bodies |
| Property testing | boundary values against actual server |
| Negative tests | invalid body, broken backend, unknown model, oversize request |
| Regression | persisted fixtures for explicit model, fallback, cached hit |

No framework-specific HTTP library gets a privileged place. Tests hit Loco's
normal interfaces as a stranger; that is the truthful contract.

## Tier 3 — model and agent evaluation harness

| Component | Needed verification |
|---|---|
| Prompt fixture | fixed tasks, known answers, explicit tools only |
| Verifier | deterministic; no second model judging by default |
| Execution trace | route/reason/model/memory mode |
| Approval gate | correct allowed/denied calls |
| Duplicate memory | merge/reject rather than add copies |
| Negative injection | hostile prompt or tool argument never executes |

Self-hosted agent eval frameworks may be evaluated separately if and only if:

- no data is uploaded;
- results can be exported locally;
- the local path can be paused/removed;
- the test is reproducible without the tool report service.

## Tier 4 — package hygiene

| Tool role | Loco requirement |
|---|---|
| formatting | one canonical code style |
| static typing | catch incorrect shapes before execution |
| security lint | subprocess, credentials, dangerous paths |
| dependency audit | vulnerabilities and unmaintained deps |
| secret scan | commit/worktree guard |
| test runner | no network, stable random seed |

Core Loco stays stdlib-first. Optional development tooling cannot make runtime
dependencies unavailable.

## Tier 5 — instrumentation

| Metric | Source |
|---|---|
| GPU availability | actual probe; unavailable must be explicit |
| route admission/failure | request event |
| cache hit/miss | cache event |
| memory search/write result | memory event |
| proposal decision | approval/reject event |
| tool status | tool call event |
| latency | request boundary |
| redaction proof | startup/export check |

Instrumentation is optional and local-first; missing instrumentation does not
block serving. Detailed content is redacted before local persistence/export.

## Tier 6 — selective ingestion and constraints

| Capability | Gate before adding |
|---|---|
| document→text | explicit user-called path only |
| office/PDF extraction | deterministic parser, fallback refused |
| constrained output | measured grammar/argument compliance improvement |
| RAG TUI/dashboard | no, unless a native UI necessity emerges |
| vector index | only if FTS/cosine benchmark loses with evidence |

## Rejected choices

- Any gateway that demands network fallback by default.
- Any tool muting explicit model or approval precedence.
- Any framework that ships its own model or secret store with Loco.
- Any system where host shell is the normal escape hatch.
- Any package with unclear license, unstable ABI or remote-only telemetry.
- Any UI/telemetry service needing credentials with no local alternative.

## Recommended order

1. Move migration logic out of runtime; choose migration once and hold.
2. Publish `docs/openapi.yaml`; schema-check all responses.
3. Wire a minimal property-test suite to real `127.0.0.1:8200`.
4. Reorganize tests around explicit/invalid/overloaded/cache/tool/memory.
5. Add local secret/credential scanner and dependency audit.
6. Add deterministic typed trace serialization.
7. Only then evaluate ingestion/constraint libraries.

## Acceptance criteria

Every proposed library must answer:

- What precise failure does it eliminate?
- Does it run offline?
- Where does state go?
- Can it be removed without data loss?
- Is its output reviewable by a human?
- Does it reduce trust in the route—or become part of trust?

If an answer is unknown, exclusion it.

## Principle

Loco wins not through accumulated third-party blocks, but by collapsing a
loca`
user's decisions into an inspectable path:

```text
pick (or ask once) → admit or refuse → answer/act → verify → record → learn
```

The best tool in the build is the one that makes a wrong action impossible, or
proves it early

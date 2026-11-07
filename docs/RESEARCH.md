# Loco — first-principles research and build scope

**Status:** Engineering research scope, not publication material.
**Evidence rule:** If a claim is not traceable to `results/`, tests, or a
reproducible run, it is a hypothesis—not a result.

## Method

Start from the machine and work backward.

```text
GPU budget
    ↓
what can load now
    ↓
what is selected, and whether it can be admitted honestly
    ↓
what the model is allowed to do
    ↓
what result is observed and verified
    ↓
what is worth remembering
```

Every feature answers one of these questions:

1. **Can it run?** VRAM, KV, context, cold-start, concurrency.
2. **Should it run?** explicit intent, model ability, task stakes.
3. **Can it act?** tool schema, policy, approval, network boundary.
4. **Can it be trusted?** verification, evidence, rollback, redaction.
5. **Did it help next time?** memory/trace → measurable improvement.
6. **Can it be repeated?** exact route, measurement, audit, release criteria.

If any answer is missing, the feature is incomplete.

## Foundational claims

### Claim 1 — Inference should be admitted, not guessed

A local interface cannot promise a model fit based on disk size or parameters.
It must measure available GPU memory at request time, use the model’s known
context/KV cost, reserve a headroom budget, and return an explicit failure if
no eligible model is admitted.

Formation:

```text
admit(t) = measured_free(t) - estimated_cost(t) ≥ headroom
```

Measurements that cannot be taken yield an unavailable response; they never
yield a fake zero. A cached exact response is valid evidence that no model must
load to answer the exact same prefix at temperature 0.

### Claim 2 — Routing belongs at the API boundary, not inside the model

The model should not decide which local model should reason about itself
unasked. Routing is an interface problem:

```text
explicit target ─────────────── always wins
omitted/loco:auto → deterministic classifier
      candidate chain → capacity gate → measured admission
```

The candidate path may change names and thresholds, but it must always:

- honor explicit targets;
- list fallback candidates;
- report confidence/reason;
- never silently substitute;
- return an actionable unavailable response if nothing fits.

This is a governance claim, not a philosophical one; it can be measured by
prefix, task and response tests.

### Claim 3 — Memory must be proposed, not imposed

A repeated conversation is not automatically a persistent fact. A durable memory
is justified only when:

- it is relevant;
- it can be verified or reviewed;
- freezing it will not silently contaminate future responses;
- it has provenance and supersession semantics.

Loco separates storage from admission:

```text
candidate → proposal → human approve/edit/reject → committed retrieval item
```

Semantic similarity alone must never commit memory. Repeated near-
duplicates are rejected or linked, not averaged into silently conflicting
records.

### Claim 4 — Retrieval is a scoring problem, not a database checkbox

Exact-match and semantic search solve different failures.

```text
rank(item) =
    0.50 × semantic_fit
  + 0.25 × lexical_fit
  + 0.15 × durable_importance
  + 0.10 × access_reinforcement
```

With time-aware decay:

```text
importance(t) = importance(0) × 2^(-age_hours / (30×24))
```

Pinned items bypass decay. Superseded items are excluded from retrieval but
retained for provenance.

### Claim 5 — Agent execution requires a witness and a boundary

The model output can be a statement of intent, not authority to act.

```text
tool request → decode → policy decision → execution (or rejection) → result
```

A local API should expose:

- tools as typed schemas;
- unknown/unallowed tools refused;
- approvals durable, not chat-only;
- redacted input/output traces;
- no host shell by default;
- retry/recovery separated from successful result.

### Claim 6 — Verification is part of the agent action, not an afterthought

Localized "it compiled" is not verification. A useful agent contract must
close the loop with the task’s ground truth.

```text
action → evidence → compare → accept/reject/retry
```

That principle applies across code, memory, data extraction and natural-language
responses.

### Claim 7 — A trace improves nothing merely because it is stored

Traces are raw material, not knowledge. A useful trace includes:

- route reason;
- prompt/response;
- tool call and result;
- memory proposal outcome;
- latency and failure state;
- human final decision.

Without a label ("this answer replaced that"), it is unfit for supervised
training or preference pairs; with labels it can support local tuning or
regression gates.

## First-principles mechanism map

| Failure | Mechanism | Evidence |
|---|---|---|
| Model chosen but cannot fit | capacity admission with headroom | 503 / success audit |
| Explicit ask silently replaced | explicit target precedence | regression tests |
| Repeated facts | dedup/supersession/link/proposal review | duplicate rejection |
| Tool appears valid but executes wrong | schema + policy + approval | tool-call checks |
| Overfit to verbose answer | deterministic review prompts and tests | golden tasks |
| Context fills/cache ignored | stable prefix assembly and compaction | latency benchmarks |
| Old memory subtly wrong | supersession | negative retrieval tests |
| User "reviewed" then reply "auto" | explicit proposal state | approve/reject UI/API test |
| Observability crashes inference | logged failures never block | local trace fallback |
| Next run repeats the same mistake | accepted traces + evaluation fixtures | regression lineage |

## Original parts to treat as Loco's contribution

Not another agent wrapper; not a clone of a general coding agent. Loco is
"local inference + control plane" built from the local constraints:

1. **Measured admission:** CPU/CUDA availability, model/context cost,
   headroom, fail-closed 503.
2. **Execution visibility:** exact route, memory events, proposals and
   approvals pushed on one live channel/UI.
3. **Separate persistence gate:** chat vs proposals vs durable memory.
4. **Cache-awareness:** only exact temperature-0; keyed by model and message
   hash.
5. **Local trace contract:** redacted, exportable, selected by value.
6. **Availability over richness:** memory degrades to lexical search; never a
   fabricated healthy state.

## What not to claim

Loco must not be presented as:

- a product-aware competitor to hosted providers;
- a proven general agent that can code better than humans;
- a replacement for every agent orchestrator;
- a trained-model contribution.

It can be evaluated as an **agentic inference control system**.

## Minimum falsifiable experiments

### E1 — admission correctness

Given target models and 12 GB GPU, requests either route successfully or
return exactly why. No partial load, no incorrect winner.

### E2 — retrieval usefulness

Given a fixed corpus of real memories, hybrid ranking versus lexical or semantic alone on:

- accepted retrievals;
- manual recall;
- duplicate rejection;
- superseded relevance.

### E3 — memory governance

Given model proposals, count:

- true positive/accepted;
- edited before acceptance;
- rejected;
- unsafe proposals refused;
- duplicate proposal reduction.

### E4 — tool execution boundary

Given tasks invoking approved/unapproved tools:

- correct schema arguments;
- disallowed tools never execute;
- approvals durable across restart;
- secrets never stored;
- failure records explicit.

### E5 — feedback value

Given accepted/rejected traces:

- can a fixed task set show improved route/tool success after changes?
- are artifacts reproducible from a clean environment?
- does memory reduce repeated corrective feedback?

## Technical system graph

```text
client/harness                  local model host
    |                                |
    | request                        |
    v                                |
interface -----> validation -----> routing intent -----> admission
    |                 |                  |                  |
    |                 |                  |                  |
    |                 +---- envelope ----+                  |
    |                                                     |
    |                    ┌──────────────┬───────────────┐ |
    v                    v              v               v |
fail closed         backends        embedder       audit/trace
                         |              |
                         v              v
                    response -- memory rank -- proposal -- commit/reject
```

## Ablation grid

### Axis 1 — route source

- explicit selected;
- omit/`loco:auto`;
- overflow fallback;
- unknown model;
- deterministic versus cached response.

### Axis 2 — memory source

- only FTS5;
- only cosine;
- hybrid score;
- pinned and supersede filter;
- approval gated/proposal direct/add.

### Axis 3 — action

- no tools;
- read tool only;
- write proposal allowed;
- privileged tool denied.

### Axis 4 — failure injection

- backend down;
- GPU measure missing;
- corrupted cache;
- low context;
- memory unavailable;
- malformed tool call.

### Axis 5 — trace feedback

- raw training data export;
- redacted export only;
- metadata-only export;
- no-export mode;
- approved/rejected only.

## Decision tree from first principles

For every request:

1. **Parse:** is body valid and message nonempty?
2. **Check untrusted action intent:** is this just answering, or acting?
3. **Classify intent:** task/class, or explicit target?
4. **Admission test:** can target fit and backend reach?
5. **Decide output route:** model or deterministic tool lane.
6. **Verify before commit:** tool results/memory writes/actions against policy.
7. **Assemble response:** prefix output or tool result.
8. **Persist:** route log, audit, optional proposal; never silent memory.
9. **Recall this situation later:** trace feedback + fixtures.

## Design rules

- Never use objective model claims without environment-specific measurement.
- Never guess VRAM; never average GPU-query values to 0.
- Never store uncertain memory content.
- Never conflate cache validity with model preference.
- Never let tool calls execute without a policy decision.
- Never let a retry hide its original failure.
- Always tell the user whether an answer came from model, cache, or gate.
- Always mark degraded mode visibly.

## Open technical questions

1. What is the minimum viable trace unit that accelerates local tuning?
2. Which memory corpus and scoring function maximizes useful recall per MiB VRAM?
3. How to detect proposal duplication without invoking the LLM?
4. Can exact cache validity be extended to local prefix caches?
5. Should embedded/first-person memories be excluded by source or policy?
6. When should a task be refused rather than answered?
7. Can a deterministic proxy preserve semantic tool arguments?
8. What redaction recall/precision is acceptable locally?

## What success looks like

- the interface remains standard chat-completions-shaped but behaves like a bounded local agent manager;
- no tool action occurs by accident;
- memory survives restarts, not hallucinated intentions;
- all changes have a known relationship to measured tasks;
- failure modes are reproducible as repository tests;
- a clean machine can verify claims from nothing but repo + local model files;
- any whitepaper claims cite attached tables/CSVs, not prose confidence.

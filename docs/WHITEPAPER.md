# Local agentic inference on a consumer GPU
## Capacity-aware routing, auditable memory, and offline trace feedback
**Project whitepaper — design and implementation draft.**

This document is not a publication. It does not claim state-of-the-art results.
Any statement whose mechanism is not shown by local tests or raw artifacts is
left open.

## Thesis
A local operator can use existing chat-style APIs without surrendering locality.
Loco is a small control system:

- picks the model the user asked for, or admits it;
- admits the chosen route only after measuring the GPU;
- stores evidence about failures, not just successes;
- separates chat content from durable memory;
- creates reviewable proposals for memory and tool actions;
- produces local traces useful for later tuning or audits.

No result may leave the machine without explicit export.

## Problem
Consumer GPUs are restricted and uneven. A model can be technically possible on
disk or listed as present, but still impossible to run with current memory and
context. Users generally expect one endpoint to choose the model intelligently
or to do what they request. Those obligations conflict when capacity cannot be
verified.

Likewise, a demonstrative assistant can leak code into context, execute shell
commands, or cache an incorrect response. Local autonomy is not useful if it is
unbounded.

## Model of the system
```text
client / harness
       |
       v
interface → validation → intent / explicit choice → capacity admission
                                      |
                +---------------------+---------------------+
                v                                           v
           local model host                           policy / memory / trace
                |                                           |
                v                                           v
          response stream / body                     proposal / audit log
```

## Four control planes

### 1. Inference admission
Every artifact has a measured cost. Request admission checks the current GPU
state, uses the configured headroom, tries eviction if allowed, and otherwise
fails clearly. If measurement can't be obtained, the API says so—never zero,
never invented capacity.

### 2. Memory governance
Memories are not keys; they are claims. A claim must pass through proposal and
review before durable use. Lexical and semantic indexes are evaluated together.
Superseded facts are excluded from context but retained for provenance. Pins
bypass decay.

### 3. Permission and policy
Tool requests are typed. Unknown tools refuse. Sensitive calls require an
approval tied to an ID and timestamp. Host shell is not included by default.
Actions are never auto-authorized because the generated text requested them.

### 4. Truth measurement
A project should give an answer only after a guarantee is in place. Loco's
guarantees are bounded:

- route admission is deterministic;
- no model substitution occurs without consent;
- degraded mode is visible;
- actions are refused by policy;
- memory committed only after review;
- logs can be redacted and exported.

## Local contents
- routes to locally installed model artifacts;
- a local vector coefficient for semantic ranking;
- full-text indexing with weighted score;
- remote embeddings optional and not default;
- a flat file UI for status, model list and memory approval;
- local host observability, no network egress required.

## Falsifiable claims
1. Explicit selection survives unknown model/choice failures and never silently
   substitutes another backend.
2. Oversized requests are rejected instead of partially admitted.
3. Unverifiable GPU measurements fail closed.
4. Memory approval rate of unverified proposals is less than unreviewed direct
   memory storage (approved corrections count as success).
5. Repeated duplicate memory is reduced by rejection/linking.
6. Unknown tool requests fail and produce an auditable refusal.
7. Cached exact responses are reused only at zero temperature and are marked as
   cache-served.
8. Exports require explicit command and include no full secrets/PII.

## Evidence required for each claim
- Configuration and source revision.
- Exact GPU-query probe and model context/K/V limits.
- Pass/fail table per task type.
- Accepted and committed memory examples with approval timestamp.
- Redacted trace capture on explicit export.
- Failure mode logs.
- Reproducibility checklist.

## Results sections to fill only from runs
- cold-start latency by artifact;
- p50/p95 end-to-end latency;
- throughput under constant context;
- memory hit/rejection rates;
- tool schema correctness;
- approval/rejection rates;
- export redaction failure/success.

## Constraints and rejections
- no orchestration graph disguised as product design;
- no automatic external calls;
- no claims about training or weight improvements without data;
- not a multi-tenant cloud model server;
- not a replacement for the user's agentic harness;
- not able to delete old user data automatically.

## Research path
1. Measure completion records from local artifact registry.
2. Evaluate Ornith-class main route against other local artifacts on locked task
   suite.
3. Evaluate explicit versus auto route invariance.
4. Evaluate hybrid retrieval versus lexical-only and semantic-only fallbacks.
5. Freeze one memory approval taxonomy.
6. Generate annotated accepted/rejected proposal corpus.
7. Verify every safety claim on redacted export.
8. Only then prepare a release-quality document.

## Final scope statement
Loco's role is not general intelligence. It is a local laboratory for turning
uncontrolled local model output into inspectable action. New models can plug
into the artifact registry; they cannot change admission, approval, rejection or
audit semantics.

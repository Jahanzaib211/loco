"""Offline training pipeline for the Loco data flywheel.

Stages, in dependency order — each stage is a separate module and the serving
process never imports this package:

    train/curate.py     traces -> gated examples (feedback, dedup, redaction)
    train/dataset.py    examples -> deterministic train/eval JSONL split
    train/train.py      split -> adapter checkpoint (heavy deps, guarded)
    train/evaluate.py   checkpoint served -> measured verdict vs baseline
    train/promote.py    verdict -> registry entry (only a pass promotes)

Principles:
  * SQLite traces are the source of truth; these stages only read them.
  * Nothing here opens a network connection except `evaluate.py`, which talks
    to the local endpoint to measure a candidate that is already served.
  * Promotion is a recorded decision, never an automatic config mutation.
  * Every artifact carries provenance: dataset hash, base model, eval report.
"""

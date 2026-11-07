"""Stage 6 — promotion registry: only a passing verdict promotes a candidate.

The registry is an append-aware JSON file next to this module.  It is the
audit trail for "what was trained, on what data, measured how, promoted by
whom".  Promotion never mutates serving configuration — it records the
decision and prints the manual step, because swapping a live model is a
deliberate act, not a side effect of a script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

REGISTRY = Path(__file__).resolve().parent / "registry.json"


def load() -> dict:
    if not REGISTRY.exists():
        return {"candidates": []}
    data = json.loads(REGISTRY.read_text())
    data.setdefault("candidates", [])
    return data


def save(data: dict) -> None:
    REGISTRY.write_text(json.dumps(data, indent=2, sort_keys=True))


def _find(data: dict, candidate_id: str) -> dict | None:
    for entry in data["candidates"]:
        if entry.get("id") == candidate_id:
            return entry
    return None


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def register(candidate_id: str, kind: str, artifact: str,
             dataset_hash: str | None = None, base: str | None = None,
             notes: str | None = None) -> dict:
    data = load()
    entry = _find(data, candidate_id)
    if entry is None:
        entry = {"id": candidate_id, "created_at": time.time(),
                 "status": "candidate"}
        data["candidates"].append(entry)
    entry.update({
        "kind": kind,
        "artifact": artifact,
        "dataset_hash": dataset_hash,
        "base": base,
        "notes": notes,
    })
    save(data)
    return entry


def promote(candidate_id: str, verdict_path: str, approved_by: str) -> dict:
    verdict = json.loads(Path(verdict_path).read_text())
    if not verdict.get("gate", {}).get("passed"):
        raise SystemExit(
            "refusing to promote: eval verdict did not pass ("
            + "; ".join(verdict.get("gate", {}).get("reasons") or ["no verdict"])
            + ")")
    data = load()
    entry = _find(data, candidate_id)
    if entry is None:
        raise SystemExit(f"unknown candidate: {candidate_id} — register it first")
    entry.update({
        "status": "promoted",
        "promoted_at": time.time(),
        "approved_by": approved_by,
        "eval_verdict": verdict,
        "eval_verdict_hash": _file_hash(verdict_path),
    })
    save(data)
    return entry


def reject(candidate_id: str, reason: str, approved_by: str) -> dict:
    data = load()
    entry = _find(data, candidate_id)
    if entry is None:
        raise SystemExit(f"unknown candidate: {candidate_id}")
    entry.update({"status": "rejected", "rejected_at": time.time(),
                  "rejected_reason": reason, "approved_by": approved_by})
    save(data)
    return entry


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("register", help="record a new candidate artifact")
    p.add_argument("id")
    p.add_argument("--kind", default="adapter")
    p.add_argument("--artifact", required=True)
    p.add_argument("--dataset-hash")
    p.add_argument("--base")
    p.add_argument("--notes")

    p = sub.add_parser("promote", help="promote after a passing eval verdict")
    p.add_argument("id")
    p.add_argument("--verdict", required=True)
    p.add_argument("--by", default="operator")

    p = sub.add_parser("reject", help="record a rejected candidate")
    p.add_argument("id")
    p.add_argument("--reason", required=True)
    p.add_argument("--by", default="operator")

    sub.add_parser("status", help="list every candidate and its state")

    args = parser.parse_args(argv)
    if args.cmd == "register":
        entry = register(args.id, args.kind, args.artifact, args.dataset_hash,
                         args.base, args.notes)
    elif args.cmd == "promote":
        entry = promote(args.id, args.verdict, args.by)
        print("recorded. Serving change is still manual:")
        print("  1. convert/export the adapter to a loadable artifact")
        print("  2. place it where the router discovers models")
        print("  3. re-run `test_all.py` before trusting the new lane")
    elif args.cmd == "reject":
        entry = reject(args.id, args.reason, args.by)
    else:
        entry = None
        data = load()
        if not data["candidates"]:
            print("registry empty")
        for candidate in data["candidates"]:
            print(f"{candidate['id']:32} {candidate.get('status'):12} "
                  f"{candidate.get('kind', '')}")
    if entry is not None:
        print(json.dumps(entry, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

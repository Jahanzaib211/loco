"""Stage 5 — measure a served candidate and gate it against a baseline.

Consumes the JSONL rows produced by the agentic eval harness
(`scripts/loco-eval agentic`) and answers one question honestly:

    is this candidate at least as useful as what we already run?

A candidate passes only if it clears an absolute pass-rate floor AND does not
regress against the stored baseline beyond a allowed drop AND introduces no
new hard errors.  A missing baseline never auto-passes: the verdict says so.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

DEFAULT_MIN_RATE = 0.50
DEFAULT_MAX_DROP_POINTS = 5.0


def load_rows(path: str | Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    counts = {key: sum(1 for row in rows if row.get("status") == key)
              for key in ("pass", "fail", "error")}
    return {
        "total": total,
        **counts,
        "pass_rate": round(counts["pass"] / total, 4) if total else 0.0,
    }


def compare(candidate_rows: list[dict], baseline_rows: list[dict]) -> dict:
    baseline = {row.get("task_id"): row.get("status") for row in baseline_rows}
    flips = {"regressed": [], "improved": [], "new_error": []}
    for row in candidate_rows:
        task = row.get("task_id")
        before = baseline.get(task)
        after = row.get("status")
        if before is None:
            continue
        if before == "pass" and after != "pass":
            flips["regressed"].append(task)
            if after == "error":
                flips["new_error"].append(task)
        elif before != "pass" and after == "pass":
            flips["improved"].append(task)
    return flips


def gate(candidate: dict, baseline: dict | None, flips: dict | None,
         min_rate: float, max_drop_points: float) -> dict:
    reasons = []
    rate = candidate["pass_rate"]
    if candidate["total"] == 0:
        reasons.append("candidate evaluated zero tasks")
    if rate < min_rate:
        reasons.append(f"pass rate {rate:.3f} below floor {min_rate:.3f}")
    if baseline and baseline["total"]:
        drop_points = (baseline["pass_rate"] - rate) * 100
        if drop_points > max_drop_points:
            reasons.append(
                f"regressed {drop_points:.1f} points vs baseline "
                f"(allowed {max_drop_points:.1f})")
    if flips and flips.get("new_error"):
        reasons.append("new hard errors: " + ", ".join(flips["new_error"]))
    if baseline is None:
        reasons.append("no baseline recorded — absolute floor only")
    return {"passed": not reasons, "reasons": reasons}


def evaluate(candidate_path: str, baseline_path: str | None,
             min_rate: float = DEFAULT_MIN_RATE,
             max_drop_points: float = DEFAULT_MAX_DROP_POINTS,
             out: str | None = None) -> dict:
    candidate_rows = load_rows(candidate_path)
    candidate = summarize(candidate_rows)
    baseline_rows = load_rows(baseline_path) if baseline_path else None
    baseline = summarize(baseline_rows) if baseline_rows else None
    flips = compare(candidate_rows, baseline_rows) if baseline_rows else None
    verdict = {
        "candidate": str(candidate_path),
        "baseline": str(baseline_path) if baseline_path else None,
        "candidate_summary": candidate,
        "baseline_summary": baseline,
        "flips": flips,
        "min_rate": min_rate,
        "max_drop_points": max_drop_points,
        "gate": gate(candidate, baseline, flips, min_rate, max_drop_points),
        "created_at": time.time(),
    }
    destination = Path(out) if out else Path(candidate_path).with_suffix(".verdict.json")
    destination.write_text(json.dumps(verdict, indent=2, sort_keys=True))
    verdict["verdict_path"] = str(destination)
    return verdict


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True,
                        help="results JSONL from `loco-eval agentic`")
    parser.add_argument("--baseline", help="results JSONL of the current role model")
    parser.add_argument("--min-rate", type=float, default=DEFAULT_MIN_RATE)
    parser.add_argument("--max-drop-points", type=float,
                        default=DEFAULT_MAX_DROP_POINTS)
    parser.add_argument("--out", help="verdict JSON path")
    args = parser.parse_args(argv)
    verdict = evaluate(args.candidate, args.baseline, args.min_rate,
                       args.max_drop_points, args.out)
    print(json.dumps(verdict, indent=2, sort_keys=True))
    return 0 if verdict["gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

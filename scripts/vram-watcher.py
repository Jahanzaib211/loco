#!/usr/bin/env python3
"""VRAM / OOM watcher for the Loco lab (stdlib only, observe-first).

Samples nvidia-smi, reports pressure, watches for OOM kills, and can
(optionally) ask Loco to unload a model when VRAM is critically low.

Nothing destructive happens unless you pass --auto-unload (and even then it
only calls Loco's control API, never kills processes). Safe defaults.

Usage:
  python3 scripts/vram-watcher.py                       # observe, print
  python3 scripts/vram-watcher.py --jsonl logs/vram.jsonl --interval 5
  python3 scripts/vram-watcher.py --warn-pct 85 --crit-pct 93 --auto-unload

Notes (this rig): RTX 3060 12GB. llama.cpp KV cache lives in VRAM when layers
are on GPU; "disk-mounting VRAM" is not how CUDA/llama.cpp work. Real levers
are KV quant (q4_0/q8_0), smaller --ctx-size, and Loco's one-resident/evict
routing. Swap is OS overflow for RAM (slow), not a VRAM tier.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


def sh(cmd: list[str], timeout: float = 8) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def gpu_sample() -> dict:
    code, out = sh([
        "nvidia-smi",
        "--query-gpu=memory.used,memory.total,memory.free,utilization.gpu,name",
        "--format=csv,noheader,nounits",
    ])
    if code != 0:
        return {"ok": False, "error": out.strip()[:240]}
    try:
        used, total, free, util, *name = [x.strip() for x in out.splitlines()[0].split(",")]
        used = int(used); total = int(total); free = int(free); util = int(util)
        return {
            "ok": True,
            "used_mib": used, "total_mib": total, "free_mib": free,
            "util_pct": util, "used_pct": round(100 * used / total, 1) if total else None,
            "name": ",".join(name).strip(),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"parse: {e}: {out[:120]}"}


def compute_apps() -> list[dict]:
    code, out = sh([
        "nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ])
    apps = []
    if code != 0:
        return apps
    for line in out.splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 3:
            try:
                apps.append({"pid": int(parts[0]), "process": parts[1], "used_mib": int(parts[2])})
            except ValueError:
                pass
    return apps


def mem_sample() -> dict:
    code, out = sh(["free", "-m"])
    if code != 0:
        return {}
    info: dict = {}
    for line in out.splitlines():
        if line.startswith("Mem:"):
            p = line.split(); info["ram_used_mib"], info["ram_total_mib"], info["ram_avail_mib"] = int(p[2]), int(p[1]), int(p[6])
        if line.startswith("Swap:"):
            p = line.split(); info["swap_used_mib"], info["swap_total_mib"] = int(p[2]), int(p[1])
    if info.get("ram_total_mib"):
        info["ram_used_pct"] = round(100 * info["ram_used_mib"] / info["ram_total_mib"], 1)
    if info.get("swap_total_mib"):
        info["swap_used_pct"] = round(100 * info["swap_used_mib"] / info["swap_total_mib"], 1)
    return info


def oom_signals(since: str = "10 min ago") -> list[str]:
    hits: list[str] = []
    for cmd in (
        ["journalctl", "-k", "--no-pager", f"--since={since}", "-q"],
        ["dmesg", "-T"],
    ):
        code, out = sh(cmd, timeout=10)
        if code != 0:
            continue
        for line in out.splitlines()[-200:]:
            low = line.lower()
            if "out of memory" in low or "oom-kill" in low or "killed process" in low:
                hits.append(line.strip()[:240])
    # dedupe while preserving order
    seen = set(); out = []
    for h in hits:
        if h not in seen:
            seen.add(h); out.append(h)
    return out[-8:]


def loco_control(action: str, model: str | None, base: str, timeout: float = 30) -> dict:
    url = f"{base.rstrip('/')}/v1/control/{action}"
    payload = {"model": model} if model else {}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"ok": True, "status": r.getcode(), "body": r.read().decode(errors="replace")[:300]}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": e.read().decode(errors="replace")[:300]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between samples")
    ap.add_argument("--warn-pct", type=float, default=85.0, help="VRAM used %% warning")
    ap.add_argument("--crit-pct", type=float, default=93.0, help="VRAM used %% critical")
    ap.add_argument("--warn-free-mib", type=int, default=1400, help="free MiB warning floor")
    ap.add_argument("--swap-warn-pct", type=float, default=80.0, help="swap used %% warning")
    ap.add_argument("--jsonl", default=None, help="append JSONL samples to this path")
    ap.add_argument("--loco", default="http://127.0.0.1:8200", help="Loco base URL")
    ap.add_argument("--auto-unload", action="store_true",
                    help="OPT-IN: at critical VRAM, POST /v1/control/unload for the largest compute app "
                         "via Loco is NOT auto-mapped to model ids; this flag only enables an unload "
                         "attempt if --unload-model is set. Default is observe-only.")
    ap.add_argument("--unload-model", default=None, help="model id to unload via Loco when critical (requires --auto-unload)")
    ap.add_argument("--oom-check-every", type=int, default=6, help="check OOM signals every N samples")
    ap.add_argument("--once", action="store_true", help="sample once and exit")
    args = ap.parse_args()

    print(f"[vram-watcher] interval={args.interval}s warn={args.warn_pct}% crit={args.crit_pct}% "
          f"auto_unload={args.auto_unload} model={args.unload_model} (observe-first)")
    n = 0
    jsonf = open(args.jsonl, "a", encoding="utf-8") if args.jsonl else None
    try:
        while True:
            n += 1
            ts = datetime.now(timezone.utc).isoformat()
            g = gpu_sample(); m = mem_sample(); apps = compute_apps()
            level = "ok"
            reasons: list[str] = []
            if g.get("ok"):
                if g["used_pct"] >= args.crit_pct or g["free_mib"] <= max(512, args.warn_free_mib // 2):
                    level = "critical"; reasons.append(f"vram {g['used_pct']}% used ({g['free_mib']} MiB free)")
                elif g["used_pct"] >= args.warn_pct or g["free_mib"] <= args.warn_free_mib:
                    level = "warn"; reasons.append(f"vram {g['used_pct']}% used ({g['free_mib']} MiB free)")
            else:
                level = "warn"; reasons.append(f"gpu sample failed: {g.get('error')}")
            if m.get("swap_used_pct", 0) >= args.swap_warn_pct:
                reasons.append(f"swap {m['swap_used_pct']}% ({m.get('swap_used_mib')}/{m.get('swap_total_mib')} MiB)")
                if level == "ok":
                    level = "warn"
            ooms = oom_signals() if (n % max(1, args.oom_check_every) == 0 or args.once) else []
            if ooms:
                level = "critical"; reasons.append(f"oom signals: {len(ooms)}")

            sample = {"ts": ts, "level": level, "reasons": reasons, "gpu": g, "mem": m, "apps": apps, "oom": ooms}

            line = f"{ts} [{level.upper():8}] " + (
                f"VRAM {g.get('used_mib')}/{g.get('total_mib')} MiB ({g.get('used_pct')}%) free={g.get('free_mib')} util={g.get('util_pct')}%"
                if g.get("ok") else f"GPU? {g.get('error')}"
            )
            if m:
                line += f" | RAM {m.get('ram_used_pct')}% swap {m.get('swap_used_pct')}%"
            if reasons:
                line += " | " + "; ".join(reasons)
            print(line, flush=True)
            if ooms:
                for o in ooms[-3:]:
                    print(f"  OOM> {o}", flush=True)
            if apps and level != "ok":
                top = sorted(apps, key=lambda a: a["used_mib"], reverse=True)[:3]
                print("  apps> " + ", ".join(f"{a['process']} pid={a['pid']} {a['used_mib']}MiB" for a in top), flush=True)

            if jsonf:
                jsonf.write(json.dumps(sample) + "\n"); jsonf.flush()

            if level == "critical" and args.auto_unload and args.unload_model:
                res = loco_control("unload", args.unload_model, args.loco)
                print(f"  [auto-unload {args.unload_model}] {res}", flush=True)

            if args.once:
                return 2 if level == "critical" else (1 if level == "warn" else 0)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[vram-watcher] stopped")
        return 0
    finally:
        if jsonf:
            jsonf.close()


if __name__ == "__main__":
    raise SystemExit(main())

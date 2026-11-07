"""Measured VRAM capacity checks for explicit and auto model routing.

No value here pretends to be a measurement.  If nvidia-smi is missing, times
out, or returns malformed output, the caller gets an explicit unavailable
state and the VRAM gate fails closed unless a lab-only override is set.
"""

from __future__ import annotations

import os
import subprocess
import time


DEFAULT_HEADROOM_MIB = 1_200
UNAVAILABLE_DETAIL = "nvidia-smi unavailable"

def query_vram(timeout: float = 3.0) -> dict:
    """Return measured VRAM facts, never substituted values.

    Success:
        {available: True, total_mib, used_mib, free_mib, util_percent,
         name, measured_at}
    Failure:
        {available: False, detail, measured_at}
    """
    started = time.time()
    try:
        out = subprocess.check_output(
            ["nvidia-smi",
             "--query-gpu=name,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            text=True,
            timeout=timeout,
        ).strip()
        first = out.splitlines()[0]
        name, used, total, util = [part.strip() for part in first.split(",")]
        used_i, total_i, util_i = int(used), int(total), int(util)
        return {
            "available": True,
            "name": name,
            "used_mib": used_i,
            "total_mib": total_i,
            "free_mib": max(total_i - used_i, 0),
            "util_percent": util_i,
            "measured_at": time.time(),
            "measurement_age_s": round(time.time() - started, 3),
        }
    except FileNotFoundError:
        detail = "nvidia-smi not installed"
    except subprocess.TimeoutExpired:
        detail = "nvidia-smi timed out"
    except (IndexError, ValueError, subprocess.CalledProcessError):
        detail = "nvidia-smi returned malformed output"
    return {
        "available": False,
        "detail": detail,
        "measured_at": time.time(),
        "measurement_age_s": round(time.time() - started, 3),
    }


def vram_allow_unknown() -> bool:
    """Escape hatch for CI on GPU-less machines; never intended for the service."""
    return os.environ.get("LOCO_ALLOW_UNKNOWN_VRAM", "").lower() in {"1", "true", "yes"}


def can_load(spec, current: dict | None = None, headroom_mib: int = DEFAULT_HEADROOM_MIB):
    """Return (allowed, reason, measured_state) for a candidate model.

    current: optional result from query_vram(); queried live when omitted.
    """
    state = current if current is not None else query_vram()
    need = spec.vram_mib_est
    if not state.get("available"):
        if vram_allow_unknown():
            return True, "vram_unverified: nvidia-smi unavailable (lab override)", state
        return False, (
            f"vram_unavailable: cannot verify memory headroom before loading {spec.id}; "
            "no request will be sent to the backend"), state
    free = state["free_mib"]
    spare = free - need
    if spare >= headroom_mib:
        return True, f"fits with {spare} MiB spare", state
    return False, (
        f"insufficient VRAM for {spec.id}: need {need} MiB, "
        f"available {free} MiB, required headroom {headroom_mib} MiB"), state

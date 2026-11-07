"""GPU telemetry via nvidia-smi, polled by a background thread.

When the GPU cannot be measured the state contains `available: false` and
`None` metrics; callers must not replace them with invented headroom.
"""

import threading
import time

from .capacity import DEFAULT_HEADROOM_MIB, query_vram

_state = {"available": False, "detail": "not measured",
          "used_mib": None, "total_mib": None, "free_mib": None,
          "headroom_mib": None, "util": None, "util_percent": None,
          "name": None, "updated": 0.0}
_lock = threading.Lock()
_POLL_INTERVAL_S = 2.0


def _poll():
    while True:
        measurement = query_vram(timeout=3.0)
        with _lock:
            if measurement.get("available"):
                _state.update(
                    available=True,
                    detail=None,
                    name=measurement["name"],
                    used_mib=measurement["used_mib"],
                    total_mib=measurement["total_mib"],
                    free_mib=measurement["free_mib"],
                    headroom_mib=measurement["free_mib"],
                    util=measurement["util_percent"],
                    util_percent=measurement["util_percent"],
                    updated=measurement["measured_at"],
                )
            else:
                _state.update(
                    available=False,
                    detail=measurement.get("detail"),
                    name=None,
                    used_mib=None,
                    total_mib=None,
                    free_mib=None,
                    headroom_mib=None,
                    util=None,
                    util_percent=None,
                    updated=measurement.get("measured_at", time.time()),
                )
        time.sleep(_POLL_INTERVAL_S)


_started = False
_thread_lock = threading.Lock()


def start():
    global _started
    with _thread_lock:
        if _started:
            return
        thread = threading.Thread(target=_poll, daemon=True,
                                  name="loco-gpu-telemetry")
        thread.start()
        _started = True


def snapshot():
    with _lock:
        result = dict(_state)
    result["capacity_headroom_mib"] = DEFAULT_HEADROOM_MIB
    return result

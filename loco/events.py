"""In-process pub/sub for live UI events. Thread-safe, zero deps."""
import json
import threading
import time

_subs = []
_lock = threading.Lock()
_dropped = 0

def subscribe(cb, max_queue=20):
    with _lock:
        _subs.append({"cb": cb, "queue": [], "max": max_queue})

def unsubscribe(cb):
    with _lock:
        _subs[:] = [s for s in _subs if s["cb"] is not cb]

def publish(event_type, **data):
    payload = {"type": event_type, "ts": time.time(), **data}
    with _lock:
        for s in list(_subs):
            if len(s["queue"]) >= s["max"]:
                s["queue"].pop(0)      # drop oldest, never block publishers
            s["queue"].append(payload)

def drain(cb):
    """Pop all queued events for a subscriber (non-blocking)."""
    with _lock:
        for s in _subs:
            if s["cb"] is cb:
                out, s["queue"] = s["queue"], []
                return out
    return []

def size():
    with _lock:
        return sum(len(s["queue"]) for s in _subs)

"""Tiny built-in HTTP client used by the router to talk to backends."""
import json
import urllib.request

def chat_completions(base_url, payload, timeout=600):
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def stream_chat_completions(base_url, payload, timeout=600):
    """Yield raw SSE lines from the backend as they arrive."""
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps({**payload, "stream": True}).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r:
            yield line

def list_models(base_url, timeout=10):
    with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=timeout) as r:
        return json.loads(r.read().decode())

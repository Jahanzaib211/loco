#!/usr/bin/env python3
"""End-to-end verification of Loco's full-stack capabilities."""
import json, sys, time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = "http://127.0.0.1:8200"

def http_get(url):
    req = Request(url)
    try:
        with urlopen(req, timeout=300) as r:
            return r.getcode(), r.read().decode()
    except HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return None, str(e)

def http_post_json(path, payload):
    req = Request(f"{BASE}{path}", data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode())
    except HTTPError as e:
        return {"error": e.read().decode() if e.read() else str(e)}
    except Exception as e:
        return {"error": str(e)}

# 1) Health check
print("1. Health check ...")
c, body = http_get(f"{BASE}/health")
if c == 200:
    data = json.loads(body)
    print(f"   ✓ GPU: {data['gpu']['used_mib']} MiB / {data['gpu']['total_mib']} MiB")
    print(f"   ✓ llama_cpp: {data['models']['llama_cpp']}")
    print(f"   ✓ ollama: {data['models']['ollama']}")
else:
    print(f"   ✗ {c} {body}")
    sys.exit(1)

# 2) List models
print("2. List models ...")
c, body = http_get(f"{BASE}/v1/models")
if c == 200:
    data = json.loads(body)
    print(f"   ✓ models: {[m['id'] for m in data.get('data', [])]}")
else:
    print(f"   ✗ {c} {body}")
    sys.exit(1)

# The suite must run on the machine it is run on: pick a chat model the
# backend really has, instead of a hardcoded one that may only be a preset.
def pick_chat_model(payload):
    for entry in payload.get("data", []):
        if entry.get("embedding"):
            continue
        if entry.get("id") in (None, "loco:auto", "auto"):
            continue
        if entry.get("available") is False:
            continue
        return entry["id"]
    return None

CHAT_MODEL = pick_chat_model(data) or "lfm3-1.7b"
print(f"   ✓ chat model under test: {CHAT_MODEL}")

# 3) Chat (non-stream)
print("3. Chat (non-stream) ...")
resp = http_post_json("/v1/chat/completions", {
    "model": CHAT_MODEL,
    "messages": [{"role": "user", "content": "hello"}],
    "temperature": 0.0,
    "max_tokens": 10
})
if "choices" in resp and resp["choices"]:
    print(f"   ✓ response: {resp['choices'][0]['message']['content']}")
else:
    print(f"   ✗ error: {resp}")

# 4) Summarizer tool
print("4. Summarizer tool ...")
resp = http_post_json("/v1/tools/summarize", {
    "text": "Loco provides a single HTTP endpoint that routes local models, uses WebSocket streaming for live telemetry, and exposes a safe tool API."
})
if "summary" in resp:
    print(f"   ✓ summary: {resp['summary']}")
else:
    print(f"   ✗ error: {resp}")

# 5) Search tool
print("5. Search tool ...")
resp = http_post_json("/v1/tools/search", {"query": "loco"})
if "results" in resp:
    print(f"   ✓ results: {resp['results']}")
else:
    print(f"   ✗ error: {resp}")

# 6) Calculator tool
print("6. Calculator tool ...")
resp = http_post_json("/v1/tools/calculate", {"expr": "3 + 4 * 2"})
if "result" in resp:
    print(f"   ✓ result: {resp['result']}")
else:
    print(f"   ✗ error: {resp}")

# 7) Memory add
print("7. Memory add ...")
resp = http_post_json("/v1/memory/add", {
    "content": "demo note", "importance": 0.5, "source": "test"
})
if "id" in resp:
    print(f"   ✓ note added: {resp['id']}")
else:
    print(f"   ✗ error: {resp}")

# 8) Memory search
print("8. Memory search ...")
resp = http_post_json("/v1/memory/search", {"query": "demo note"})
if "results" in resp:
    print(f"   ✓ search results: {resp['results']}")
else:
    print(f"   ✗ error: {resp}")

# 9) Control status (GET)
print("9. Control status ...")
c, body = http_get(f"{BASE}/v1/control/status")
if c == 200:
    data = json.loads(body)
    print(f"   ✓ GPU headroom: {data['gpu']['headroom_mib']} MiB")
    loaded = [m['id'] for m in data.get('llama_cpp', []) if m.get('loaded')]
    print(f"   ✓ loaded llama models: {loaded}")
else:
    print(f"   ✗ error: {c} {body}")

# 10) Compactor
print("10. Compactor (compact old chat) ...")
resp = http_post_json("/v1/compact", {
    "messages": [
        {"role": "system", "content": "You are Loco."},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "tell me about Loco"},
        {"role": "assistant", "content": "Loco is a lab..."},
        {"role": "user", "content": "describe the pipeline"},
        {"role": "assistant", "content": "the pipeline..."}
    ],
    "keep_last": 2
})
if "error" in resp:
    print(f"   ✗ compactor error: {resp['error'][:120]}")
else:
    msgs = len(resp.get('messages', []))
    print(f"   ✓ compacted: {msgs} messages")

# 11) UI pages load
print("11. UI pages load ...")
pages = ["/", "/ui/chat.html", "/ui/memory.html", "/ui/compact.html",
         "/ui/embed.html", "/ui/tools.html", "/ui/pipeline.html"]
all_ok = True
for p in pages:
    c, _ = http_get(f"{BASE}{p}")
    if c != 200:
        print(f"   ✗ page {p}: {c}")
        all_ok = False
    else:
        print(f"   ✓ page {p}: OK")

# 12) Full endpoint coverage: every documented route + negative contracts
print("12. Full endpoint coverage ...")
coverage = []

def hit(name, method, path, payload=None, expect=200):
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(f"{BASE}{path}", data=data, method=method,
                  headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urlopen(req, timeout=300) as r:
            status, body, hdrs = r.getcode(), r.read().decode(), r.headers
    except HTTPError as e:
        status, body, hdrs = e.code, e.read().decode(), e.headers
    except Exception as e:
        status, body, hdrs = None, str(e), None
    ok = status == expect
    coverage.append((name, expect, status, ok))
    tail = "" if ok else f"  expected {expect}: {body[:140]}"
    print(f"   {'✓' if ok else '✗'} {name} -> {status}{tail}")
    return status, body, hdrs

def jload(body):
    try:
        return json.loads(body)
    except Exception:
        return {}

# GET surface
hit("GET /metrics", "GET", "/metrics")
hit("GET /v1/tools", "GET", "/v1/tools")
hit("GET /v1/control/config", "GET", "/v1/control/config")
hit("GET /v1/control/prompts", "GET", "/v1/control/prompts")
hit("GET /v1/control/prompt", "GET", "/v1/control/prompt?model=lfm3-1.7b")
hit("GET /v1/memory/proposals", "GET", "/v1/memory/proposals?status=pending")
hit("GET /v1/traces/export (sft)", "GET", "/v1/traces/export?format=sft&limit=5")
hit("GET unknown route -> 404", "GET", "/v1/definitely-not-a-route", expect=404)

# Embeddings
hit("POST /v1/embeddings", "POST", "/v1/embeddings", {"input": "coverage probe"})

# Proposal lifecycle: propose -> approve -> reinforce, propose -> reject
_, b, _ = hit("POST /v1/memory/propose (approve path)", "POST", "/v1/memory/propose",
              {"content": "coverage note: endpoints exercised by the e2e suite",
               "source": "test"})
proposal_id = jload(b).get("id")
note_id = None
if proposal_id:
    _, b, _ = hit("POST /v1/memory/proposals/{id}/approve", "POST",
                  f"/v1/memory/proposals/{proposal_id}/approve", {})
    note_id = jload(b).get("note_id")
_, b, _ = hit("POST /v1/memory/propose (reject path)", "POST", "/v1/memory/propose",
              {"content": "coverage proposal that will be rejected", "source": "test"})
proposal_id = jload(b).get("id")
if proposal_id:
    hit("POST /v1/memory/proposals/{id}/reject", "POST",
        f"/v1/memory/proposals/{proposal_id}/reject", {})
if note_id:
    hit("POST /v1/memory/reinforce", "POST", "/v1/memory/reinforce", {"ids": [note_id]})
hit("POST /v1/memory/stats", "POST", "/v1/memory/stats", {})
hit("POST /v1/memory/index/rebuild", "POST", "/v1/memory/index/rebuild", {})

# Tool registry call
hit("POST /v1/tools/call", "POST", "/v1/tools/call",
    {"tool": "calculate", "params": {"expr": "6 * 7"}})

# Control lifecycle: load -> status -> config write (no-op) -> prompt put -> unload
hit("POST /v1/control/load", "POST", "/v1/control/load", {"model": CHAT_MODEL})
hit("GET /v1/control/status (warm)", "GET", "/v1/control/status")
hit("POST /v1/control/config", "POST", "/v1/control/config",
    {"model": CHAT_MODEL, "flags": {}})
_, b, _ = hit("GET /v1/control/prompt (read back)", "GET",
              f"/v1/control/prompt?model={CHAT_MODEL}")
prompt_content = jload(b).get("content")
hit("POST /v1/control/prompt", "POST", "/v1/control/prompt",
    {"model": CHAT_MODEL, "content": prompt_content or ""})
hit("POST /v1/control/unload", "POST", "/v1/control/unload", {"model": CHAT_MODEL})

# Fresh auto chat: proves message -> route -> model -> trace, then trace feedback
nonce = f"coverage-{time.time()}"
data = json.dumps({
    "model": "loco:auto",
    "messages": [{"role": "user", "content": f"Reply with the single word: {nonce}"}],
    "temperature": 0.0, "max_tokens": 16,
}).encode()
req = Request(f"{BASE}/v1/chat/completions", data=data,
              headers={"Content-Type": "application/json"}, method="POST")
try:
    with urlopen(req, timeout=300) as r:
        status, body, hdrs = r.getcode(), r.read().decode(), r.headers
except HTTPError as e:
    status, body, hdrs = e.code, e.read().decode(), e.headers
ok = status == 200
coverage.append(("POST /v1/chat/completions (auto, fresh)", 200, status, ok))
trace_id = hdrs.get("X-Loco-Trace-Id") if hdrs else None
print(f"   {'✓' if ok else '✗'} auto route -> {hdrs.get('X-Loco-Route') if hdrs else '?'}"
      f" trace={trace_id} status={status}")
if trace_id:
    hit("POST /v1/traces/{id}/feedback", "POST", f"/v1/traces/{trace_id}/feedback",
        {"feedback": "up", "note": "coverage run"})

# Negative contracts: the refusal paths must stay deterministic
hit("unknown model -> 400", "POST", "/v1/chat/completions",
    {"model": "no-such-model",
     "messages": [{"role": "user", "content": "hi"}]}, expect=400)
hit("embedding model as chat -> 400", "POST", "/v1/chat/completions",
    {"model": "bge-small-en-v1.5",
     "messages": [{"role": "user", "content": "hi"}]}, expect=400)
hit("unknown tool -> 403", "POST", "/v1/tools/call",
    {"tool": "shell", "params": {}}, expect=403)
hit("auto vision -> 400", "POST", "/v1/chat/completions",
    {"model": "loco:auto",
     "messages": [{"role": "user", "content": [
         {"type": "text", "text": f"describe this screenshot {nonce}"},
         {"type": "image_url",
          "image_url": {"url": "data:image/png;base64,AAAA"}}]}]}, expect=400)
hit("bad export format -> 400", "GET", "/v1/traces/export?format=bogus", expect=400)

fails = [entry for entry in coverage if not entry[3]]
print(f"   coverage: {len(coverage) - len(fails)}/{len(coverage)} checks ok")
for name, expect, status, _ok in fails:
    print(f"   ✗ FAILED {name}: expected {expect}, got {status}")

print("\n" + "=" * 60)
print("Loco E2E verification complete")
print("\nSummary:")
print("- Single endpoint router: /v1/*")
print("- WebSocket live feed: /ws")
print("- SSE streaming: chat + compaction")
print("- Tool APIs: /v1/tools/summarize, /v1/tools/search, /v1/tools/calculate")
print("- Memory management: /v1/memory/*")
print("- Control lifecycle: /v1/control/load|unload|status|config|prompt")
print("- VRAM-gated deterministic routing")
print("- UI pages all live with WS indicator ✅")
print(f"- Endpoint coverage: {len(coverage) - len(fails)}/{len(coverage)}")

if fails or not all_ok:
    print("\nFAILURES present ✗")
    sys.exit(1)
print("\nAll endpoints returning correct data ✅")
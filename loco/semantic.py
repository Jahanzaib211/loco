"""Embedding client for the dedicated bge-small sidecar on :8210."""
import json
import urllib.request

EMBED_URL = "http://127.0.0.1:8210/v1/embeddings"

def embed_text(text, timeout=30):
    req = urllib.request.Request(EMBED_URL,
                                 data=json.dumps({"input": text}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    vec = data.get("data", [{}])[0].get("embedding")
    if not vec:
        raise RuntimeError(f"embedding response missing vector: {str(data)[:200]}")
    return vec

def embed_batch(texts, timeout=120):
    return [embed_text(t, timeout=timeout) for t in texts]

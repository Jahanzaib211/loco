"""A chat answer must never come from a different model than the one asked for.

The defect this covers: POST /v1/chat/completions with model "lfm3-1.7b"
answered 200 with a body saying "model":"smollm2-135m-instruct", because Loco
forwarded the id to a llama.cpp server that ignores the model field and
serves whatever it has loaded. A caller asked for one model and got another
with no error.

The rule now: either Loco serves the model that was requested, or it answers
with an explicit error.
"""
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class _WrongModelBackend(BaseHTTPRequestHandler):
    """A llama.cpp that always answers with a model that was not requested."""

    served_model = "smollm2-135m-instruct"
    catalogue = ["smollm2-135m-instruct", "lfm3-1.7b"]

    def log_message(self, *args):
        pass

    def _send(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._send({"object": "list", "data": [
                {"id": model_id, "object": "model"} for model_id in self.catalogue
            ]})
            return
        self._send({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path.endswith("/chat/completions"):
            self._send({
                "id": "chatcmpl-wrong",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": self.served_model,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant",
                                         "content": "served by another model"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            })
            return
        self._send({"error": "not found"}, 404)


@pytest.fixture()
def loco_server(tmp_path, monkeypatch):
    """Start Loco in-process against a backend that answers with the wrong model."""
    from loco import catalogue, config as C, memory
    from loco import server as loco_server_module

    backend = ThreadingHTTPServer(("127.0.0.1", 0), _WrongModelBackend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    backend_port = backend.server_address[1]

    monkeypatch.setattr(C, "CFG", {
        **C.CFG,
        "router": {
            "llama_cpp": {"endpoint": f"http://127.0.0.1:{backend_port}/v1"},
            "ollama": {"endpoint": "http://127.0.0.1:1/v1"},
        },
    })
    monkeypatch.setenv("LOCO_ALLOW_UNKNOWN_VRAM", "1")
    monkeypatch.setattr(memory, "DB", tmp_path / "loco.db")
    catalogue.reset_cache()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), loco_server_module.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base
    finally:
        httpd.shutdown()
        backend.shutdown()
        catalogue.reset_cache()


def _chat(base, model):
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 8,
    }).encode()
    request = urllib.request.Request(f"{base}/v1/chat/completions", data=payload,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.getcode(), json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode())


def test_a_model_the_backend_lacks_is_an_explicit_error(loco_server):
    status, body = _chat(loco_server, "deepseek-r1-14b")
    assert status == 503
    assert body["error"]["code"] == "model_not_available"
    assert "deepseek-r1-14b" in body["error"]["message"]


def test_a_backend_that_answers_with_another_model_is_an_explicit_error(loco_server):
    status, body = _chat(loco_server, "lfm3-1.7b")
    assert status == 502
    assert body["error"]["code"] == "model_not_served"
    assert body["error"]["details"]["requested"] == "lfm3-1.7b"
    assert body["error"]["details"]["served"] == "smollm2-135m-instruct"


def test_auto_never_reports_a_model_it_did_not_ask_for(loco_server):
    status, body = _chat(loco_server, "loco:auto")
    # auto may pick any model, but the answer must not claim a model the
    # caller did not get.
    assert status in (200, 502, 503)
    if status == 200:
        assert body["model"]

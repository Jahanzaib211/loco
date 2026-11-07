"""/v1/models must publish what the backends can serve, not the registry.

Two defects are covered:

* D1 - the model list advertised eight chat models that were not on disk, so
  a client registered models that could never be loaded;
* D8 - the embedding model had no explicit marker, so a client could only
  guess from its name.

A fake llama.cpp backend is started in-process, so availability is measured
through the real HTTP path.
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loco import catalogue  # noqa: E402


class _FakeLlama(BaseHTTPRequestHandler):
    """A llama.cpp that only has the models it was built with."""

    models = []

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
            self._send({"object": "list", "data": [{"id": mid, "object": "model"}
                                                   for mid in self.models]})
            return
        self._send({"error": "not found"}, 404)


@pytest.fixture(autouse=True)
def no_sidecar(monkeypatch):
    """The embedding sidecar is a separate process; pin its state for the tests."""
    monkeypatch.setattr(catalogue, "_embedding_sidecar_up", lambda: False)


@pytest.fixture()
def fake_llama(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeLlama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    monkeypatch.setattr(catalogue.C, "CFG", {
        **catalogue.C.CFG,
        "router": {
            "llama_cpp": {"endpoint": f"http://127.0.0.1:{port}/v1"},
            "ollama": {"endpoint": "http://127.0.0.1:1/v1"},
        },
    })
    catalogue.reset_cache()
    yield server
    server.shutdown()
    catalogue.reset_cache()


def _ids(payload):
    return [entry["id"] for entry in payload["data"]]


def test_only_servable_models_are_published(fake_llama):
    fake_llama.RequestHandlerClass.models = ["smollm2-135m-instruct"]
    payload = catalogue.models_payload()
    assert _ids(payload) == ["loco:auto", "smollm2-135m-instruct"]


def test_unavailable_models_are_marked_when_asked_for_all(fake_llama):
    fake_llama.RequestHandlerClass.models = ["smollm2-135m-instruct"]
    payload = catalogue.models_payload(show_all=True)
    entries = {entry["id"]: entry for entry in payload["data"]}
    assert "lfm3-1.7b" in entries
    assert entries["lfm3-1.7b"]["available"] is False
    assert entries["smollm2-135m-instruct"]["available"] is True


def test_embedding_model_carries_an_explicit_marker(fake_llama):
    fake_llama.RequestHandlerClass.models = ["smollm2-135m-instruct"]
    payload = catalogue.models_payload(show_all=True)
    entries = {entry["id"]: entry for entry in payload["data"]}
    assert entries["bge-small-en-v1.5"]["embedding"] is True
    assert "embedding" not in entries["smollm2-135m-instruct"]


def test_availability_follows_the_backend(fake_llama):
    fake_llama.RequestHandlerClass.models = ["smollm2-135m-instruct"]
    assert catalogue.is_available("smollm2-135m-instruct") is True
    assert catalogue.is_available("lfm3-1.7b") is False
    assert catalogue.available_chat_ids() == ["smollm2-135m-instruct"]


def test_a_dead_backend_publishes_no_chat_model(monkeypatch):
    monkeypatch.setattr(catalogue.C, "CFG", {
        **catalogue.C.CFG,
        "router": {
            "llama_cpp": {"endpoint": "http://127.0.0.1:1/v1"},
            "ollama": {"endpoint": "http://127.0.0.1:1/v1"},
        },
    })
    catalogue.reset_cache()
    assert catalogue.available_chat_ids() == []
    assert _ids(catalogue.models_payload()) == ["loco:auto"]

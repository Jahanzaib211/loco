"""Loco HTTP server: the single endpoint.

POST /v1/chat/completions   routed by model / deterministic task rules
POST /v1/embeddings         dedicated bge-small sidecar :8210
POST /v1/compact            summarize older turns, keep pinned + recent
POST /v1/memory/add         ingest an atomic memory (embeds + stores)
POST /v1/memory/search      semantic search over memories
POST /v1/memory/reinforce   access reinforcement (+1 access_count)
POST /v1/memory/stats       counts (notes, pinned, cache, route log)
POST /v1/memory/index/rebuild  re-mirror note vectors into optional ANN index
POST /v1/control/load|unload  model lifecycle from the UI
GET  /v1/control/status     loaded state + GPU
GET  /v1/models             all local models + loco:auto alias
GET  /health                GPU telemetry + status
GET  /metrics               prometheus-ish plaintext
GET  /ui[/...]              the Loco lab UI
"""
import json, time, traceback, urllib.error, urllib.request, subprocess, shutil
from pathlib import Path
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as C
from . import telemetry, routing, semantic, memory, backend, exporter
from . import vector_store, capacity
from . import events
from . import ws as ws_mod
from . import systemprompts, presets
from . import tools as _tools
from . import flywheel
from . import __version__
from .models import (AUTO_MODEL, EMBEDDING_MODELS, EmbeddingModelNotChatCapable,
                     MODEL_SPECS, ModelNotFoundError, canonical_model_id,
                     chat_model_payload, known_model, model_ids)
from .routing import ModelFitError, UnsupportedCapabilityError

START = time.time()
UI_DIR = Path(__file__).resolve().parent.parent / "ui"
CHAT_MODELS = model_ids()

def _gpu():
    return telemetry.snapshot()

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -- helpers ---------------------------------------------------------------
    def _safe_float(self, value, default=1.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _read_json(self, limit=8 * 1024 * 1024):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > limit:
            return None
        try:
            return json.loads(self.rfile.read(length).decode())
        except Exception:
            return None

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _err(self, msg, status=500, code="loco_error", details=None):
        payload = {"message": msg, "type": "loco_error", "code": code}
        if details:
            payload["details"] = details
        self._json({"error": payload}, status)

    def _overloaded(self, error, t0, gpu=None, trace=None):
        details = {
            "model": error.model,
            "vram_free_mib": error.free_mib,
            "required_mib": error.required_mib,
            "fallback_candidates": error.fallback_candidates,
        }
        if trace:
            trace.fail("overloaded", str(error), model=error.model)
        memory.log_route("overloaded", error.model, "none", "overloaded",
                         None, None, (time.time() - t0) * 1000, 0,
                         gpu.get("used_mib") if gpu else None,
                         trace_id=trace.trace_id if trace else None)
        self._err(str(error), 503, "overloaded", details)

    def _route_backend(self, model, payload, stream):
        ep = C.endpoints()
        b = routing.backend_for(model)
        return (ep["ollama"] if b == "ollama" else ep["llama-cpp"]), b

    def _loco_headers(self, decision=None, cached=False, trace=None):
        headers = [
            ("X-Loco-Version", "0.2.0"),
            ("X-Loco-Route", decision.model if decision else "cache"),
            ("X-Loco-Backend", decision.backend if decision else "cache"),
            ("X-Loco-Memory", "sqlite-hybrid"),
            ("X-Loco-Cached", "1" if cached else "0"),
        ]
        if trace is not None:
            headers.append(("X-Loco-Trace", trace.mode))
            if trace.trace_id:
                headers.append(("X-Loco-Trace-Id", trace.trace_id))
        return headers

    def _send_loco_response(self, body_bytes, decision=None, cached=False,
                             content_type="application/json", trace=None):
        self.send_response(200)
        for name, value in self._loco_headers(decision, cached, trace):
            self.send_header(name, value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    # -- GET -------------------------------------------------------------------
    def do_GET(self):
        if self.path == "/ws":
            return self._ws()
        if self.path in ("/", "/ui", "/ui/"):
            return self._ui("index.html")
        if self.path.startswith("/ui/"):
            return self._ui(self.path[4:])
        if self.path.startswith("/v1/memory/proposals"):
            from urllib.parse import urlparse, parse_qs
            if self.path.split("?", 1)[0] == "/v1/memory/proposals":
                query = parse_qs(urlparse(self.path).query)
                status = (query.get("status") or ["pending"])[0]
                try:
                    limit = int((query.get("limit") or ["25"])[0])
                except ValueError:
                    return self._err("limit must be an integer", 400, "bad_request")
                return self._json({"proposals": memory.list_proposals(status, limit)})
            return self._err("unknown proposals path", 404, "not_found")
        if self.path.startswith("/v1/traces/export"):
            from urllib.parse import urlparse, parse_qs
            query = parse_qs(urlparse(self.path).query)
            fmt = (query.get("format") or ["events"])[0]
            try:
                start = float(query["from"][0]) if "from" in query else None
                end = float(query["to"][0]) if "to" in query else None
                limit = int((query.get("limit") or ["100"])[0])
            except (TypeError, ValueError):
                return self._err("from, to and limit must be numbers", 400, "bad_request")
            try:
                body = exporter.export_jsonl(start, end, limit, fmt).encode()
            except ValueError as exc:
                return self._err(str(exc), 400, "bad_request")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Disposition",
                             f'attachment; filename="{exporter.export_filename(fmt)}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/v1/tools" or self.path == "/v1/tools/schemas":
            return self._json({"object": "list",
                               "data": _tools.registry.list_tool_schemas(),
                               "local_only": True})
        if self.path == "/v1/control/status":
            return self._control_status()
        if self.path == "/v1/control/config":
            return self._cfg_get()
        if self.path == "/v1/control/prompts":
            return self._prompts_list()
        if self.path.startswith("/v1/control/prompt"):
            return self._prompt_get()
        if self.path == "/health":
            gpu = _gpu()
            self._json({"status": "ok" if gpu.get("available") else "degraded",
                        "repo": "Loco",
                        "version": __version__,
                        "uptime_s": round(time.time() - START, 1),
                        "gpu": gpu,
                        "components": {
                            "nvidia_smi": "ok" if gpu.get("available") else "unavailable",
                            "memory": "sqlite",
                            "vector_index": ("on" if vector_store.status().get("ready")
                                             else "off"),
                            "llama_cpp": "configured",
                            "ollama": "configured",
                        },
                        "models": {
                            "llama_cpp": [model_id for model_id, spec in MODEL_SPECS.items()
                                          if spec.backend == "llama-cpp"],
                            "ollama": [model_id for model_id, spec in MODEL_SPECS.items()
                                       if spec.backend == "ollama"],
                            "embeddings": ["bge-small-en-v1.5"],
                        }})
            return
        if self.path == "/v1/models":
            self._json({"object": "list",
                        "data": [
                            {"id": AUTO_MODEL, "object": "model", "owned_by": "loco",
                             "permission": [], "aliases": ["auto"]},
                            *chat_model_payload(),
                            {"id": "bge-small-en-v1.5", "object": "model",
                             "owned_by": "loco-recipe", "permission": []},
                        ]})
            return
        if self.path == "/metrics":
            gpu = _gpu()
            st = memory.stats()
            lines = ["# HELP loco_gpu_available 1 when nvidia-smi measurement is current",
                     "# TYPE loco_gpu_available gauge",
                     f"loco_gpu_available {1 if gpu.get('available') else 0}"]
            if gpu.get("available"):
                lines.extend([
                    "# TYPE loco_gpu_used_mib gauge",
                    f"loco_gpu_used_mib {gpu.get('used_mib')}",
                    "# TYPE loco_gpu_total_mib gauge",
                    f"loco_gpu_total_mib {gpu.get('total_mib')}",
                    "# TYPE loco_gpu_free_mib gauge",
                    f"loco_gpu_free_mib {gpu.get('free_mib')}",
                ])
            lines.extend([
                "# TYPE loco_notes_total gauge",
                f"loco_notes_total {st['notes']}",
                "# TYPE loco_route_log_rows_total counter",
                f"loco_route_log_rows_total {st['route_log_rows']}",
                "# TYPE loco_cache_hits_total counter",
                f"loco_cache_hits_total {st['cache_hits']}",
                "# TYPE loco_vector_index_ready gauge",
                f"loco_vector_index_ready {1 if st['vector_index'].get('ready') else 0}",
                "# TYPE loco_vector_index_points gauge",
                f"loco_vector_index_points {st['vector_index'].get('points', 0)}",
            ])
            body = ("\n".join(lines) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._err(f"unknown path {self.path}", 404, "not_found")

    def _ws(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self._err("missing Sec-WebSocket-Key", 400, "bad_request"); return
        raw = self.connection
        resp = ("HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {ws_mod.accept_key(key)}\r\n\r\n").encode()
        try:
            raw.sendall(resp)
        except OSError:
            return
        sentinel = object()
        events.subscribe(sentinel)

        def drain():
            return events.drain(sentinel)

        def snap():
            gpu = telemetry.snapshot()
            return {"type": "gpu", "ts": time.time(),
                    "used_mib": gpu.get("used_mib"), "total_mib": gpu.get("total_mib"),
                    "free_mib": gpu.get("headroom_mib"), "util_pct": gpu.get("util")}
        try:
            ws_mod.pump(raw, drain, snap)
        finally:
            events.unsubscribe(sentinel)
        self.close_connection = True

    def _ui(self, rel):
        rel = rel or "index.html"
        fp = (UI_DIR / rel).resolve()
        if not str(fp).startswith(str(UI_DIR.resolve())) or not fp.is_file():
            fp = UI_DIR / "index.html"
        ctype = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
        body = fp.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- POST ------------------------------------------------------------------
    def do_POST(self):
        t0 = time.time()
        path = self.path.rstrip("/")
        try:
            if path == "/v1/chat/completions":
                return self._chat(t0)
            if path == "/v1/embeddings":
                return self._embeddings()
            if path == "/v1/compact":
                return self._compact()
            if path.startswith("/v1/traces/"):
                tail = path[len("/v1/traces/"):]
                trace_id, separator, action = tail.partition("/")
                if trace_id and separator and action == "feedback":
                    return self._trace_feedback(trace_id)
                return self._err("unknown trace action", 404, "not_found")
            if path == "/v1/memory/add":
                return self._madd()
            if path == "/v1/memory/search":
                return self._msearch()
            if path == "/v1/memory/propose":
                return self._mpropose()
            if path.startswith("/v1/memory/proposals/"):
                tail = path[len("/v1/memory/proposals/"):]
                proposal_id, separator, action = tail.partition("/")
                if proposal_id and separator and action in {"approve", "reject"}:
                    return self._mresolve_proposal(proposal_id, action)
                return self._err("unknown proposal action", 404, "not_found")
            if path == "/v1/memory/proposals":
                return self._mpropose()
            if path == "/v1/memory/reinforce":
                return self._mreinforce()
            if path == "/v1/memory/stats":
                return self._json(memory.stats())
            if path == "/v1/memory/index/rebuild":
                return self._json(memory.rebuild_vector_index())
            if path == "/v1/control/load":
                return self._control_load()
            if path == "/v1/control/unload":
                return self._control_unload()
            if path == "/v1/control/config":
                return self._cfg_set()
            if path == "/v1/control/prompt":
                return self._prompt_put()
            if path == "/v1/tools/call":
                return self._tool_call()
            if path == "/v1/tools/summarize":
                return self._tool_summarize()
            if path == "/v1/tools/search":
                return self._tool_search()
            if path == "/v1/tools/calculate":
                return self._tool_calc()
            self._err(f"unknown path {self.path}", 404, "not_found")
        except Exception as e:
            traceback.print_exc()
            self._err(str(e), 500)

    def _chat_cache_probe(self, payload, trace=None):
        """Return (raw_response, model_id) for exact temp=0 cache hits."""
        if bool(payload.get("stream", False)):
            return None
        if self._safe_float(payload.get("temperature"), 1.0) != 0.0:
            return None
        messages = payload.get("messages", [])
        requested = payload.get("model")
        if requested is None or requested in {AUTO_MODEL, "auto"}:
            task = routing.classify(messages)
            candidates = routing.candidate_chain(task)
        else:
            try:
                candidates = [canonical_model_id(str(requested))]
            except Exception:
                return None
        for model_id in candidates:
            cached = memory.cache_get(model_id, messages)
            if cached is not None:
                if trace is not None:
                    trace.cache_hit(model_id, routing.backend_for(model_id), "cache")
                return cached, model_id
        return None

    def _select_chat(self, payload, trace=None):
        """Resolve an explicit or auto request; translate typed failures."""
        try:
            return routing.select_model(payload)
        except EmbeddingModelNotChatCapable as exc:
            if trace:
                trace.fail("model_rejected", str(exc))
            self._err(str(exc), 400, "embedding_model_not_chat_capable")
        except ModelNotFoundError as exc:
            if trace:
                trace.fail("model_rejected", str(exc))
            self._err(str(exc), 400, "model_not_found")
        return None

    def _chat(self, t0):
        payload = self._read_json()
        if payload is None:
            self._err("invalid or missing JSON body", 400, "bad_request"); return
        if not isinstance(payload, dict):
            self._err("chat body must be a JSON object", 400, "bad_request"); return

        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            self._err("messages must be a non-empty array", 400, "bad_request"); return

        gpu = _gpu()
        trace = flywheel.Trace(payload, dict(self.headers))
        requested = payload.get("model")
        if requested is None or requested in {AUTO_MODEL, "auto"}:
            candidates = routing.candidate_chain(routing.classify(messages))
        else:
            try:
                candidates = [canonical_model_id(str(requested))]
            except Exception:
                candidates = []
        trace.start(candidates)

        # Exact cache hits never force a model admission or VRAM eviction.
        probe = self._chat_cache_probe(payload, trace)
        if probe is not None:
            cached, model_id = probe
            memory.log_route("cache", model_id, "cache", "cache-hit",
                             None, None, (time.time() - t0) * 1000,1,
                             gpu.get("used_mib"), trace_id=trace.trace_id)
            self._send_loco_response(cached.encode(), cached=True, trace=trace)
            return

        try:
            decision = self._select_chat(payload, trace)
        except UnsupportedCapabilityError as exc:
            self._err(str(exc), 400, "unsupported_capability",
                      {"capability": "image-input", "route": AUTO_MODEL})
            if trace:
                trace.fail("capability_unavailable", str(exc))
            return
        except ModelFitError as exc:
            self._overloaded(exc, t0, gpu, trace)
            return
        if decision is None:
            # _select_chat already sent a 400 response.
            return

        trace.route(decision.model, decision.backend, decision.task,
                    decision.action, confidence=decision.confidence,
                    reason=decision.reason, explicit=decision.explicit)

        model = decision.model
        payload["messages"] = systemprompts.inject(payload.get("messages", []), model)
        payload["model"] = model
        stream = bool(payload.get("stream", False))
        backend_url, bname = self._route_backend(model, payload, stream)

        if not stream and self._safe_float(payload.get("temperature"), 1.0) == 0.0:
            cached = memory.cache_get(model, payload.get("messages", []))
            if cached is not None:
                trace.cache_hit(model, bname, decision.task)
                memory.log_route(decision.task, model, bname, "cache-hit",
                                 None, None, (time.time() - t0) * 1000,1,
                                 gpu.get("used_mib"),
                                 trace_id=trace.trace_id,
                                 confidence=decision.confidence)
                self._send_loco_response(cached.encode(), decision,
                                         cached=True, trace=trace)
                return

        try:
            if stream:
                return self._stream_proxy(backend_url, payload, t0, decision,
                                          gpu, trace)
            raw = backend.chat_completions(backend_url, payload)
            resp = json.loads(raw)
            if self._safe_float(payload.get("temperature"), 1.0) == 0.0:
                memory.cache_put(model, payload.get("messages", []), raw.decode())
            usage = resp.get("usage") or {}
            memory.log_route(decision.task, model, bname, decision.action,
                             usage.get("prompt_tokens"), usage.get("completion_tokens"),
                             (time.time() - t0) * 1000, 0, gpu.get("used_mib"),
                             trace_id=trace.trace_id,
                             confidence=decision.confidence)
            trace.response_json(raw, model=model, backend=bname)
            self._send_loco_response(raw, decision, trace=trace)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:500]
            memory.log_route(decision.task, model, bname, f"http-{e.code}", None, None,
                             (time.time() - t0) * 1000, 0, gpu.get("used_mib"),
                             trace_id=trace.trace_id,
                             confidence=decision.confidence)
            trace.fail("backend_http_error", f"http-{e.code}: {body}", model=model)
            self._err(f"backend error {e.code}: {body}", 502, "backend_error")
        except Exception as e:
            memory.log_route(decision.task, model, bname, "error", None, None,
                             (time.time() - t0) * 1000, 0, gpu.get("used_mib"),
                             trace_id=trace.trace_id,
                             confidence=decision.confidence)
            trace.fail("backend_unreachable", str(e), model=model)
            self._err(f"backend unreachable: {e}", 502, "backend_unreachable")

    def _stream_proxy(self, backend_url, payload, t0, decision, gpu, trace):
        self.send_response(200)
        for name, value in self._loco_headers(decision, trace=trace):
            self.send_header(name, value)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        n = 0
        raw_len = 0
        captured = bytearray() if trace is not None and trace.enabled else None
        tool_calls = {}

        def _capture_sse_line(line):
            if trace is None or not trace.enabled:
                return
            text = line.decode("utf-8", errors="replace")
            if not text.startswith("data:"):
                return
            data_text = text[5:].strip()
            if data_text in {"[DONE]", ""}:
                return
            try:
                parsed = json.loads(data_text)
            except json.JSONDecodeError:
                return
            for choice in parsed.get("choices") or []:
                for call in (choice.get("delta") or {}).get("tool_calls") or []:
                    idx = call.get("index", 0)
                    current = tool_calls.setdefault(idx, {
                        "index": idx, "id": call.get("id"),
                        "type": call.get("type"),
                        "function": {"name": "", "arguments": ""},
                    })
                    if call.get("id"):
                        current["id"] = call["id"]
                    if call.get("type"):
                        current["type"] = call["type"]
                    function = (current["function"] if isinstance(current["function"], dict)
                                else {})
                    incoming = call.get("function") or {}
                    if incoming.get("name"):
                        function["name"] = function.get("name", "") + str(incoming["name"])
                    if incoming.get("arguments"):
                        function["arguments"] = (
                            function.get("arguments", "") + str(incoming["arguments"]))
                    current["function"] = function

        try:
            for line in backend.stream_chat_completions(backend_url, payload):
                raw_len += len(line)
                if captured is not None and len(captured) + len(line) <= flywheel.MAX_TRACE_BYTES:
                    captured += line
                _capture_sse_line(line)
                self.wfile.write(line)
                self.wfile.flush()
                n += 1
            tool_list = sorted(tool_calls.values(), key=lambda item: item["index"])
            memory.log_route(decision.task, decision.model,
                             routing.backend_for(decision.model), "stream",
                             None, None, (time.time() - t0) * 1000, 0,
                             gpu.get("used_mib"), trace_id=trace.trace_id,
                             confidence=decision.confidence)
            if trace:
                trace.response_stream(
                    model=decision.model, backend=routing.backend_for(decision.model),
                    raw_buffer=bytes(captured) if captured is not None else b"",
                    chunks=n, tool_calls=tool_list, captured_bytes=raw_len)
        except Exception as e:
            if trace:
                trace.fail("stream_error", str(e), model=decision.model)
            self.wfile.write(f"data: {{\"error\":\"{e}\"}}\n\n".encode())
            memory.log_route(decision.task, decision.model,
                             routing.backend_for(decision.model), "stream-error",
                             None, None, (time.time() - t0) * 1000, 0,
                             gpu.get("used_mib"), trace_id=trace.trace_id,
                             confidence=decision.confidence)

    def _embeddings(self):
        payload = self._read_json()
        if payload is None:
            self._err("invalid or missing JSON body", 400, "bad_request"); return
        text = payload.get("input")
        if text is None:
            self._err("input required", 400, "bad_request"); return
        if isinstance(text, list):
            items = text
        else:
            items = [text]
        if not items or any(item is None for item in items):
            self._err("input must not contain null items", 400, "bad_request"); return
        out = []
        try:
            for i, item in enumerate(items):
                out.append({"object": "embedding", "index": i,
                            "embedding": semantic.embed_text(str(item))})
        except Exception as e:
            self._err(f"embedding backend unavailable: {e}", 503,
                      "embedding_backend_unavailable")
            return
        self._json({"object": "list", "data": out,
                    "model": "bge-small-en-v1.5",
                    "usage": {"prompt_tokens": sum(len(str(i).split()) for i in items),
                              "total_tokens": sum(len(str(i).split()) for i in items)}})

    def _compact(self):
        payload = self._read_json() or {}
        messages = payload.get("messages") or []
        if not isinstance(messages, list) or len(messages) < 6:
            self._err("need at least 6 messages to compact", 400, "bad_request"); return
        keep_last = int(payload.get("keep_last", 4))
        pinned = set(payload.get("pinned_indices") or [])
        system = [m for m in messages if m.get("role") == "system"]
        body = [m for m in messages if m.get("role") != "system"]
        pinned_msgs = [body[i] for i in sorted(pinned) if i < len(body)]
        recent = body[-keep_last:]
        older = body[:-keep_last]
        older = [m for m in older if m not in pinned_msgs]
        if not older:
            self._json({"messages": system + pinned_msgs + recent, "compacted": 0}); return
        transcript = "\n".join(f"[{m.get('role','?')}] {str(m.get('content',''))[:2000]}" for m in older)
        summary_prompt = (
            "Compress this chat transcript into a dense memory brief. Keep every concrete fact: "
            "names, paths, decisions, numbers, preferences, open questions. No greetings. "
            "Output ONLY the brief, plain text, ≤300 words.\n\n" + transcript)
        try:
            compact_target = routing.select_model(
                {"model": "ornith-1.5-9b", "messages": [
                    {"role": "user", "content": summary_prompt}]})
            compact_backend = (
                C.endpoints()["ollama"] if compact_target.backend == "ollama"
                else C.endpoints()["llama-cpp"])
            resp = json.loads(backend.chat_completions(
                compact_backend,
                {"model": compact_target.model,
                 "messages": [{"role": "user", "content": summary_prompt}],
                 "max_tokens": 900, "temperature": 0.2}).decode())
            brief = resp["choices"][0]["message"].get("content") or ""
        except ModelFitError as e:
            self._err(f"compaction model cannot be admitted: {e}", 503,
                      "overloaded"); return
        except Exception as e:
            self._err(f"compaction model failed: {e}", 502, "compact_failed"); return
        summary_msg = {"role": "system",
                       "content": "MEMORY BRIEF (auto-compacted):\n" + brief.strip()}
        self._json({"messages": system + [summary_msg] + pinned_msgs + recent,
                    "compacted": len(older)})

    def _madd(self):
        payload = self._read_json()
        if payload is None:
            self._err("invalid or missing JSON body", 400, "bad_request"); return
        content = (payload.get("content") or "").strip()
        if not content:
            self._err("content required", 400, "bad_request"); return
        try:
            emb = semantic.embed_text(content)
        except Exception as e:
            self._err(f"embedding backend unavailable: {e}", 503,
                      "embedding_backend_unavailable"); return
        nid = memory.add_note(
            content, emb,
            importance=self._safe_float(payload.get("importance"), 0.5),
            pinned=1 if payload.get("pinned") else 0,
            source=payload.get("source", "api"),
            memory_type=payload.get("memory_type", "general"))
        self._json({"id": nid, "stored": True})

    def _mpropose(self):
        payload = self._read_json()
        if payload is None:
            self._err("invalid or missing JSON body", 400, "bad_request"); return
        content = (payload.get("content") or "").strip()
        if not content:
            self._err("content required", 400, "bad_request"); return
        try:
            emb = semantic.embed_text(content)
        except Exception as e:
            self._err(f"embedding backend unavailable: {e}", 503,
                      "embedding_backend_unavailable"); return
        proposal_id = memory.propose_note(
            content, emb,
            importance=self._safe_float(payload.get("importance"), 0.5),
            source=payload.get("source", "api"),
            memory_type=payload.get("memory_type", "general"),
            reason=payload.get("reason"),
            metadata=payload.get("metadata") or {})
        self._json({"id": proposal_id, "status": "pending", "stored": False})

    def _mresolve_proposal(self, proposal_id, action):
        payload = self._read_json() or {}
        if action == "approve":
            content_override = payload.get("content")
            try:
                note_id = memory.approve_proposal(
                    proposal_id, content_override=content_override)
            except RuntimeError as exc:
                self._err(str(exc), 503, "embedding_backend_unavailable"); return
            if note_id is None:
                self._err("proposal not found or already resolved", 404,
                          "proposal_not_found"); return
            self._json({"ok": True, "id": proposal_id, "note_id": note_id,
                        "status": "approved"})
            return
        if not memory.reject_proposal(proposal_id):
            self._err("proposal not found or already resolved", 404,
                      "proposal_not_found"); return
        self._json({"ok": True, "id": proposal_id, "status": "rejected"})

    def _trace_feedback(self, trace_id):
        payload = self._read_json()
        if payload is None:
            self._err("invalid or missing JSON body", 400, "bad_request"); return
        feedback = str(payload.get("feedback", "")).lower()
        if feedback not in {"up", "down", "helpful", "unhelpful"}:
            self._err("feedback must be one of: up, down, helpful, unhelpful",
                      400, "bad_request"); return
        note = payload.get("note")
        memory.record_trace(trace_id, "user_feedback", {
            "feedback": feedback,
            "note": note,
            "accepted": payload.get("accepted"),
            "used": payload.get("used"),
        })
        self._json({"ok": True, "trace_id": trace_id, "feedback": feedback})

    def _msearch(self):
        payload = self._read_json()
        if payload is None:
            self._err("invalid or missing JSON body", 400, "bad_request"); return
        q = (payload.get("query") or "").strip()
        if not q:
            self._err("query required", 400, "bad_request"); return
        try:
            k = int(payload.get("k", 8))
        except (TypeError, ValueError):
            self._err("k must be an integer", 400, "bad_request"); return
        k = max(1, min(k, 32))
        try:
            emb = semantic.embed_text(q)
            mode = "hybrid"
        except Exception as e:
            # FTS remains available when bge-small is unavailable, but semantic
            # recovery is explicitly reported as degraded.
            emb = [0.0] * 384
            mode = "fts_degraded"
        hits = memory.hybrid_search(q, emb, k=k)
        self._json({"results": hits, "memory_mode": mode, "k": k})

    def _mreinforce(self):
        payload = self._read_json() or {}
        ids = payload.get("ids") or []
        if not isinstance(ids, list):
            self._err("ids must be a list", 400, "bad_request"); return
        memory.reinforce([str(i) for i in ids])
        self._json({"ok": True, "reinforced": len(ids)})

    def _control_status(self):
        ep = C.endpoints()["llama-cpp"].rstrip("/")
        try:
            data = json.loads(urllib.request.urlopen(
                f"{ep}/models", timeout=5).read())
            llama = [{"id": m.get("id"),
                      "loaded": (m.get("status") or {}).get("value") == "loaded"}
                     for m in data.get("data", [])]
            presets_active = any(
                "ctx-size" in (m.get("status") or {}).get("preset", "")
                for m in data.get("data", []))
        except Exception as e:
            llama = [{"error": str(e)}]
            presets_active = False

        ollama_bin = shutil.which("ollama")
        if not ollama_bin:
            ollama_ps = "unavailable: ollama binary not on PATH"
        else:
            try:
                ollama_ps = subprocess.check_output(
                    [ollama_bin, "ps"], text=True, timeout=5)
            except Exception as e:
                ollama_ps = f"unavailable: {e}"

        self._json({"gpu": _gpu(), "llama_cpp": llama, "ollama_ps": ollama_ps,
                    "presets_active": presets_active,
                    "preset_hint": None if presets_active else
                        "run scripts/install-preset-unit.sh once (sudo) to make per-model flags live",
                    "backends": {"llama_cpp": ep,
                                 "ollama": C.endpoints()["ollama"]}})

    def _control_load(self):
        payload = self._read_json() or {}
        model = (payload.get("model") or "").strip()
        if not model:
            self._err("model required", 400, "bad_request"); return
        if model == "bge-small-en-v1.5":
            self._json({"ok": True, "detail": "embedding sidecar is always resident"}); return
        evicted_mib = 0
        if not model.startswith("huihui_ai/"):
            # The llama router spawns one child per model and does not evict for
            # VRAM: a resident sibling makes the new child OOM with a bare 500.
            # Secure measured headroom first, exactly as chat admission does.
            spec = known_model(model)
            if spec is None:
                self._err(f"model {model} is not configured", 400, "model_not_found"); return
            model = spec.id
            if spec.backend == "llama-cpp" and not routing.is_llama_resident(spec.id):
                allowed, reason, state = capacity.can_load(spec)
                if not allowed:
                    evicted_mib = routing.try_unload_llama()
                    time.sleep(0.5)
                    allowed, reason, state = capacity.can_load(spec)
                    if not allowed:
                        self._err(
                            f"refusing to load {spec.id}: {reason}", 503, "overloaded",
                            {"model": spec.id, "evicted_mib": evicted_mib,
                             "free_mib": state.get("free_mib"),
                             "required_mib": spec.vram_mib_est,
                             "headroom_mib": capacity.DEFAULT_HEADROOM_MIB})
                        return
        try:
            if model.startswith("huihui_ai/"):
                req = urllib.request.Request(
                    f"{C.endpoints()['ollama'].rstrip('/').removesuffix('/v1')}/api/generate",
                    data=json.dumps({"model": model, "prompt": "ping",
                                     "stream": False,
                                     "options": {"num_predict": 1}}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(req, timeout=180).read()
            else:
                ep = C.endpoints()["llama-cpp"]
                backend.chat_completions(ep, {
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1, "temperature": 0}, timeout=300)
            detail = f"{model} is warming — check /v1/control/status"
            if evicted_mib:
                detail += f" (evicted {evicted_mib} MiB of resident siblings)"
            self._json({"ok": True, "detail": detail, "evicted_mib": evicted_mib})
            events.publish("control", action="load", model=model,
                           evicted_mib=evicted_mib)
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode(errors="replace")[:300]
            except Exception:
                body = ""
            self._err(f"load failed: HTTP {e.code} {body}", 502, "load_failed")
        except Exception as e:
            self._err(f"load failed: {e}", 502, "load_failed")

    def _control_unload(self):
        payload = self._read_json() or {}
        model = (payload.get("model") or "").strip()
        if not model:
            self._err("model required", 400, "bad_request"); return
        if model == "bge-small-en-v1.5":
            self._err("embedding sidecar is pinned", 400, "pinned"); return
        if model.startswith("huihui_ai/"):
            try:
                req = urllib.request.Request(
                    f"{C.endpoints()['ollama'].rstrip('/').removesuffix('/v1')}/api/generate",
                    data=json.dumps({"model": model, "prompt": "", "stream": False,
                                     "keep_alive": 0}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(req, timeout=30).read()
                self._json({"ok": True, "detail": "gemma unloaded by ollama"})
                events.publish("control", action="unload", model=model)
                return
            except Exception as e:
                self._err(f"ollama unload failed: {e}", 502, "unload_failed"); return
        try:
            req = urllib.request.Request(
                f"{C.endpoints()['llama-cpp'].rstrip('/').removesuffix('/v1')}/models/unload",
                data=json.dumps({"model": model}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=20).read()
            detail = f"unloaded {model}"
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace") if e.fp else ""
            if e.code == 400 and "not running" in body:
                # Unload is idempotent: an unloaded model is the desired state.
                self._json({"ok": True, "detail": f"{model} already unloaded"})
                events.publish("control", action="unload", model=model)
                return
            self._err(f"llama.cpp unload failed: HTTP {e.code} {body[:200]}",
                      502, "unload_failed")
            return
        except Exception as e:
            self._err(f"llama.cpp unload failed: {e}", 502, "unload_failed"); return
        self._json({"ok": True, "detail": detail})
        events.publish("control", action="unload", model=model)

    def _cfg_get(self):
        self._json({"presets": presets.read(), "flag_docs": presets.flag_docs()})

    def _cfg_set(self):
        payload = self._read_json() or {}
        model, flags = (payload.get("model") or "").strip(), payload.get("flags") or {}
        if not model or not isinstance(flags, dict):
            self._err("model and flags{...} required", 400, "bad_request"); return
        updated = presets.write_model(model, flags)
        self._json({"ok": True, "model": model,
                    "flags": updated.get(model, {}),
                    "apply": "sudo systemctl restart llama-server  # one-time install: scripts/install-preset-unit.sh"})

    def _prompts_list(self):
        self._json({"prompts": systemprompts.list_all()})

    def _prompt_get(self):
        from urllib.parse import urlparse, parse_qs
        model = (parse_qs(urlparse(self.path).query).get("model") or [""])[0]
        content = systemprompts.get(model)
        self._json({"model": model, "content": content, "exists": content is not None})

    def _prompt_put(self):
        payload = self._read_json() or {}
        model = (payload.get("model") or "").strip()
        content = payload.get("content")
        if not model or content is None:
            self._err("model and content required", 400, "bad_request"); return
        systemprompts.put(model, str(content))
        self._json({"ok": True, "model": model, "bytes": len(str(content))})

    # -- tools (agentic API) ------------------------------------------------
    def _tool_call(self):
        payload = self._read_json()
        if payload is None:
            self._err("invalid or missing JSON body", 400, "bad_request"); return
        name = str(payload.get("tool") or payload.get("name") or "").strip()
        if not name:
            self._err("tool name required", 400, "bad_request"); return
        params = payload.get("params")
        if params is None:
            raw_args = payload.get("arguments")
            if isinstance(raw_args, str) and raw_args.strip():
                try:
                    params = json.loads(raw_args)
                except json.JSONDecodeError:
                    self._err("arguments must be a JSON object", 400, "bad_request"); return
            else:
                params = {}
        if not isinstance(params, dict):
            self._err("params must be a JSON object", 400, "bad_request"); return

        trace = flywheel.Trace(payload, dict(self.headers))
        trace.record("tool_called", {"tool": name, "params": params})
        spec = _tools.registry.TOOL_REGISTRY.get(name)
        if spec is None:
            trace.fail("tool_rejected", f"unknown tool: {name}")
            self._err(f"unknown local tool: {name}", 403, "unknown_tool"); return
        if spec.get("approval") and not bool(payload.get("confirmed")):
            trace.fail("tool_approval_required", f"tool: {name}")
            self._err(f"confirmation required for {name}", 403,
                      "approval_required"); return
        try:
            result = _tools.registry.call_tool(name, params)
        except ValueError as e:
            trace.fail("tool_invalid_params", str(e))
            self._err(str(e), 400, "invalid_tool_params"); return
        except Exception as e:
            trace.fail("tool_failed", str(e))
            self._err(f"tool failed: {e}", 502, "tool_failed"); return
        trace.record("tool_completed", {
            "tool": name,
            "status": result["status"],
            "duration_ms": result["duration_ms"],
        })
        self._json(result)

    def _tool_summarize(self):
        payload = self._read_json() or {}
        text = payload.get("text")
        if not text:
            self._err("`text` required", 400, "bad_request"); return
        summary = _tools.summarizer.summarize(str(text))
        self._json({"summary": summary})

    def _tool_search(self):
        payload = self._read_json() or {}
        query = payload.get("query")
        if not query:
            self._err("`query` required", 400, "bad_request"); return
        results = _tools.search.search(str(query))
        self._json({"results": results})

    def _tool_calc(self):
        payload = self._read_json() or {}
        expr = payload.get("expr")
        if not expr:
            self._err("`expr` required", 400, "bad_request"); return
        result = _tools.calculator.calculate(str(expr))
        self._json({"result": result})

def main(port=8200):
    telemetry.start()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Loco listening on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()

if __name__ == "__main__":
    main()

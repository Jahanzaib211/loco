"""Minimal JSON HTTP helpers for stdlib http.server."""
import json
import sys

def read_json_body(handler, max_bytes=8 * 1024 * 1024):
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length <= 0 or length > max_bytes:
        return None
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None

def send_json(handler, obj, status=200, headers=None):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    for k, v in (headers or {}).items():
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)

def openai_error(handler, message, status=500, code="loco_error", type_="loco_error"):
    send_json(handler, {"error": {"message": message, "type": type_, "code": code}},
              status=status)

def send_sse_head(handler):
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("X-Loco-Route-Id", "loco-1")
    handler.end_headers()

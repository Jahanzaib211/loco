#!/usr/bin/env python3
"""Capability audit — measure what each local model can ACTUALLY do.

Facts come from running the models, not from metadata claims. For every
registered artifact we probe:

  text      : does it return non-empty content for a plain instruction?
  reasoning : does it emit a separate reasoning channel (thinking-first)?
  vision    : does the backend accept an image payload, or refuse it?
  tool      : does it emit a structured tool call?
  pdf       : can the backend read a PDF at all?

Usage:  python3 scripts/capability-audit.py [--json OUT.json]
"""
from __future__ import annotations

import argparse
import base64
import json
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib

ROUTER = "http://127.0.0.1:8080"
LOCO = "http://127.0.0.1:8200"


# --------------------------------------------------------------- tiny assets
def _png(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def _pdf(text: str) -> bytes:
    body = f"BT /F1 18 Tf 20 50 Td ({text}) Tj ET"
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"4 0 obj<</Length " + str(len(body)).encode() + b">>stream\n" + body.encode()
        + b"\nendstream\nendobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF"
    )


PNG_B64 = base64.b64encode(_png(64, 64, (255, 0, 0))).decode()
PDF_B64 = base64.b64encode(_pdf("SECRETCODE-4417")).decode()


# ------------------------------------------------------------------- http
def post(url: str, payload: dict, timeout: float = 300) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode()
            status = r.getcode()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        status = e.code
    except Exception as e:  # noqa: BLE001 - report transport failures inline
        return 0, str(e)
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def get(url: str, timeout: float = 30) -> tuple[int, dict | str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode()
            status = r.getcode()
    except urllib.error.HTTPError as e:
        body, status = e.read().decode(errors="replace"), e.code
    except Exception as e:  # noqa: BLE001
        return 0, str(e)
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


# ------------------------------------------------------------------- gguf
def gguf_arch(path: str) -> dict:
    """Read architecture + key dims straight from the GGUF header."""
    out: dict = {}
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return out
            struct.unpack("<I", f.read(4))
            struct.unpack("<Q", f.read(8))
            n_kv, = struct.unpack("<Q", f.read(8))

            def rstr() -> str:
                n, = struct.unpack("<Q", f.read(8))
                return f.read(n).decode("utf-8", "replace")

            def rval(t: int):
                fmt = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i",
                       6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}
                return struct.unpack("<" + fmt[t], f.read(struct.calcsize("<" + fmt[t])))[0]

            for _ in range(n_kv):
                key = rstr()
                t, = struct.unpack("<I", f.read(4))
                if t == 8:
                    val = rstr()
                elif t in (0, 1, 2, 3, 4, 5, 6, 7, 10, 11, 12):
                    val = rval(t)
                elif t == 9:
                    et, = struct.unpack("<I", f.read(4))
                    ln, = struct.unpack("<Q", f.read(8))
                    vals = []
                    for _ in range(ln):
                        if et == 8:
                            vals.append(rstr())
                        elif et in (1, 7):
                            vals.append(rval(et))
                        elif et in (2, 3):
                            vals.append(rval(et))
                        elif et in (4, 5, 6):
                            vals.append(rval(et))
                        elif et in (10, 11, 12):
                            vals.append(rval(et))
                        else:
                            break
                    val = vals
                else:
                    break
                if key in ("general.architecture", "general.name", "general.file_type") or \
                   key.endswith(("context_length", "block_count", "embedding_length")):
                    out[key] = val
    except Exception:  # noqa: BLE001 - a malformed file is itself a finding
        pass
    return out


# ------------------------------------------------------------------ probes
def ensure_loaded(model: str) -> str:
    """The router keeps one artifact resident; ask Loco's control plane to
    admit this one (it evicts the incumbent). Returns a short status word."""
    status, body = post(f"{LOCO}/v1/control/load", {"model": model}, timeout=300)
    if status == 200:
        time.sleep(2.0)
        return "loaded"
    return f"load-failed({status})"


def probe_text(model: str, budget: int = 800) -> dict:
    status, body = post(f"{ROUTER}/v1/chat/completions", {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: HELLO"}],
        "max_tokens": budget, "temperature": 0.2,
    })
    if status != 200 or not isinstance(body, dict):
        return {"ok": False, "status": status, "error": str(body)[:200]}
    msg = (body.get("choices") or [{}])[0].get("message") or {}
    content = (msg.get("content") or "").strip()
    reasoning = (msg.get("reasoning_content") or "").strip()
    return {
        "ok": bool(content),
        "status": status,
        "content_chars": len(content),
        "reasoning_chars": len(reasoning),
        "reasoning_model": bool(reasoning),
        "finish_reason": (body.get("choices") or [{}])[0].get("finish_reason"),
        "completion_tokens": (body.get("usage") or {}).get("completion_tokens"),
        "needs_budget": bool(reasoning and not content),
    }


def probe_image(model: str) -> dict:
    status, body = post(f"{ROUTER}/v1/chat/completions", {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "What colour is this image? One word."},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_B64}"}},
        ]}],
        "max_tokens": 64, "temperature": 0.2,
    })
    if status == 200 and isinstance(body, dict):
        msg = (body.get("choices") or [{}])[0].get("message") or {}
        return {"ok": True, "status": 200, "answer": (msg.get("content") or "")[:80]}
    text = str(body)
    refuses = "not supported" in text.lower() or "mmproj" in text.lower()
    return {"ok": False, "status": status, "refused": refuses, "error": text[:200]}


def probe_pdf(model: str) -> dict:
    status, body = post(f"{ROUTER}/v1/chat/completions", {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "Read the PDF and reply with the exact code inside."},
            {"type": "image_url", "image_url": {"url": f"data:application/pdf;base64,{PDF_B64}"}},
        ]}],
        "max_tokens": 64, "temperature": 0.2,
    })
    if status == 200 and isinstance(body, dict):
        msg = (body.get("choices") or [{}])[0].get("message") or {}
        return {"ok": True, "status": 200, "answer": (msg.get("content") or "")[:80]}
    text = str(body)
    return {"ok": False, "status": status, "refused": "not supported" in text.lower(),
            "error": text[:200]}


def probe_tool(model: str) -> dict:
    status, body = post(f"{ROUTER}/v1/chat/completions", {
        "model": model,
        "messages": [{"role": "user", "content": "What is 6 * 7? Use the calculate tool."}],
        "tools": [{"type": "function", "function": {
            "name": "calculate", "description": "Evaluate arithmetic",
            "parameters": {"type": "object",
                           "properties": {"expr": {"type": "string"}},
                           "required": ["expr"]}}}],
        "tool_choice": "auto", "max_tokens": 256, "temperature": 0.2,
    })
    if status != 200 or not isinstance(body, dict):
        return {"ok": False, "status": status, "error": str(body)[:200]}
    msg = (body.get("choices") or [{}])[0].get("message") or {}
    calls = msg.get("tool_calls") or []
    return {"ok": bool(calls), "status": 200, "tool_calls": len(calls),
            "tool_name": ((calls[0].get("function") or {}).get("name") if calls else None)}


# The managed backend is reached through Loco (it owns backend selection).
def _probe_via_loco(model: str, content: list, tools: list | None = None) -> dict:
    payload: dict = {"model": model, "messages": [{"role": "user", "content": content}],
                     "max_tokens": 256, "temperature": 0.2}
    if tools is not None:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    status, body = post(f"{LOCO}/v1/chat/completions", payload)
    if status != 200 or not isinstance(body, dict):
        return {"ok": False, "status": status, "error": str(body)[:200]}
    msg = (body.get("choices") or [{}])[0].get("message") or {}
    calls = msg.get("tool_calls") or []
    return {"ok": True, "status": 200,
            "content": (msg.get("content") or "")[:120],
            "reasoning_chars": len(msg.get("reasoning_content") or ""),
            "tool_calls": len(calls),
            "tool_name": ((calls[0].get("function") or {}).get("name") if calls else None)}


def probe_pdf_managed(model: str) -> dict:
    r = _probe_via_loco(model, [
        {"type": "text", "text": "Read the PDF and reply with the exact code inside."},
        {"type": "image_url", "image_url": {"url": f"data:application/pdf;base64,{PDF_B64}"}},
    ])
    if not r.get("ok"):
        return {"ok": False, "status": r.get("status"), "refused": "not supported" in str(r.get("error", "")).lower(),
                "error": str(r.get("error", ""))[:200]}
    answer = r.get("content") or ""
    return {"ok": bool(answer), "status": 200, "answer": answer}


def probe_tool_managed(model: str) -> dict:
    r = _probe_via_loco(model, "What is 6 * 7? Use the calculate tool.", tools=[{
        "type": "function", "function": {
            "name": "calculate", "description": "Evaluate arithmetic",
            "parameters": {"type": "object", "properties": {"expr": {"type": "string"}},
                           "required": ["expr"]}}}])
    if not r.get("ok"):
        return {"ok": False, "status": r.get("status"), "error": str(r.get("error", ""))[:200]}
    return {"ok": bool(r.get("tool_calls")), "status": 200,
            "tool_calls": r.get("tool_calls"), "tool_name": r.get("tool_name")}


# -------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", dest="out", default=None)
    args = ap.parse_args()

    status, models = get(f"{ROUTER}/v1/models")
    if status != 200 or not isinstance(models, dict):
        print(f"router unavailable ({status}) — start llama-server first", file=sys.stderr)
        return 2
    ids = [m.get("id") for m in models.get("data", []) if m.get("id")]
    ids = [i for i in ids if "gemma" not in i.lower()]  # served by the managed backend
    managed = ["huihui_ai/gemma-4-abliterated:e4b"]

    report: dict = {"router": ROUTER, "models": {}}

    print(f"auditing {len(ids) + len(managed)} artifacts — real runs, no metadata claims\n")
    for model in ids + managed:
        row: dict = {}
        print(f"{model}")
        if model not in managed:
            loaded = ensure_loaded(model)
            if loaded != "loaded":
                print(f"   load      ✗ {loaded}")
                row["load"] = loaded
                report["models"][model] = row
                print()
                continue
            row["load"] = loaded
            print("   load      ✓ admitted")
        row["text"] = probe_text(model)
        t = row["text"]
        if t.get("ok"):
            print(f"   text      ✓ {t['content_chars']}ch"
                  + (f" · reasoning {t['reasoning_chars']}ch" if t.get("reasoning_model") else ""))
        else:
            print(f"   text      ✗ {t.get('error', '')[:90]}")

        if model in managed:
            # The managed backend serves the multimodal build; probe it through Loco.
            status, body = post(f"{LOCO}/v1/chat/completions", {
                "model": model,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "What colour is this image? One word."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_B64}"}},
                ]}],
                "max_tokens": 64, "temperature": 0.2,
            })
            if status == 200 and isinstance(body, dict):
                msg = (body.get("choices") or [{}])[0].get("message") or {}
                row["vision"] = {"ok": True, "status": 200, "answer": (msg.get("content") or "")[:80]}
            else:
                row["vision"] = {"ok": False, "status": status, "error": str(body)[:200]}
            print(f"   vision    {'✓' if row['vision']['ok'] else '✗'} {row['vision'].get('answer', row['vision'].get('error','')[:90])}")
            row["pdf"] = probe_pdf_managed(model)
            p = row["pdf"]
            print(f"   pdf       {'✓' if p.get('ok') else '✗'} "
                  + (p.get("answer", "") or ("refused" if p.get("refused") else p.get("error", "")[:70])))
            row["tool"] = probe_tool_managed(model)
            tt = row["tool"]
            print(f"   tool      {'✓' if tt.get('ok') else '✗'} "
                  + (f"{tt.get('tool_name')} ({tt.get('tool_calls')})" if tt.get("ok") else tt.get("error", "")[:70]))
            print()
            report["models"][model] = row
            continue

        # local artifacts: probe the router directly (it owns the loaded model)
        row["vision"] = probe_image(model)
        v = row["vision"]
        print(f"   vision    {'✓' if v.get('ok') else '✗'} "
              + (v.get("answer", "") or ("refused: " + v.get("error", "")[:70])))
        row["pdf"] = probe_pdf(model)
        p = row["pdf"]
        print(f"   pdf       {'✓' if p.get('ok') else '✗'} "
              + (p.get("answer", "") or ("refused" if p.get("refused") else p.get("error", "")[:70])))
        row["tool"] = probe_tool(model)
        tt = row["tool"]
        print(f"   tool      {'✓' if tt.get('ok') else '✗'} "
              + (f"{tt.get('tool_name')} ({tt.get('tool_calls')})" if tt.get("ok") else tt.get("error", "")[:70]))
        print()
        report["models"][model] = row

    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

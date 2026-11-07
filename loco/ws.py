"""Minimal RFC 6455 server for the Loco lab UI. Zero dependencies.

Handles: upgrade handshake, text frames, ping/pong, close.
Server->client semantics: pushes gpu (1s) + route/memory/control events.
Client->server: {"type":"ping"} honored; everything else ignored.
Raw socket reads (no buffered files) so timeouts work and the loop never starves.
"""
import base64
import hashlib
import json
import socket
import time

WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

def accept_key(key):
    return base64.b64encode(hashlib.sha1(key.encode() + WS_GUID).digest()).decode()

def send_frame(sock, payload: bytes, opcode=0x1):
    length = len(payload)
    header = bytearray([0x80 | opcode])
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126); header += length.to_bytes(2, "big")
    else:
        header.append(127); header += length.to_bytes(8, "big")
    sock.sendall(bytes(header) + payload)

def send_text(sock, obj):
    send_frame(sock, json.dumps(obj, ensure_ascii=False).encode(), 0x1)

def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("client closed")
        buf += chunk
    return buf

def recv_frame(sock):
    b0 = recv_exact(sock, 1)[0]
    b1 = recv_exact(sock, 1)[0]
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        length = int.from_bytes(recv_exact(sock, 2), "big")
    elif length == 127:
        length = int.from_bytes(recv_exact(sock, 8), "big")
    mask = recv_exact(sock, 4) if masked else b""
    payload = recv_exact(sock, length) if length else b""
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload

def pump(sock, bus_drain, gpu_snapshot):
    sock.settimeout(0.4)
    last_gpu = 0.0
    try:
        while True:
            try:
                opcode, payload = recv_frame(sock)
            except socket.timeout:
                opcode, payload = None, None
            except (ConnectionError, OSError):
                break
            if opcode == 0x8:          # close
                break
            if opcode == 0x9:          # ping
                send_frame(sock, payload, 0xA)
            elif opcode == 0x1 and payload:
                try:
                    msg = json.loads(payload.decode())
                    if msg.get("type") == "ping":
                        send_text(sock, {"type": "pong", "ts": time.time()})
                except Exception:
                    pass
            now = time.time()
            for ev in bus_drain():
                send_text(sock, ev)
            if now - last_gpu >= 1.0:
                send_text(sock, gpu_snapshot())
                last_gpu = now
    finally:
        try:
            send_frame(sock, b"", 0x8)
        except OSError:
            pass

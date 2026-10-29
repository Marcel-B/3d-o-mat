"""Rig service on oi-o-mat: puts a skeleton and skin weights into a GLB with SkinTokens (VAST-AI, MIT code and weights),
through the Apple-Silicon port github.com/chris-straka/skintokens (Rust, Metal; the built binary is GPL-3.0 and only
run here as a separate process, nothing of it is linked or copied). The LLM gate starts this service when a request
needs it (backend type "tool") and checks that oi-o-mat has the memory for it; 3d-o-mat asks through the gate.

    GET  /v1/models   {"data": [{"id": "skintokens", "available", "unavailable", "downloaded", "memoryGb"}]}
    POST /v1/rig      {"model": "skintokens", "glb": "<base64>", "class": "humanoid|quadruped|custom", "seed": 1}
                      -> {"format": "glb", "data": "<base64>", "report": {...}}; 422 when skintokens ran and failed
                         (for example no humanoid core), 500 when it could not run
    GET  /health

Settings in rig.json next to this folder (install-rig.sh writes it): {"binary": ".../bin/skintokens",
"home": "/Volumes/T7/skintokens", "port": 11240, "memoryGb": 8}. Plain Python 3.9, macOS's own /usr/bin/python3:
keep `from __future__ import annotations` and no newer syntax.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.environ.get("OMAT3D_RIG_SETTINGS") or os.path.join(os.path.dirname(HERE), "rig.json")
MODEL = "skintokens"
CLASSES = ("humanoid", "quadruped", "custom")
MAX_BODY = 200 * 1024 * 1024
# One rig at a time; skintokens locks too, but a second request should wait here instead of failing there.
LOCK = threading.Lock()


def settings() -> dict:
    try:
        with open(SETTINGS) as file:
            values = json.load(file)
    except FileNotFoundError:
        values = {}
    home = os.path.expanduser(values.get("home") or "/Volumes/T7/skintokens")
    return {
        "binary": os.path.expanduser(values.get("binary") or os.path.join(home, "src", "bin", "skintokens")),
        "home": home,
        "port": int(values.get("port") or 11240),
        # Measured on oi-o-mat (M6, 32 GB) with a 226k-vertex TRELLIS.2 figure: 8.3 GB peak footprint, 34.5 s.
        "memoryGb": float(values.get("memoryGb") or 8),
        "timeoutSeconds": int(values.get("timeoutSeconds") or 900),
    }


def weights(home: str) -> bool:
    return os.path.isfile(os.path.join(home, "weights", "grpo_1400.ckpt"))


def model_info() -> dict:
    s = settings()
    available, reason = True, None
    if not os.access(s["binary"], os.X_OK):
        available, reason = False, f"skintokens fehlt: {s['binary']} (install-rig.sh)"
    return {
        "id": MODEL,
        "label": "SkinTokens (Skelett und Gewichte)",
        "available": available,
        "unavailable": reason,
        "downloaded": weights(s["home"]),
        "memoryGb": s["memoryGb"],
    }


def rig(body: dict) -> tuple[int, dict]:
    if body.get("model") not in (None, MODEL):
        return 400, {"error": {"message": f"unbekanntes Modell {body.get('model')}"}}
    try:
        glb = base64.b64decode(body.get("glb") or "", validate=True)
    except (ValueError, TypeError):
        return 400, {"error": {"message": "glb ist kein Base64"}}
    if glb[:4] != b"glTF":
        return 400, {"error": {"message": "glb ist keine GLB-Datei"}}
    kind = body.get("class") or "humanoid"
    if kind not in CLASSES:
        return 400, {"error": {"message": f"class: {', '.join(CLASSES)}"}}
    seed = body.get("seed", 1)
    if not isinstance(seed, int) or seed < 0:
        return 400, {"error": {"message": "seed: ganze Zahl ab 0"}}
    s = settings()
    with LOCK, tempfile.TemporaryDirectory(prefix="rig-") as work:
        source, target, report = (os.path.join(work, name) for name in ("in.glb", "out.glb", "report.json"))
        with open(source, "wb") as file:
            file.write(glb)
        command = [s["binary"], "rig", source, target, "--report", report, "--class", kind, "--seed", str(seed)]
        try:
            done = subprocess.run(command, capture_output=True, timeout=s["timeoutSeconds"],
                                  env={**os.environ, "SKINTOKENS_HOME": s["home"]})
        except (OSError, subprocess.TimeoutExpired) as error:
            return 500, {"error": {"message": f"skintokens lief nicht: {error}"}}
        details = {}
        if os.path.isfile(report):
            with open(report) as file:
                details = json.load(file)
        if done.returncode != 0 or not os.path.isfile(target):
            lines = (done.stderr or done.stdout).decode(errors="replace").strip().splitlines()
            reason = details.get("reason") or (lines[-1] if lines else f"Exit {done.returncode}")
            # 1: ran and failed (no humanoid core, bad mesh) is the caller's to change; 2: setup or usage.
            return (422 if done.returncode == 1 else 500), {"error": {"message": f"skintokens: {reason}"}, "report": details}
        with open(target, "rb") as file:
            data = base64.b64encode(file.read()).decode()
    return 200, {"format": "glb", "data": data, "report": details.get("rig", details)}


class Handler(BaseHTTPRequestHandler):
    server_version = "3d-o-mat-rig"

    def send(self, status: int, value: dict) -> None:
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 – the name http.server calls
        if self.path == "/v1/models":
            return self.send(200, {"object": "list", "data": [model_info()]})
        if self.path == "/health":
            return self.send(200, {"ok": True})
        self.send(404, {"error": {"message": f"unbekannt: GET {self.path}"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/rig":
            return self.send(404, {"error": {"message": f"unbekannt: POST {self.path}"}})
        length = int(self.headers.get("content-length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self.send(413 if length > MAX_BODY else 400, {"error": {"message": "Inhalt fehlt oder ist zu groß"}})
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            return self.send(400, {"error": {"message": "kein JSON"}})
        status, value = rig(body if isinstance(body, dict) else {})
        self.send(status, value)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"rig: {fmt % args}\n")


def main() -> int:
    s = settings()
    # Only the gate on this Mac talks to it.
    server = ThreadingHTTPServer(("127.0.0.1", s["port"]), Handler)
    sys.stderr.write(f"rig: hört auf 127.0.0.1:{s['port']}, skintokens {s['binary']}\n")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

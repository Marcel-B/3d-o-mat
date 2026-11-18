"""Prep service on oi-o-mat: turns a TRELLIS figure (.blend with a body without head and a head with a long neck) into a
clean and a small version for rigging, with figur_aufbereiten.py (next to this file) in a Python that has bpy 5.3,
numpy, scipy and scikit-image. The LLM gate starts this service when a request needs it (backend type "tool") and checks
that oi-o-mat has the memory for it; 3d-o-mat asks through the gate.

    GET  /v1/models    {"data": [{"id": "figur-aufbereiten", "available", "unavailable", "downloaded", "memoryGb"}]}
    POST /v1/prepare   {"model": "figur-aufbereiten", "blend": "<base64>", "options": {...}}
                       -> {"exitCode", "passed", "report", "files": {"blend", "glb", "preview", "fbx"}, "log"}
                          200 when the script ran through: exit 0, or exit 2 (the check from 14 directions found visible
                          back faces; the files are there, passed is false); 422 when the script refused the input
                          (exit 1 with its own message, e.g. not one or two mesh objects); 500 when it could not run
    POST /v1/player    {"model": "figur-aufbereiten", "head": "<base64 GLB>", "body": "<base64 .blend>",
                        "rig": "<base64 .blend>", "name": "azari", "options": {...}}
                       -> wie /v1/prepare, dazu files.fbx (65 Knochen) und files.textures (ZIP mit <name>-farbe.png,
                          <name>-normal.png). Eine Spielerfigur in einem Zug: kopf_aufsetzen.py setzt den Kopf (Trellis,
                          mit Hals) an die Stelle des Kopfes in body (Eingabe-.blend des Teams: Koerper_ohne_Kopf und
                          Kopf_langer_Hals als Vorlage), figur_aufbereiten.py bereitet auf, rig_uebertragen.py gibt ihr
                          Skelett und Gewichte aus rig (Teamfigur mit Armature). Exit-Code 2 der Aufbereitung: kein Rig,
                          passed false, die Dateien zum Ansehen.
    GET  /health

options (all optional, checked again here): height (m, --hoehe), body_triangles, head_triangles, texture, head_voxels,
skin_color [r, g, b] (0..1), body, head (object names), retouch (list of boxes, see README of the script), check (false
skips the check and the preview), fbx (true also writes an FBX; comes as a ZIP with its texture folder).

Settings in prep.json next to this folder (install-prep.sh writes it): {"python": ".../venv/bin/python", "port": 11241,
"memoryGb": 10}, optional "timeoutSeconds" (3600). Plain Python 3.9, macOS's own /usr/bin/python3: keep
`from __future__ import annotations` and no newer syntax.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.environ.get("OMAT3D_PREP_SETTINGS") or os.path.join(os.path.dirname(HERE), "prep.json")
SCRIPT = os.environ.get("OMAT3D_PREP_SCRIPT") or os.path.join(HERE, "figur_aufbereiten.py")
HEAD_SCRIPT = os.environ.get("OMAT3D_HEAD_SCRIPT") or os.path.join(HERE, "kopf_aufsetzen.py")
RIG_SCRIPT = os.environ.get("OMAT3D_RIG_SCRIPT") or os.path.join(HERE, "rig_uebertragen.py")
PLAYER = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
MODEL = "figur-aufbereiten"
MAX_BODY = 512 * 1024 * 1024
TEXTURES = (512, 1024, 2048, 4096)
NAME = re.compile(r"^[^\x00-\x1f]{1,63}$")
# One figure at a time: Blender takes several GB and all cores.
LOCK = threading.Lock()


class Invalid(ValueError):
    pass


def settings() -> dict:
    try:
        with open(SETTINGS) as file:
            values = json.load(file)
    except FileNotFoundError:
        values = {}
    return {
        "python": os.path.expanduser(values.get("python") or "/Volumes/T7/figur-aufbereiten/venv/bin/python"),
        "port": int(values.get("port") or 11241),
        # Not measured on oi-o-mat yet; on the Mac mini klein-apose-1 (124 MB .blend) peaked at about this.
        "memoryGb": float(values.get("memoryGb") or 10),
        "timeoutSeconds": int(values.get("timeoutSeconds") or 3600),
    }


def model_info() -> dict:
    s = settings()
    available, reason = True, None
    if not os.access(s["python"], os.X_OK):
        available, reason = False, f"Python mit bpy fehlt: {s['python']} (install-prep.sh)"
    elif not os.path.isfile(SCRIPT):
        available, reason = False, f"figur_aufbereiten.py fehlt: {SCRIPT}"
    return {"id": MODEL, "label": "Figur aufbereiten (Blender)", "available": available, "unavailable": reason,
            "downloaded": available, "memoryGb": s["memoryGb"]}


def number(options: dict, name: str, low, high, kind=int):
    value = options.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind is int and value != int(value)):
        raise Invalid(f"{name}: eine Zahl von {low} bis {high}")
    if not low <= value <= high:
        raise Invalid(f"{name}: erlaubt sind {low} bis {high}, nicht {value}")
    return kind(value)


def arguments(options: dict, work: str) -> list:
    """The command line for figur_aufbereiten.py from the options; raises Invalid for anything it would not take."""
    if not isinstance(options, dict):
        raise Invalid("options muss ein Objekt sein")
    known = {"height", "body_triangles", "head_triangles", "texture", "head_voxels", "skin_color", "body", "head",
             "retouch", "check", "fbx"}
    unknown = sorted(set(options) - known)
    if unknown:
        raise Invalid(f"unbekannte Optionen: {', '.join(unknown)}")
    args = ["--input", os.path.join(work, "in.blend"), "--output", os.path.join(work, "figur.blend"),
            "--glb", os.path.join(work, "figur.glb"), "--bericht", os.path.join(work, "bericht.json")]
    for name, flag, low, high, kind in (("height", "--hoehe", 0.3, 3.0, float),
                                        ("body_triangles", "--koerper-dreiecke", 2000, 300000, int),
                                        ("head_triangles", "--kopf-dreiecke", 2000, 200000, int),
                                        ("head_voxels", "--kopf-voxel", 100, 800, int)):
        value = number(options, name, low, high, kind)
        if value is not None:
            args += [flag, str(value)]
    texture = options.get("texture")
    if texture is not None:
        if isinstance(texture, bool) or texture not in TEXTURES:
            raise Invalid(f"texture: {', '.join(map(str, TEXTURES))}")
        args += ["--textur", str(int(texture))]
    skin = options.get("skin_color")
    if skin is not None:
        if (not isinstance(skin, list) or len(skin) != 3
                or any(isinstance(c, bool) or not isinstance(c, (int, float)) or not 0 <= c <= 1 for c in skin)):
            raise Invalid("skin_color: drei Zahlen von 0 bis 1 (r, g, b)")
        args += ["--hautfarbe", ",".join(f"{float(c):.4f}" for c in skin)]
    for name, flag in (("body", "--koerper"), ("head", "--kopf")):
        value = options.get(name)
        if value is not None:
            if not isinstance(value, str) or not NAME.match(value):
                raise Invalid(f"{name}: der Name eines Objekts in der .blend (bis 63 Zeichen)")
            args += [flag, value]
    retouch = options.get("retouch")
    if retouch is not None:
        if not isinstance(retouch, list) or not 0 < len(retouch) <= 50 or not all(isinstance(b, dict) for b in retouch):
            raise Invalid("retouch: eine Liste von 1 bis 50 Quadern")
        for box in retouch:
            for corner in ("lo", "hi"):
                point = box.get(corner)
                if (not isinstance(point, list) or len(point) != 3
                        or any(isinstance(c, bool) or not isinstance(c, (int, float)) for c in point)):
                    raise Invalid(f"retouch: jeder Quader braucht {corner} mit drei Zahlen")
        path = os.path.join(work, "retusche.json")
        with open(path, "w") as file:
            json.dump(retouch, file)
        args += ["--retusche", path]
    check = options.get("check", True)
    if not isinstance(check, bool):
        raise Invalid("check: true oder false")
    if check:
        args += ["--vorschau", os.path.join(work, "vorschau.png")]
    else:
        args += ["--ohne-pruefung"]
    fbx = options.get("fbx", False)
    if not isinstance(fbx, bool):
        raise Invalid("fbx: true oder false")
    if fbx:
        args += ["--fbx", os.path.join(work, "fbx", "figur.fbx")]
        os.makedirs(os.path.join(work, "fbx"))
    return args


def encoded(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as file:
        return base64.b64encode(file.read()).decode()


def zipped(folder: str) -> str | None:
    """The FBX and its texture folder (FBX has no combined metal/roughness map) as one ZIP."""
    if not os.path.isdir(folder) or not os.listdir(folder):
        return None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for base, _, names in os.walk(folder):
            for name in sorted(names):
                path = os.path.join(base, name)
                archive.write(path, os.path.relpath(path, folder))
    return base64.b64encode(buffer.getvalue()).decode()


def blend_bytes(value, what: str) -> bytes:
    try:
        data = base64.b64decode(value or "", validate=True)
    except (ValueError, TypeError):
        raise Invalid(f"{what} ist kein Base64") from None
    # Blender writes "BLENDER" at the start, or a zstd frame when the file is compressed (Blender 3.0 and later).
    if data[:7] != b"BLENDER" and data[:4] != b"\x28\xb5\x2f\xfd":
        raise Invalid(f"{what} ist keine .blend-Datei")
    return data


def tail(done) -> list:
    output = (done.stdout + b"\n" + done.stderr).decode(errors="replace")
    return [line for line in output.strip().splitlines() if line.strip()][-60:]


def prepare(body: dict) -> tuple:
    if body.get("model") not in (None, MODEL):
        return 400, {"error": {"message": f"unbekanntes Modell {body.get('model')}"}}
    try:
        blend = blend_bytes(body.get("blend"), "blend")
    except Invalid as invalid:
        return 400, {"error": {"message": str(invalid)}}
    with LOCK, tempfile.TemporaryDirectory(prefix="prep-") as work:
        with open(os.path.join(work, "in.blend"), "wb") as file:
            file.write(blend)
        return run_prepare(body.get("options") or {}, work)


def run_prepare(options: dict, work: str) -> tuple:
    """figur_aufbereiten.py auf work/in.blend; die Ergebnisse bleiben in work (figur.blend, figur.glb, ...)."""
    s = settings()
    try:
        args = arguments(options, work)
    except Invalid as invalid:
        return 400, {"error": {"message": str(invalid)}}
    command = [s["python"], SCRIPT, *args]
    try:
        done = subprocess.run(command, capture_output=True, timeout=s["timeoutSeconds"], cwd=work)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 500, {"error": {"message": f"figur_aufbereiten.py lief nicht: {error}"}}
    log = tail(done)
    report = {}
    if os.path.isfile(os.path.join(work, "bericht.json")):
        with open(os.path.join(work, "bericht.json")) as file:
            report = json.load(file)
    if done.returncode not in (0, 2):
        errors = [line for line in done.stderr.decode(errors="replace").strip().splitlines() if line.strip()]
        reason = errors[-1] if errors else f"Exit {done.returncode}"
        crashed = b"Traceback" in done.stderr or done.returncode != 1
        # 1 without a traceback: the script's own sys.exit("…") about the input, the caller's to change.
        return (500 if crashed else 422), {"error": {"message": f"figur_aufbereiten.py: {reason}"},
                                           "exitCode": done.returncode, "report": report, "log": log}
    files = {"blend": encoded(os.path.join(work, "figur.blend")), "glb": encoded(os.path.join(work, "figur.glb")),
             "preview": encoded(os.path.join(work, "vorschau.png")), "fbx": zipped(os.path.join(work, "fbx"))}
    if not files["glb"] or not files["blend"]:
        return 500, {"error": {"message": "figur_aufbereiten.py endete ohne .blend oder GLB"},
                     "exitCode": done.returncode, "report": report, "log": log}
    passed = None if "pruefung_bestanden" not in report else bool(report["pruefung_bestanden"])
    if done.returncode == 2:
        passed = False
    return 200, {"exitCode": done.returncode, "passed": passed, "report": report,
                 "files": {key: value for key, value in files.items() if value}, "log": log}


def player(body: dict) -> tuple:
    """Kopf aufsetzen, aufbereiten, Rig uebertragen: eine Spielerfigur in einem Zug (siehe Kopf der Datei)."""
    if body.get("model") not in (None, MODEL):
        return 400, {"error": {"message": f"unbekanntes Modell {body.get('model')}"}}
    name = body.get("name")
    if not isinstance(name, str) or not PLAYER.match(name):
        return 400, {"error": {"message": "name: Kleinbuchstaben, Ziffern und -, wie der Ordner im Spiel (z. B. azari)"}}
    try:
        head = base64.b64decode(body.get("head") or "", validate=True)
        if head[:4] != b"glTF":
            raise Invalid("head ist keine GLB-Datei")
        team_body = blend_bytes(body.get("body"), "body")
        rig = blend_bytes(body.get("rig"), "rig")
    except (ValueError, TypeError):
        return 400, {"error": {"message": "head ist kein Base64"}}
    except Invalid as invalid:
        return 400, {"error": {"message": str(invalid)}}
    s = settings()
    with LOCK, tempfile.TemporaryDirectory(prefix="spieler-") as work:
        for file_name, data in (("kopf.glb", head), ("koerper.blend", team_body), ("rig.blend", rig)):
            with open(os.path.join(work, file_name), "wb") as file:
                file.write(data)

        def step(script: str, *args: str):
            try:
                return subprocess.run([s["python"], script, *args], capture_output=True, timeout=s["timeoutSeconds"], cwd=work)
            except (OSError, subprocess.TimeoutExpired) as error:
                return error

        done = step(HEAD_SCRIPT, "koerper.blend", "kopf.glb", "in.blend")
        if not hasattr(done, "returncode") or done.returncode != 0 or not os.path.isfile(os.path.join(work, "in.blend")):
            reason = (tail(done)[-1:] or ["?"])[0] if hasattr(done, "returncode") else str(done)
            return 500, {"error": {"message": f"kopf_aufsetzen.py: {reason}"},
                         "log": tail(done) if hasattr(done, "returncode") else []}
        head_log = tail(done)
        options = body.get("options") or {}
        if not isinstance(options, dict) or "fbx" in options:
            return 400, {"error": {"message": "options: ein Objekt, ohne fbx (die Spielerfigur ist immer eine FBX)"}}
        status, answer = run_prepare(options, work)
        if status != 200 or answer.get("passed") is False:
            answer["log"] = head_log + answer.get("log", [])
            return status, answer
        os.makedirs(os.path.join(work, "fbx"))
        done = step(RIG_SCRIPT, "rig.blend", "figur.glb", "spieler-rig.blend", os.path.join("fbx", f"{name}.fbx"), name)
        fbx = encoded(os.path.join(work, "fbx", f"{name}.fbx"))
        if not hasattr(done, "returncode") or done.returncode != 0 or not fbx:
            reason = (tail(done)[-1:] or ["?"])[0] if hasattr(done, "returncode") else str(done)
            return 500, {"error": {"message": f"rig_uebertragen.py: {reason}"}, "report": answer.get("report"),
                         "log": tail(done) if hasattr(done, "returncode") else []}
        textures = io.BytesIO()
        with zipfile.ZipFile(textures, "w", zipfile.ZIP_DEFLATED) as archive:
            for suffix in ("farbe", "normal"):
                path = os.path.join(work, "fbx", f"{name}-{suffix}.png")
                if os.path.isfile(path):
                    archive.write(path, os.path.basename(path))
        answer["files"]["fbx"] = fbx
        answer["files"]["textures"] = base64.b64encode(textures.getvalue()).decode()
        answer["log"] = head_log + answer.get("log", []) + tail(done)[-10:]
    return 200, answer


class Handler(BaseHTTPRequestHandler):
    server_version = "3d-o-mat-prep"

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
        if self.path not in ("/v1/prepare", "/v1/player"):
            return self.send(404, {"error": {"message": f"unbekannt: POST {self.path}"}})
        length = int(self.headers.get("content-length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self.send(413 if length > MAX_BODY else 400, {"error": {"message": "Inhalt fehlt oder ist zu groß"}})
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            return self.send(400, {"error": {"message": "kein JSON"}})
        handler = player if self.path == "/v1/player" else prepare
        status, value = handler(body if isinstance(body, dict) else {})
        self.send(status, value)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"prep: {fmt % args}\n")


def main() -> int:
    s = settings()
    # Only the gate on this Mac talks to it.
    server = ThreadingHTTPServer(("127.0.0.1", s["port"]), Handler)
    sys.stderr.write(f"prep: hört auf 127.0.0.1:{s['port']}, Python {s['python']}\n")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

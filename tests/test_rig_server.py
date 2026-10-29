"""Der Rig-Dienst (rig/server.py) gegen ein nachgestelltes skintokens (tests/fake/skintokens)."""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests" / "fake" / "skintokens"


@pytest.fixture
def server(tmp_path):
    home = tmp_path / "skintokens"
    (home / "weights").mkdir(parents=True)
    (home / "weights" / "grpo_1400.ckpt").write_bytes(b"x")
    settings = tmp_path / "rig.json"
    settings.write_text(json.dumps({"binary": str(FAKE), "home": str(home), "port": 11249, "memoryGb": 8}))
    process = subprocess.Popen([sys.executable, str(ROOT / "rig" / "server.py")],
                               env={**os.environ, "OMAT3D_RIG_SETTINGS": str(settings)}, stderr=subprocess.PIPE)
    for _ in range(50):
        try:
            urllib.request.urlopen("http://127.0.0.1:11249/health", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    yield "http://127.0.0.1:11249"
    process.terminate()
    process.wait()


def call(url, path, body=None):
    request = urllib.request.Request(url + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_models_and_rig(server):
    status, models = call(server, "/v1/models")
    assert status == 200 and models["data"][0] == {
        "id": "skintokens", "label": "SkinTokens (Skelett und Gewichte)", "available": True, "unavailable": None,
        "downloaded": True, "memoryGb": 8.0}
    glb = b"glTF\x02\x00\x00\x00figur"
    status, answer = call(server, "/v1/rig", {"model": "skintokens", "glb": base64.b64encode(glb).decode(), "class": "humanoid", "seed": 3})
    assert status == 200 and answer["format"] == "glb"
    assert base64.b64decode(answer["data"]) == glb + b"+armature"
    assert answer["report"]["joints_raw"] == 40 and answer["report"]["seed"] == 3


def test_failures(server):
    assert call(server, "/v1/rig", {"glb": base64.b64encode(b"nope").decode()})[0] == 400
    assert call(server, "/v1/rig", {"glb": base64.b64encode(b"glTFxx").decode(), "class": "giraffe"})[0] == 400
    status, answer = call(server, "/v1/rig", {"glb": base64.b64encode(b"glTFfail").decode()})
    assert status == 422 and "kein Mensch" in answer["error"]["message"]

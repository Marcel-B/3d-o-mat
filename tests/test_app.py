"""Der ganze Weg ohne oi-o-mat: die Schranke ist ein httpx.MockTransport, die T7 ein temporärer Ordner."""
import base64
import io
import json
import time

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from omat3d import cutout
from omat3d.app import create_app
from omat3d.config import Config
from omat3d.gate import Gate
from omat3d.store import MARKER

GLB = b"glTF\x02\x00\x00\x00fake"


def disc_on_white(size=200, alpha=False) -> bytes:
    y, x = np.mgrid[:size, :size]
    inside = (x - size / 2) ** 2 + (y - size / 2) ** 2 < (size / 4) ** 2
    rgba = np.zeros((size, size, 4), np.uint8)
    rgba[...] = (255, 255, 255, 0 if alpha else 255)
    rgba[inside] = (200, 40, 40, 255)
    buffer = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buffer, "PNG")
    return buffer.getvalue()


def sse(*events) -> bytes:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


class FakeGate:
    """Antwortet wie die Schranke; answers ist eine Liste von Antworten für /v1/3d/generations, der Reihe nach."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/gate/status":
            return httpx.Response(200, json={"tonwerk": {"idle": True}, "waiting": [], "running": [], "apps": {
                "3d-o-mat": {"known": True, "held": None, "models": [{"model": "trellis", "state": "aus", "approved": True}]}}})
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            text = json.dumps({"suitable": True, "object": "rote Scheibe", "problems": [], "tips": ["flach"]}) \
                if isinstance(body["messages"][0]["content"], list) \
                else "```json\n" + json.dumps({"prompts": ["a red ceramic disc"], "note": "schlicht"}) + "\n```"
            return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})
        if request.url.path == "/v1/3d/generations":
            assert request.headers["authorization"] == "Bearer 3d-o-mat"
            body = json.loads(request.content)
            assert body["stream"] is True and body["model"] == "trellis" and body["texture_size"] == 1024
            return self.answers.pop(0)
        return httpx.Response(404, json={"error": {"message": "unbekannt"}})


def done_stream():
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(
        {"type": "progress", "stage": "trellis-shape", "step": 1, "total": 2},
        {"type": "complete", "format": "glb", "data": base64.b64encode(GLB).decode()}))


@pytest.fixture
def make(tmp_path):
    def build(answers, mounted=True):
        if mounted:
            (tmp_path / MARKER).write_text("")
        fake = FakeGate(answers)
        config = Config(data_dir=str(tmp_path), trellis_model="trellis", assist_model="gemma",
                        defaults={"texture_size": 1024}, cutout="flood")
        gate = Gate("http://gate", "3d-o-mat", transport=httpx.MockTransport(fake))
        app = create_app(config, gate=gate, register=False, log=lambda *_: None)
        app.state.jobs.stop.wait = lambda seconds: time.sleep(0.01)  # Retry-After im Test nicht abwarten
        return TestClient(app), fake
    return build


def wait_for(client, job_id, states=("done", "failed"), timeout=10):
    until = time.time() + timeout
    while time.time() < until:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] in states:
            return job
        client.app.state.jobs.wake.set()
        time.sleep(0.05)
    raise AssertionError(f"Auftrag hängt: {job}")


def upload(client):
    response = client.post("/api/images", files={"file": ("scheibe.png", disc_on_white(), "image/png")})
    assert response.status_code == 200, response.text
    return response.json()


def test_flood_cutout_frees_the_object():
    png, info = cutout.cut(disc_on_white(), "flood")
    image = Image.open(io.BytesIO(png))
    alpha = np.asarray(image)[:, :, 3]
    assert info["method"] == "flood" and image.width == image.height
    assert alpha[0, 0] == 0 and alpha[image.height // 2, image.width // 2] == 255
    assert 0.15 < info["subject"] < 0.25


def test_existing_alpha_is_kept():
    _, info = cutout.cut(disc_on_white(alpha=True), "flood")
    assert info["method"] == "alpha"


def test_image_to_glb(make):
    with make([done_stream()])[0] as client:
        image = upload(client)
        assert image["cutout"]["method"] == "flood"
        assert client.get(f"/api/images/{image['id']}/cutout").headers["content-type"] == "image/png"
        job = client.post("/api/jobs", json={"imageId": image["id"], "options": {"seed": 7}}).json()
        job = wait_for(client, job["id"])
        assert job["state"] == "done" and job["glbBytes"] == len(GLB)
        download = client.get(f"/api/jobs/{job['id']}/model.glb?download=true")
        assert download.content == GLB
        assert "scheibe-" in download.headers["content-disposition"]
        assert client.get("/api/status").json()["gate"]["registered"] is True


def test_broken_stream_and_busy_are_retried(make):
    broken = httpx.Response(200, content=sse({"type": "progress", "stage": "trellis-shape", "step": 1, "total": 2}))
    busy = httpx.Response(503, headers={"retry-after": "1"}, json={"error": {"message": "zu lange gewartet"}})
    with make([broken, busy, done_stream()])[0] as client:
        job = client.post("/api/jobs", json={"imageId": upload(client)["id"]}).json()
        job = wait_for(client, job["id"])
        assert job["state"] == "done" and job["attempts"] == 3


def test_not_approved_fails_with_hint(make):
    refused = httpx.Response(403, json={"error": {"message": "trellis ist für 3d-o-mat nicht freigegeben"}})
    with make([refused])[0] as client:
        job = client.post("/api/jobs", json={"imageId": upload(client)["id"]}).json()
        job = wait_for(client, job["id"])
        assert job["state"] == "failed" and "/gate" in job["message"]


def test_without_data_drive_nothing_is_stored(make, tmp_path):
    with make([], mounted=False)[0] as client:
        response = client.post("/api/images", files={"file": ("x.png", disc_on_white(), "image/png")})
        assert response.status_code == 503 and "nicht verbunden" in response.json()["error"]
        assert not (tmp_path / "images").exists()


def test_gemma_check_and_prompts(make):
    with make([])[0] as client:
        image = upload(client)
        checked = client.post(f"/api/images/{image['id']}/check").json()
        assert checked["check"]["suitable"] is True and checked["check"]["object"] == "rote Scheibe"
        prompts = client.post("/api/prompts", json={"idea": "eine rote Scheibe"}).json()
        assert prompts["prompts"] == ["a red ceramic disc"]


def test_bad_options_are_refused(make):
    with make([])[0] as client:
        image = upload(client)
        assert client.post("/api/jobs", json={"imageId": image["id"], "options": {"texture_size": 1000}}).status_code == 422
        assert client.post("/api/jobs", json={"imageId": "../../etc"}).status_code == 404

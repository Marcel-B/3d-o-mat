"""Die Schnittstelle unter /v1 (models3d.py, export.py) mit einem nachgestellten TRELLIS.2: hochladen, Auftrag, Stand,
GLB holen, Fehler bei falschem SHA-256 und unzulässigen Werten, Abbrechen, Neustart. Die Ablage läuft wie in
test_app.py einmal direkt und einmal über das nachgestellte ssh, also auch durch store/launch.py."""
import base64
import hashlib
import io
import json
import os
import struct
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from omat3d.animate import chunks
from omat3d.app import create_app
from omat3d.config import Config
from omat3d.export import ExportError, finish
from omat3d.gate import Gate
from omat3d.printout import _accessor
from omat3d.store import Store

MARKER = ".3d-o-mat"
FAKE_SSH = Path(__file__).resolve().parent / "fake"
BOX = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], np.float32)
BOX_FACES = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                      [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]], np.uint16)
RED, GREY = (200, 30, 30), (0, 128, 0)


def png(size, colour) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), colour).save(buffer, "PNG")
    return buffer.getvalue()


def trellis_glb() -> bytes:
    """Wie TRELLIS.2 es liefern könnte, nur klein: ein Kopf (Quader 0,6 breit, 0,6 hoch) auf einem Hals (0,2 breit,
    0,4 hoch, nach rechts versetzt), als zwei Primitiven mit demselben Material, unter einem verschobenen Knoten, ohne
    Normalen. Die UVs liegen nur in der linken oberen Ecke des Atlas; der Rest der Textur ist schwarz."""
    neck = (BOX * [0.2, 0.4, 0.2] + [0.1, 0, -0.1]).astype(np.float32)
    head = (BOX * [0.6, 0.6, 0.6] + [-0.3, 0.4, -0.3]).astype(np.float32)
    uv = (BOX[:, :2] * 0.25 + 0.05).astype(np.float32)
    texture = Image.new("RGB", (64, 64), (0, 0, 0))
    texture.paste(Image.new("RGB", (20, 20), RED), (2, 2))
    base = io.BytesIO()
    texture.save(base, "PNG")
    blobs = [neck.tobytes(), head.tobytes(), uv.tobytes(), BOX_FACES.tobytes(), base.getvalue(), png(64, GREY)]
    views, binary = [], b""
    for blob in blobs:
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(blob)})
        binary += blob + b"\0" * (-len(blob) % 4)
    document = {
        "asset": {"version": "2.0", "generator": "mlx-serve"}, "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"translation": [5, 5, 5], "children": [1]}, {"mesh": 0}],
        "meshes": [{"primitives": [
            {"attributes": {"POSITION": 0, "TEXCOORD_0": 2}, "indices": 3, "material": 0},
            {"attributes": {"POSITION": 1, "TEXCOORD_0": 2}, "indices": 3, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "metallicRoughnessTexture": {"index": 1},
                                                "metallicFactor": 1.0, "roughnessFactor": 1.0}}],
        "textures": [{"source": 0}, {"source": 1}],
        "images": [{"bufferView": 4, "mimeType": "image/png"}, {"bufferView": 5, "mimeType": "image/png"}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5126, "count": 8, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5126, "count": 8, "type": "VEC2"},
            {"bufferView": 3, "componentType": 5123, "count": 36, "type": "SCALAR"}],
        "bufferViews": views, "buffers": [{"byteLength": len(binary)}],
    }
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def face_photo() -> bytes:
    """Ein „Kopf“ auf weißem Grund, als JPEG wie ein Foto."""
    image = Image.new("RGB", (160, 160), (255, 255, 255))
    image.paste(Image.new("RGB", (60, 80), (190, 140, 110)), (50, 40))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG")
    return buffer.getvalue()


class FakeTrellis:
    """Die Schranke mit TRELLIS.2: merkt sich, was ankam, und meldet Stufen, wie MLX-Serve es tut."""

    def __init__(self, hold=None):
        self.bodies = []
        self.hold = hold

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/gate/status":
            return httpx.Response(200, json={"waiting": [], "running": [], "apps": {}})
        assert request.url.path == "/v1/3d/generations"
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.hold:
            self.hold.wait(10)
        events = [{"type": "progress", "stage": "trellis-sparse-structure", "step": 1, "total": 2},
                  {"type": "progress", "stage": "trellis-shape-1024", "step": 1, "total": 2},
                  {"type": "progress", "stage": "texture-bake", "step": 1, "total": 1},
                  {"type": "complete", "format": "glb", "data": base64.b64encode(trellis_glb()).decode()}]
        text = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=text.encode())


@pytest.fixture(params=["folder", "ssh"])
def make(tmp_path, request, monkeypatch):
    (tmp_path / MARKER).write_text("")

    def build(fake=None):
        fake = fake or FakeTrellis()
        config = Config(trellis_model="trellis", assist_model="gemma", cutout="flood")
        if request.param == "ssh":
            monkeypatch.setenv("PATH", f"{FAKE_SSH}{os.pathsep}{os.environ['PATH']}")
            monkeypatch.setenv("OMAT3D_STORE_FOLDER", str(tmp_path))
            store = Store(ssh="marcel@oi-o-mat", key="/ssh/id_ed25519", log=lambda *_: None)
        else:
            store = Store(folder=str(tmp_path), log=lambda *_: None)
        gate = Gate("http://gate", "3d-o-mat", transport=httpx.MockTransport(fake))
        app = create_app(config, gate=gate, store=store, register=False, log=lambda *_: None)
        return TestClient(app, headers={"authorization": "Bearer kaderwerk"}), fake
    return build


def until(client, job_id, statuses=("done", "failed"), timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/v1/models3d/generations/{job_id}").json()
        if job["status"] in statuses:
            return job
        client.app.state.jobs.wake.set()
        time.sleep(0.05)
    raise AssertionError(f"Auftrag hängt: {job}")


def upload(client, data=None) -> str:
    response = client.post("/v1/files", content=data or face_photo(), headers={"content-type": "application/octet-stream"})
    assert response.status_code in (200, 201), response.text
    return response.json()["sha256"]


def test_head_from_picture(make):
    client, fake = make()
    with client:
        photo = face_photo()
        digest = upload(client, photo)
        assert digest == hashlib.sha256(photo).hexdigest()
        assert client.post("/v1/files", content=photo).status_code == 200  # schon da
        assert client.get(f"/v1/files/{digest}").content == photo

        response = client.post("/v1/models3d/generations", json={
            "image": digest, "faces": 30000, "texture": 512, "seed": 1234, "creator": "kaderwerk",
            "label": "HOCHOFEN 9 / KLEIN"})
        assert response.status_code == 202, response.text
        created = response.json()
        assert created["id"].startswith("job_") and created["status"] == "queued" and created["seed"] == 1234
        assert created["position"] >= 0

        job = until(client, created["id"])
        assert job["status"] == "done", job
        assert job["progress"] == 1.0 and job["error"] is None and job["position"] == 0
        result = job["result"]
        assert result["texture"] == 512 and result["faces"] == 24 and result["vertices"] == 16
        assert fake.bodies[0]["texture_size"] == 512 and fake.bodies[0]["decimation_target"] == 30000
        assert fake.bodies[0]["seed"] == 1234 and fake.bodies[0]["model"] == "trellis"

        glb = client.get(f"/v1/files/{result['file']}")
        assert glb.headers["content-type"] == "model/gltf-binary"
        assert hashlib.sha256(glb.content).hexdigest() == result["file"] and len(glb.content) == result["bytes"]
        document, _ = chunks(glb.content)
        assert document["asset"]["extras"] == {"model": "trellis", "seed": 1234, "image": digest, "creator": "kaderwerk",
                                               "label": "HOCHOFEN 9 / KLEIN", "height": 0.25, "faces": 24, "texture": 512}

        listed = client.get("/v1/models3d/generations").json()["data"]
        assert [entry["id"] for entry in listed] == [created["id"]]
        # Auf der Seite steht der Auftrag mit dem freigestellten Bild in der Liste.
        page = client.get("/api/jobs").json()[0]
        assert page["kind"] == "api" and page["imageName"] == "HOCHOFEN 9 / KLEIN" and page["imageId"]
        assert client.get(f"/api/jobs/{page['id']}/model.glb").content == glb.content
        assert client.get("/v1/busy").json()["busy"] is False


def test_without_seed_the_server_picks_one(make):
    client, fake = make()
    with client:
        created = client.post("/v1/models3d/generations", json={"image": upload(client)}).json()
        assert isinstance(created["seed"], int)
        job = until(client, created["id"])
        assert job["status"] == "done" and fake.bodies[0]["seed"] == created["seed"]
        assert fake.bodies[0]["texture_size"] == 2048 and fake.bodies[0]["decimation_target"] == 30000


@pytest.mark.parametrize("body, status, words", [
    ({"image": "abc"}, 400, "SHA-256"),
    ({"image": "0" * 64}, 404, "Keine Datei"),
    ({"image": "IMG", "faces": 100}, 400, "image"),
    ({"faces": 1999}, 400, "faces"),
    ({"faces": 500001}, 400, "faces"),
    ({"faces": "viel"}, 400, "faces"),
    ({"texture": 3000}, 400, "texture"),
    ({"texture": True}, 400, "texture"),
    ({"seed": -1}, 400, "seed"),
    ({"remove_background": "ja"}, 400, "remove_background"),
    ({"label": 7}, 400, "label"),
    ({"steps": 3}, 400, "Unbekannte Felder"),
])
def test_wrong_requests_are_refused(make, body, status, words):
    client, _ = make()
    with client:
        if "image" not in body:
            body = {"image": upload(client), **body}
        response = client.post("/v1/models3d/generations", json=body)
        assert response.status_code == status, response.text
        assert words in response.json()["error"]["message"]


def test_wrong_files_and_ids(make):
    client, _ = make()
    with client:
        assert client.post("/v1/files", content=b"").status_code == 400
        refused = client.post("/v1/files", content=b"kein Bild")
        assert refused.status_code == 415 and "PNG" in refused.json()["error"]["message"]
        assert client.get("/v1/files/xyz").status_code == 400
        assert client.get(f"/v1/files/{'a' * 64}").status_code == 404
        assert client.get("/v1/models3d/generations/job_20260101-000000-abcd").status_code == 404
        missing = client.get("/v1/models3d/generations/nichts")
        assert missing.status_code == 404 and "error" in missing.json()
        assert client.post("/v1/models3d/generations", content=b"{kaputt").status_code == 400


def test_waiting_job_can_be_taken_out(make):
    client, _ = make()
    # Ohne `with` läuft der Arbeiter nicht: die Aufträge bleiben in der Schlange.
    digest = upload(client)
    first = client.post("/v1/models3d/generations", json={"image": digest}).json()
    second = client.post("/v1/models3d/generations", json={"image": digest}).json()
    assert (first["position"], second["position"]) == (1, 2)
    assert client.get("/v1/busy").json() == {"busy": True, "running": None, "waiting": 2}
    taken = client.delete(f"/v1/models3d/generations/{first['id']}")
    assert taken.status_code == 200 and taken.json()["status"] == "canceled"
    assert client.get(f"/v1/models3d/generations/{second['id']}").json()["position"] == 1
    assert client.delete(f"/v1/models3d/generations/{first['id']}").status_code == 409
    jobs = client.app.state.jobs
    jobs.update(second["id"].removeprefix("job_"), state="running", stage="trellis-shape-1024", step=1, total=2)
    running = client.get(f"/v1/models3d/generations/{second['id']}").json()
    assert running["status"] == "running" and 0.3 < running["progress"] < 0.55
    assert client.delete(f"/v1/models3d/generations/{second['id']}").status_code == 409


def test_queue_survives_a_restart(make):
    client, _ = make()
    digest = upload(client)
    interrupted = client.post("/v1/models3d/generations", json={"image": digest}).json()
    waiting = client.post("/v1/models3d/generations", json={"image": digest}).json()
    client.app.state.jobs.update(interrupted["id"].removeprefix("job_"), state="running", stage="trellis-shape-1024")
    # Neu gestartet: dieselbe Ablage, ein frischer Server.
    again, _ = make()
    with again:
        failed = until(again, interrupted["id"], ("failed",))
        assert "Neustart" in failed["error"]
        assert until(again, waiting["id"])["status"] == "done"


def test_export_shapes_the_glb():
    glb, info = finish(trellis_glb(), 128, 0.25, {"seed": 1})
    document, binary = chunks(glb)
    assert len(document["meshes"]) == 1 and len(document["meshes"][0]["primitives"]) == 1
    assert "skins" not in document and "animations" not in document
    primitive = document["meshes"][0]["primitives"][0]
    assert set(primitive["attributes"]) == {"POSITION", "NORMAL", "TEXCOORD_0"} and "indices" in primitive
    points = _accessor(document, binary, primitive["attributes"]["POSITION"])
    normals = _accessor(document, binary, primitive["attributes"]["NORMAL"])
    # 0,25 m hoch, unten auf 0, der Hals (nicht die ganze Hülle) mittig über dem Ursprung.
    assert points[:, 1].min() == pytest.approx(0) and points[:, 1].max() == pytest.approx(0.25)
    neck = points[points[:, 1] < 0.01]
    assert (neck[:, 0].min() + neck[:, 0].max()) / 2 == pytest.approx(0, abs=1e-6)
    assert (neck[:, 2].min() + neck[:, 2].max()) / 2 == pytest.approx(0, abs=1e-6)
    assert points[:, 0].min() < -0.1  # der Kopf ragt nach links über den Hals
    assert np.allclose(np.linalg.norm(normals, axis=1), 1, atol=1e-5)
    assert info == {"faces": 24, "vertices": 16, "texture": 128, "bytes": len(glb)}
    pictures = []
    for image in document["images"]:
        view = document["bufferViews"][image["bufferView"]]
        pictures.append(Image.open(io.BytesIO(binary[view["byteOffset"]:view["byteOffset"] + view["byteLength"]])))
    assert [picture.size for picture in pictures] == [(128, 128), (128, 128)]
    assert all(document["images"][i]["mimeType"] == "image/png" for i in (0, 1))
    # Was nicht zu einer Insel gehört, hat die Farbe ihres Rands, statt schwarz an den Nähten durchzuscheinen.
    base = np.asarray(pictures[0])
    assert tuple(base[127, 127]) == RED and tuple(base[64, 2]) == RED
    material = document["materials"][0]["pbrMetallicRoughness"]
    assert material["baseColorTexture"] == {"index": 0} and material["metallicRoughnessTexture"] == {"index": 1}


def test_export_refuses_two_materials():
    document, binary = chunks(trellis_glb())
    document["materials"].append(document["materials"][0])
    document["meshes"][0]["primitives"][1]["material"] = 1
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(binary), 0x004E4942) + binary
    with pytest.raises(ExportError, match="Materialien"):
        finish(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body, 128, 0.25, {})

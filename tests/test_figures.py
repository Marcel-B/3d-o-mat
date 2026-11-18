"""Eine Figur aufbereiten: der Dienst auf oi-o-mat (prep/server.py) mit einem nachgestellten figur_aufbereiten.py
(tests/fake), einmal als eigener Prozess über HTTP und einmal hinter der Schnittstelle /v1/figures/preparations (figures.py,
jobs.py), wobei die Schranke den Dienst direkt aufruft. Geprüft: Ergebnis und Bericht, Exit-Code 2 (Prüfung nicht
bestanden), Exit-Code 1 (Eingabe abgelehnt), Absturz, unzulässige Werte, Abbrechen und dass die Figur danach ein Skelett
bekommen kann."""
import base64
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from omat3d.app import create_app
from omat3d.config import Config
from omat3d.gate import Gate
from omat3d.store import Store

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests" / "fake" / "figur_aufbereiten.py"
BLEND = b"BLENDER-v503REND figur koerper kopf"


def load_server(tmp_path, monkeypatch):
    settings = tmp_path / "prep.json"
    settings.write_text(json.dumps({"python": sys.executable, "port": 11259, "memoryGb": 10}))
    spec = importlib.util.spec_from_file_location("prep_server", ROOT / "prep" / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "SETTINGS", str(settings))
    monkeypatch.setattr(module, "SCRIPT", str(FAKE))
    monkeypatch.setattr(module, "HEAD_SCRIPT", str(ROOT / "tests" / "fake" / "kopf_aufsetzen.py"))
    monkeypatch.setattr(module, "RIG_SCRIPT", str(ROOT / "tests" / "fake" / "rig_uebertragen.py"))
    return module


# --- der Dienst als eigener Prozess

@pytest.fixture
def service(tmp_path):
    settings = tmp_path / "prep.json"
    settings.write_text(json.dumps({"python": sys.executable, "port": 11258, "memoryGb": 10}))
    process = subprocess.Popen([sys.executable, str(ROOT / "prep" / "server.py")], stderr=subprocess.PIPE,
                               env={**os.environ, "OMAT3D_PREP_SETTINGS": str(settings), "OMAT3D_PREP_SCRIPT": str(FAKE)})
    for _ in range(50):
        try:
            urllib.request.urlopen("http://127.0.0.1:11258/health", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    yield "http://127.0.0.1:11258"
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


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_service_prepares(service):
    status, models = call(service, "/v1/models")
    assert status == 200 and models["data"][0]["id"] == "figur-aufbereiten" and models["data"][0]["available"] is True
    status, answer = call(service, "/v1/prepare", {"model": "figur-aufbereiten", "blend": b64(BLEND), "options": {
        "height": 1.89, "texture": 1024, "skin_color": [0.8, 0.6, 0.5], "head": "Kopf_langer_Hals", "fbx": True,
        "retouch": [{"name": "Ruecken", "lo": [0, 0, 0], "hi": [1, 1, 1], "dunkel_unter": 0.42}]}})
    assert status == 200, answer
    assert answer["exitCode"] == 0 and answer["passed"] is True
    arguments = answer["report"]["argumente"]
    assert arguments["hoehe"] == "1.89" and arguments["textur"] == "1024" and arguments["kopf"] == "Kopf_langer_Hals"
    assert arguments["hautfarbe"] == "0.8000,0.6000,0.5000"
    assert answer["report"]["retusche"][0]["name"] == "Ruecken"
    files = {key: base64.b64decode(value) for key, value in answer["files"].items()}
    assert files["glb"].startswith(b"glTF") and files["blend"].startswith(b"BLENDER") and files["preview"].startswith(b"\x89PNG")
    names = sorted(zipfile.ZipFile(io.BytesIO(files["fbx"])).namelist())
    assert names == ["figur.fbx", "figur_texturen/farbe.png"]


def test_service_reports_exit_codes(service):
    status, answer = call(service, "/v1/prepare", {"blend": b64(BLEND + b"backside")})
    assert status == 200 and answer["exitCode"] == 2 and answer["passed"] is False
    assert answer["report"]["pruefung_rueckseiten_pixel"] == 812 and "glb" in answer["files"]
    status, answer = call(service, "/v1/prepare", {"blend": b64(BLEND + b"fail")})
    assert status == 422 and "Erwarte ein oder zwei Mesh-Objekte" in answer["error"]["message"]
    status, answer = call(service, "/v1/prepare", {"blend": b64(BLEND + b"crash")})
    assert status == 500 and "bpy ist abgestürzt" in answer["error"]["message"]
    status, answer = call(service, "/v1/prepare", {"blend": b64(BLEND), "options": {"check": False}})
    assert status == 200 and answer["passed"] is None and "preview" not in answer["files"]


@pytest.mark.parametrize("body, words", [
    ({"blend": b64(b"PK\x03\x04zip")}, "keine .blend"),
    ({"blend": "%%%"}, "Base64"),
    ({"blend": b64(BLEND), "model": "skintokens"}, "unbekanntes Modell"),
    ({"blend": b64(BLEND), "options": {"texture": 3000}}, "texture"),
    ({"blend": b64(BLEND), "options": {"height": 9}}, "height"),
    ({"blend": b64(BLEND), "options": {"skin_color": [1, 2, 3]}}, "skin_color"),
    ({"blend": b64(BLEND), "options": {"wireframe": True}}, "unbekannte Optionen"),
])
def test_service_refuses(service, body, words):
    status, answer = call(service, "/v1/prepare", body)
    assert status == 400 and words in answer["error"]["message"]


# --- hinter der Schnittstelle /v1

class Gateway:
    """Die Schranke: reicht /v1/prepare an den Dienst weiter (im selben Prozess), merkt sich die Optionen."""

    def __init__(self, server, hold=None):
        self.server = server
        self.bodies = []
        self.hold = hold

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/gate/status":
            return httpx.Response(200, json={"waiting": [], "running": [], "apps": {}})
        if request.url.path == "/v1/rig":
            body = json.loads(request.content)
            glb = base64.b64decode(body["glb"])
            return httpx.Response(200, json={"format": "glb", "data": b64(glb + b"+armature"), "report": {}})
        assert request.url.path in ("/v1/prepare", "/v1/player")
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.hold:
            self.hold.wait(10)
        handler = self.server.player if request.url.path == "/v1/player" else self.server.prepare
        status, answer = handler(body)
        return httpx.Response(status, json=answer)


@pytest.fixture
def make(tmp_path, monkeypatch):
    (tmp_path / "store").mkdir()
    (tmp_path / "store" / ".3d-o-mat").write_text("")
    server = load_server(tmp_path, monkeypatch)

    def build(hold=None):
        gateway = Gateway(server, hold)
        config = Config(trellis_model="trellis", assist_model="gemma", cutout="flood")
        store = Store(folder=str(tmp_path / "store"), log=lambda *_: None)
        gate = Gate("http://gate", "3d-o-mat", transport=httpx.MockTransport(gateway))
        app = create_app(config, gate=gate, store=store, register=False, log=lambda *_: None)
        return TestClient(app, headers={"authorization": "Bearer iron-rush"}), gateway
    return build


def upload(client, data=BLEND) -> str:
    response = client.post("/v1/files", content=data, headers={"content-type": "application/octet-stream"})
    assert response.status_code in (200, 201), response.text
    return response.json()["sha256"]


def until(client, api_id, statuses=("done", "failed"), timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/v1/figures/preparations/{api_id}").json()
        if job["status"] in statuses:
            return job
        client.app.state.jobs.wake.set()
        time.sleep(0.05)
    raise AssertionError(f"Aufbereitung hängt: {job}")


def test_prepare_through_the_interface(make):
    client, gateway = make()
    with client:
        digest = upload(client)
        response = client.post("/v1/figures/preparations", json={"blend": digest, "label": "KLEIN", "fbx": True,
                                                                  "head_triangles": 15000})
        assert response.status_code == 202, response.text
        created = response.json()
        assert created["id"].startswith("prep_") and created["status"] == "queued"
        job = until(client, created["id"])
        assert job["status"] == "done", job
        assert job["passed"] is True and job["error"] is None and job["creator"] == "iron-rush"
        assert gateway.bodies[0]["model"] == "figur-aufbereiten"
        assert gateway.bodies[0]["options"] == {"height": 1.89, "head_triangles": 15000, "check": True, "fbx": True}
        result = job["result"]
        assert result["exit_code"] == 0 and result["dreiecke_klein_gesamt"] == 47533 and result["pruefung_bestanden"]
        assert set(result["files"]) == {"glb", "blend", "preview", "fbx", "report"}
        glb = client.get(f"/v1/files/{result['files']['glb']}")
        assert glb.headers["content-type"] == "model/gltf-binary" and glb.content.startswith(b"glTF")
        assert client.get(f"/v1/files/{result['files']['fbx']}").headers["content-type"] == "application/zip"
        report = client.get(f"/v1/files/{result['files']['report']}").json()
        assert report["kopf_modus"] == "haut+gesicht"
        assert [entry["id"] for entry in client.get("/v1/figures/preparations").json()["data"]] == [created["id"]]
        # Nicht in der Liste der Bild-Aufträge, aber auf der Seite, mit GLB und Skelett zum Einsetzen.
        assert client.get("/v1/models3d/generations").json()["data"] == []
        page = client.get(f"/api/jobs/{job['job']}").json()
        assert page["kind"] == "prep" and page["imageName"] == "KLEIN"
        assert client.get(f"/api/jobs/{job['job']}/model.glb").content == glb.content
        rig = client.post(f"/api/jobs/{job['job']}/rig", json={"class": "humanoid", "seed": 1})
        assert rig.status_code == 200, rig.text


def test_failed_check_keeps_the_files(make):
    client, _ = make()
    with client:
        created = client.post("/v1/figures/preparations", json={"blend": upload(client, BLEND + b"backside")}).json()
        job = until(client, created["id"])
        assert job["status"] == "failed" and job["passed"] is False
        assert "Prüfung nicht bestanden: 812 Pixel" in job["error"]
        assert job["result"]["exit_code"] == 2 and "glb" in job["result"]["files"]


def test_refused_input_fails_with_the_reason(make):
    client, _ = make()
    with client:
        created = client.post("/v1/figures/preparations", json={"blend": upload(client, BLEND + b"fail")}).json()
        job = until(client, created["id"])
        assert job["status"] == "failed" and job["result"] is None
        assert "Erwarte ein oder zwei Mesh-Objekte" in job["error"]


@pytest.mark.parametrize("body, status, words", [
    ({"blend": "abc"}, 400, "SHA-256"),
    ({"blend": "0" * 64}, 404, "erst mit POST /v1/files"),
    ({"blend": "BLEND", "texture": 3000}, 400, "texture"),
    ({"blend": "BLEND", "height": 0.1}, 400, "height"),
    ({"blend": "BLEND", "check": "ja"}, 400, "check"),
    ({"blend": "BLEND", "retouch": [{"lo": [0, 0]}]}, 400, "retouch"),
    ({"blend": "BLEND", "seed": 1}, 400, "Unbekannte Felder"),
    ({"blend": "IMAGE"}, 400, "keine .blend"),
])
def test_refused_requests(make, body, status, words):
    client, _ = make()
    with client:
        blend = upload(client)
        image = upload(client, b"\x89PNG\r\n\x1a\n" + b"x" * 20)
        body = {**body, "blend": {"BLEND": blend, "IMAGE": image}.get(body["blend"], body["blend"])}
        response = client.post("/v1/figures/preparations", json=body)
        assert response.status_code == status and words in response.json()["error"]["message"], response.text


def test_upload_takes_only_known_files(make):
    client, _ = make()
    with client:
        response = client.post("/v1/files", content=b"PK\x03\x04 zip")
        assert response.status_code == 415 and ".blend" in response.json()["error"]["message"]
        assert client.post("/v1/files", content=b"glTF\x02\x00\x00\x00kopf").status_code == 201
        response = client.post("/v1/files", content=b"\x89PNG\r\n\x1a\n" + b"x" * (41 * 1024 * 1024))
        assert response.status_code == 413


def test_waiting_preparation_can_be_canceled(make):
    import threading
    hold = threading.Event()
    client, _ = make(hold)
    with client:
        digest = upload(client)
        first = client.post("/v1/figures/preparations", json={"blend": digest}).json()
        second = client.post("/v1/figures/preparations", json={"blend": digest}).json()
        until(client, first["id"], ("running",))
        assert client.delete(f"/v1/figures/preparations/{first['id']}").status_code == 409
        assert client.delete(f"/v1/figures/preparations/{second['id']}").json()["status"] == "canceled"
        hold.set()
        assert until(client, first["id"])["status"] == "done"
        assert client.get("/v1/figures/preparations/prep_nope").status_code == 404


GLB = b"glTF\x02\x00\x00\x00kopf mit hals"
RIG = b"BLENDER-v503 teamfigur mit armature"


def test_player_figure_in_one_job(make):
    """Kopf + Teamkörper + Teamrig -> aufbereitet und gerigged: FBX, Texturen, GLB, Bericht."""
    client, gateway = make()
    with client:
        ids = {key: upload(client, data) for key, data in (("head", GLB), ("body", BLEND), ("rig", RIG))}
        response = client.post("/v1/figures/players", json={"name": "azari", **ids, "head_triangles": 15000})
        assert response.status_code == 202, response.text
        job = until(client, response.json()["id"])
        assert job["status"] == "done", job
        assert job["player"]["name"] == "azari" and job["label"] == "AZARI"
        sent = gateway.bodies[0]
        assert sent["name"] == "azari" and sent["options"] == {"height": 1.89, "head_triangles": 15000, "check": True}
        files = job["result"]["files"]
        assert {"fbx", "textures", "glb", "blend", "preview", "report"} <= set(files)
        assert client.get(f"/v1/files/{files['fbx']}").content == b"Kaydara FBX Binary azari"
        names = zipfile.ZipFile(io.BytesIO(client.get(f"/v1/files/{files['textures']}").content)).namelist()
        assert sorted(names) == ["azari-farbe.png", "azari-normal.png"]


def test_player_figure_stops_before_the_rig_when_the_check_fails(make):
    client, _ = make()
    with client:
        ids = {"head": upload(client, GLB + b"backside"), "body": upload(client), "rig": upload(client, RIG)}
        job = until(client, client.post("/v1/figures/players", json={"name": "riggs", **ids}).json()["id"])
        assert job["status"] == "failed" and job["passed"] is False and "fbx" not in job["result"]["files"]


@pytest.mark.parametrize("change, status, words", [
    ({"name": "Azari"}, 400, "name"),
    ({"head": "BODY"}, 400, "head: erwartet model/gltf-binary"),
    ({"rig": "0" * 64}, 404, "rig: keine Datei"),
    ({"fbx": True}, 400, "Unbekannte Felder"),
])
def test_player_figure_refused(make, change, status, words):
    client, _ = make()
    with client:
        ids = {"head": upload(client, GLB), "body": upload(client), "rig": upload(client, RIG)}
        body = {"name": "azari", **ids, **change}
        if body.get("head") == "BODY":
            body["head"] = ids["body"]
        response = client.post("/v1/figures/players", json=body)
        assert response.status_code == status and words in response.json()["error"]["message"], response.text


def test_service_player_failures(tmp_path, monkeypatch):
    server = load_server(tmp_path, monkeypatch)
    base = {"name": "azari", "head": b64(GLB), "body": b64(BLEND), "rig": b64(RIG)}
    status, answer = server.player({**base, "head": b64(b"glTFnohead")})
    assert status == 500 and "Kein Hals" in answer["error"]["message"]
    status, answer = server.player({**base, "rig": b64(RIG + b"brokenrig")})
    assert status == 500 and "Armature fehlt" in answer["error"]["message"]
    assert server.player({**base, "options": {"fbx": True}})[0] == 400
    assert server.player({**base, "name": "../x"})[0] == 400

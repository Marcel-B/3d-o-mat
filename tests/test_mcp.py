"""Der MCP-Server unter /mcp (mcp.py): Protokoll (initialize, tools/list, Benachrichtigungen, Fehler) und der ganze Weg
eines Agents mit nachgestellter Schranke: Bild hochladen, ansehen, prüfen, malen, in 3D, warten, Skelett, Export als
GLB und 3MF, dazu FBX nur, wenn die Route da ist."""
import base64
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from omat3d.app import create_app
from omat3d.config import Config
from omat3d.gate import Gate
from omat3d.store import Store
from test_app import FakeGate, disc_on_white
from test_models3d import MARKER, FakeTrellis, face_photo

BASE = "http://testserver"


class Gates:
    """TRELLIS.2 mit einem echten kleinen Netz (test_models3d), Gemma und img-o-mat wie in test_app."""

    def __init__(self):
        self.trellis = FakeTrellis()
        self.other = FakeGate([])
        self.rigged = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/v1/chat/completions", "/v1/images/generations"):
            return self.other(request)
        return self.trellis(request)


@pytest.fixture
def client(tmp_path):
    (tmp_path / MARKER).write_text("")
    config = Config(trellis_model="trellis", assist_model="gemma", cutout="flood")
    gate = Gate("http://gate", "3d-o-mat", transport=httpx.MockTransport(Gates()))
    app = create_app(config, gate=gate, store=Store(folder=str(tmp_path), log=lambda *_: None), register=False,
                     log=lambda *_: None)
    with TestClient(app) as test_client:
        yield test_client


def rpc(client, method, params=None, ident=1):
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}},
                           headers={"accept": "application/json, text/event-stream"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == ident and "error" not in body, body
    return body["result"]


def tool(client, name, **arguments):
    result = rpc(client, "tools/call", {"name": name, "arguments": arguments})
    return result


def ok(client, name, **arguments):
    result = tool(client, name, **arguments)
    assert result["isError"] is False, result["content"][0]["text"]
    return result.get("structuredContent", json.loads(result["content"][0]["text"]) if result["content"][0]["type"] == "text" else None)


def failed(client, name, **arguments) -> str:
    result = tool(client, name, **arguments)
    assert result["isError"] is True, result
    return result["content"][0]["text"]


def wait(client, job_id):
    for _ in range(100):
        client.app.state.jobs.wake.set()
        job = ok(client, "get_job", job_id=job_id, wait_seconds=1)
        if job["state"] not in ("queued", "waiting", "running"):
            return job
    raise AssertionError(job)


def test_protocol(client):
    result = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "test", "version": "1"}})
    assert result["protocolVersion"] == "2025-06-18" and result["serverInfo"]["name"] == "3d-o-mat"
    assert "tools" in result["capabilities"] and f"Address: {BASE}" in result["instructions"]
    assert rpc(client, "initialize", {"protocolVersion": "1999-01-01"})["protocolVersion"] == "2025-11-25"
    # Benachrichtigungen bekommen keine Antwort.
    note = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert note.status_code == 202 and not note.content
    assert rpc(client, "ping") == {}
    tools = {t["name"]: t for t in rpc(client, "tools/list")["tools"]}
    assert {"get_status", "upload_image", "create_model", "get_job", "add_skeleton", "export_model"} <= set(tools)
    assert all(t["inputSchema"]["type"] == "object" and t["description"] for t in tools.values())
    assert client.get("/mcp").status_code == 405
    unknown = client.post("/mcp", json={"jsonrpc": "2.0", "id": 7, "method": "nope"}).json()
    assert unknown["error"]["code"] == -32601
    assert client.post("/mcp", content=b"{kaputt").json()["error"]["code"] == -32700
    batch = client.post("/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                      {"jsonrpc": "2.0", "method": "notifications/initialized"}]).json()
    assert batch == [{"jsonrpc": "2.0", "id": 1, "result": {}}]
    # Ein Browser auf einer fremden Seite (DNS-Rebinding) kommt nicht durch.
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                       headers={"origin": "http://evil.example"}).status_code == 403
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                       headers={"origin": BASE}).status_code == 200


def test_wrong_calls_are_tool_errors(client):
    assert "Unknown tool" in client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                        "params": {"name": "zaubern"}}).json()["error"]["message"]
    assert "fehlt: job_id" in failed(client, "get_job")
    assert "unbekannt: farbe" in failed(client, "get_job", job_id="x", farbe="rot")
    assert "404" in failed(client, "get_job", job_id="20200101-000000-abcd")
    assert "Genau eins" in failed(client, "create_model")
    assert "Base64" in failed(client, "upload_image", data_base64="%%%")
    assert "nur mit image_sha256" in failed(client, "create_model", image_id="x", height=1)


def test_picture_to_model_to_print(client):
    status = ok(client, "get_status")
    assert status["storage"]["mounted"] and status["queue"]["waiting"] == 0
    image = ok(client, "upload_image", data_base64=base64.b64encode(disc_on_white()).decode(), filename="scheibe.png")
    assert image["name"] == "scheibe" and image["cutout"]["method"] == "flood"
    assert [i["id"] for i in ok(client, "list_images")] == [image["id"]]
    shown = tool(client, "show_image", image_id=image["id"])
    assert shown["content"][0]["type"] == "image" and shown["content"][0]["mimeType"] == "image/png"
    assert ok(client, "check_image", image_id=image["id"])["check"]["suitable"] is True

    job = ok(client, "create_model", image_id=image["id"], texture_size=1024, faces=20000, seed=5)
    assert job["state"] == "queued" and job["options"] == {"texture_size": 1024, "decimation_target": 20000, "seed": 5}
    done = wait(client, job["id"])
    assert done["state"] == "done" and done["progress"] == 1.0 and done["skeleton"] is False
    glb_url = done["downloads"]["glb"]
    assert glb_url.startswith(f"{BASE}/api/jobs/{job['id']}/model.glb")
    assert client.get(glb_url.removeprefix(BASE)).content[:4] == b"glTF"
    assert [j["id"] for j in ok(client, "list_jobs", state="done")] == [job["id"]]

    exported = ok(client, "export_model", job_id=job["id"], format="glb")
    assert exported["url"] == glb_url
    printed = ok(client, "export_model", job_id=job["id"], format="3mf", height_mm=50, base=False)
    data = client.get(printed["url"].removeprefix(BASE))
    assert data.status_code == 200 and data.content[:2] == b"PK" and len(data.content) == printed["bytes"]
    assert "attachment" in data.headers["content-disposition"]
    assert client.get(printed["url"].removeprefix(BASE) + "x").status_code == 404
    assert "wartende" in failed(client, "cancel_job", job_id=job["id"])
    rig = ok(client, "add_skeleton", job_id=job["id"], kind="humanoid")
    assert rig["kind"] == "rig" and rig["sourceJobId"] == job["id"] and rig["state"] == "queued"
    assert "class" in failed(client, "add_skeleton", job_id=job["id"], kind="drache")


def test_picture_by_hash_like_kaderwerk(client):
    sha = client.post("/v1/files", content=face_photo()).json()["sha256"]
    job = ok(client, "create_model", image_sha256=sha, texture_size=1024, height=0.3, label="Kopf")
    assert job["kind"] == "api" and job["apiId"] == f"job_{job['id']}" and job["app"] == "mcp"
    done = wait(client, job["apiId"])
    assert done["state"] == "done"
    assert client.get(done["downloads"]["glbByHash"].removeprefix(BASE)).content[:4] == b"glTF"


def test_painting(client):
    painting = ok(client, "paint_images", prompt="a red ceramic disc", n=2)
    assert painting["state"] == "running"
    result = ok(client, "get_painting", painting_id=painting["id"], wait_seconds=10)
    assert result["state"] == "done" and len(result["imageIds"]) == 2
    assert ok(client, "write_image_prompts", idea="Teller")["prompts"] == ["a red ceramic disc"]


def test_fbx_comes_with_its_route(client):
    """FBX gibt es erst mit der Route GET /api/jobs/<id>/model.fbx, und nur, wenn /api/status nicht "fbx": false
    meldet (Blender fehlt)."""
    server = client.app.state.mcp
    image = ok(client, "upload_image", data_base64=base64.b64encode(disc_on_white()).decode())
    job = wait(client, ok(client, "create_model", image_id=image["id"], texture_size=1024)["id"])

    server.has_route = lambda path: False
    assert ok(client, "get_status")["fbx"] is None and "fbx_unity" not in ok(client, "get_job", job_id=job["id"])["downloads"]
    assert "noch kein FBX" in failed(client, "export_model", job_id=job["id"], format="fbx")

    server.has_route = lambda path: True
    answers = {}
    real = server.call

    async def call(method, path, **kwargs):
        if path == "/api/status":
            return {**await real(method, path, **kwargs), **answers}
        return await real(method, path, **kwargs)
    server.call = call
    answers["fbx"] = False
    assert "Blender" in failed(client, "export_model", job_id=job["id"], format="fbx")
    answers["fbx"] = True
    job = ok(client, "get_job", job_id=job["id"])
    assert job["downloads"]["fbx_unreal"] == f"{BASE}/api/jobs/{job['id']}/model.fbx?target=unreal"
    exported = ok(client, "export_model", job_id=job["id"], format="fbx", target="unreal")
    assert exported["url"].endswith("target=unreal") and ok(client, "get_status")["fbx"] is True

"""Gelenke verschieben: die Bindematrizen folgen, das Netz steht in der Grundhaltung wie vorher."""

import json
import struct

import numpy as np
import pytest

from omat3d import skeleton
from omat3d.animate import chunks
from omat3d.app import create_app
from omat3d.config import Config
from omat3d.printout import _accessor
from omat3d.store import Store
from fastapi.testclient import TestClient


def rigged_glb() -> bytes:
    """Hüfte bei (0, 1, 0), ein Bein-Knochen bei (0, -0.5, 0) darunter, ein Punkt zur Hälfte an beiden Knochen."""
    hips = np.eye(4); hips[1, 3] = 1.0
    leg = np.eye(4); leg[1, 3] = 0.5
    inverses = np.array([np.linalg.inv(hips).T, np.linalg.inv(leg).T], np.float32).reshape(-1)
    points = np.array([[0.1, 0.7, 0.0]], np.float32)
    joints = np.array([[0, 1, 0, 0]], np.uint8)
    weights = np.array([[0.5, 0.5, 0, 0]], np.float32)
    binary = inverses.tobytes() + points.tobytes() + joints.tobytes() + b"\0" * 12 + weights.tobytes()
    o1, o2, o3 = inverses.nbytes, inverses.nbytes + points.nbytes, inverses.nbytes + points.nbytes + 16
    document = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0, 3]}],
        "nodes": [{"name": "Armature", "children": [1]}, {"name": "Hips", "translation": [0, 1, 0], "children": [2]},
                  {"name": "LeftUpLeg", "translation": [0, -0.5, 0]}, {"name": "Figur", "mesh": 0, "skin": 0}],
        "skins": [{"joints": [1, 2], "inverseBindMatrices": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 1, "JOINTS_0": 2, "WEIGHTS_0": 3}}]}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 2, "type": "MAT4"},
                      {"bufferView": 1, "componentType": 5126, "count": 1, "type": "VEC3", "min": [0.1, 0.7, 0], "max": [0.1, 0.7, 0]},
                      {"bufferView": 2, "componentType": 5121, "count": 1, "type": "VEC4"},
                      {"bufferView": 3, "componentType": 5126, "count": 1, "type": "VEC4"}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": inverses.nbytes},
                        {"buffer": 0, "byteOffset": o1, "byteLength": points.nbytes},
                        {"buffer": 0, "byteOffset": o2, "byteLength": 4},
                        {"buffer": 0, "byteOffset": o3, "byteLength": weights.nbytes}],
        "buffers": [{"byteLength": len(binary)}],
    }
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def skinned_point(glb: bytes) -> np.ndarray:
    """Der eine Punkt in der Grundhaltung, über die Gewichte und Bindematrizen gerechnet (wie three.js)."""
    document, binary = chunks(glb)
    nodes = document["nodes"]
    world = skeleton._world_matrices(nodes)
    inverse = _accessor(document, binary, 0).reshape(-1, 4, 4).transpose(0, 2, 1)
    point = np.append(_accessor(document, binary, 1)[0], 1.0)
    skin = document["skins"][0]
    out = np.zeros(4)
    for weight, j in ((0.5, 0), (0.5, 1)):
        out += weight * (world[skin["joints"][j]] @ inverse[j] @ point)
    return out[:3]


def test_a_moved_joint_keeps_the_mesh_in_place_and_updates_the_inverses():
    before = rigged_glb()
    after, count = skeleton.move_joints(before, {2: [0.0, -0.3, 0.1]})
    assert count == 1
    document, binary = chunks(after)
    assert document["nodes"][2]["translation"] == [0.0, -0.3, 0.1]
    world = skeleton._world_matrices(document["nodes"])
    inverse = _accessor(document, binary, 0).reshape(-1, 4, 4).transpose(0, 2, 1)
    assert np.allclose(inverse[1] @ world[2], np.eye(4), atol=1e-6)
    assert np.allclose(skinned_point(after), skinned_point(before), atol=1e-6)
    # alles andere in der Datei ist geblieben
    assert _accessor(document, binary, 1).tolist() == [[pytest.approx(0.1), pytest.approx(0.7), 0.0]]
    assert len(chunks(after)[1]) == len(chunks(before)[1])


def test_unchanged_and_bad_moves():
    glb = rigged_glb()
    same, count = skeleton.move_joints(glb, {2: [0, -0.5, 0]})
    assert same is glb and count == 0
    for bad in ({3: [0, 0, 0]}, {2: [0, 0]}, {2: ["a", 0, 0]}, {2: [1e4, 0, 0]}):
        with pytest.raises(skeleton.SkeletonError):
            skeleton.move_joints(glb, bad)
    with pytest.raises(skeleton.SkeletonError, match="GLB"):
        skeleton.move_joints(b"glTF\x02\x00\x00\x00kaputt", {2: [0, 0, 0]})


def test_the_api_keeps_the_original_and_resets(tmp_path):
    (tmp_path / ".3d-o-mat").write_text("")
    store = Store(folder=str(tmp_path), log=lambda *_: None)
    job = "20260101-000000-aa02"
    store.write("jobs", {"id": job, "kind": "rig", "state": "done", "imageName": "Figur", "fingers": {},
                         "created": "2026-01-01T00:00:00", "options": {}})
    store.put("jobs", job, "model.glb", rigged_glb())
    config = Config(trellis_model="trellis", assist_model="gemma", defaults={}, cutout="flood")
    app = create_app(config, gate=None, store=store, register=False, log=lambda *_: None)
    with TestClient(app) as client:
        moved = client.post(f"/api/jobs/{job}/skeleton", json={"moves": {"2": [0, -0.3, 0.1]}})
        assert moved.status_code == 200 and moved.json() == {"moved": 1, "skeletonEdited": 1}
        assert client.get(f"/api/jobs/{job}").json()["skeletonEdited"] == 1
        served = chunks(client.get(f"/api/jobs/{job}/model.glb").content)[0]
        assert served["nodes"][2]["translation"] == [0.0, -0.3, 0.1]
        assert chunks(store.get("jobs", job, "skeleton-original.glb"))[0]["nodes"][2]["translation"] == [0, -0.5, 0]
        again = client.post(f"/api/jobs/{job}/skeleton", json={"moves": {"2": [0, -0.2, 0.1]}})
        assert again.json() == {"moved": 1, "skeletonEdited": 2}
        assert client.post(f"/api/jobs/{job}/skeleton", json={"moves": {"3": [0, 0, 0]}}).status_code == 422
        assert client.post(f"/api/jobs/{job}/skeleton", json={"moves": {"x": [0, 0, 0]}}).status_code == 422
        assert client.post(f"/api/jobs/{job}/skeleton/reset").json() == {"skeletonEdited": 0}
        assert chunks(client.get(f"/api/jobs/{job}/model.glb").content)[0]["nodes"][2]["translation"] == [0, -0.5, 0]
        assert "skeletonEdited" in client.get(f"/api/jobs/{job}").json()

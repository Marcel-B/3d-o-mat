"""Die Fingerketten eines SkinTokens-Skeletts bekommen Mixamo-Namen, beim Einsetzen und beim ersten Lesen."""

import json
import struct

import pytest

from omat3d import fingers
from omat3d.animate import chunks
from omat3d.config import Config
from omat3d.app import create_app
from omat3d.store import Store
from fastapi.testclient import TestClient


def glb(nodes: list) -> bytes:
    document = {"asset": {"version": "2.0"}, "nodes": nodes, "scenes": [{"nodes": [0]}],
                "buffers": [{"byteLength": 4}], "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 4}]}
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", 4, 0x004E4942) + b"\1\2\3\4"
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def chain(nodes: list, parent: int, prefix: str, start: list, count: int) -> None:
    """Hängt eine Kette aus count Knochen an parent; der erste sitzt bei start, die weiteren je 1,5 cm weiter."""
    for k in range(count):
        nodes.append({"name": f"{prefix}{len(nodes)}", "translation": start if k == 0 else [0, 0.015, 0]})
        index = len(nodes) - 1
        nodes[parent].setdefault("children", []).append(index)
        parent = index


def skeleton(left: int, right: int) -> list:
    """Hüfte, zwei Hände; links left Ketten (Daumen nah am Gelenk, die anderen an den Knöcheln), rechts right."""
    nodes = [{"name": "Armature", "children": [1]}, {"name": "mixamorig:Hips", "children": [2, 3]},
             {"name": "mixamorig:LeftHand"}, {"name": "mixamorig:RightHand"}]
    for hand, count, sign in ((2, left, -1), (3, right, 1)):
        if count:
            chain(nodes, hand, "extra_", [sign * 0.011, 0.031, -0.023], 3)  # der Daumen
        # Knöchel von der Daumenseite weg: Zeigefinger zuerst, kleiner Finger zuletzt
        for k in range(count - 1):
            chain(nodes, hand, "bone_", [sign * (0.013 - 0.012 * k), 0.07, -0.02 + 0.01 * k], 3)
    return nodes


def names(data: bytes) -> list:
    return [node.get("name") for node in chunks(data)[0]["nodes"]]


def test_five_chains_become_thumb_and_four_fingers_and_one_chain_a_mitten():
    data, named = fingers.name_fingers(glb(skeleton(5, 2)))
    assert named == {"Left": 5, "Right": 2}
    found = names(data)
    for finger in ("Thumb", "Index", "Middle", "Ring", "Pinky"):
        assert [f"mixamorig:LeftHand{finger}{k}" for k in (1, 2, 3)] == [n for n in found if n.startswith(f"mixamorig:LeftHand{finger}")]
    assert [n for n in found if "RightHand" in n] == ["mixamorig:RightHand"] + [f"mixamorig:RightHand{f}{k}" for f in ("Thumb", "Index") for k in (1, 2, 3)]
    # der Zeigefinger ist die Kette neben dem Daumen (quer über die Hand gesehen, hier entlang z)
    document = chunks(data)[0]["nodes"]
    knuckle = lambda name: next(n for n in document if n["name"] == f"mixamorig:LeftHand{name}1")["translation"]  # noqa: E731
    assert knuckle("Index")[2] < knuckle("Middle")[2] < knuckle("Ring")[2] < knuckle("Pinky")[2]
    assert abs(knuckle("Index")[2] - knuckle("Thumb")[2]) < abs(knuckle("Pinky")[2] - knuckle("Thumb")[2])


def test_named_fingers_and_hands_without_chains_stay_as_they_are():
    nodes = skeleton(0, 0)
    chain(nodes, 2, "mixamorig:LeftHandThumb", [0, 0.03, 0], 1)
    nodes[-1]["name"] = "mixamorig:LeftHandThumb1"
    data = glb(nodes)
    same, named = fingers.name_fingers(data)
    assert same is data and named == {"Left": 0, "Right": 0}
    # noch einmal benennen ändert nichts mehr
    first, _ = fingers.name_fingers(glb(skeleton(3, 3)))
    again, named = fingers.name_fingers(first)
    assert again is first and named == {"Left": 0, "Right": 0}
    with pytest.raises(fingers.FingerError):
        fingers.name_fingers(b"glTF\x02\x00\x00\x00kaputt")


def test_an_older_skeleton_is_named_when_it_is_first_read(tmp_path):
    (tmp_path / ".3d-o-mat").write_text("")
    store = Store(folder=str(tmp_path), log=lambda *_: None)
    store.write("jobs", {"id": "20260101-000000-aa01", "kind": "rig", "state": "done", "imageName": "Figur",
                         "created": "2026-01-01T00:00:00", "options": {}})
    store.put("jobs", "20260101-000000-aa01", "model.glb", glb(skeleton(5, 5)))
    config = Config(trellis_model="trellis", assist_model="gemma", defaults={}, cutout="flood")
    app = create_app(config, gate=None, store=store, register=False, log=lambda *_: None)
    with TestClient(app) as client:
        served = client.get("/api/jobs/20260101-000000-aa01/model.glb")
        assert served.status_code == 200 and "mixamorig:RightHandPinky3" in names(served.content)
        job = client.get("/api/jobs/20260101-000000-aa01").json()
        assert job["fingers"] == {"Left": 5, "Right": 5}
        assert "mixamorig:LeftHandThumb1" in names(store.get("jobs", "20260101-000000-aa01", "model.glb"))

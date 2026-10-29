"""Die Proben als glTF-Animationen: angehängt an ein GLB, das sonst bleibt, wie es war."""
import json
import math
import struct

import pytest

from omat3d import animate
from omat3d.animate import AnimationError, add_animations


def minimal_glb() -> bytes:
    """Zwei Knoten (Hüfte, Bein) und ein kleiner Binärteil, wie ein GLB mit Skelett im Kleinen."""
    binary = b"\x01\x02\x03"  # absichtlich nicht auf 4 Byte aufgefüllt
    document = {"asset": {"version": "2.0"}, "nodes": [{"name": "Hips", "children": [1]}, {"name": "LeftUpLeg"}],
                "scenes": [{"nodes": [0]}], "buffers": [{"byteLength": len(binary)}],
                "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 3}]}
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    padded = binary + b"\0"
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(padded), 0x004E4942) + padded
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def test_animations_are_appended_and_the_rest_stays():
    glb = minimal_glb()
    clips = [{"name": "Gehen", "times": [0, 0.5, 1], "tracks": [
        {"node": 1, "path": "rotation", "values": [0, 0, 0, 1, 0.38, 0, 0, 0.92, 0, 0, 0, 1]},
        {"node": 0, "path": "translation", "values": [0, 1, 0, 0, 1.1, 0, 0, 1, 0]}]}]
    out = add_animations(glb, clips)
    document, binary = animate._chunks(out)
    assert struct.unpack_from("<I", out, 8)[0] == len(out) and len(out) % 4 == 0
    assert binary[:3] == b"\x01\x02\x03" and document["nodes"] == animate._chunks(glb)[0]["nodes"]
    animation = document["animations"][0]
    assert animation["name"] == "Gehen" and [c["target"] for c in animation["channels"]] == [
        {"node": 1, "path": "rotation"}, {"node": 0, "path": "translation"}]
    times = document["accessors"][animation["samplers"][0]["input"]]
    assert times["min"] == [0] and times["max"] == [1] and times["count"] == 3
    view = document["bufferViews"][document["accessors"][animation["samplers"][0]["output"]]["bufferView"]]
    assert view["byteOffset"] % 4 == 0 and document["buffers"] == [{"byteLength": len(binary)}]
    assert struct.unpack_from("<4f", binary, view["byteOffset"] + 16) == pytest.approx((0.38, 0, 0, 0.92))


def test_bad_animations_are_refused():
    glb = minimal_glb()
    good = {"name": "Tritt", "times": [0, 1], "tracks": [{"node": 1, "path": "rotation", "values": [0, 0, 0, 1] * 2}]}
    for clips, message in [([], "Proben"), ([{**good, "times": [1, 0]}], "aufsteigen"),
                           ([{**good, "tracks": [{"node": 7, "path": "rotation", "values": [0] * 8}]}], "Knoten"),
                           ([{**good, "tracks": [{"node": 1, "path": "scale", "values": [0] * 6}]}], "Pfad"),
                           ([{**good, "tracks": [{"node": 1, "path": "rotation", "values": [0] * 5}]}], "Werte")]:
        with pytest.raises(AnimationError, match=message):
            add_animations(glb, clips)
    with pytest.raises(AnimationError, match="GLB"):
        add_animations(b"glTF\x02\x00\x00\x00fake", [good])


def test_animated_nodes_lose_their_matrix():
    glb = minimal_glb()
    document, binary = animate._chunks(glb)
    # 90° um Y, Größe 2, verschoben: so schreiben manche Werkzeuge Knoten
    document["nodes"][1]["matrix"] = [0, 0, -2, 0, 0, 2, 0, 0, 2, 0, 0, 0, 1, 2, 3, 1]
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    padded = binary + b"\0" * (-len(binary) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(padded), 0x004E4942) + padded
    glb = struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body
    out, _ = animate._chunks(add_animations(glb, [{"name": "Tritt", "times": [0, 1], "tracks": [
        {"node": 1, "path": "rotation", "values": [0, 0, 0, 1] * 2}]}]))
    node = out["nodes"][1]
    assert "matrix" not in node and node["translation"] == [1, 2, 3] and node["scale"] == pytest.approx([2, 2, 2])
    assert node["rotation"] == pytest.approx([0, math.sqrt(0.5), 0, math.sqrt(0.5)])

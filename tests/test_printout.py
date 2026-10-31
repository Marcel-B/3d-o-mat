"""Die Druckdatei: Haltung einbacken, wasserdicht, Sockel, 3MF und STL. Mit einem kleinen GLB aus zwei Quadern an zwei
Knochen, wie eine Figur mit Skelett im Kleinen."""
import io
import json
import math
import struct
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from omat3d.printout import PrintError, make_print, posed_triangles

CUBE = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], np.float32)
# Zwölf Dreiecke, nach außen gewendet.
CUBE_FACES = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                       [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]], np.uint16)


def figure_glb(open_top=False) -> bytes:
    """Ein Rumpf (0,4 breit, 1 hoch) am Knochen Hips und ein Arm (1 lang, 0,2 dick), der waagrecht zur Seite zeigt,
    am Knochen LeftArm. Mit open_top fehlt dem Rumpf der Deckel."""
    body = CUBE * [0.4, 1, 0.4] + [-0.2, 0, -0.2]
    arm = CUBE * [1, 0.2, 0.2] + [0.2, 0.75, -0.1]
    points = np.concatenate([body, arm]).astype(np.float32)
    faces = np.concatenate([CUBE_FACES if not open_top else CUBE_FACES[[0, 1, 2, 3, 4, 5, 8, 9, 10, 11]],
                            CUBE_FACES + 8]).astype(np.uint16)
    joints = np.array([[0, 0, 0, 0]] * 8 + [[1, 0, 0, 0]] * 8, np.uint8)
    weights = np.array([[1, 0, 0, 0]] * 16, np.float32)
    # Die Knochen: Hips im Ursprung, LeftArm an der Schulter (0,2 | 0,85 | 0).
    inverse = np.stack([np.eye(4), np.eye(4)]).astype(np.float32)
    inverse[1, :3, 3] = [-0.2, -0.85, 0]
    blobs = [points.tobytes(), faces.tobytes(), joints.tobytes(), weights.tobytes(),
             inverse.transpose(0, 2, 1).tobytes()]
    views, binary = [], b""
    for blob in blobs:
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(blob)})
        binary += blob + b"\0" * (-len(blob) % 4)
    document = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0, 2]}],
        "nodes": [{"name": "mixamorig:Hips", "children": [1]},
                  {"name": "mixamorig:LeftArm", "translation": [0.2, 0.85, 0]},
                  {"name": "Figur", "mesh": 0, "skin": 0}],
        "skins": [{"joints": [0, 1], "inverseBindMatrices": 4}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "JOINTS_0": 2, "WEIGHTS_0": 3}, "indices": 1}]}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 16, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": len(faces) * 3, "type": "SCALAR"},
            {"bufferView": 2, "componentType": 5121, "count": 16, "type": "VEC4"},
            {"bufferView": 3, "componentType": 5126, "count": 16, "type": "VEC4"},
            {"bufferView": 4, "componentType": 5126, "count": 2, "type": "MAT4"}],
        "bufferViews": views, "buffers": [{"byteLength": len(binary)}],
    }
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def down(angle=90):
    """Der Arm um die Z-Achse nach unten gedreht (Quaternion x, y, z, w)."""
    half = math.radians(-angle) / 2
    return [0, 0, math.sin(half), math.cos(half)]


def model_points(data: bytes) -> np.ndarray:
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("3D/3dmodel.model"))
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    assert root.get("unit") == "millimeter"
    return np.array([[float(v.get(a)) for a in "xyz"] for v in root.iterfind(".//m:vertex", ns)])


def test_pose_moves_only_the_arm():
    glb = figure_glb()
    rest = posed_triangles(glb).reshape(-1, 3)
    posed = posed_triangles(glb, {"1": down()}).reshape(-1, 3)
    assert rest[:, 0].max() == pytest.approx(1.2)
    # Der Arm hängt jetzt an der Schulter herab: nicht mehr breit, dafür tiefer.
    assert posed[:, 0].max() == pytest.approx(0.3, abs=1e-5) and posed[:, 1].min() == pytest.approx(-0.15, abs=1e-5)
    assert np.allclose(posed[:36], rest[:36])


def test_closed_parts_become_one_body_on_a_base():
    data, info = make_print(figure_glb(), {"1": down(60)}, height=100)
    assert info["method"] == "closed" and info["volumeCm3"] > 0
    points = model_points(data)
    assert points[:, 2].min() == pytest.approx(0, abs=1e-3)
    # Figur 100 mm, dazu der Sockel (3 mm) minus 0,5 mm Einsenken.
    assert points[:, 2].max() == pytest.approx(102.5, abs=0.05)
    assert info["sizeMm"][2] == pytest.approx(102.5, abs=0.05)


def test_open_mesh_is_closed_over_voxels():
    data, info = make_print(figure_glb(open_top=True), height=60, base=False, fmt="stl")
    assert info["method"] == "voxels"
    count = struct.unpack_from("<I", data, 80)[0]
    assert len(data) == 84 + 50 * count == 84 + 50 * info["triangles"]
    record = np.dtype([("normal", "<f4", 3), ("points", "<f4", (3, 3)), ("attribute", "<u2")])
    points = np.frombuffer(data, record, offset=84)["points"].reshape(-1, 3)
    # Wasserdicht: jede Kante gehört zu genau zwei Dreiecken.
    _, inverse = np.unique(np.round(points, 4), axis=0, return_inverse=True)
    faces = inverse.reshape(-1, 3)
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    assert (np.unique(edges, axis=0, return_counts=True)[1] == 2).all()
    assert info["sizeMm"][2] == pytest.approx(60, abs=1.5)


def test_bad_requests_are_refused():
    glb = figure_glb()
    for pose, message in [({"9": [0, 0, 0, 1]}, "Knoten"), ({"x": [0, 0, 0, 1]}, "Knoten"),
                          ({"1": [0, 0, 1]}, "Drehung"), ({"1": [0, 0, 0, 0]}, "leere")]:
        with pytest.raises(PrintError, match=message):
            make_print(glb, pose)
    with pytest.raises(PrintError, match="Höhe"):
        make_print(glb, height=5)
    with pytest.raises(PrintError, match="Format"):
        make_print(glb, fmt="obj")
    with pytest.raises(PrintError, match="GLB"):
        make_print(b"glTF\x02\x00\x00\x00fake")


def test_distance_to_a_triangle():
    from omat3d.printout import _triangle_distance
    a, b, c = np.array([[0, 0, 0]] * 4.0), np.array([[1, 0, 0]] * 4.0), np.array([[0, 1, 0]] * 4.0)
    # Über der Fläche, neben einer Kante, hinter einer Ecke, auf der Fläche.
    points = np.array([[0.2, 0.2, 0.5], [0.5, -0.3, 0], [2, 0, 0], [0.1, 0.1, 0]])
    assert _triangle_distance(points, a, b, c) == pytest.approx([0.5, 0.3, 1, 0])

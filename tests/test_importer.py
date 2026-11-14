"""Eigene Figuren importieren: OBJ mit Material und Textur, glTF mit Dateien daneben, ZIP, GLB mit und ohne
Mixamo-Skelett, und der Weg über die Seite bis zum Skelett von SkinTokens und zur Druckdatei."""
import base64
import io
import json
import time
import zipfile

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from omat3d.animate import chunks, pack
from omat3d.app import create_app
from omat3d.config import Config
from omat3d.gate import Gate
from omat3d.importer import LIMBS, ImportFailed, import_model, without_skin
from omat3d.printout import _accessor
from omat3d.store import Store

from test_app import MARKER, FakeGate
from test_printout import figure_glb

# Ein Würfel als OBJ: Vierecke, eins davon mit negativen Indizes, mit UVs, Normalen und Material.
CUBE_OBJ = b"""# Wuerfel
mtllib wuerfel.mtl
o Wuerfel
v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
v 0 0 1
v 1 0 1
v 1 1 1
v 0 1 1
vt 0 0
vt 1 0
vt 1 1
vt 0 1
usemtl Holz
f 1/1 4/4 3/3 2/2
f 5/1 6/2 7/3 8/4
f 1/1 2/2 6/3 5/4
f 4/1 8/4 7/3 3/2
usemtl Lack
f 1/1 5/2 8/3 4/4
f -7/1 -6/2 -2/3 -3/4
"""
CUBE_MTL = b"""newmtl Holz
Kd 0.5 0.3 0.1
map_Kd -s 1 1 1 textures\\Holz.TGA
newmtl Lack
Kd 0.9 0.1 0.1
d 0.5
"""


def picture(kind="PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (4, 4), (120, 80, 40)).save(out, kind)
    return out.getvalue()


def mixamo_glb() -> bytes:
    """figure_glb mit allen Knochen, die die Bewegungsbibliothek braucht (ohne Gewichte an den neuen)."""
    document, binary = chunks(figure_glb())
    nodes = document["nodes"]
    for name in LIMBS:
        if f"mixamorig:{name}" not in [n.get("name") for n in nodes]:
            nodes.append({"name": f"mixamorig:{name}"})
            nodes[0].setdefault("children", []).append(len(nodes) - 1)
            document["skins"][0]["joints"].append(len(nodes) - 1)
    # Die Bindematrizen bleiben zwei; für den Test reicht, was die Namen sagen.
    return pack(document, binary)


def test_obj_with_material_and_texture_becomes_one_glb():
    glb, info = import_model({"wuerfel.obj": CUBE_OBJ, "wuerfel.mtl": CUBE_MTL, "textures/holz.tga": picture("TGA")})
    assert info == {"format": "obj", "name": "wuerfel", "triangles": 12, "skeleton": None, "notes": []}
    document, binary = chunks(glb)
    primitives = document["meshes"][0]["primitives"]
    assert len(primitives) == 2 and len(document["buffers"]) == 1
    wood, lacquer = (document["materials"][p["material"]] for p in primitives)
    assert wood["pbrMetallicRoughness"]["baseColorTexture"] == {"index": 0}
    assert lacquer["pbrMetallicRoughness"]["baseColorFactor"] == [0.9, 0.1, 0.1, 0.5] and lacquer["alphaMode"] == "BLEND"
    # TGA kennt glTF nicht: eingebettet als PNG.
    assert document["images"][0]["mimeType"] == "image/png"
    view = document["bufferViews"][document["images"][0]["bufferView"]]
    assert binary[view["byteOffset"]:view["byteOffset"] + 8] == b"\x89PNG\r\n\x1a\n"
    points = _accessor(document, binary, primitives[0]["attributes"]["POSITION"])
    assert points.min() == 0 and points.max() == 1
    # Ohne vn im OBJ: Normalen aus den Flächen, Länge 1.
    normals = _accessor(document, binary, primitives[0]["attributes"]["NORMAL"])
    assert np.allclose(np.linalg.norm(normals, axis=1), 1)
    # v von unten (OBJ) wird v von oben (glTF).
    uv = _accessor(document, binary, primitives[0]["attributes"]["TEXCOORD_0"])
    assert sorted(set(uv[:, 1].round(3))) == [0.0, 1.0]


def test_missing_texture_costs_the_texture_not_the_figure():
    glb, info = import_model({"wuerfel.obj": CUBE_OBJ, "wuerfel.mtl": CUBE_MTL})
    assert info["triangles"] == 12 and any("Holz.TGA" in note for note in info["notes"])
    assert "images" not in chunks(glb)[0]
    _, info = import_model({"wuerfel.obj": CUBE_OBJ})
    assert info["notes"] == ["wuerfel.mtl fehlt, die Figur bleibt ohne Farben"]


def test_gltf_with_files_beside_it_and_as_zip():
    document, binary = chunks(figure_glb())
    document["buffers"] = [{"byteLength": len(binary), "uri": "figur.bin"}]
    document["images"] = [{"uri": "haut%20farbe.png"}, {"uri": "data:image/png;base64," + base64.b64encode(picture()).decode()}]
    gltf = json.dumps(document).encode()
    glb, info = import_model({"figur.gltf": gltf, "figur.bin": binary, "haut farbe.png": picture()})
    packed, data = chunks(glb)
    assert info["format"] == "gltf" and info["skeleton"] == "other" and info["triangles"] == 24
    assert "uri" not in packed["buffers"][0] and all("bufferView" in image for image in packed["images"])
    assert np.array_equal(_accessor(packed, data, 0), _accessor(document, binary, 0))

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("Figur/figur.gltf", gltf)
        z.writestr("Figur/figur.bin", binary)
        z.writestr("Figur/Haut Farbe.PNG", picture())
        z.writestr("__MACOSX/Figur/._figur.gltf", b"junk")
    glb_zip, info = import_model({"figur.zip": archive.getvalue()})
    assert info["notes"] == ["Das Skelett hat keine Mixamo-Namen; SkinTokens kann ein neues einsetzen"]
    assert chunks(glb_zip)[0]["images"] == packed["images"]


def test_glb_with_mixamo_skeleton_is_kept_byte_for_byte():
    data = mixamo_glb()
    glb, info = import_model({"Held.glb": data})
    assert glb == data and info["skeleton"] == "mixamo" and info["name"] == "Held"


def test_foreign_skeleton_is_taken_off_for_skintokens():
    document, _ = chunks(without_skin(figure_glb()))
    assert "skins" not in document and "skin" not in document["nodes"][2]
    assert set(document["meshes"][0]["primitives"][0]["attributes"]) == {"POSITION"}
    plain = without_skin(figure_glb())
    assert without_skin(plain) is plain


@pytest.mark.parametrize("files, words", [
    ({"bild.png": picture()}, "Keine GLB"),
    ({"a.glb": figure_glb(), "b.obj": CUBE_OBJ}, "Eine Figur je Import"),
    ({"a.glb": b"glTF kaputt"}, "Kein lesbares GLB"),
    ({"a.obj": b"v 0 0 0\n"}, "keine Flächen"),
    ({"a.obj": b"v 0 0 0\nv 1 0 0\nf 1 2 9\n"}, "nicht gibt"),
    ({"a.gltf": b"{"}, "kein lesbares glTF"),
    ({"a.gltf": json.dumps({"asset": {"version": "2.0"}, "buffers": [{"uri": "a.bin", "byteLength": 4}]}).encode()}, "a.bin fehlt"),
    ({"a.zip": b"PK kaputt"}, "kein lesbares ZIP"),
])
def test_what_does_not_become_a_figure(files, words):
    with pytest.raises(ImportFailed, match=words):
        import_model(files)


def test_compressed_mesh_is_refused():
    document, binary = chunks(figure_glb())
    document["extensionsRequired"] = ["KHR_draco_mesh_compression"]
    with pytest.raises(ImportFailed, match="komprimiert"):
        import_model({"a.glb": pack(document, binary)})


class SkinTokensGate(FakeGate):
    """Der Rig-Dienst bekommt das Netz ohne das fremde Skelett und gibt eins mit Mixamo-Namen zurück."""

    def __call__(self, request):
        if request.url.path == "/v1/rig":
            glb = base64.b64decode(json.loads(request.content)["glb"])
            self.sent = chunks(glb)[0]
            return httpx.Response(200, json={"format": "glb", "data": base64.b64encode(mixamo_glb()).decode(),
                                              "report": {"joints_raw": 22}})
        return super().__call__(request)


@pytest.fixture
def client(tmp_path):
    (tmp_path / MARKER).write_text("")
    fake = SkinTokensGate([])
    config = Config(trellis_model="trellis", assist_model="gemma", defaults={"texture_size": 1024}, cutout="flood")
    store = Store(folder=str(tmp_path), log=lambda *_: None)
    gate = Gate("http://gate", "3d-o-mat", transport=httpx.MockTransport(fake))
    app = create_app(config, gate=gate, store=store, register=False, log=lambda *_: None)
    with TestClient(app) as test_client:
        test_client.fake = fake
        yield test_client


def send(client, *files):
    return client.post("/api/jobs/import", files=[("files", (name, data, "application/octet-stream")) for name, data in files])


def test_imported_obj_is_a_finished_model(client):
    response = send(client, ("wuerfel.obj", CUBE_OBJ), ("wuerfel.mtl", CUBE_MTL), ("Holz.tga", picture("TGA")))
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["kind"] == "import" and job["state"] == "done" and job["imageId"] is None
    assert job["import"] == {"format": "obj", "triangles": 12, "skeleton": None, "notes": []}
    assert [j["id"] for j in client.get("/api/jobs").json()] == [job["id"]]
    download = client.get(f"/api/jobs/{job['id']}/model.glb?download=true")
    assert download.content[:4] == b"glTF" and 'filename="wuerfel-' in download.headers["content-disposition"]
    printed = client.post(f"/api/jobs/{job['id']}/print", json={"height": 40, "format": "stl"})
    assert printed.status_code == 200 and json.loads(printed.headers["x-print-info"])["sizeMm"][2] >= 40
    assert client.post(f"/api/jobs/{job['id']}/skeleton", json={"moves": {"1": [0, 0, 0]}}).status_code == 409


def test_imported_mixamo_figure_goes_straight_to_the_library(client):
    job = send(client, ("Held.glb", mixamo_glb())).json()
    assert job["import"]["skeleton"] == "mixamo" and job["fingers"] == {"Left": 0, "Right": 0}
    assert client.post(f"/api/jobs/{job['id']}/rig", json={}).status_code == 409
    clips = {"clips": [{"name": "Gehen", "times": [0, 1], "tracks": [{"node": 1, "path": "rotation", "values": [0, 0, 0, 1] * 2}]}]}
    animated = client.post(f"/api/jobs/{job['id']}/animated.glb", json=clips)
    assert animated.status_code == 200 and chunks(animated.content)[0]["animations"][0]["name"] == "Gehen"
    assert "-skelett-" in client.get(f"/api/jobs/{job['id']}/model.glb?download=true").headers["content-disposition"]


def test_imported_figure_with_foreign_skeleton_gets_one_from_skintokens(client):
    job = send(client, ("Fremd.glb", figure_glb())).json()
    assert job["import"]["skeleton"] == "other"
    assert client.post(f"/api/jobs/{job['id']}/animated.glb", json={"clips": [{"name": "x", "times": [0, 1], "tracks": []}]}).status_code == 409
    rig = client.post(f"/api/jobs/{job['id']}/rig", json={}).json()
    until = 200
    while rig["state"] not in ("done", "failed") and until:
        client.app.state.jobs.wake.set()
        rig = client.get(f"/api/jobs/{rig['id']}").json()
        until -= 1
        time.sleep(0.05)
    assert rig["state"] == "done", rig
    assert "skins" not in client.fake.sent


def test_bad_import_is_explained(client):
    response = send(client, ("bild.png", picture()))
    assert response.status_code == 422 and "Keine GLB" in response.json()["detail"]

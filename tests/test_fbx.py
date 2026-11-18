"""FBX für Unity und Unreal: der Aufruf von Blender mit einem nachgestellten Blender (tests/fake/blender), die Wege unter
/api, und wenn ein echtes Blender da ist (OMAT3D_TEST_BLENDER oder `blender` im PATH), die Umwandlung selbst: das FBX
wird mit Blender wieder gelesen und auf Knochen, Gewichte, Takes, Länge und Maßstab geprüft."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from omat3d.animate import add_animations
from omat3d.fbx import FbxError, FbxUnavailable, convert, find_blender
from test_app import GLB, make_rig_client, upload, wait_for
from test_printout import figure_glb

FAKE = str(Path(__file__).resolve().parent / "fake" / "blender")
REAL = os.environ.get("OMAT3D_TEST_BLENDER") or shutil.which("blender")


def test_without_blender_fbx_says_so():
    assert find_blender("") is None and find_blender("/gibt/es/nicht") is None
    assert find_blender(FAKE) == FAKE
    with pytest.raises(FbxUnavailable, match="Blender"):
        convert(GLB, "unity", None)
    with pytest.raises(FbxUnavailable, match="starten"):
        convert(GLB, "unity", "/gibt/es/nicht")


def test_blender_is_called_with_the_target():
    data, info = convert(GLB, "unreal", FAKE)
    assert data == b"Kaydara FBX Binary  \x00unreal|" + GLB and info["target"] == "unreal"
    with pytest.raises(FbxError, match="Ziel"):
        convert(GLB, "godot", FAKE)


def test_blender_errors_are_short():
    with pytest.raises(FbxError, match="nicht umwandeln: GLB ohne Netz$"):
        convert(GLB + b"fail", "unity", FAKE)
    with pytest.raises(FbxError, match="nicht umwandeln: Blender 5.2.4"):
        convert(GLB + b"silent", "unity", FAKE)
    with pytest.raises(FbxError, match="nach 1 s"):
        convert(GLB + b"hang", "unity", FAKE, timeout=1)


def test_routes(tmp_path):
    with make_rig_client(tmp_path) as client:
        client.app.state.config.blender = FAKE
        assert client.get("/api/status").json()["fbx"] is True
        model = wait_for(client, client.post("/api/jobs", json={"imageId": upload(client)["id"]}).json()["id"])
        fbx = client.get(f"/api/jobs/{model['id']}/model.fbx?target=unreal")
        assert fbx.status_code == 200 and fbx.content.endswith(b"unreal|" + GLB)
        assert f"-unreal-{model['id']}.fbx" in fbx.headers["content-disposition"]
        assert json.loads(fbx.headers["x-fbx-info"])["bones"] == 2
        assert client.get(f"/api/jobs/{model['id']}/model.fbx?target=maya").status_code == 422
        clips = {"clips": [{"name": "Tritt", "times": [0, 1], "tracks": []}]}
        assert client.post(f"/api/jobs/{model['id']}/animated.fbx", json=clips).status_code == 409
        rig = wait_for(client, client.post(f"/api/jobs/{model['id']}/rig", json={}).json()["id"])
        # Das nachgestellte GLB ist keins, also scheitert schon das Anhängen der Animationen.
        refused = client.post(f"/api/jobs/{rig['id']}/animated.fbx", json=clips)
        assert refused.status_code == 422 and "GLB" in refused.json()["detail"]
        assert client.post(f"/api/jobs/{rig['id']}/animated.fbx?target=x", json=clips).status_code == 422
        client.app.state.config.blender = ""
        assert client.get("/api/status").json()["fbx"] is False
        missing = client.get(f"/api/jobs/{model['id']}/model.fbx")
        assert missing.status_code == 503 and "Blender" in missing.json()["detail"]


CHECK = """
import json, sys, bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=sys.argv[sys.argv.index("--") + 1])
mesh = next(o for o in bpy.context.scene.objects if o.type == "MESH")
arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
groups = {g.index: g.name for g in mesh.vertex_groups}
weighted = {groups[g.group] for v in mesh.data.vertices for g in v.groups if g.weight > 0.5}
json.dump({"bones": sorted(b.name for b in arm.data.bones), "weighted": sorted(weighted),
           "height": max(z) - min(z) if (z := [(mesh.matrix_world @ v.co).z for v in mesh.data.vertices]) else 0,
           "clips": {a.name.split("|")[-1]: list(a.frame_range) for a in bpy.data.actions}},
          open(sys.argv[sys.argv.index("--") + 2], "w"))
"""


@pytest.mark.skipif(not REAL, reason="kein Blender (OMAT3D_TEST_BLENDER)")
@pytest.mark.parametrize("target", ["unity", "unreal"])
def test_real_blender_keeps_skin_and_takes(tmp_path, target):
    # Der Arm hebt sich in einer Sekunde um 90° (30 Bilder je Sekunde, 31 Schlüssel).
    times = [i / 30 for i in range(31)]
    lift = [v for t in times for v in (0.0, 0.0, 0.7071 * t, (1 - 0.5 * t * t) ** 0.5)]
    norm = [(lift[i:i + 4], sum(x * x for x in lift[i:i + 4]) ** 0.5) for i in range(0, len(lift), 4)]
    values = [x / n for q, n in norm for x in q]
    glb = add_animations(figure_glb(), [{"name": "Heben", "times": times,
                                         "tracks": [{"node": 1, "path": "rotation", "values": values}]}])
    data, info = convert(glb, target, REAL)
    assert data.startswith(b"Kaydara FBX Binary")
    assert info["bones"] == 2 and info["clips"] == [{"name": "Heben", "frames": 31}]
    (tmp_path / "figur.fbx").write_bytes(data)
    (tmp_path / "check.py").write_text(CHECK)
    subprocess.run([REAL, "-b", "--factory-startup", "--python-exit-code", "3", "--python", str(tmp_path / "check.py"),
                    "--", str(tmp_path / "figur.fbx"), str(tmp_path / "out.json")], check=True, capture_output=True)
    read = json.loads((tmp_path / "out.json").read_text())
    assert read["bones"] == ["mixamorig:Hips", "mixamorig:LeftArm"]
    assert read["weighted"] == ["mixamorig:Hips", "mixamorig:LeftArm"]
    assert read["clips"] == {"Heben": [1.0, 31.0]}
    # Blender liest die Einheit des FBX mit; zurück kommt in beiden Fällen der eine Meter des GLB.
    assert abs(read["height"] - 1.0) < 1e-3

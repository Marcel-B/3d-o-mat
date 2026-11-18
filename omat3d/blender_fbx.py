"""Runs inside Blender, not in 3d-o-mat: GLB in, FBX out. Started by omat3d/fbx.py as
`blender -b --factory-startup --python-exit-code 3 --python blender_fbx.py -- IN.glb OUT.fbx unity|unreal REPORT.json`.

It imports nothing from omat3d, because Blender runs it with its own Python. Blender is GPL; 3d-o-mat (MIT) only hands it
files in a process of its own, never imports bpy.
"""
import json
import sys
import time

import bpy

# Every clip 3d-o-mat bakes (probes and library) runs at 30 frames per second.
FPS = 30


def main(source: str, target: str, kind: str, report: str) -> None:
    started = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    # Before the import: the glTF importer turns seconds into frames at the scene's rate, and Blender's default of 24
    # would make every clip a fifth too short once the FBX plays at 30.
    scene.render.fps = FPS
    scene.render.fps_base = 1.0
    bpy.ops.import_scene.gltf(filepath=source, bone_heuristic="TEMPERANCE", import_shading="NORMALS")

    armatures = [o for o in scene.objects if o.type == "ARMATURE"]
    meshes = [o for o in scene.objects if o.type == "MESH"]
    if not meshes:
        raise ValueError("GLB ohne Netz")
    # Unreal drops an armature object called "Armature" instead of making it an extra root bone.
    for armature in armatures:
        armature.name = "Armature"
        armature.data.name = "Armature"
    # Embedded images need an extension, or some readers (three.js, older FBX SDKs) cannot tell their type.
    for index, image in enumerate(bpy.data.images):
        extension = ".jpg" if image.file_format == "JPEG" else ".png"
        image.name = f"textur_{index}{extension}"
        image.filepath_raw = "//" + image.name
    actions = [a.name for a in bpy.data.actions]

    bpy.ops.export_scene.fbx(
        filepath=target, use_selection=False, object_types={"ARMATURE", "MESH"},
        # Unity: metres arrive as scale 1. Unreal: centimetres as they are.
        apply_unit_scale=True, apply_scale_options="FBX_SCALE_ALL" if kind == "unity" else "FBX_SCALE_NONE",
        axis_forward="-Z", axis_up="Y", bake_space_transform=False,
        # Only the bones SkinTokens or the import brought, with their Mixamo names, so Unity's Humanoid finds them.
        add_leaf_bones=False, use_armature_deform_only=True, primary_bone_axis="Y", secondary_bone_axis="X",
        armature_nodetype="NULL",
        # Each action becomes a take of its own; the NLA route exported none in the trial.
        bake_anim=bool(actions), bake_anim_use_all_actions=True, bake_anim_use_nla_strips=False,
        bake_anim_force_startend_keying=True, bake_anim_step=1.0, bake_anim_simplify_factor=0.0,
        path_mode="COPY", embed_textures=True, mesh_smooth_type="FACE")

    info = {
        "target": kind, "bones": sum(len(a.data.bones) for a in armatures), "meshes": len(meshes),
        "clips": [{"name": a.name, "frames": int(a.frame_range[1] - a.frame_range[0]) + 1}
                  for a in bpy.data.actions if a.name in actions],
        "fps": FPS, "seconds": round(time.time() - started, 2), "blender": bpy.app.version_string,
    }
    with open(report, "w", encoding="utf-8") as handle:
        json.dump(info, handle)


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(args) != 4 or args[2] not in ("unity", "unreal"):
        print("OMAT3D-FEHLER blender_fbx.py -- IN.glb OUT.fbx unity|unreal REPORT.json", flush=True)
        sys.exit(2)
    try:
        main(*args)
    except Exception as error:
        # fbx.py shows this line instead of Blender's whole output.
        print(f"OMAT3D-FEHLER {error}", flush=True)
        raise

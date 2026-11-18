#!/usr/bin/env python3
"""Stands in for prep/rig_uebertragen.py: VORLAGE.blend SPIELER.glb AUS.blend AUS.fbx NAME. Writes the FBX and the two
textures beside it; a template that contains b"brokenrig" fails."""
import os
import sys
rig, glb, out_blend, out_fbx, name = sys.argv[1:6]
if b"brokenrig" in open(rig, "rb").read():
    raise RuntimeError("Armature fehlt")
open(out_blend, "wb").write(b"BLENDER-rig")
open(out_fbx, "wb").write(b"Kaydara FBX Binary " + name.encode())
for suffix in ("farbe", "normal"):
    open(os.path.join(os.path.dirname(out_fbx), f"{name}-{suffix}.png"), "wb").write(b"\x89PNG " + suffix.encode())

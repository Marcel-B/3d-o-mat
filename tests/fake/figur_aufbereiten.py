#!/usr/bin/env python3
"""Stands in for prep/figur_aufbereiten.py: same arguments, writes small outputs. An input that contains b"fail" is
refused like a .blend without one or two meshes (exit 1 with a message), b"crash" ends with a traceback, b"backside"
fails the check (exit 2, pruefung_bestanden false); otherwise everything passes. The arguments go into the report."""
import argparse
import json
import os
import sys

ap = argparse.ArgumentParser()
for flag in ("--input", "--output", "--glb", "--fbx", "--hoehe", "--koerper", "--kopf", "--koerper-dreiecke",
             "--kopf-dreiecke", "--textur", "--kopf-voxel", "--hautfarbe", "--retusche", "--vorschau", "--bericht"):
    ap.add_argument(flag)
ap.add_argument("--ohne-pruefung", action="store_true")
a = ap.parse_args()
data = open(a.input, "rb").read()
if b"fail" in data:
    sys.exit("Erwarte ein oder zwei Mesh-Objekte oder --koerper/--kopf. Vorhanden: []")
if b"crash" in data:
    raise RuntimeError("bpy ist abgestürzt")
open(a.output, "wb").write(b"BLENDER-v503" + data[12:])
open(a.glb, "wb").write(b"glTF\x02\x00\x00\x00figur")
if a.fbx:
    open(a.fbx, "wb").write(b"Kaydara FBX Binary")
    os.makedirs(os.path.splitext(a.fbx)[0] + "_texturen")
    open(os.path.join(os.path.splitext(a.fbx)[0] + "_texturen", "farbe.png"), "wb").write(b"\x89PNG")
passed = b"backside" not in data
report = {"eingabe": os.path.basename(a.input), "dreiecke_klein_gesamt": 47533, "kopf_modus": "haut+gesicht",
          "argumente": {k: v for k, v in vars(a).items() if v not in (None, False) and k not in ("input", "output", "glb", "bericht", "vorschau", "fbx", "retusche")}}
if a.retusche:
    report["retusche"] = json.load(open(a.retusche))
if not a.ohne_pruefung:
    open(a.vorschau, "wb").write(b"\x89PNG vorschau")
    report.update(pruefung_rueckseiten_pixel=0 if passed else 812, pruefung_bestanden=passed)
json.dump(report, open(a.bericht, "w"))
print(json.dumps(report))
if not passed:
    sys.exit(2)

"""Uebertraegt das Rig einer Teamfigur (Blend mit Armature + gerigtem Netz, z.B. klein65.blend) auf einen anderen
Spieler mit demselben Koerper (GLB aus figur_aufbereiten.py). Der Spieler wird auf den Koerper der Vorlage eingepasst
(Breite unterhalb 1,3 m), die Gewichte kommen von der naechsten Flaeche der Vorlage; Kopfpunkte, die weiter als
1,5 cm von der Vorlage entfernt sind (anderer Kopf, Haare), haengen ganz am Head-Knochen.
Mit NAME heissen Material und Bilder Figur_<name>, <name>-farbe, <name>-normal (so findet Unity sie, siehe
AnimationImport.cs in iron-rush), und die beiden Bilder liegen als PNG neben der FBX.
Aufruf: python rig_uebertragen.py VORLAGE.blend SPIELER.glb AUS.blend AUS.fbx [NAME]"""
import bpy, sys, numpy as np
from mathutils import Matrix
from mathutils.bvhtree import BVHTree
vorlage, glb, aus_blend, aus_fbx = sys.argv[1:5]
name = sys.argv[5] if len(sys.argv) > 5 else None
bpy.ops.wm.open_mainfile(filepath=vorlage, load_ui=False)
sc = bpy.context.scene
arm = [o for o in sc.objects if o.type == 'ARMATURE'][0]
src = [o for o in sc.objects if o.type == 'MESH'][0]
P = 'mixamorig:'
# Vorlage in Ruhelage auswerten
if arm.animation_data: act = arm.animation_data.action; arm.animation_data.action = None
else: act = None
for pb in arm.pose.bones: pb.matrix_basis = Matrix()
bpy.context.view_layer.update()
cs = np.array([src.matrix_world @ v.co for v in src.data.vertices])
bpy.ops.import_scene.gltf(filepath=glb)
dst = [o for o in bpy.context.selected_objects if o.type == 'MESH'][0]
for o in list(bpy.context.selected_objects):
    if o is not dst: bpy.data.objects.remove(o)
dst.parent = None
cd = np.array([dst.matrix_world @ v.co for v in dst.data.vertices])
ws, wd = np.ptp(cs[cs[:, 2] < 1.3, 0]), np.ptp(cd[cd[:, 2] < 1.3, 0])
s = ws / wd
xs = (cs[cs[:, 2] < 1.3, 0].max() + cs[cs[:, 2] < 1.3, 0].min()) / 2
xd = (cd[cd[:, 2] < 1.3, 0].max() + cd[cd[:, 2] < 1.3, 0].min()) / 2
T = Matrix.Translation((xs, 0, 0)) @ Matrix.Scale(s, 4) @ Matrix.Translation((-xd, 0, 0))
dst.matrix_world = T @ dst.matrix_world
bpy.context.view_layer.objects.active = dst
for o in sc.objects: o.select_set(o is dst)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
cd = np.array([v.co[:] for v in dst.data.vertices])
print('Massstab', round(s, 5))
# Gewichte: naechste Flaeche der Vorlage, baryzentrisch
sm = src.data
bvh = BVHTree.FromPolygons([tuple(v) for v in cs], [tuple(p.vertices) for p in sm.polygons])
ng = len(src.vertex_groups)
Ws = np.zeros((len(cs), ng))
for v in sm.vertices:
    for g in v.groups: Ws[v.index, g.group] = g.weight
for g in src.vertex_groups: dst.vertex_groups.new(name=g.name)
head = src.vertex_groups[P + 'Head'].index
neck_z = (arm.matrix_world @ arm.data.bones[P + 'Head'].head_local).z
Wd = np.zeros((len(cd), ng)); fern = 0
from mathutils import Vector
for i, p in enumerate(cd):
    loc, nor, fi, dist = bvh.find_nearest(Vector(p))
    vs = sm.polygons[fi].vertices
    a, b, c = (Vector(cs[j]) for j in vs[:3])
    # baryzentrische Gewichte im Dreieck
    v0, v1, v2 = b - a, c - a, loc - a
    d00, d01, d11, d20, d21 = v0.dot(v0), v0.dot(v1), v1.dot(v1), v2.dot(v0), v2.dot(v1)
    den = d00 * d11 - d01 * d01 or 1e-12
    bv = (d11 * d20 - d01 * d21) / den; bw = (d00 * d21 - d01 * d20) / den; bu = 1 - bv - bw
    w = bu * Ws[vs[0]] + bv * Ws[vs[1]] + bw * Ws[vs[2]]
    if p[2] > neck_z - 0.03 and dist > 0.015:
        w = np.zeros(ng); w[head] = 1; fern += 1
    w = np.clip(w, 0, None); w /= max(w.sum(), 1e-9)
    Wd[i] = w
for g in dst.vertex_groups:
    col = Wd[:, g.index]
    for i in np.where(col > 1e-4)[0]:
        g.add([int(i)], float(col[i]), 'REPLACE')
print('Kopfpunkte fern der Vorlage -> Head', fern, 'von', len(cd))
# Zusammengewachsene Fingerspitzen trennen (Splitter zwischen den Fingern, finger_trennen.py daneben)
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import finger_trennen
finger_trennen.trennen(dst)
bpy.data.objects.remove(src)
dst.name = 'Figur'; dst.parent = arm; dst.matrix_parent_inverse = arm.matrix_world.inverted()
mod = dst.modifiers.new('Armature', 'ARMATURE'); mod.object = arm
if act: arm.animation_data.action = act
if name:
    import os
    # Nur die Bilder im Material des Spielers: die Vorlage bringt eigene mit gleichem Namen mit (Figur_klein_Farbe).
    mine = []
    for m in dst.data.materials:
        if not m:
            continue
        m.name = f'Figur_{name}'
        for node in (m.node_tree.nodes if m.node_tree else []):
            if node.type == 'TEX_IMAGE' and node.image and node.image not in mine:
                mine.append(node.image)
    for img in bpy.data.images:
        if img not in mine and img.name.startswith(name + '-'):
            img.name = 'vorlage-' + img.name
    for img in mine:
        kind = 'farbe' if 'Farbe' in img.name or 'Base' in img.name else ('normal' if 'Normal' in img.name else None)
        if kind is None:
            continue
        img.name = f'{name}-{kind}'
        img.filepath_raw = os.path.join(os.path.dirname(os.path.abspath(aus_fbx)), f'{name}-{kind}.png')
        img.file_format = 'PNG'
        img.save()
bpy.ops.wm.save_as_mainfile(filepath=aus_blend)
bpy.ops.export_scene.fbx(filepath=aus_fbx, add_leaf_bones=False, bake_anim=True, path_mode='COPY',
                         embed_textures=True, armature_nodetype='NULL')

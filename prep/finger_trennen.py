"""Trennt zusammengewachsene Finger einer gerigten Figur (bpy 5.3).

Trellis laesst eng anliegende Finger zusammenwachsen ("Haut zwischen den Fingern"). Mit einem Rig, dessen
Fingergewichte unterhalb der Fingerspalte hart einem Finger gehoeren (Vorlage aus finger_neu.py, z. B. Klein v3),
werden je Hand und Nachbarpaar (Zeige-Mittel, Mittel-Ring, Ring-klein) die Flaechen entfernt, die beide Finger
verbinden und unterhalb der Spalte liegen. Die Oeffnungen werden wieder geschlossen: je Finger eine Seitenwand,
dazu der neue Grund der Spalte. Es kommen keine neuen Punkte dazu, die Gewichte bleiben.

Die neuen Flaechen sind einfarbig: alle Ecken bekommen den hellsten hautfarbenen Texturpunkt der Nachbarschaft
(sonst landen sie oft auf dunklen Naht- oder Handschuhpunkten). Ihre eigene Normale ist die Flaechennormale.
Fingerflaechen, deren Textur quer ueber den Atlas reicht (sichtbar als Streifen), werden ebenso auf Haut gesetzt.

    trennen(mesh_object, armature, log=print) -> Bericht (dict)
"""
import bmesh
import numpy as np
from mathutils import Vector

P = 'mixamorig:'
FINGER = ('Index', 'Middle', 'Ring', 'Pinky')
SPALTE = 0.30          # Anteil der Fingerlaenge unter den Knoecheln, ab dem die Finger getrennt sind (wie finger_neu)


def _farbbild(obj):
    for m in obj.data.materials:
        if m and m.use_nodes:
            for n in m.node_tree.nodes:
                if n.type == 'TEX_IMAGE' and n.image and any(l.to_socket.name == 'Base Color'
                                                            for o in n.outputs for l in o.links):
                    return n.image
    return None


def _ringe(kanten):
    """Randkanten in einfache Ringe zerlegen (auch wenn sich zwei Ringe in einem Punkt beruehren)"""
    nb = {}
    for e in kanten:
        a, b = e.verts
        nb.setdefault(a, set()).add(b)
        nb.setdefault(b, set()).add(a)
    out = []
    while True:
        start = next((v for v in sorted(nb, key=lambda v: -len(nb[v])) if nb[v]), None)
        if start is None:
            break
        path = [start]
        while True:
            cur = path[-1]
            if not nb[cur]:
                path = None
                break
            nxt = nb[cur].pop()
            nb[nxt].discard(cur)
            if nxt in path:
                i = path.index(nxt)
                out.append(path[i:])
                path = path[:i + 1]
                if len(path) == 1:
                    break
            else:
                path.append(nxt)
        if path is None:
            out.append(None)
    return out


def trennen(obj, arm, log=print):
    me = obj.data
    MW = obj.matrix_world
    gi = {g.index: g.name for g in obj.vertex_groups}
    bericht = {}
    bm = bmesh.new()
    bm.from_mesh(me)
    # An UV-Naehten liegen Punkte doppelt (glTF trennt sie); sonst gaelten die Naehte als Raender. Die UVs haengen an
    # den Ecken der Flaechen und bleiben dabei, wie sie sind.
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    uv = bm.loops.layers.uv.active
    dl = bm.verts.layers.deform.active
    neu_lay = bm.faces.layers.int.new('finger_neu_flaeche')
    if dl is None:
        bm.free()
        log('Finger getrennt: keine Gewichte')
        return {'fehler': 'keine Gewichte'}

    bild = _farbbild(obj)
    if bild is not None and bild.size[0] > 0:
        TW, TH = bild.size
        tex = np.array(bild.pixels[:], dtype=np.float32).reshape(TH, TW, -1)[:, :, :3]
    else:
        tex = None

    def farbe(u):
        return tex[min(TH - 1, max(0, int((u.y % 1) * TH))), min(TW - 1, max(0, int((u.x % 1) * TW)))]

    def uv_spanne(f):
        return max((l[uv].uv - f.loops[0][uv].uv).length for l in f.loops)

    def hautpunkt(verts, ausser):
        """Hellster hautfarbener Texturpunkt (rot > gruen > blau) an den Ecken der Nachbarflaechen"""
        beste, wert = None, -1
        for v in verts:
            for l in v.link_loops:
                if l.face in ausser or not l.face.is_valid or l.face[neu_lay] or uv_spanne(l.face) > 0.1:
                    continue
                if tex is None:
                    return l[uv].uv.copy()
                c = farbe(l[uv].uv)
                w = float(c.sum()) + (1.0 if c[0] > c[1] > c[2] else 0.0)
                if w > wert:
                    beste, wert = l[uv].uv.copy(), w
        return beste

    neu_alle = set()

    def neue_flaeche(verts, vorbild, nach):
        try:
            f = bm.faces.new(verts)
        except ValueError:
            return []
        f.material_index = vorbild.material_index
        f.smooth = vorbild.smooth
        f.normal_update()
        if f.normal.dot(nach) < 0:
            f.normal_flip()
        f[neu_lay] = 1
        if uv:
            hp = hautpunkt(f.verts, {f} | neu_alle)
            if hp is not None:
                for l in f.loops:
                    l[uv].uv = hp.copy()
        neu_alle.add(f)
        if len(verts) > 3:
            r = bmesh.ops.triangulate(bm, faces=[f])
            for t in r['faces']:
                t[neu_lay] = 1
            neu_alle.discard(f)
            neu_alle.update(r['faces'])
            return r['faces']
        return [f]

    for side in ('Right', 'Left'):
        lab = {}
        for v in bm.verts:
            s = np.zeros(4)
            for g, w in v[dl].items():
                for k, fn in enumerate(FINGER):
                    if f'{side}Hand{fn}' in gi.get(g, ''):
                        s[k] += w
            if s.sum() > 0.05:
                lab[v] = int(np.argmax(s))
        if not lab:
            bericht[side] = 'keine Finger'
            continue
        zk = float(np.mean([(arm.matrix_world @ arm.data.bones[f'{P}{side}Hand{f}1'].head_local).z for f in FINGER]))
        zmin = min((MW @ v.co).z for v in lab)
        z_spalt = zk - SPALTE * (zk - zmin)
        info = {}
        for k in range(3):
            paar = f'{FINGER[k]}-{FINGER[k + 1]}'
            lab = {v: l for v, l in lab.items() if v.is_valid}      # lose Punkte des vorigen Paars sind geloescht
            weg = [f for f in bm.faces if all(v in lab for v in f.verts)
                   and {lab[v] for v in f.verts} == {k, k + 1} and (MW @ f.calc_center_median()).z < z_spalt]
            if not weg:
                info[paar] = 'schon getrennt'
                continue
            beruehrt = {v for f in weg for v in f.verts}
            nA = max(1, sum(1 for v in lab if lab[v] == k))
            nB = max(1, sum(1 for v in lab if lab[v] == k + 1))
            cA = sum((v.co for v in lab if lab[v] == k), Vector()) / nA
            cB = sum((v.co for v in lab if lab[v] == k + 1), Vector()) / nB
            bmesh.ops.delete(bm, geom=weg, context='FACES_ONLY')
            rand = [e for e in bm.edges if e.is_boundary and e.verts[0] in beruehrt and e.verts[1] in beruehrt]
            gefuellt = fehler = 0
            for ring in _ringe(rand):
                if ring is None or len(ring) < 3:
                    fehler += 1
                    continue
                vorbild = next(f for v in ring for f in v.link_faces)
                labs = [lab.get(v, k) for v in ring]
                if len(set(labs)) == 1:
                    other, own = (cB, cA) if labs[0] == k else (cA, cB)
                    gefuellt += len(neue_flaeche(ring, vorbild, other - own))
                    continue
                n = len(ring)
                s0 = next(i for i in range(n) if labs[i] != labs[i - 1])
                ring = ring[s0:] + ring[:s0]
                labs = labs[s0:] + labs[:s0]
                laeufe = []
                for v, l in zip(ring, labs):
                    if laeufe and laeufe[-1][0] == l:
                        laeufe[-1][1].append(v)
                    else:
                        laeufe.append([l, [v]])
                if len(laeufe) != 2:
                    fehler += 1
                    continue
                (la, A), (lb, B) = laeufe
                for l, kette in ((la, A), (lb, B)):
                    own, other = (cA, cB) if l == k else (cB, cA)
                    if len(kette) >= 3:
                        gefuellt += len(neue_flaeche(kette, vorbild, other - own))
                grund = [A[0], A[-1], B[0], B[-1]]
                grund = [v for i, v in enumerate(grund) if v not in grund[:i]]
                if len(grund) >= 3:
                    gefuellt += len(neue_flaeche(grund, vorbild, Vector((0, 0, -1)) @ MW.to_3x3()))
            lose_k = [e for v in beruehrt if v.is_valid for e in v.link_edges if not e.link_faces]
            if lose_k:
                bmesh.ops.delete(bm, geom=list(set(lose_k)), context='EDGES')
            lose_p = [v for v in beruehrt if v.is_valid and not v.link_faces]
            if lose_p:
                bmesh.ops.delete(bm, geom=lose_p, context='VERTS')
            info[paar] = dict(entfernt=len(weg), neu=gefuellt, nicht_geschlossen=fehler)
        if uv:
            lab = {v: l for v, l in lab.items() if v.is_valid}
            streifen = [f for f in bm.faces if f.is_valid and all(v in lab for v in f.verts) and uv_spanne(f) > 0.1]
            for f in streifen:
                hp = hautpunkt(f.verts, set(streifen))
                if hp is not None:
                    for l in f.loops:
                        l[uv].uv = hp.copy()
            info['streifen_auf_haut'] = len(streifen)
        bericht[side] = dict(spalte_z=round(z_spalt, 3), **info)

    bm.normal_update()
    bm.to_mesh(me)
    bm.free()
    me.update()
    neu = me.attributes.get('finger_neu_flaeche')
    if neu is not None:
        cn = [c.vector.copy() for c in me.corner_normals]
        anzahl = 0
        for p in me.polygons:
            if neu.data[p.index].value:
                anzahl += 1
                for li in p.loop_indices:
                    cn[li] = p.normal.copy()
        me.normals_split_custom_set(cn)
        me.attributes.remove(me.attributes['finger_neu_flaeche'])
        bericht['neue_flaechen'] = anzahl
    log('Finger getrennt: ' + ', '.join(f'{s} {bericht[s]}' for s in ('Right', 'Left') if s in bericht))
    return bericht

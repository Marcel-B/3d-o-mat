#!/usr/bin/env python3
"""
figur_aufbereiten.py - bereitet eine Trellis-Figur (Koerper + aufgesetzter Kopf) fuer das Rigging auf.

Eingabe:  .blend mit zwei Mesh-Objekten (Koerper ohne Kopf, Kopf mit Hals), Z zeigt nach oben.
Ausgabe:  .blend mit drei Collections
            1_Original  die Eingabeobjekte, unveraendert, ausgeblendet
            2_Sauber    volle Aufloesung: Innenhaut entfernt, Oeffnungen geschlossen, Flaechen richtig herum
            3_Klein     verkleinert, neu abgewickelt, Farbe / ORM / Normal Map neu gebacken

Aufruf (Python mit dem pip-Modul bpy):
    python figur_aufbereiten.py --input figur.blend --output figur-fertig.blend

Aufruf (in Blender, dessen Python dann scipy und scikit-image braucht):
    blender -b -P figur_aufbereiten.py -- --input figur.blend --output figur-fertig.blend

Abhaengigkeiten: bpy (oder Blender), numpy, scipy, scikit-image.
"""
import sys, os, json, math, time, argparse, tempfile
import numpy as np
import bpy, bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

try:
    import scipy.sparse as sp
    from scipy.sparse.csgraph import maximum_flow, breadth_first_order
    from scipy.spatial import cKDTree
    from scipy import ndimage
    from skimage import measure
except ImportError as ex:  # pragma: no cover
    sys.exit("Es fehlen scipy oder scikit-image im verwendeten Python: %s" % ex)

T0 = time.time()


def log(*a):
    print("[%5.0fs]" % (time.time() - T0), *a, flush=True)


# ----------------------------------------------------------------------------- Hilfen

def sphere_dirs(n):
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    th = np.pi * (1 + 5 ** 0.5) * i
    return [Vector(x) for x in np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], 1)]


def dup(obj, name):
    o = obj.copy()
    o.data = obj.data.copy()
    o.name = name
    o.data.name = name
    bpy.context.scene.collection.objects.link(o)
    o.hide_viewport = False
    o.hide_render = False
    return o


def activate(o):
    for x in bpy.context.view_layer.objects:
        x.select_set(False)
    o.select_set(True)
    bpy.context.view_layer.objects.active = o


def tri_count(o):
    return int(sum(len(p.vertices) - 2 for p in o.data.polygons))


def base_color_node(mat):
    if not mat or not mat.node_tree:
        return None
    for n in mat.node_tree.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            l = n.inputs['Base Color'].links
            if l and l[0].from_node.type == 'TEX_IMAGE' and l[0].from_node.image:
                return l[0].from_node
    return None


def orm_node(mat):
    if not mat or not mat.node_tree:
        return None
    for n in mat.node_tree.nodes:
        if n.type == 'SEPARATE_COLOR' and n.inputs[0].links and n.inputs[0].links[0].from_node.type == 'TEX_IMAGE':
            return n.inputs[0].links[0].from_node
    return None


def image_pixels(img):
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    return px.reshape(h, w, 4)


def boundary_chains(bm):
    """Offene Raender in Laufrichtung der Flaechen ablaufen. Liefert (Punkte, Kanten, ok)."""
    start = {}
    for f in bm.faces:
        for l in f.loops:
            if l.edge.is_boundary:
                start[l.edge] = l
    used = set()
    chains = []
    for e0, l0 in start.items():
        if e0 in used:
            continue
        verts, edges, l, ok = [], [], l0, True
        for _ in range(200000):
            e = l.edge
            if e in used:
                ok = (e is e0)
                break
            used.add(e)
            verts.append(l.vert)
            edges.append(e)
            v = l.link_loop_next.vert
            l2 = l.link_loop_next
            guard = 0
            while not l2.edge.is_boundary:
                if len(l2.edge.link_faces) != 2:
                    ok = False
                    break
                lr = l2.link_loop_radial_next
                if lr.vert is v:
                    ok = False
                    break
                l2 = lr.link_loop_next
                guard += 1
                if guard > 10000:
                    ok = False
                    break
            if not ok:
                break
            l = l2
        chains.append((verts, edges, ok))
    return chains


def chain_stats(verts, edges):
    P = np.array([v.co[:] for v in verts])
    c = P.mean(0)
    nrm = np.zeros(3)
    for i in range(len(P)):
        nrm += np.cross(P[i] - c, P[(i + 1) % len(P)] - c)
    n = -nrm / max(np.linalg.norm(nrm), 1e-20)   # Fuellflaeche laeuft gegen die Randrichtung
    per = sum(e.calc_length() for e in edges)
    diam = np.linalg.norm(P.max(0) - P.min(0))
    flat = np.sqrt((((P - c) @ n) ** 2).mean()) / max(diam, 1e-12)
    return dict(c=c, n=n, per=per, flat=flat)


def exposure(bvh, c, n, dirs, eps):
    nn = Vector(n)
    p = Vector(c) + nn * eps
    k = e = 0
    for d in dirs:
        if d.dot(nn) > 0.2:
            k += 1
            if bvh.ray_cast(p, d)[0] is None:
                e += 1
    return e / max(k, 1)


# ----------------------------------------------------------------------------- Koerper

def clean_body(src, rep):
    """Verschweissen, Innenhaut per Minimalschnitt abtrennen, Oeffnungen schliessen."""
    o = dup(src, 'Koerper_sauber')
    me = o.data
    bm = bmesh.new()
    bm.from_mesh(me)
    OB = bm.edges.layers.int.new('orig_open')
    PT = bm.faces.layers.int.new('patch')
    uvl = bm.loops.layers.uv.active
    co = np.array([v.co[:] for v in bm.verts])
    D = float((co.max(0) - co.min(0)).max())
    rep['koerper_dreiecke_original'] = int(sum(len(f.verts) - 2 for f in bm.faces))
    nv0 = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=5e-6 * D)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.normal_update()
    bm.faces.ensure_lookup_table()
    bm.faces.index_update()
    for e in bm.edges:
        e[OB] = 1 if e.is_boundary else 0
    nf = len(bm.faces)
    rep['koerper_punkte_original'] = nv0
    rep['koerper_punkte_verschweisst'] = len(bm.verts)
    log('Koerper: %d Punkte -> %d verschweisst, %d Dreiecke' % (nv0, len(bm.verts), nf))

    # 1) Sichtbarkeit je Dreieck und Partner auf der anderen Wandseite
    bvh = BVHTree.FromBMesh(bm)
    rc = bvh.ray_cast
    D96 = sphere_dirs(96)
    eps = 1.4e-4 * D
    esc = np.zeros(nf, np.float32)
    partner = np.full(nf, -1, np.int32)
    pdist = np.full(nf, -1, np.float32)
    for f in bm.faces:
        nrm = f.normal
        c = f.calc_center_median()
        p = c + nrm * eps
        e = k = 0
        for d in D96:
            if d.dot(nrm) > 0.15:
                k += 1
                if rc(p, d)[0] is None:
                    e += 1
        esc[f.index] = e / max(k, 1)
        loc, hn, idx, dist = rc(c - nrm * eps, -nrm, 0.018 * D)
        if loc is not None:
            partner[f.index] = idx
            pdist[f.index] = dist
    log('Koerper: Sichtbarkeit berechnet')

    # 2) Minimalschnitt zwischen sicher aussen und sicher innen
    hp = partner >= 0
    cand = np.where((esc == 0) & hp & (pdist < 0.009 * D))[0]
    cand = cand[esc[partner[cand]] >= 0.5]
    D400 = sphere_dirs(400)
    vis = np.zeros(len(cand), bool)
    for j, fi in enumerate(cand):
        f = bm.faces[fi]
        nrm = f.normal
        p = f.calc_center_median() + nrm * eps
        for d in D400:
            if d.dot(nrm) > 0.05 and rc(p, d)[0] is None:
                vis[j] = True
                break
    inner = cand[~vis]
    outer = np.where(esc >= 0.6)[0]
    side = np.ones(nf, np.int8)
    rep['koerper_innenhaut_gefunden'] = bool(len(inner) > 0.02 * nf)
    if rep['koerper_innenhaut_gefunden']:
        I, J, C = [], [], []
        for e in bm.edges:
            lf = e.link_faces
            if len(lf) < 2:
                continue
            c = max(int(e.calc_length() / D * 4e6), 1)
            for a in range(len(lf)):
                for b in range(a + 1, len(lf)):
                    I += [lf[a].index, lf[b].index]
                    J += [lf[b].index, lf[a].index]
                    C += [c, c]
        s, t, INF = nf, nf + 1, 10 ** 9
        I += [s] * len(outer)
        J += list(outer)
        C += [INF] * len(outer)
        I += list(inner)
        J += [t] * len(inner)
        C += [INF] * len(inner)
        G = sp.csr_matrix((np.array(C, np.int32), (np.array(I), np.array(J))), shape=(nf + 2, nf + 2))
        r = maximum_flow(G, s, t)
        R = (G - r.flow)
        R.data = np.maximum(R.data, 0)
        R.eliminate_zeros()
        order = breadth_first_order(R, s, directed=True, return_predecessors=False)
        side[:] = -1
        side[order[order < nf]] = 1
        log('Koerper: Innenhaut abgetrennt (%d innen, %d aussen)' % ((side < 0).sum(), (side > 0).sum()))
    else:
        log('Koerper: keine Innenhaut gefunden, ueberspringe das Abtrennen')

    bmesh.ops.delete(bm, geom=[f for f in bm.faces if side[f.index] < 0], context='FACES')
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
    bm.normal_update()

    # 3) Oeffnungen schliessen; grosse Oeffnungen, die schon in der Eingabe offen waren (Hals), bleiben offen
    D200 = sphere_dirs(200)
    closed, kept_open, failed, pid = [], [], 0, 0
    for verts, edges, ok in boundary_chains(bm):
        if len(verts) < 3:
            continue
        st = chain_stats(verts, edges)
        orig = sum(e[OB] for e in edges) / len(edges)
        ex = exposure(bvh, st['c'], st['n'], D200, 4.5e-4 * D)
        info = dict(mitte=[round(float(x), 4) for x in st['c']], umfang=round(float(st['per']), 4), sichtbar=round(ex, 2))
        if orig >= 0.5 and st['per'] > 0.1 * D:
            kept_open.append(dict(info, grund='war in der Eingabe offen (z.B. Hals)'))
            continue
        if ex <= 0.05 and st['flat'] > 0.15:
            kept_open.append(dict(info, grund='verdeckt und nicht eben'))
            continue
        newf = []
        if ok and len(set(verts)) == len(verts):
            try:
                newf = [bm.faces.new(list(reversed(verts)))]
            except Exception:
                newf = []
        if not newf:
            newf = bmesh.ops.holes_fill(bm, edges=edges, sides=0)['faces']
        if not newf:
            failed += 1
            continue
        newf = bmesh.ops.triangulate(bm, faces=newf, quad_method='BEAUTY', ngon_method='BEAUTY')['faces']
        pid += 1
        for f in newf:
            f[PT] = pid
            f.smooth = True
        if st['per'] > 0.02 * D:
            closed.append(info)
    for _ in range(2):
        pf = [f for f in bm.faces if f[PT] > 0]
        ie = [e for e in {e for f in pf for e in f.edges} if all(g[PT] > 0 for g in e.link_faces) and e.calc_length() > 0.0055 * D]
        if not ie:
            break
        bmesh.ops.subdivide_edges(bm, edges=ie, cuts=1, use_grid_fill=False)
        pf = [f for f in bm.faces if f[PT] > 0]
        bmesh.ops.triangulate(bm, faces=[f for f in pf if len(f.verts) > 3], quad_method='BEAUTY', ngon_method='BEAUTY')
        pf = [f for f in bm.faces if f[PT] > 0]
        iv = [v for v in {v for f in pf for v in f.verts} if all(g[PT] > 0 for g in v.link_faces)]
        for _ in range(10):
            bmesh.ops.smooth_vert(bm, verts=iv, factor=0.5, use_axis_x=True, use_axis_y=True, use_axis_z=True)
    pf = [f for f in bm.faces if f[PT] > 0]
    byp = {}
    for f in pf:
        f.material_index = 1
        byp.setdefault(f[PT], []).append(f)
    if uvl is not None:
        for fs in byp.values():   # Flicken bekommen die Farbe vom naechsten Randpunkt
            rim = {}
            for f in fs:
                for v in f.verts:
                    if v in rim:
                        continue
                    for g in v.link_faces:
                        if g[PT] == 0:
                            rim[v] = np.mean([l[uvl].uv[:] for l in g.loops], 0)
                            break
            if not rim:
                continue
            rp = np.array([v.co[:] for v in rim])
            ru = np.array(list(rim.values()))
            for f in fs:
                c = np.array(f.calc_center_median()[:])
                j = int(np.argmin(((rp - c) ** 2).sum(1)))
                for l in f.loops:
                    l[uvl].uv = ru[j]
    bm.normal_update()
    rep.update(koerper_dreiecke_sauber=len(bm.faces), koerper_oeffnungen_geschlossen=pid, koerper_oeffnungen_gross=closed,
               koerper_offen_gelassen=kept_open, koerper_fuellen_fehlgeschlagen=failed,
               koerper_offene_kanten=sum(1 for e in bm.edges if e.is_boundary),
               koerper_nicht_mannigfaltig=sum(1 for e in bm.edges if len(e.link_faces) > 2))
    bm.to_mesh(me)
    me.validate()
    me.update()
    bm.free()
    for a in ('orig_open', 'patch'):
        if a in me.attributes:
            me.attributes.remove(me.attributes[a])
    if me.materials and me.materials[0]:
        m0 = me.materials[0].copy()
        m0.name = 'Koerper_sauber'
        me.materials[0] = m0
        m1 = m0.copy()
        m1.name = 'Flicken'
        me.materials.append(m1)
    log('Koerper: %d Oeffnungen geschlossen, %d offen gelassen, %d Dreiecke' % (pid, len(kept_open), rep['koerper_dreiecke_sauber']))
    return o


def retouch(o, regions, rep):
    """Dunkle Stellen in angegebenen Quadern (Objektkoordinaten) in einer Kopie der Farbtextur uebermalen."""
    node = base_color_node(o.material_slots[0].material) if o.material_slots else None
    if node is None:
        log('Retusche: keine Farbtextur gefunden, uebersprungen')
        return
    img0 = node.image
    W, H = img0.size
    px = image_pixels(img0)
    out = px.copy()
    bm = bmesh.new()
    bm.from_mesh(o.data)
    uvl = bm.loops.layers.uv.active
    total = 0
    for r in regions:
        lo, hi = np.array(r['lo'], float), np.array(r['hi'], float)
        thr = float(r.get('dunkel_unter', 0.42))
        coloured = bool(r.get('nur_farbig', True))
        tx, ty, tp = [], [], []
        for f in bm.faces:
            c = np.array(f.calc_center_median()[:])
            if (c < lo).any() or (c > hi).any():
                continue
            uv = np.array([l[uvl].uv[:] for l in f.loops]) * [W, H]
            P = np.array([v.co[:] for v in f.verts])
            if np.abs(uv.max(0) - uv.min(0)).max() < 1e-6:
                continue
            x0, y0 = np.floor(uv.min(0) - 2).astype(int)
            x1, y1 = np.ceil(uv.max(0) + 2).astype(int)
            gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            den = (uv[1, 1] - uv[2, 1]) * (uv[0, 0] - uv[2, 0]) + (uv[2, 0] - uv[1, 0]) * (uv[0, 1] - uv[2, 1])
            if abs(den) < 1e-9:
                continue
            w0 = ((uv[1, 1] - uv[2, 1]) * (gx - uv[2, 0]) + (uv[2, 0] - uv[1, 0]) * (gy - uv[2, 1])) / den
            w1 = ((uv[2, 1] - uv[0, 1]) * (gx - uv[2, 0]) + (uv[0, 0] - uv[2, 0]) * (gy - uv[2, 1])) / den
            w2 = 1 - w0 - w1
            A2 = abs(den)
            el = [np.linalg.norm(uv[1] - uv[2]), np.linalg.norm(uv[2] - uv[0]), np.linalg.norm(uv[0] - uv[1])]
            ins = (w0 * A2 / el[0] >= -1.5) & (w1 * A2 / el[1] >= -1.5) & (w2 * A2 / el[2] >= -1.5)
            if not ins.any():
                continue
            tx.append(gx[ins].astype(int))
            ty.append(gy[ins].astype(int))
            tp.append(w0[ins][:, None] * P[0] + w1[ins][:, None] * P[1] + w2[ins][:, None] * P[2])
        if not tx:
            continue
        tx = np.concatenate(tx) % W
        ty = np.concatenate(ty) % H
        tp = np.concatenate(tp)
        _, ui = np.unique(ty.astype(np.int64) * W + tx, return_index=True)
        tx, ty, tp = tx[ui], ty[ui], tp[ui]
        col = px[ty, tx, :3]
        val = col.max(1)
        sat = val - col.min(1)
        reg = np.zeros((H, W), bool)
        reg[ty, tx] = True
        dark = np.zeros((H, W), bool)
        dark[ty, tx] = val < thr
        dark = ndimage.binary_dilation(dark, iterations=3) & reg
        isd = dark[ty, tx]
        good = (~isd) & (val > thr + 0.12)
        if coloured:
            good &= sat > 0.3
        if isd.sum() == 0 or good.sum() < 50:
            continue
        k = min(24, int(good.sum()))
        _, idx = cKDTree(tp[good]).query(tp[isd], k=k)
        rng = np.random.default_rng(1)
        pick = idx[np.arange(len(idx)), rng.integers(0, k, len(idx))]
        out[ty[isd], tx[isd], :3] = 0.6 * col[good][idx].mean(1) + 0.4 * col[good][pick]
        total += int(isd.sum())
    bm.free()
    img = img0.copy()
    img.name = 'Koerper_Farbe_retuschiert'
    img.pixels.foreach_set(out.ravel())
    img.update()
    img.pack()
    for sl in o.material_slots:
        for nd in sl.material.node_tree.nodes:
            if nd.type == 'TEX_IMAGE' and nd.image == img0:
                nd.image = img
    rep['retusche_texel'] = total
    log('Retusche: %d Texel uebermalt' % total)


def decimate_body(sauber, target, rep):
    o = dup(sauber, 'Koerper_klein')
    me = o.data
    activate(o)
    n = tri_count(o)
    if n > target:
        m = o.modifiers.new('d', 'DECIMATE')
        m.ratio = target / n
        bpy.ops.object.modifier_apply(modifier='d')
    if 'custom_normal' in me.attributes:
        me.attributes.remove(me.attributes['custom_normal'])
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    for f in bm.faces:
        f.smooth = True
    bm.to_mesh(me)
    me.validate()
    me.update()
    bm.free()
    rep['koerper_dreiecke_klein'] = tri_count(o)
    log('Koerper verkleinert: %d Dreiecke' % rep['koerper_dreiecke_klein'])
    return o


# ----------------------------------------------------------------------------- Kopf

def clean_head(src, rep, skin_rgb=None, skin_tol=0.18):
    """Nach innen gedrehte Flaechen umdrehen; liefert das Objekt und Daten je Dreieck fuer spaeter."""
    o = dup(src, 'Kopf_sauber')
    me = o.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.normal_update()
    bm.faces.ensure_lookup_table()
    bm.faces.index_update()
    nf = len(bm.faces)
    co = np.array([v.co[:] for v in bm.verts])
    D = float((co.max(0) - co.min(0)).max())
    bvh = BVHTree.FromBMesh(bm)
    rc = bvh.ray_cast
    dirs = sphere_dirs(240)
    eps = 4.3e-4 * D
    eF = np.zeros(nf, np.float32)
    eB = np.zeros(nf, np.float32)
    for f in bm.faces:
        nrm = f.normal
        c = f.calc_center_median()
        pf, pb = c + nrm * eps, c - nrm * eps
        e = k = eb = kb = 0
        for d in dirs:
            dn = d.dot(nrm)
            if dn > 0.1:
                k += 1
                if rc(pf, d)[0] is None:
                    e += 1
            elif dn < -0.1:
                kb += 1
                if rc(pb, d)[0] is None:
                    eb += 1
        eF[f.index] = e / max(k, 1)
        eB[f.index] = eb / max(kb, 1)
    flip = eB > eF + 0.2
    cen = np.array([f.calc_center_median()[:] for f in bm.faces])
    uvl = bm.loops.layers.uv.active
    node = base_color_node(o.material_slots[0].material) if o.material_slots else None
    skin = np.zeros(nf, bool)
    if node is not None and uvl is not None:
        px = image_pixels(node.image)
        H, W = px.shape[:2]
        uvc = np.array([np.mean([l[uvl].uv[:] for l in f.loops], 0) for f in bm.faces])
        col = px[np.clip((uvc[:, 1] * H).astype(int), 0, H - 1), np.clip((uvc[:, 0] * W).astype(int), 0, W - 1), :3]
        if skin_rgb is not None:
            skin = np.linalg.norm(col - np.array(skin_rgb)[None], axis=1) < skin_tol
        else:
            skin = (col.mean(1) > 0.36) & (col[:, 0] > col[:, 1]) & (col[:, 1] > col[:, 2] * 0.95)
    for f in bm.faces:
        if flip[f.index]:
            f.normal_flip()
    bm.normal_update()
    T = np.array([[v.co[:] for v in f.verts] for f in bm.faces])
    cap = None
    big = [c for c in boundary_chains(bm) if len(c[0]) > 60]
    if big:   # unterste grosse Oeffnung (Halsende) fuer den Volumenaufbau verschliessen
        zlo, zhi = co[:, 2].min(), co[:, 2].max()
        low = min(big, key=lambda c: np.mean([v.co.z for v in c[0]]))
        P = np.array([v.co[:] for v in low[0]])
        if P[:, 2].mean() < zlo + 0.3 * (zhi - zlo):
            cap = np.stack([P, np.roll(P, -1, 0), np.repeat(P.mean(0)[None], len(P), 0)], 1)
    bm.to_mesh(me)
    me.update()
    bm.free()
    if me.materials and me.materials[0]:
        m0 = me.materials[0].copy()
        m0.name = 'Kopf_sauber'
        me.materials[0] = m0
    vis = (eF > 0.02) | (eB > 0.02)
    rep.update(kopf_dreiecke_original=nf, kopf_flaechen_umgedreht=int(flip.sum()), kopf_unsichtbar_anteil=round(float(1 - vis.mean()), 2))
    log('Kopf: %d Dreiecke, %d umgedreht, %d%% von aussen unsichtbar' % (nf, flip.sum(), 100 * (1 - vis.mean())))
    return o, dict(T=T, cap=cap, D=D, cen=cen, vis=vis, skin=skin & vis)


def solid_surface(T, cap, h, K):
    """Dreiecke in ein Voxelgitter eintragen, Hohlraum fuellen, Aussenflaeche herausziehen."""
    if cap is not None:
        T = np.concatenate([T, cap])
    lo = T.reshape(-1, 3).min(0) - h * (K + 4)
    hi = T.reshape(-1, 3).max(0) + h * (K + 4)
    dims = np.ceil((hi - lo) / h).astype(int)
    surf = np.zeros(dims, bool)
    e1, e2 = T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
    L = np.maximum(np.maximum(np.linalg.norm(e1, axis=1), np.linalg.norm(e2, axis=1)), np.linalg.norm(T[:, 2] - T[:, 1], axis=1))
    ns = np.clip(np.ceil(L / (0.45 * h)).astype(int), 1, 600)
    for n in np.unique(ns):
        idx = np.where(ns == n)[0]
        i, j = np.meshgrid(np.arange(n + 1), np.arange(n + 1))
        m = (i + j) <= n
        u, v = (i[m] / n)[None, :, None], (j[m] / n)[None, :, None]
        step = max(1, 4000000 // int(m.sum()))
        for s in range(0, len(idx), step):
            ii = idx[s:s + step]
            pts = (T[ii, 0][:, None, :] + u * e1[ii][:, None, :] + v * e2[ii][:, None, :]).reshape(-1, 3)
            q = ((pts - lo) / h).astype(int)
            surf[q[:, 0], q[:, 1], q[:, 2]] = True
    st = ndimage.generate_binary_structure(3, 1)
    dil = ndimage.binary_dilation(surf, st, iterations=K)
    solid = ndimage.binary_erosion(ndimage.binary_fill_holes(dil), st, iterations=K) | surf
    fill_ratio = float(solid.sum()) / max(float(dil.sum()), 1.0)
    lab, nl = ndimage.label(solid)
    if nl > 1:
        sizes = ndimage.sum(solid, lab, range(1, nl + 1))
        solid = lab == (1 + int(np.argmax(sizes)))
    sdf = ndimage.distance_transform_edt(~solid) - ndimage.distance_transform_edt(solid)
    sdf = ndimage.gaussian_filter(sdf, 0.5)
    V, F, _, _ = measure.marching_cubes(sdf, level=-0.5, spacing=(h, h, h))
    V = V + lo + h * 0.5
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    if (a * np.cross(b, c)).sum() < 0:
        F = F[:, ::-1]
    return V, F, fill_ratio, int(dims.prod())


def decimate_group(o, value, target):
    """Nur die Dreiecke mit prot == value verkleinern, in zwei Schritten auf den Zielwert."""
    me = o.data
    for attempt in range(3):
        pr = np.zeros(len(me.polygons), np.int32)
        me.attributes['prot'].data.foreach_get('value', pr)
        mask = pr == value
        n = int(mask.sum())
        if n <= target * 1.02:
            break
        ratio = target / n
        if attempt == 0:
            ratio = min(1.0, ratio * 1.15)
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='FACE')
        bpy.ops.mesh.select_all(action='DESELECT')
        bpy.ops.object.mode_set(mode='OBJECT')
        me.polygons.foreach_set('select', mask)
        me.update()
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.decimate(ratio=ratio)
        bpy.ops.object.mode_set(mode='OBJECT')


def build_small_head(src, sauber, hd, target, voxel_div, rep):
    D = hd['D']
    h = D / voxel_div
    V, F, fill_ratio, nvox = solid_surface(hd['T'], hd['cap'], h, 4)
    rep.update(kopf_voxel=nvox, kopf_volumen_gefuellt=bool(fill_ratio > 1.3))
    if fill_ratio <= 1.3:
        log('WARNUNG Kopf: das Volumen liess sich nicht fuellen (grosse Luecke in der Aussenhaut?); innen bleiben Flaechen stehen')
    me = bpy.data.meshes.new('Kopf_klein')
    me.from_pydata(V.tolist(), [], F.tolist())
    me.update()
    o = bpy.data.objects.new('Kopf_klein', me)
    o.matrix_world = src.matrix_world.copy()
    bpy.context.scene.collection.objects.link(o)
    activate(o)
    log('Kopf: geschlossene Flaeche mit %d Dreiecken aufgebaut' % len(F))
    if len(me.polygons) > 300000:
        m = o.modifiers.new('d', 'DECIMATE')
        m.ratio = 300000 / len(me.polygons)
        bpy.ops.object.modifier_apply(modifier='d')
    nf = len(me.polygons)
    cen = np.zeros(nf * 3)
    me.polygons.foreach_get('center', cen)
    cen = cen.reshape(-1, 3)
    nor = np.zeros(nf * 3)
    me.polygons.foreach_get('normal', nor)
    nor = nor.reshape(-1, 3)
    area = np.zeros(nf)
    me.polygons.foreach_get('area', area)
    prot = np.zeros(nf, bool)
    vis = hd['vis']
    mode = 'gleichmaessig'
    if vis.sum() > 0 and hd['skin'].sum() > 0:
        dist, idx = cKDTree(hd['cen'][vis]).query(cen)
        skin = hd['skin'][vis][idx] & (dist < 0.017 * D)
        share = area[skin].sum() / area.sum()
        if 0.05 < share < 0.8:
            # Hoehe im Kopf (lokale Achse zur Welt-Z): der lange Hals unter dem Kinn steckt spaeter im Kragen und
            # soll das Budget des geschuetzten Teils nicht aufbrauchen.
            up = np.array(src.matrix_world.inverted().to_3x3() @ Vector((0, 0, 1)))
            up /= np.linalg.norm(up)
            hz = cen @ up
            face_band = hz > hz.min() + 0.3 * (hz.max() - hz.min())
            prot = skin & face_band
            mode = 'Haut geschuetzt'
            s = (area[skin, None] * nor[skin]).sum(0)
            if np.linalg.norm(s) / area[skin].sum() > 0.1:   # Blickrichtung des Gesichts aus den Hautflaechen
                f = s / np.linalg.norm(s)
                rep['kopf_blickrichtung'] = 'aus der Haut'
            else:
                # Haut rundum (langer Hals, kurze Haare, Kopfhaut zwischen Straehnen) hebt sich auf. Die Eingabe ist
                # wie die Trellis-Figur ausgerichtet: das Gesicht schaut in der Welt nach -Y.
                f = np.array(src.matrix_world.inverted().to_3x3() @ Vector((0, -1, 0)))
                f /= np.linalg.norm(f)
                rep['kopf_blickrichtung'] = 'nach vorn (-Y)'
            front = skin & (nor @ f > 0.5) & face_band
            if area[front].sum() > 0:
                c = (area[front, None] * cen[front]).sum(0) / area[front].sum()
                r = math.sqrt(area[front].sum() / math.pi)
                d = cen - c
                t = d @ f
                rad = np.linalg.norm(d - t[:, None] * f[None], axis=1)
                prot |= (rad < 1.15 * r) & (t > -0.35 * r) & (nor @ f > -0.1)
                mode = 'Haut und Gesichtsbereich geschuetzt'
            lv = np.zeros(nf * 3, np.int32)
            me.polygons.foreach_get('vertices', lv)
            lv = lv.reshape(-1, 3)
            for _ in range(2):
                vm = np.zeros(len(me.vertices), bool)
                vm[lv[prot].ravel()] = True
                prot = vm[lv].any(1)
        rep['kopf_hautanteil'] = round(float(share), 2)
    rep['kopf_modus'] = mode
    if mode == 'gleichmaessig':
        log('WARNUNG Kopf: keine brauchbare Hautflaeche erkannt, verkleinere gleichmaessig')
    P = me.attributes.new('prot', 'INT', 'FACE')
    P.data.foreach_set('value', prot.astype(np.int32))
    if prot.any():
        decimate_group(o, 0, int(target * 0.45))
        decimate_group(o, 1, int(target * 0.55))
    else:
        decimate_group(o, 0, target)
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.normal_update()
    bm.faces.ensure_lookup_table()
    # einzelne nach innen gekippte Dreiecke umdrehen
    bvh = BVHTree.FromBMesh(bm)
    rc = bvh.ray_cast
    dirs = sphere_dirs(240)
    eps = 4.3e-4 * D
    nflip = 0
    for f in bm.faces:
        nrm = f.normal
        c = f.calc_center_median()
        pf, pb = c + nrm * eps, c - nrm * eps
        e = k = eb = kb = 0
        for d in dirs:
            dn = d.dot(nrm)
            if dn > 0.1:
                k += 1
                e += rc(pf, d)[0] is None
            elif dn < -0.1:
                kb += 1
                eb += rc(pb, d)[0] is None
        if eb / max(kb, 1) > 0.02 and e / max(k, 1) <= 0.02:
            f.normal_flip()
            nflip += 1
    bm.normal_update()
    L = bm.faces.layers.int.get('prot')
    el = [max(e.calc_length() for e in f.edges) for f in bm.faces if f[L] > 0]
    for f in bm.faces:
        f.smooth = True
    rep.update(kopf_dreiecke_klein=len(bm.faces), kopf_dreiecke_gesicht=len(el),
               kopf_gesicht_laengste_kante_anteil=round(float(max(el) / D), 3) if el else None,
               kopf_offene_kanten=sum(1 for e in bm.edges if e.is_boundary),
               kopf_nicht_mannigfaltig=sum(1 for e in bm.edges if len(e.link_faces) > 2), kopf_klein_umgedreht=nflip)
    bm.to_mesh(me)
    me.validate()
    me.update()
    bm.free()
    me.attributes.remove(me.attributes['prot'])
    log('Kopf verkleinert: %d Dreiecke (%s, davon %d im geschuetzten Bereich)' % (rep['kopf_dreiecke_klein'], mode, len(el)))
    return o


# ----------------------------------------------------------------------------- Backen

def bake(low, high, size, prefix, extr_rel, maxd_rel):
    sc = bpy.context.scene
    me = low.data
    bb = np.array([low.matrix_world @ Vector(c) for c in low.bound_box])
    Dw = float((bb.max(0) - bb.min(0)).max())
    activate(low)
    while me.uv_layers:
        me.uv_layers.remove(me.uv_layers[0])
    me.uv_layers.new(name='UVMap')
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.0015, area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    hm = [s.material for s in high.material_slots if s.material]
    has_col = all(base_color_node(m) is not None for m in hm) and bool(hm)
    has_orm = all(orm_node(m) is not None for m in hm) and bool(hm)
    lm = bpy.data.materials.new(prefix)
    lm.use_nodes = True
    nt = lm.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    pr = nt.nodes.new('ShaderNodeBsdfPrincipled')
    nt.links.new(pr.outputs['BSDF'], out.inputs['Surface'])

    def tex(name, cs):
        im = bpy.data.images.new(prefix + '_' + name, size, size, alpha=False)
        im.colorspace_settings.name = cs
        n = nt.nodes.new('ShaderNodeTexImage')
        n.image = im
        n.name = name
        return n
    nc = tex('Farbe', 'sRGB')
    nt.links.new(nc.outputs['Color'], pr.inputs['Base Color'])
    no = None
    if has_orm:
        no = tex('ORM', 'Non-Color')
        sep = nt.nodes.new('ShaderNodeSeparateColor')
        nt.links.new(no.outputs['Color'], sep.inputs[0])
        nt.links.new(sep.outputs['Green'], pr.inputs['Roughness'])
        nt.links.new(sep.outputs['Blue'], pr.inputs['Metallic'])
    nn = tex('Normal', 'Non-Color')
    nmap = nt.nodes.new('ShaderNodeNormalMap')
    nt.links.new(nn.outputs['Color'], nmap.inputs['Color'])
    nt.links.new(nmap.outputs['Normal'], pr.inputs['Normal'])
    lm.use_backface_culling = True
    me.materials.clear()
    me.materials.append(lm)
    for p in me.polygons:
        p.material_index = 0
    sc.render.engine = 'CYCLES'
    sc.cycles.device = 'CPU'
    sc.cycles.samples = 1
    sc.cycles.use_denoising = False
    b = sc.render.bake
    b.use_selected_to_active = True
    b.cage_extrusion = extr_rel * Dw
    b.max_ray_distance = maxd_rel * Dw
    b.margin = max(4, size // 128)
    b.use_clear = True
    b.target = 'IMAGE_TEXTURES'
    b.normal_space = 'TANGENT'
    for x in bpy.context.view_layer.objects:
        x.select_set(False)
    high.select_set(True)
    low.select_set(True)
    bpy.context.view_layer.objects.active = low

    def run(node, kind, getter=None, pass_filter=None):
        for n in nt.nodes:
            n.select = False
        node.select = True
        nt.nodes.active = node
        added = []
        if getter is not None:
            for m in hm:
                srcn = getter(m)
                mt = m.node_tree
                o_ = next(n for n in mt.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output)
                prev = o_.inputs['Surface'].links[0].from_socket if o_.inputs['Surface'].links else None
                e = mt.nodes.new('ShaderNodeEmission')
                mt.links.new(srcn.outputs['Color'], e.inputs['Color'])
                mt.links.new(e.outputs[0], o_.inputs['Surface'])
                added.append((mt, e, prev, o_))
        if pass_filter:
            bpy.ops.object.bake(type=kind, pass_filter=pass_filter)
        else:
            bpy.ops.object.bake(type=kind)
        for mt, e, prev, o_ in added:
            if prev is not None:
                mt.links.new(prev, o_.inputs['Surface'])
            mt.nodes.remove(e)
        node.image.pack()
    if has_col:
        run(nc, 'EMIT', base_color_node)
    else:
        run(nc, 'DIFFUSE', None, {'COLOR'})
    if no is not None:
        run(no, 'EMIT', orm_node)
    run(nn, 'NORMAL')
    log('%s: Texturen in %d px gebacken' % (low.name, size))


# ----------------------------------------------------------------------------- Pruefung und Vorschau

def _dirs14():
    return [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)] + \
           [(a, b, c) for a in (1, -1) for b in (1, -1) for c in (0.6, -0.6)]


def _camera(center, direction, scale):
    sc = bpy.context.scene
    cd = bpy.data.cameras.new('pruefkamera')
    cd.type = 'ORTHO'
    cd.ortho_scale = scale
    cd.clip_start = scale * 0.001
    cd.clip_end = scale * 100
    co = bpy.data.objects.new('pruefkamera', cd)
    sc.collection.objects.link(co)
    d = Vector(direction).normalized()
    co.location = Vector(center) - d * scale * 10
    co.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
    sc.camera = co
    return co


def _render(path):
    sc = bpy.context.scene
    sc.render.filepath = path
    sc.render.image_settings.file_format = 'PNG'
    bpy.ops.render.render(write_still=True)
    im = bpy.data.images.load(path)
    px = image_pixels(im).copy()
    bpy.data.images.remove(im)
    return px


def _world(strength, color=(0.9, 0.9, 0.9, 1)):
    sc = bpy.context.scene
    w = bpy.data.worlds.new('pruefwelt')
    w.use_nodes = True
    sc.world = w
    bg = w.node_tree.nodes['Background']
    bg.inputs[0].default_value = color
    bg.inputs[1].default_value = strength


def check_and_preview(names, preview_path, rep):
    """Nach dem Speichern: Rueckseiten aus 14 Richtungen zaehlen und eine Vorschau rendern (aendert nur den Speicher)."""
    sc = bpy.context.scene
    objs = [bpy.data.objects[n] for n in names]
    for o in bpy.data.objects:
        if o.type == 'MESH':
            o.hide_render = o not in objs
            o.hide_viewport = o.hide_render
    pts = np.array([o.matrix_world @ Vector(c) for o in objs for c in o.bound_box])
    center = (pts.max(0) + pts.min(0)) / 2
    scale = float((pts.max(0) - pts.min(0)).max()) * 1.12
    sc.render.engine = 'CYCLES'
    sc.cycles.device = 'CPU'
    sc.cycles.use_denoising = False
    sc.view_settings.view_transform = 'Standard'
    sc.render.film_transparent = False
    tmp = tempfile.mkdtemp()
    if preview_path:
        _world(0.9, (0.75, 0.77, 0.8, 1))
        sd = bpy.data.lights.new('prueflicht', 'SUN')
        sd.energy = 2.5
        so = bpy.data.objects.new('prueflicht', sd)
        sc.collection.objects.link(so)
        so.rotation_euler = (1.1, 0, -0.5)
        sc.cycles.samples = 24
        sc.render.resolution_x, sc.render.resolution_y = 620, 1000
        sc.render.resolution_percentage = 100
        tiles = []
        for d in ((0, 1, 0), (0.8, 1, -0.15), (0, -1, 0)):
            _camera(center, d, scale)
            tiles.append(_render(os.path.join(tmp, 'v.png')))
        sheet = np.concatenate(tiles, axis=1)
        im = bpy.data.images.new('vorschau', sheet.shape[1], sheet.shape[0], alpha=True)
        im.pixels.foreach_set(sheet.ravel())
        im.filepath_raw = preview_path
        im.file_format = 'PNG'
        im.save()
        bpy.data.objects.remove(so)
        log('Vorschau gespeichert: %s' % preview_path)
    # Rueckseiten: graues Material, nach innen zeigende Flaechen leuchten rot
    m = bpy.data.materials.new('pruefmaterial')
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    mix = nt.nodes.new('ShaderNodeMixShader')
    geo = nt.nodes.new('ShaderNodeNewGeometry')
    dif = nt.nodes.new('ShaderNodeBsdfDiffuse')
    dif.inputs[0].default_value = (0.6, 0.6, 0.65, 1)
    em = nt.nodes.new('ShaderNodeEmission')
    em.inputs[0].default_value = (1, 0, 0.05, 1)
    nt.links.new(geo.outputs['Backfacing'], mix.inputs[0])
    nt.links.new(dif.outputs[0], mix.inputs[1])
    nt.links.new(em.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs[0])
    for o in objs:
        o.data.materials.clear()
        o.data.materials.append(m)
        for p in o.data.polygons:
            p.material_index = 0
    _world(1.0)
    sc.cycles.samples = 6
    sc.render.resolution_x = sc.render.resolution_y = 600
    per_dir, total = [], 0
    for d in _dirs14():
        _camera(center, d, scale)
        px = _render(os.path.join(tmp, 'r.png'))
        red = int(((px[..., 0] > 0.78) & (px[..., 1] < 0.27) & (px[..., 2] < 0.35)).sum())
        per_dir.append(red)
        total += red
    rep['pruefung_rueckseiten_pixel'] = total
    rep['pruefung_rueckseiten_je_richtung'] = per_dir
    rep['pruefung_bestanden'] = bool(total < 200)
    log('Pruefung: %d Pixel mit sichtbarer Rueckseite in 14 Ansichten (%s)' % (total, 'ok' if total < 200 else 'BITTE ANSEHEN'))


# ----------------------------------------------------------------------------- GLB

def export_glb(names, path, height, rep):
    """Die verkleinerten Objekte zu einem Mesh mit einem Material und einem Texturatlas verbinden und als GLB schreiben."""
    parts = [dup(bpy.data.objects[n], 'glb_' + n) for n in names]
    for x in bpy.context.view_layer.objects:
        x.select_set(False)
    for d in parts:
        d.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    n = len(parts)
    maps = {'Farbe': [], 'ORM': [], 'Normal': []}
    size = None
    for i, d in enumerate(parts):
        me = d.data
        uv = np.zeros(len(me.loops) * 2)
        me.uv_layers.active.data.foreach_get('uv', uv)
        uv = uv.reshape(-1, 2)
        uv[:, 0] = (uv[:, 0] + i) / n          # Teil i belegt die i-te Spalte des Atlas
        me.uv_layers.active.data.foreach_set('uv', uv.ravel())
        me.uv_layers.active.name = 'UVMap'
        nt = d.material_slots[0].material.node_tree
        for key in maps:
            nd = nt.nodes.get(key)
            ok = nd is not None and nd.image is not None
            maps[key].append(image_pixels(nd.image) if ok else None)
            if ok:
                size = nd.image.size[0]
    m = bpy.data.materials.new('Figur_klein')
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    pr = nt.nodes.new('ShaderNodeBsdfPrincipled')
    nt.links.new(pr.outputs['BSDF'], out.inputs['Surface'])
    fallback = {'Farbe': (0.5, 0.5, 0.5, 1), 'ORM': (0, 0.5, 0, 1), 'Normal': (0.5, 0.5, 1, 1)}
    for key, tiles in maps.items():
        if all(t is None for t in tiles):
            continue
        tiles = [t if t is not None else np.tile(np.array(fallback[key], np.float32), (size, size, 1)) for t in tiles]
        atlas = np.concatenate(tiles, axis=1)
        im = bpy.data.images.new('Figur_klein_' + key, atlas.shape[1], atlas.shape[0], alpha=False)
        im.colorspace_settings.name = 'sRGB' if key == 'Farbe' else 'Non-Color'
        im.pixels.foreach_set(atlas.ravel())
        im.pack()
        nd = nt.nodes.new('ShaderNodeTexImage')
        nd.image = im
        nd.name = key
        if key == 'Farbe':
            nt.links.new(nd.outputs['Color'], pr.inputs['Base Color'])
        elif key == 'ORM':
            sep = nt.nodes.new('ShaderNodeSeparateColor')
            nt.links.new(nd.outputs['Color'], sep.inputs[0])
            nt.links.new(sep.outputs['Green'], pr.inputs['Roughness'])
            nt.links.new(sep.outputs['Blue'], pr.inputs['Metallic'])
        else:
            nm = nt.nodes.new('ShaderNodeNormalMap')
            nt.links.new(nd.outputs['Color'], nm.inputs['Color'])
            nt.links.new(nm.outputs['Normal'], pr.inputs['Normal'])
    if n > 1:
        bpy.ops.object.join()
    o = parts[0]
    o.name = 'Figur_klein'
    me = o.data
    me.name = 'Figur_klein'
    me.materials.clear()
    me.materials.append(m)
    for p in me.polygons:
        p.material_index = 0
    co = np.zeros(len(me.vertices) * 3)
    me.vertices.foreach_get('co', co)
    co = co.reshape(-1, 3)
    zmin, zmax = co[:, 2].min(), co[:, 2].max()
    s = height / (zmax - zmin) if height else 1.0
    co[:, 2] -= zmin                           # Fuesse auf den Boden, dann auf die Zielhoehe skalieren
    co *= s
    me.vertices.foreach_set('co', co.ravel())
    me.validate()
    me.update()
    activate(o)
    kind = 'fbx' if path.lower().endswith('.fbx') else 'glb'
    if kind == 'fbx':
        # FBX bettet nur Texturen ein, die als Datei vorliegen: neben die FBX schreiben und einbetten
        tdir = os.path.splitext(path)[0] + '_texturen'
        os.makedirs(tdir, exist_ok=True)
        for nd in nt.nodes:
            if nd.type == 'TEX_IMAGE' and nd.image:
                fp = os.path.join(tdir, nd.image.name + '.png')
                nd.image.save(filepath=fp)
                nd.image.unpack(method='REMOVE') if nd.image.packed_file else None
                nd.image.filepath = fp
                nd.image.source = 'FILE'
        bpy.ops.export_scene.fbx(filepath=path, use_selection=True, object_types={'MESH'}, apply_scale_options='FBX_SCALE_ALL',
                                 axis_forward='-Z', axis_up='Y', path_mode='COPY', embed_textures=True,
                                 bake_anim=False, add_leaf_bones=False, mesh_smooth_type='FACE', use_tspace=True)
    else:
        bpy.ops.export_scene.gltf(filepath=path, export_format='GLB', use_selection=True, export_yup=True,
                                  export_apply=True, export_tangents=True)
    rep.update({kind: os.path.basename(path), kind + '_dreiecke': tri_count(o), kind + '_hoehe': round(float((zmax - zmin) * s), 3),
                kind + '_atlas': [size * n, size] if size else None})
    bpy.data.objects.remove(o)
    for nd in list(nt.nodes):                  # aufraeumen, damit ein zweiter Export dieselben Namen bekommt
        if nd.type == 'TEX_IMAGE' and nd.image:
            bpy.data.images.remove(nd.image)
    bpy.data.materials.remove(m)
    bpy.data.meshes.remove(me)
    log('%s gespeichert: %s (%d Dreiecke, Hoehe %.3f)' % (kind.upper(), path, rep[kind + '_dreiecke'], rep[kind + '_hoehe']))


# ----------------------------------------------------------------------------- Ablauf

def main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:]
    ap = argparse.ArgumentParser(description='Trellis-Figur bereinigen, verkleinern und neu texturieren.')
    ap.add_argument('--input', required=True, help='.blend mit Koerper und Kopf als getrennte Objekte')
    ap.add_argument('--output', help='Zieldatei (.blend)')
    ap.add_argument('--glb', help='zusaetzlich die verkleinerte Figur als ein Mesh mit einem Material als GLB schreiben')
    ap.add_argument('--hoehe', type=float, help='Figur im GLB auf diese Gesamthoehe skalieren (Meter), Fuesse auf z = 0')
    ap.add_argument('--fbx', help='wie --glb, aber als FBX mit eingebetteten Texturen (z.B. fuer den Upload bei Mixamo)')
    ap.add_argument('--nur-export', '--nur-glb', dest='nur_glb', action='store_true',
                    help='--input ist eine fertige Ausgabedatei; nur deren Collection 3_Klein als GLB/FBX schreiben')
    ap.add_argument('--koerper', help='Objektname des Koerpers (sonst automatisch: das tiefer liegende Mesh)')
    ap.add_argument('--kopf', help='Objektname des Kopfes (sonst automatisch: das hoeher liegende Mesh)')
    ap.add_argument('--koerper-dreiecke', type=int, default=30000)
    ap.add_argument('--kopf-dreiecke', type=int, default=17500)
    ap.add_argument('--textur', type=int, default=2048, help='Kantenlaenge der gebackenen Texturen')
    ap.add_argument('--kopf-voxel', type=int, default=390, help='Voxel entlang der laengsten Kopfkante')
    ap.add_argument('--hautfarbe', help='r,g,b (0..1) als Referenz, falls die automatische Hauterkennung nicht passt')
    ap.add_argument('--retusche', help='JSON mit Quadern, in denen dunkle Stellen der Koerpertextur uebermalt werden')
    ap.add_argument('--vorschau', help='PNG mit drei Ansichten der verkleinerten Figur')
    ap.add_argument('--bericht', help='JSON mit Kennzahlen und Pruefergebnis')
    ap.add_argument('--ohne-pruefung', action='store_true')
    a = ap.parse_args(argv)

    if a.nur_glb and not (a.glb or a.fbx):
        sys.exit('--nur-export braucht --glb oder --fbx')
    if not a.nur_glb and not a.output:
        sys.exit('--output fehlt')
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.input), load_ui=False, use_scripts=False)
    sc = bpy.context.scene
    if a.nur_glb:
        c = bpy.data.collections.get('3_Klein')
        objs = [o for o in c.objects if o.type == 'MESH'] if c else []
        if not objs:
            sys.exit('Keine Collection 3_Klein mit Mesh-Objekten in %s' % a.input)
        objs.sort(key=lambda o: np.mean([(o.matrix_world @ Vector(p)).z for p in o.bound_box]))
        rep = dict(eingabe=os.path.basename(a.input))
        for target in (a.glb, a.fbx):
            if target:
                export_glb([o.name for o in objs], os.path.abspath(target), a.hoehe, rep)
        if a.bericht:
            with open(a.bericht, 'w') as fh:
                json.dump(rep, fh, indent=2, ensure_ascii=False)
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        return
    meshes = [o for o in sc.objects if o.type == 'MESH']
    body = bpy.data.objects.get(a.koerper) if a.koerper else None
    head = bpy.data.objects.get(a.kopf) if a.kopf else None
    if (a.koerper and body is None) or (a.kopf and head is None):
        sys.exit('Objekt nicht gefunden. Vorhanden: %s' % [o.name for o in meshes])
    if body is None and head is None:
        if len(meshes) == 2:
            zc = [np.mean([(o.matrix_world @ Vector(c)).z for c in o.bound_box]) for o in meshes]
            body, head = (meshes[0], meshes[1]) if zc[0] < zc[1] else (meshes[1], meshes[0])
        elif len(meshes) == 1:
            body = meshes[0]
        else:
            sys.exit('Erwarte ein oder zwei Mesh-Objekte oder --koerper/--kopf. Vorhanden: %s' % [o.name for o in meshes])
    originals = [o for o in (body, head) if o is not None]
    for o in originals:
        o.hide_viewport = False
        o.hide_render = False
    rep = dict(eingabe=os.path.basename(a.input), koerper=body.name if body else None, kopf=head.name if head else None)
    log('Koerper: %s | Kopf: %s' % (rep['koerper'], rep['kopf']))

    sauber, klein = [], []
    if body is not None:
        ks = clean_body(body, rep)
        if a.retusche:
            with open(a.retusche) as fh:
                retouch(ks, json.load(fh), rep)
        kk = decimate_body(ks, a.koerper_dreiecke, rep)
        bake(kk, ks, a.textur, 'Koerper_klein', 0.003, 0.007)
        sauber.append(ks)
        klein.append(kk)
    if head is not None:
        rgb = [float(x) for x in a.hautfarbe.split(',')] if a.hautfarbe else None
        hs, hd = clean_head(head, rep, rgb)
        hk = build_small_head(head, hs, hd, a.kopf_dreiecke, a.kopf_voxel, rep)
        bake(hk, hs, a.textur, 'Kopf_klein', 0.015, 0.037)
        sauber.append(hs)
        klein.append(hk)

    def coll(name, objs, hide):
        c = bpy.data.collections.new(name)
        sc.collection.children.link(c)
        for o in objs:
            for uc in list(o.users_collection):
                uc.objects.unlink(o)
            c.objects.link(o)
            o.hide_viewport = hide
            o.hide_render = hide
    coll('1_Original', originals, True)
    coll('2_Sauber', sauber, True)
    coll('3_Klein', klein, False)
    for m in bpy.data.materials:
        m.use_backface_culling = True
    for im in bpy.data.images:
        if im.packed_file is None and im.source != 'VIEWER' and im.size[0] > 0:
            im.pack()
    rep['dreiecke_klein_gesamt'] = int(sum(tri_count(o) for o in klein))
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.output), compress=True)
    log('Gespeichert: %s' % a.output)
    for target in (a.glb, a.fbx):
        if target:
            export_glb([o.name for o in klein], os.path.abspath(target), a.hoehe, rep)

    if not a.ohne_pruefung:
        check_and_preview([o.name for o in klein], os.path.abspath(a.vorschau) if a.vorschau else None, rep)
    rep['laufzeit_sekunden'] = int(time.time() - T0)
    if a.bericht:
        with open(a.bericht, 'w') as fh:
            json.dump(rep, fh, indent=2, ensure_ascii=False)
    print(json.dumps({k: v for k, v in rep.items() if not isinstance(v, list)}, indent=2, ensure_ascii=False))
    if not a.ohne_pruefung and not rep.get('pruefung_bestanden', True):
        sys.exit(2)


if __name__ == '__main__':
    main()

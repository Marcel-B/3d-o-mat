"""Trennt ueberlappende Texturflaechen an den Haenden (bpy 5.3).

Nach dem Aufbereiten teilen sich manche Handflaechen ein Stueck der Textur mit einer anderen, weit entfernten Flaeche
(z. B. Daumenspitze und Handruecken). Wer die Hand spaeter in Blender uebermalt, malt dann an zwei Stellen. Diese
Flaechen (bevorzugt der Daumen, dann ein Finger, dann die Hand) bekommen samt zusammenhaengenden Nachbarn am selben
Knochen ein eigenes Stueck in einem freien Bereich der Textur. Farbe und Normal-Map werden dorthin kopiert, das
Aussehen bleibt gleich. Die geaenderten Bilder werden in die .blend gepackt.

    trennen(mesh_object, log=print) -> Bericht (dict)
"""
import bmesh
import numpy as np

PAD = 4


def _bilder(obj):
    out = {}
    for m in obj.data.materials:
        if not (m and m.use_nodes):
            continue
        for n in m.node_tree.nodes:
            if n.type == 'TEX_IMAGE' and n.image and n.image.size[0] > 0:
                ziel = [l.to_socket.name for o in n.outputs for l in o.links]
                out['farbe' if 'Base Color' in ziel else n.image.name] = n.image
    return out


def trennen(obj, log=print):
    me = obj.data
    bilder = _bilder(obj)
    if 'farbe' not in bilder:
        log('Texturflaechen: keine Farbtextur')
        return {'fehler': 'keine Farbtextur'}
    W, H = bilder['farbe'].size
    names = {g.index: g.name for g in obj.vertex_groups}
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    uvl = bm.loops.layers.uv.active
    dl = bm.verts.layers.deform.active
    if uvl is None or dl is None:
        bm.free()
        return {'fehler': 'keine UV oder Gewichte'}

    def knochen(f):
        w = {}
        for v in f.verts:
            for g, x in v[dl].items():
                n = names.get(g, '')
                w[n] = w.get(n, 0) + x
        return max(w, key=w.get).replace('mixamorig:', '') if w else ''

    lab = [knochen(f) for f in bm.faces]

    def pix(f):
        return np.array([(l[uvl].uv.x * W, l[uvl].uv.y * H) for l in f.loops])

    def raster(p):
        x0, y0 = np.floor(p.min(0)).astype(int)
        x1, y1 = np.ceil(p.max(0)).astype(int)
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        px, py = xs.ravel() + 0.5, ys.ravel() + 0.5
        ok = np.zeros(px.shape, bool)
        for t in range(1, len(p) - 1):
            A, B, C = p[0], p[t], p[t + 1]
            d1 = (px - B[0]) * (A[1] - B[1]) - (A[0] - B[0]) * (py - B[1])
            d2 = (px - C[0]) * (B[1] - C[1]) - (B[0] - C[0]) * (py - C[1])
            d3 = (px - A[0]) * (C[1] - A[1]) - (C[0] - A[0]) * (py - A[1])
            ok |= ~(((d1 < 0) | (d2 < 0) | (d3 < 0)) & ((d1 > 0) | (d2 > 0) | (d3 > 0)))
        x, y = xs.ravel()[ok], ys.ravel()[ok]
        k = (x >= 0) & (x < W) & (y >= 0) & (y < H)
        return x[k], y[k]

    def ueberlappungen():
        owner = -np.ones((H, W), np.int32)
        paare = set()
        for f in bm.faces:
            x, y = raster(pix(f))
            if len(x) == 0:
                continue
            alt = owner[y, x]
            for q in np.unique(alt[alt >= 0]):
                g = bm.faces[int(q)]
                if ('Hand' in lab[f.index] or 'Hand' in lab[g.index]) and not (set(f.verts) & set(g.verts)):
                    paare.add((f.index, g.index))
            owner[y, x] = f.index
        return paare, owner >= 0

    def rang(i):
        l = lab[i]
        if 'Thumb' in l:
            return 0
        if any(k in l for k in ('Index', 'Middle', 'Ring', 'Pinky')):
            return 1
        return 2 if 'Hand' in l else 3

    def uv_gleich(e, f, g):
        a = [l for l in f.loops if l.edge == e][0]
        b = [l for l in g.loops if l.edge == e][0]
        return ((a[uvl].uv - b.link_loop_next[uvl].uv).length < 1e-5
                and (a.link_loop_next[uvl].uv - b[uvl].uv).length < 1e-5)

    paare, belegt = ueberlappungen()
    bericht = {'ueberlappungen_vorher': len(paare)}
    wandern = set()
    for f, g in paare:
        wandern.add(f if rang(f) < rang(g) or (rang(f) == rang(g) and f < g) else g)
    stuecke, gesehen = [], set()
    for s in sorted(wandern):
        if s in gesehen:
            continue
        comp, todo = set(), [s]
        while todo:
            i = todo.pop()
            if i in comp:
                continue
            comp.add(i)
            f = bm.faces[i]
            for e in f.edges:
                for g in e.link_faces:
                    if g.index not in comp and (g.index in wandern or lab[g.index] == lab[i]) and uv_gleich(e, f, g):
                        todo.append(g.index)
        gesehen |= comp
        stuecke.append(sorted(comp))

    dil = belegt.copy()
    for _ in range(PAD):
        dil[1:, :] |= dil[:-1, :]
        dil[:-1, :] |= dil[1:, :]
        dil[:, 1:] |= dil[:, :-1]
        dil[:, :-1] |= dil[:, 1:]

    def platz(w, h):
        S = np.zeros((H + 1, W + 1), np.int32)
        S[1:, 1:] = dil.cumsum(0).cumsum(1)
        for y in range(0, H - h, 4):
            row = S[y + h, w:] - S[y, w:] - S[y + h, :W + 1 - w] + S[y, :W + 1 - w]
            xs = np.nonzero(row == 0)[0]
            if len(xs):
                return int(xs[0]), y
        return None

    pixel = {n: np.array(b.pixels[:], np.float32).reshape(b.size[1], b.size[0], -1) for n, b in bilder.items()}
    info = []
    for comp in stuecke:
        P = np.vstack([pix(bm.faces[i]) for i in comp])
        x0, y0 = np.floor(P.min(0)).astype(int) - PAD
        x1, y1 = np.ceil(P.max(0)).astype(int) + PAD
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        w, h = x1 - x0, y1 - y0
        pl = platz(w, h)
        if pl is None:
            info.append(dict(flaechen=len(comp), fehler='kein Platz'))
            continue
        nx, ny = pl
        for n, arr in pixel.items():
            if arr.shape[:2] == (H, W):
                arr[ny:ny + h, nx:nx + w] = arr[y0:y1, x0:x1]
        dil[ny:ny + h, nx:nx + w] = True
        du, dv = (nx - x0) / W, (ny - y0) / H
        for i in comp:
            for l in bm.faces[i].loops:
                l[uvl].uv.x += du
                l[uvl].uv.y += dv
        info.append(dict(flaechen=len(comp), knochen=sorted({lab[i] for i in comp})))
    bericht['stuecke'] = info
    bericht['ueberlappungen_nachher'] = len(ueberlappungen()[0])
    bm.to_mesh(me)
    bm.free()
    me.update()
    if info:
        for n, b in bilder.items():
            if pixel[n].shape[:2] == (H, W):
                b.pixels = pixel[n].ravel()
                if b.packed_file:
                    b.pack()
    log(f"Texturflaechen getrennt: {len(info)} Stuecke, Ueberlappungen {bericht['ueberlappungen_vorher']} -> "
        f"{bericht['ueberlappungen_nachher']}")
    return bericht


if __name__ == '__main__':
    # Aufruf: Blender -b --factory-startup --python uv_trennen.py -- EIN.blend AUS.blend
    import bpy, sys, json
    ein, aus = sys.argv[sys.argv.index('--') + 1:][:2]
    bpy.ops.wm.open_mainfile(filepath=ein, load_ui=False)
    ob = [o for o in bpy.context.scene.objects if o.type == 'MESH'][0]
    print('BERICHT ' + json.dumps(trennen(ob)))
    bpy.ops.wm.save_as_mainfile(filepath=aus)

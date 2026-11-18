"""Trennt zusammengewachsene Finger einer gerigten Figur (bpy 5.3).

Trellis laesst eng anliegende Finger an den Beruehrungsstellen zusammenwachsen, und das Verkleinern macht daraus
duenne Bruecken-Dreiecke zwischen zwei Fingern. In Ruhe stehen sie als Splitter zwischen den Fingern, sobald die
Finger sich bewegen, ziehen sie Faeden. Bruecke heisst hier: alle drei Punkte gehoeren ueberwiegend zu Fingerknochen
(nicht zur Hand), aber nicht alle zum selben Finger. Der Daumen zaehlt wie ein Finger.

Die Bruecken werden geloescht, die Oeffnungen, die dadurch an jedem Finger entstehen, je Finger mit einem Faecher
um einen neuen Mittelpunkt geschlossen.
Die neuen Flaechen bekommen die UV des naechsten vorhandenen Eckpunkts (sie liegen verdeckt zwischen den Fingern)
und dieselben Gewichte wie ihre Punkte, die ja schon da sind.

    trennen(mesh_object) -> Anzahl geloeschter Bruecken
"""
import bmesh

FINGERS = ('Thumb', 'Index', 'Middle', 'Ring', 'Pinky')


def _finger(name):
    if 'Hand' not in name:
        return None
    for f in FINGERS:
        if 'Hand' + f in name:
            return name[:name.index('Hand')] + f   # z. B. mixamorig:Left + Ring
    return None


def trennen(obj, log=print):
    me = obj.data
    names = {g.index: g.name for g in obj.vertex_groups}
    bm = bmesh.new()
    bm.from_mesh(me)
    # An UV-Naehten liegen Punkte doppelt (glTF trennt sie); verschweisst laufen die Raender um die Luecke als eine
    # Kette. Die UVs haengen an den Ecken der Flaechen und bleiben dabei, wie sie sind.
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bm.verts.index_update()
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    deform = bm.verts.layers.deform.active
    owner = []
    for v in bm.verts:
        weights = v[deform] if deform is not None else {}
        if not weights:
            owner.append(None)
            continue
        name = names.get(max(weights.items(), key=lambda kv: kv[1])[0], '')
        # Nur Mittel- und Endglieder: am Grundglied steckt der Finger im Handschuh, dort ist das Netz zusammen
        # gewollt, und die Textur der Luecke waere sichtbar.
        owner.append(_finger(name) if name[-1:] in ('2', '3') else None)
    bridges = []
    for f in bm.faces:
        fs = [owner[v.index] for v in f.verts]
        if all(fs) and len(set(fs)) > 1:
            bridges.append(f)
    if not bridges:
        bm.free()
        return 0
    touched = {e for f in bridges for e in f.edges}
    bmesh.ops.delete(bm, geom=bridges, context='FACES_ONLY')
    # Die neuen Raender laufen um die Luecke zwischen zwei Fingern herum, ueber beide Finger. Jeder Finger bekommt
    # seinen Teil des Randes einzeln geschlossen (Faecher um einen neuen Mittelpunkt), sonst entstuende die Bruecke neu.
    border = [e for e in touched if e.is_valid and e.is_boundary]
    uv = bm.loops.layers.uv.active
    nbr = {}
    for e in border:
        a, b = e.verts
        nbr.setdefault(a, []).append(b)
        nbr.setdefault(b, []).append(a)
    seen, chains = set(), []
    for start in list(nbr):
        if start in seen:
            continue
        # Kette je Finger: zusammenhaengende Randpunkte desselben Fingers
        who = owner[start.index] if start.index < len(owner) else None
        if who is None:
            seen.add(start)
            continue
        part, stack = [], [start]
        seen.add(start)
        while stack:
            v = stack.pop()
            part.append(v)
            for n in nbr.get(v, []):
                if n not in seen and n.index < len(owner) and owner[n.index] == who:
                    seen.add(n)
                    stack.append(n)
        if len(part) >= 3:
            chains.append(part)
    new = []
    for part in chains:
        # Reihenfolge entlang des Randes
        ends = [v for v in part if sum(1 for n in nbr[v] if n in part) < 2] or part[:1]
        order, cur, prev = [ends[0]], ends[0], None
        while True:
            nxt = [n for n in nbr[cur] if n in part and n is not prev and n not in order]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            order.append(cur)
        if len(order) < 3:
            continue
        centre = bm.verts.new(sum((v.co for v in order), order[0].co * 0) / len(order))
        if deform is not None:
            near = min(order, key=lambda v: (v.co - centre.co).length)
            for k, w in near[deform].items():
                centre[deform][k] = w
        for a, b in zip(order, order[1:] + order[:1]):
            try:
                f = bm.faces.new((a, b, centre))
            except ValueError:
                continue
            new.append(f)
    # Kleine Restloecher (bis 8 Kanten), wo eine Seite zu kurz fuer einen eigenen Faecher war: lieber ein winziges
    # Stueck Steg tief in der Luecke als ein Loch.
    rest = [e for e in bm.edges if e.is_valid and e.is_boundary and
            (e in touched or any(f in new for f in e.link_faces))]
    if rest:
        before = set(bm.faces)
        bmesh.ops.holes_fill(bm, edges=rest, sides=8)
        new += [f for f in bm.faces if f not in before]
    bm.normal_update()
    if uv is not None:
        for f in new:
            for loop in f.loops:
                others = [l for l in loop.vert.link_loops if l.face not in new]
                if not others:
                    # der neue Mittelpunkt: UV des naechsten Punkts im Faecher
                    src = min((l for l in f.loops if l is not loop), key=lambda l: (l.vert.co - loop.vert.co).length)
                    others = [l for l in src.vert.link_loops if l.face not in new]
                if others:
                    loop[uv].uv = others[0][uv].uv
    for f in new:
        f.material_index = next((l.face.material_index for v in f.verts for l in v.link_loops if l.face not in new), 0)
        f.smooth = True
    if new:
        bmesh.ops.recalc_face_normals(bm, faces=new)
    bm.to_mesh(me)
    bm.free()
    me.update()
    log(f'Finger getrennt: {len(bridges)} Bruecken-Dreiecke entfernt, {len(chains)} Seiten mit {len(new)} Dreiecken geschlossen')
    return len(bridges)

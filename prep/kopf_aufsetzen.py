"""Setzt einen neuen Trellis-Kopf (GLB aus 3d-o-mat, Kopf mit Hals und Schultern) an die Stelle des Kopfes einer
vorhandenen Eingabe-.blend (Koerper_ohne_Kopf + Kopf_langer_Hals) und schreibt eine neue Eingabe fuer
figur_aufbereiten.py. Groesse aus Kinn-bis-Scheitel, Halsbreite und Schaedelbreite (Median der drei), danach an die
Gesichtsbreite des alten Kopfes angeglichen (Haare zaehlen dort nicht mit). Lage: Kinnhoehe
und Schaedelmitte wie beim alten Kopf. Schultern: unterhalb des Kinns faellt weg, was ausserhalb der Halsellipse liegt.
Hals: unterhalb des Kiefers Hoehe fuer Hoehe auf Breite und Tiefe des alten Halses gebracht (nur schmaler).
Laeuft mit Python und bpy 5.3 (wie figur_aufbereiten.py), Teil des Auftrags "Spielerfigur" (prep/server.py).
Aufruf: python kopf_aufsetzen.py VORLAGE.blend KOPF.glb AUSGABE.blend [halsfaktor]"""
import bpy, bmesh, sys, numpy as np
from mathutils import Matrix, Vector

vorlage, kopf_glb, ausgabe = sys.argv[1:4]
halsfaktor = float(sys.argv[4]) if len(sys.argv) > 4 else 1.12

def welt(o):
    m = np.array(o.matrix_world)
    co = np.array([v.co for v in o.data.vertices])
    return co @ m[:3, :3].T + m[:3, 3]

def slice_(co, z, dz):
    return co[np.abs(co[:, 2] - z) < dz]

def kennzahlen(co):
    """Kinn (wo die Tiefe nach vorn springt, von unten gesucht oberhalb der schmalsten Halsstelle), Scheitel,
    Halsbreite, Schaedelbreite und -mitte."""
    z0, z1 = co[:, 2].min(), co[:, 2].max(); h = z1 - z0; dz = h * 0.006
    zs = np.linspace(z0 + h * 0.02, z1 - h * 0.02, 200)
    rows = []
    for z in zs:
        s = slice_(co, z, dz)
        rows.append((z, np.ptp(s[:, 0]) if len(s) else 0, s[:, 1].min() if len(s) else 0))
    rows = np.array(rows)
    # Schaedel: groesste Breite in der oberen Haelfte
    top = rows[rows[:, 0] > z0 + h * 0.5]
    z_skull = top[np.argmax(top[:, 1]), 0]
    # Kinn: im unteren Teil der Punkt, an dem ymin (Vorderkante) am weitesten nach vorn springt
    low = rows[(rows[:, 0] < z_skull)]
    jump = np.diff(low[:, 2])
    i = int(np.argmin(jump))  # staerkster Sprung nach vorn (ymin kleiner)
    z_chin = low[i + 1, 0]
    neck = rows[(rows[:, 0] > z_chin - h * 0.12) & (rows[:, 0] < z_chin + h * 0.15) & (rows[:, 1] > 0)]
    z_neck = neck[np.argmin(neck[:, 1]), 0]
    s = slice_(co, (z_chin + z_skull) / 2 + (z_skull - z_chin) * 0.3, dz * 2)
    centre = np.array([(s[:, 0].min() + s[:, 0].max()) / 2, (s[:, 1].min() + s[:, 1].max()) / 2])
    return dict(z_chin=z_chin, z_top=z1, z_skull=z_skull, skull_w=top[:, 1].max(),
                neck_w=neck[:, 1].min(), z_neck=z_neck, centre=centre)

bpy.ops.wm.open_mainfile(filepath=vorlage, load_ui=False)
alt = bpy.data.objects['Kopf_langer_Hals']
co_alt = welt(alt)
ka = kennzahlen(co_alt)
alt_name = alt.name
bpy.data.objects.remove(alt)
bpy.ops.import_scene.gltf(filepath=kopf_glb)
neu = [o for o in bpy.context.selected_objects if o.type == 'MESH'][0]
for o in list(bpy.context.selected_objects):
    if o is not neu and o.type != 'MESH':
        bpy.data.objects.remove(o)
neu.parent = None
co = welt(neu)
kn = kennzahlen(co)
h = co[:, 2].max() - co[:, 2].min()
z_neck = kn['z_neck']
s = [(ka['z_top'] - ka['z_chin']) / (kn['z_top'] - kn['z_chin']), ka['neck_w'] / kn['neck_w'], ka['skull_w'] / kn['skull_w']]
s0 = float(np.median(s))
# Gesichtsbreite: Kinn bis Scheitel und Schaedelbreite haengen an den Haaren (flache Frisur = groesseres Gesicht bei
# gleicher Hoehe). Darum zum Schluss die Breite zwischen 20 und 50 % der Kopfhoehe ueber dem Kinn (Kiefer bis
# Schlaefen) an die des alten Kopfes angleichen, gemessen nach dem ersten Massstab.
unit = ka['z_top'] - ka['z_chin']
def breiten(c, z_chin, faktor):
    out = []
    for f in (0.2, 0.3, 0.4, 0.5):
        sl = slice_(c, z_chin + f * unit / faktor, unit * 0.02 / faktor)
        out.append(np.ptp(sl[:, 0]) * faktor if len(sl) else np.nan)
    return np.array(out)
b_alt, b_neu = breiten(co_alt, ka['z_chin'], 1.0), breiten(co, kn['z_chin'], s0)
korrektur = float(np.nanmean(b_alt) / np.nanmean(b_neu))
scale = s0 * korrektur
print('alt', {k: (np.round(v, 4) if not isinstance(v, np.ndarray) else v.round(4)) for k, v in ka.items()})
print('neu', {k: (np.round(v, 4) if not isinstance(v, np.ndarray) else v.round(4)) for k, v in kn.items()})
print('Massstab je Mass', np.round(s, 4), '->', round(s0, 4), '| Gesichtsbreite alt', np.round(b_alt, 4), 'neu', np.round(b_neu, 4),
      '-> Korrektur', round(korrektur, 3), '-> Massstab', round(scale, 4))
# Schultern weg: unterhalb des Kinns alles ausserhalb einer Ellipse um die Halsmitte
ns = slice_(co, z_neck, h * 0.006)
cx = (ns[:, 0].min() + ns[:, 0].max()) / 2
rx = np.ptp(ns[:, 0]) / 2 * halsfaktor
# Tiefe: von der Hinterkante des Halses aus, Hals etwa rund (vorn liegt auf dieser Hoehe schon der Kiefer)
ry = rx
cy = ns[:, 1].max() - np.ptp(ns[:, 0]) / 2
m = np.array(neu.matrix_world)
bm = bmesh.new(); bm.from_mesh(neu.data)
weg = []
for v in bm.verts:
    p = m[:3, :3] @ np.array(v.co) + m[:3, 3]
    if p[2] < kn['z_chin'] and ((p[0] - cx) / rx) ** 2 + ((p[1] - cy) / ry) ** 2 > 1:
        weg.append(v)
bmesh.ops.delete(bm, geom=weg, context='VERTS')
bm.to_mesh(neu.data); bm.free()
print('Punkte entfernt (Schultern)', len(weg))
# skalieren und verschieben: Kinnhoehe und Schaedelmitte wie beim alten Kopf
c_neu = np.array([kn['centre'][0], kn['centre'][1], kn['z_chin']])
c_alt = np.array([ka['centre'][0], ka['centre'][1], ka['z_chin']])
T = Matrix.Translation(Vector(c_alt)) @ Matrix.Scale(scale, 4) @ Matrix.Translation(Vector(-c_neu))
neu.matrix_world = T @ neu.matrix_world
bpy.context.view_layer.objects.active = neu
for o in bpy.context.view_layer.objects: o.select_set(o is neu)
bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

# Hals angleichen: Manche Scans haben einen Hals, der so breit wie der Kiefer gerade in den Kragen laeuft
# (Kessler: 13 % breiter und tiefer als Klein). Der Kopf wirkt dann zu gross, obwohl das Gesicht passt. Unterhalb
# des Kiefers wird der Hals darum quer und in der Tiefe auf die Masse des alten Halses gebracht (nur schmaler, nie
# breiter), zum Kiefer hin weich auslaufend. Kiefer: wo der alte Kopf ueber dem Hals 20 % breiter als der Hals wird.
def profil(c, zs, dz):
    out = []
    for z in zs:
        sl = slice_(c, z, dz)
        out.append((np.ptp(sl[:, 0]), np.ptp(sl[:, 1]), (sl[:, 0].min() + sl[:, 0].max()) / 2,
                    (sl[:, 1].min() + sl[:, 1].max()) / 2) if len(sl) > 20 else (np.nan,) * 4)
    return np.array(out)
co_neu = welt(neu)
dz = unit * 0.01
z_lo = max(co_alt[:, 2].min(), co_neu[:, 2].min()) + dz * 2
zs = np.arange(z_lo, ka['z_chin'] + unit * 0.5, dz)
pa = profil(co_alt, zs, dz)
w_hals = np.nanmin(pa[zs < ka['z_chin'] + unit * 0.2, 0])
i_hals = int(np.nanargmin(np.where(zs < ka['z_chin'] + unit * 0.2, pa[:, 0], np.inf)))
ueber = np.where((np.arange(len(zs)) > i_hals) & (pa[:, 0] > w_hals * 1.2))[0]
if len(ueber):
    z_kiefer = zs[ueber[0]]
    band = zs < z_kiefer - dz
    pn = profil(co_neu, zs[band], dz)
    # Hoehe fuer Hoehe auf den alten Hals: oben am Kiefer schmal, unten im Kragen so breit wie der alte Hals,
    # damit zwischen Hals und Kragen kein Spalt entsteht (ein fester Faktor liess unten den Kragenrand sehen).
    zb = zs[band]
    def glatt(r):
        r = np.where(np.isfinite(r), r, np.nan)
        ok = np.isfinite(r)
        r = np.interp(zb, zb[ok], r[ok]) if ok.any() else np.ones_like(zb)
        r = np.convolve(np.pad(r, 2, mode='edge'), np.ones(5) / 5, mode='valid')
        return np.clip(r, 0.75, 1.0)
    rxs, rys = glatt(pa[band, 0] / pn[:, 0]), glatt(pa[band, 1] / pn[:, 1])
    cx, cy = np.nanmedian(pn[:, 2]), np.nanmedian(pn[:, 3])
    blend = unit * 0.08
    for v in neu.data.vertices:
        z = v.co[2]
        if z >= z_kiefer:
            continue
        rx = float(np.interp(z, zb, rxs)); ry = float(np.interp(z, zb, rys))
        t = min(1.0, (z_kiefer - z) / blend); t = t * t * (3 - 2 * t)
        v.co[0] = cx + (v.co[0] - cx) * (1 - t * (1 - rx))
        v.co[1] = cy + (v.co[1] - cy) * (1 - t * (1 - ry))
    neu.data.update()
    print('Hals angeglichen: Kiefer bei', round(float(z_kiefer), 4), '| quer', np.round(rxs[::max(1, len(rxs) // 6)], 3),
          'tief', np.round(rys[::max(1, len(rys) // 6)], 3), '(von unten nach oben)')
else:
    print('Hals angeglichen: Kiefer im alten Kopf nicht gefunden, Hals unveraendert')
neu.name = alt_name; neu.data.name = alt_name
bpy.ops.outliner.orphans_purge(do_recursive=True)
for im in bpy.data.images:
    if im.packed_file is None and im.size[0] > 0: im.pack()
bpy.ops.wm.save_as_mainfile(filepath=ausgabe, compress=True)
cn = welt(neu); print('neuer Kopf', cn.min(0).round(4), cn.max(0).round(4), len(neu.data.polygons), 'Flaechen')

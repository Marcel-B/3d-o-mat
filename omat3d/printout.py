"""Ein GLB als Datei für den 3D-Drucker: die Haltung aus dem Viewer einbacken, wasserdicht machen, Sockel, 3MF oder STL.

1. Haltung: Die Seite schickt für die Knoten des Skeletts ihre Drehung (wie stage.ts sie gerade zeigt). Jeder Punkt
   folgt seinen Knochen mit den Gewichten aus SkinTokens (Linear Blend Skinning, dieselbe Rechnung wie three.js).
2. Lage: glTF hat Y oben, Slicer Z oben; die Figur wird auf die gewünschte Höhe in Millimetern skaliert, mit dem
   tiefsten Punkt auf 0.
3. Wasserdicht: Sind alle Teile geschlossen, verschmilzt manifold3d sie zu einem Körper. Sonst (Löcher, Kleidung als
   einzelne Fläche) wird die Figur in Voxel umgerechnet: Außenraum von den Rändern her fluten, alles andere ist voll,
   die Oberfläche neu erzeugen. Das schließt kleine Löcher und macht hauchdünne Flächen etwa 1 mm dick.
4. Sockel: eine runde Scheibe unter dem, was am Boden steht; die Figur sinkt 0,5 mm ein, damit beides ein Teil ist.

Gerechnet wird auf der CPU im Container, ohne Modell auf oi-o-mat.
"""
import io
import json
import math
import struct
import zipfile

import numpy as np

from .animate import AnimationError, _chunks

MIN_HEIGHT, MAX_HEIGHT = 20.0, 400.0
FORMATS = ("3mf", "stl")
# Höchstens so viele Voxel; bei größeren Figuren wird das Raster gröber.
MAX_CELLS = 30_000_000
SINK = 0.5
# So viele Dreiecke (die der nächsten Proben) prüft das Raster je Zelle für den genauen Abstand zur Oberfläche.
NEAREST = 12

COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
TYPES = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}


class PrintError(ValueError):
    pass


def _accessor(document: dict, binary: bytes, index: int) -> np.ndarray:
    accessor = document["accessors"][index]
    if "bufferView" not in accessor or accessor.get("sparse"):
        raise PrintError("GLB mit dünn besetzten Daten wird nicht unterstützt")
    view = document["bufferViews"][accessor["bufferView"]]
    if view.get("buffer", 0) != 0:
        raise PrintError("GLB mit externen Daten wird nicht unterstützt")
    width = COMPONENTS[accessor["type"]]
    kind = np.dtype(TYPES[accessor["componentType"]])
    count = accessor["count"]
    offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = view.get("byteStride") or width * kind.itemsize
    if offset + stride * (count - 1) + width * kind.itemsize > len(binary):
        raise PrintError("GLB ist unvollständig")
    raw = np.ndarray((count, width), kind, binary, offset, (stride, kind.itemsize))
    values = raw.astype(np.float64)
    if accessor.get("normalized") and kind.kind in "iu":
        values /= np.iinfo(kind).max
    return values


def _matrix(node: dict, rotation=None) -> np.ndarray:
    if "matrix" in node and rotation is None:
        return np.array(node["matrix"], float).reshape(4, 4).T
    if "matrix" in node:
        # Wie in animate._trs: die Lage aus der Matrix behalten, nur die Drehung tauschen.
        base = np.array(node["matrix"], float).reshape(4, 4).T
        translation, scale = base[:3, 3], np.linalg.norm(base[:3, :3], axis=0)
    else:
        translation = np.array(node.get("translation", [0, 0, 0]), float)
        scale = np.array(node.get("scale", [1, 1, 1]), float)
    x, y, z, w = rotation if rotation is not None else node.get("rotation", [0, 0, 0, 1])
    turn = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    out = np.eye(4)
    out[:3, :3] = turn * scale
    out[:3, 3] = translation
    return out


def _rotations(pose, nodes: int) -> dict[int, list[float]]:
    if pose is None:
        return {}
    if not isinstance(pose, dict) or len(pose) > nodes:
        raise PrintError("Haltung passt nicht zum Modell")
    out = {}
    for key, value in pose.items():
        try:
            node = int(key)
        except (TypeError, ValueError):
            raise PrintError("Haltung nennt einen unbekannten Knoten") from None
        if not 0 <= node < nodes or not isinstance(value, list) or len(value) != 4 \
                or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in value):
            raise PrintError("Haltung nennt einen unbekannten Knoten oder keine Drehung")
        length = math.sqrt(sum(v * v for v in value))
        if length < 1e-6:
            raise PrintError("Haltung enthält eine leere Drehung")
        out[node] = [v / length for v in value]
    return out


def posed_triangles(glb: bytes, pose: dict | None = None) -> np.ndarray:
    """Alle Dreiecke der Szene in der Haltung, als (n, 3, 3) in den Maßen des GLB (Y oben)."""
    try:
        document, binary = _chunks(glb)
    except AnimationError as error:
        raise PrintError(str(error)) from None
    nodes = document.get("nodes") or []
    rotations = _rotations(pose, len(nodes))
    parent = {child: i for i, node in enumerate(nodes) for child in node.get("children", [])}
    local = [_matrix(node, rotations.get(i)) for i, node in enumerate(nodes)]
    world: dict[int, np.ndarray] = {}

    def to_world(i: int) -> np.ndarray:
        if i not in world:
            world[i] = (to_world(parent[i]) if i in parent else np.eye(4)) @ local[i]
        return world[i]

    scenes = document.get("scenes") or [{"nodes": [i for i in range(len(nodes)) if i not in parent]}]
    pending = list(scenes[document.get("scene", 0)].get("nodes", []))
    shown = []
    while pending:
        i = pending.pop()
        shown.append(i)
        pending.extend(nodes[i].get("children", []))

    parts = []
    for i in shown:
        node = nodes[i]
        if "mesh" not in node:
            continue
        for primitive in document["meshes"][node["mesh"]]["primitives"]:
            if primitive.get("mode", 4) != 4 or "POSITION" not in primitive["attributes"]:
                continue
            points = _accessor(document, binary, primitive["attributes"]["POSITION"])
            if "indices" in primitive:
                faces = _accessor(document, binary, primitive["indices"]).astype(np.int64).reshape(-1, 3)
            else:
                faces = np.arange(len(points) - len(points) % 3).reshape(-1, 3)
            homogeneous = np.c_[points, np.ones(len(points))]
            attributes = primitive["attributes"]
            if "skin" in node and "JOINTS_0" in attributes and "WEIGHTS_0" in attributes:
                # Bei Netzen mit Skelett zählt die Lage des Netz-Knotens nicht (glTF 2.0, 3.7.3.3), nur die Knochen.
                skin = document["skins"][node["skin"]]
                joints = skin["joints"]
                inverse = (_accessor(document, binary, skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
                           if "inverseBindMatrices" in skin else np.tile(np.eye(4), (len(joints), 1, 1)))
                bones = np.stack([to_world(j) @ inverse[k] for k, j in enumerate(joints)])
                which = _accessor(document, binary, attributes["JOINTS_0"]).astype(np.int64)
                weights = _accessor(document, binary, attributes["WEIGHTS_0"])
                if which.max(initial=0) >= len(joints):
                    raise PrintError("GLB nennt einen Knochen, den es nicht gibt")
                total = weights.sum(axis=1, keepdims=True)
                weights = np.where(total > 0, weights / np.where(total > 0, total, 1), [[1, 0, 0, 0]])
                moved = np.zeros_like(homogeneous)
                for column in range(4):
                    moved += weights[:, column:column + 1] * np.einsum("nij,nj->ni", bones[which[:, column]], homogeneous)
                points = moved[:, :3]
            else:
                points = (to_world(i) @ homogeneous.T).T[:, :3]
            if len(faces) and faces.max() < len(points):
                parts.append(points[faces])
    if not parts:
        raise PrintError("GLB enthält kein Netz")
    return np.concatenate(parts)


def _weld(triangles: np.ndarray, tolerance: float) -> tuple[np.ndarray, np.ndarray]:
    """Punkte, die (fast) aufeinander liegen, zu einem machen; ohne entartete Dreiecke."""
    flat = triangles.reshape(-1, 3)
    keys = np.round(flat / tolerance).astype(np.int64)
    unique, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    faces = inverse.reshape(-1, 3)
    faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])]
    return flat[first], faces


def _manifold(points: np.ndarray, faces: np.ndarray):
    import manifold3d

    return manifold3d.Manifold(manifold3d.Mesh(vert_properties=np.asarray(points, np.float32),
                                               tri_verts=np.asarray(faces, np.uint32)))


def _closed(points: np.ndarray, faces: np.ndarray):
    """Die Teile als ein Körper, wenn jedes Teil geschlossen ist; sonst None."""
    import manifold3d
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    if len(faces) == 0 or (counts != 2).any():
        return None
    graph = coo_matrix((np.ones(len(faces) * 3), (np.repeat(faces[:, 0], 3), faces.ravel())), shape=(len(points),) * 2)
    count, label = connected_components(graph, directed=False)
    pieces = []
    for part in range(count):
        mask = label[faces[:, 0]] == part
        if not mask.any():
            continue
        used, local = np.unique(faces[mask], return_inverse=True)
        piece = _manifold(points[used], local.reshape(-1, 3))
        if piece.status() != manifold3d.Error.NoError or piece.is_empty():
            return None
        # Eine nach innen gekehrte Hülle hätte negatives Volumen.
        if piece.volume() < 0:
            return None
        pieces.append(piece)
    return manifold3d.Manifold.batch_boolean(pieces, manifold3d.OpType.Add) if len(pieces) > 1 else pieces[0]


def _triangle_distance(points: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Abstand je Punkt zu je einem Dreieck (gleich lange Reihen); nächster Punkt nach Ericson, Real-Time Collision
    Detection, 5.1.5."""
    ab, ac, ap = b - a, c - a, points - a
    d1, d2 = (ab * ap).sum(-1), (ac * ap).sum(-1)
    bp = points - b
    d3, d4 = (ab * bp).sum(-1), (ac * bp).sum(-1)
    cp = points - c
    d5, d6 = (ab * cp).sum(-1), (ac * cp).sum(-1)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    with np.errstate(divide="ignore", invalid="ignore"):
        total = va + vb + vc
        v, w = np.nan_to_num(vb / total), np.nan_to_num(vc / total)
        closest = a + ab * v[:, None] + ac * w[:, None]
        # Ränder und Ecken, von innen nach außen überschrieben.
        t = np.clip(np.nan_to_num((d4 - d3) / ((d4 - d3) + (d5 - d6))), 0, 1)
        edge = (va <= 0) & (d4 - d3 >= 0) & (d5 - d6 >= 0)
        closest[edge] = (b + (c - b) * t[:, None])[edge]
        t = np.clip(np.nan_to_num(d2 / (d2 - d6)), 0, 1)
        edge = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
        closest[edge] = (a + ac * t[:, None])[edge]
        t = np.clip(np.nan_to_num(d1 / (d1 - d3)), 0, 1)
        edge = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
        closest[edge] = (a + ab * t[:, None])[edge]
    closest[(d6 >= 0) & (d5 <= d6)] = c[(d6 >= 0) & (d5 <= d6)]
    closest[(d3 >= 0) & (d4 <= d3)] = b[(d3 >= 0) & (d4 <= d3)]
    closest[(d1 <= 0) & (d2 <= 0)] = a[(d1 <= 0) & (d2 <= 0)]
    return np.linalg.norm(points - closest, axis=1)


def _voxels(triangles: np.ndarray, pitch: float, close: float):
    """Wasserdicht über ein Raster: was von außen erreichbar ist, ist außen; alles andere ist voll.

    Das Raster entscheidet nur, was innen und außen ist. Die neue Oberfläche liegt im genauen Abstand shell zur alten
    (Abstand zum nächsten Dreieck, nicht zur nächsten Zelle); so bleibt sie glatt statt treppig, und eine einzelne
    Fläche wird 2 * shell dick.
    """
    from scipy import ndimage
    from scipy.spatial import cKDTree
    from skimage import measure

    shell = 0.6 * pitch
    low = triangles.reshape(-1, 3).min(axis=0) - close - 3 * pitch
    high = triangles.reshape(-1, 3).max(axis=0) + close + 3 * pitch
    shape = np.ceil((high - low) / pitch).astype(int) + 1
    # Die Oberfläche dicht genug abtasten, dass keine Zelle zwischen zwei Proben frei bleibt.
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    per = np.minimum(np.ceil(area / (0.5 * pitch) ** 2), 2000).astype(np.int64)
    rng = np.random.default_rng(0)
    owner = np.repeat(np.arange(len(triangles)), per)
    u, v = rng.random(len(owner)), rng.random(len(owner))
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    samples = a[owner] + u[:, None] * (b - a)[owner] + v[:, None] * (c - a)[owner]
    samples = np.concatenate([samples, triangles.reshape(-1, 3)])
    owner = np.concatenate([owner, np.repeat(np.arange(len(triangles)), 3)])
    occupied = np.zeros(shape, bool)
    cells = np.clip(np.round((samples - low) / pitch).astype(int), 0, shape - 1)
    occupied[cells[:, 0], cells[:, 1], cells[:, 2]] = True
    del cells, u, v
    distance = (ndimage.distance_transform_edt(~occupied) * pitch).astype(np.float32)
    del occupied
    labels, _ = ndimage.label(distance > close)
    edge = np.unique(np.concatenate([labels[0].ravel(), labels[-1].ravel(), labels[:, 0].ravel(), labels[:, -1].ravel(),
                                     labels[:, :, 0].ravel(), labels[:, :, -1].ravel()]))
    outside = np.isin(labels, edge[edge > 0])
    del labels
    # Nahe der Oberfläche den genauen Abstand, zu den Dreiecken der nächsten Proben (große Dreiecke haben viele Proben,
    # werden also auch gefunden). Die Zelle ist höchstens eine halbe Raumdiagonale von einer Probe entfernt; das bleibt
    # die Obergrenze.
    band = np.argwhere(distance <= shell + 2 * pitch)
    tree = cKDTree(samples)
    for start in range(0, len(band), 50_000):
        chunk = band[start:start + 50_000]
        where = low + chunk * pitch
        _, nearest = tree.query(where, k=NEAREST, workers=-1)
        which = owner[nearest.ravel()]
        exact = _triangle_distance(np.repeat(where, NEAREST, axis=0), a[which], b[which], c[which])
        cell = tuple(chunk.T)
        distance[cell] = np.minimum(exact.reshape(-1, NEAREST).min(axis=1), distance[cell] + 0.87 * pitch)
    # Der Außenraum endete close vor der Oberfläche; bis auf shell an sie heran, aber nicht durch Löcher hinein.
    open_ = distance > shell
    outside = ndimage.binary_dilation(outside, iterations=int(np.ceil((close - shell) / pitch)) + 2, mask=open_)
    # Innen negativ: nahe der Oberfläche glatt nach dem Abstand, tiefer drinnen (und in geschlossenen Löchern) fest.
    field = np.where(outside | ~open_, distance - shell, -0.5 * pitch).astype(np.float32)
    del distance, outside, open_, samples, owner, tree
    points, faces, _, _ = measure.marching_cubes(field, 0.0, spacing=(pitch,) * 3)
    solid = _manifold(points + low, faces)
    if solid.is_empty():
        raise PrintError("Die Figur ließ sich nicht schließen")
    return solid


def _base(solid, height: float):
    """Eine runde Scheibe unter dem, was am Boden steht (die untersten 3 % der Höhe)."""
    import manifold3d

    mesh = solid.to_mesh()
    points = np.asarray(mesh.vert_properties)[:, :3]
    floor = points[points[:, 2] <= points[:, 2].min() + max(1.0, 0.03 * height)]
    center = (floor[:, :2].min(axis=0) + floor[:, :2].max(axis=0)) / 2
    radius = max(np.linalg.norm(floor[:, :2] - center, axis=1).max() + max(4.0, 0.06 * height), 10.0)
    thick = min(max(3.0, 0.03 * height), 8.0)
    disc = manifold3d.Manifold.cylinder(thick, radius, radius, 128).translate([center[0], center[1], -thick + SINK])
    return (solid + disc).translate([0, 0, thick - SINK])


def _three_mf(points: np.ndarray, faces: np.ndarray, name: str) -> bytes:
    vertices = "".join(f'<vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>' for x, y, z in points.tolist())
    triangles = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in faces.tolist())
    title = name.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="de-DE" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<metadata name="Title">{title}</metadata><metadata name="Application">3d-o-mat</metadata>'
             f'<resources><object id="1" name="{title}" type="model"><mesh><vertices>{vertices}</vertices>'
             f'<triangles>{triangles}</triangles></mesh></object></resources>'
             '<build><item objectid="1"/></build></model>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml",
                         '<?xml version="1.0" encoding="UTF-8"?>\n'
                         '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                         '</Types>')
        archive.writestr("_rels/.rels",
                         '<?xml version="1.0" encoding="UTF-8"?>\n'
                         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
                         'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        archive.writestr("3D/3dmodel.model", model)
    return buffer.getvalue()


def _stl(points: np.ndarray, faces: np.ndarray, name: str) -> bytes:
    triangles = points[faces].astype(np.float32)
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    record = np.zeros(len(faces), [("normal", "<f4", 3), ("points", "<f4", (3, 3)), ("attribute", "<u2")])
    record["normal"], record["points"] = normals, triangles
    header = f"3d-o-mat {name}".encode("ascii", "replace")[:80].ljust(80, b" ")
    return header + struct.pack("<I", len(faces)) + record.tobytes()


def make_print(glb: bytes, pose: dict | None = None, height: float = 120.0, base: bool = True,
               fmt: str = "3mf", name: str = "3d-o-mat") -> tuple[bytes, dict]:
    """Die Datei für den Drucker und was dabei herauskam (Weg, Maße, Volumen)."""
    if fmt not in FORMATS:
        raise PrintError("Format 3mf oder stl")
    if not MIN_HEIGHT <= height <= MAX_HEIGHT:
        raise PrintError(f"Höhe {MIN_HEIGHT:.0f} bis {MAX_HEIGHT:.0f} mm")
    triangles = posed_triangles(glb, pose)
    # Y oben (glTF) zu Z oben (Slicer); vorn (+Z im glTF) wird -Y, also zum Betrachter im Slicer.
    triangles = np.stack([triangles[..., 0], -triangles[..., 2], triangles[..., 1]], axis=-1)
    flat = triangles.reshape(-1, 3)
    extent = flat.max(axis=0) - flat.min(axis=0)
    if extent[2] <= 0:
        raise PrintError("Das Modell ist flach")
    scale = height / extent[2]
    triangles = (triangles - [*(flat.min(axis=0) + flat.max(axis=0))[:2] / 2, flat[:, 2].min()]) * scale
    points, faces = _weld(triangles, 1e-4)
    solid = _closed(points, faces)
    method = "closed"
    if solid is None:
        method = "voxels"
        size = triangles.reshape(-1, 3).max(axis=0) - triangles.reshape(-1, 3).min(axis=0)
        pitch = max(height / 550, 0.2)
        while np.prod(np.ceil((size + 2 + 8 * pitch) / pitch)) > MAX_CELLS:
            pitch *= 1.15
        solid = _voxels(triangles, pitch, close=1.5 * pitch)
    if base:
        solid = _base(solid, height)
    mesh = solid.to_mesh()
    points = np.asarray(mesh.vert_properties, np.float64)[:, :3]
    faces = np.asarray(mesh.tri_verts, np.int64)
    low, high = points.min(axis=0), points.max(axis=0)
    info = {"method": method, "triangles": int(len(faces)), "volumeCm3": round(solid.volume() / 1000, 1),
            "sizeMm": [round(float(v), 1) for v in high - low]}
    data = _three_mf(points, faces, name) if fmt == "3mf" else _stl(points, faces, name)
    return data, info


if __name__ == "__main__":
    import sys

    path, out = sys.argv[1], sys.argv[2]
    pose = json.loads(sys.argv[3]) if len(sys.argv) > 3 else None
    data, info = make_print(open(path, "rb").read(), pose, fmt=out.rsplit(".", 1)[-1])
    open(out, "wb").write(data)
    print(info)

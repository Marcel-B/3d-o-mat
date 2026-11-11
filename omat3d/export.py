"""Das GLB für andere Programme (die Schnittstelle unter /v1, erster Nutzer Kaderwerk): aus dem, was TRELLIS.2 liefert,
eine Datei, die ein Spiel ohne Nacharbeit an einen Knochen hängen kann.

- Genau ein Mesh mit einer Primitive: POSITION, NORMAL, TEXCOORD_0, Indizes. Mehrere Primitiven mit demselben Material
  werden zusammengelegt, Knotentransformationen eingerechnet. Kein Skelett, keine Animation.
- PBR mit Basisfarbe-Textur und Metallic-Roughness-Textur, beide PNG in der bestellten Kantenlänge. Fehlt die
  Metallic-Roughness-Textur, wird sie aus den Faktoren gemalt.
- Y oben, das Gesicht nach +Z (so legt TRELLIS.2 die Vorderseite des Bilds schon hin: an einer Figur zeigen die Zehen
  nach +Z), Maße in Metern: das Modell wird auf `height` skaliert. Der Ursprung liegt mittig unten am Hals, also in
  der Mitte des untersten Querschnitts, nicht der ganzen Hülle (eine Nase schöbe die sonst nach vorn).
- Der UV-Atlas kommt von TRELLIS.2 und bleibt, wie er ist. Damit an den Nähten keine Farbe ausläuft, bekommt jedes
  Texel außerhalb der Inseln die Farbe des nächsten Texels innerhalb; das ist mehr als jeder Rand von 4 Pixeln und
  hält auch in kleinen Mip-Stufen.
- asset.extras: Modell, Seed, Flächen, Textur, SHA-256 des Eingabebilds.
"""
import io
import json
import struct

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .animate import AnimationError, chunks
from .printout import PrintError, _accessor, _matrix

# Was unten als Hals zählt, als Anteil der Höhe.
NECK = 0.04


class ExportError(ValueError):
    pass


def _world(document: dict) -> list[tuple[int, np.ndarray]]:
    """(Mesh, Weltmatrix) für jeden Knoten der Szene, der ein Mesh trägt."""
    nodes = document.get("nodes") or []
    scenes = document.get("scenes") or []
    roots = scenes[document.get("scene", 0)].get("nodes", []) if scenes else range(len(nodes))
    found = []

    def walk(index: int, parent: np.ndarray, depth: int) -> None:
        if depth > 64:
            raise ExportError("GLB mit zu tief geschachtelten Knoten")
        node = nodes[index]
        matrix = parent @ _matrix(node)
        if "mesh" in node:
            found.append((node["mesh"], matrix))
        for child in node.get("children", []):
            walk(child, matrix, depth + 1)

    for root in roots:
        walk(root, np.eye(4), 0)
    return found


def _image(document: dict, binary: bytes, texture: dict | None) -> Image.Image | None:
    if not texture:
        return None
    source = document["textures"][texture["index"]].get("source")
    if source is None:
        raise ExportError("Textur ohne Bild (nur Erweiterungen wie WebP werden nicht unterstützt)")
    image = document["images"][source]
    if "bufferView" not in image:
        raise ExportError("GLB mit externem Bild wird nicht unterstützt")
    view = document["bufferViews"][image["bufferView"]]
    start = view.get("byteOffset", 0)
    return Image.open(io.BytesIO(binary[start:start + view["byteLength"]]))


def _islands(uv: np.ndarray, faces: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Welche Texel ein Dreieck des Atlas berührt (mit Umriss, damit auch die Kanten zählen)."""
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    points = uv * size
    for a, b, c in faces:
        triangle = [tuple(points[a]), tuple(points[b]), tuple(points[c])]
        draw.polygon(triangle, fill=255, outline=255)
    return np.asarray(mask) > 0


def _bleed(pixels: np.ndarray, inside: np.ndarray) -> np.ndarray:
    """Jedes Texel außerhalb der Inseln nimmt die Farbe des nächsten Texels innerhalb."""
    if inside.all() or not inside.any():
        return pixels
    _, (rows, cols) = ndimage.distance_transform_edt(~inside, return_indices=True)
    return pixels[rows, cols]


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG", compress_level=6)
    return buffer.getvalue()


def _pad(data: bytes, fill: bytes = b"\x00") -> bytes:
    return data + fill * (-len(data) % 4)


def finish(glb: bytes, texture: int, height: float, extras: dict) -> tuple[bytes, dict]:
    """Das GLB von TRELLIS.2 als Datei für ein Spiel; zurück kommen sie und faces, vertices, texture, bytes."""
    try:
        document, binary = chunks(glb)
    except AnimationError as error:
        raise ExportError(str(error)) from error
    meshes = _world(document)
    if not meshes:
        raise ExportError("GLB ohne Mesh")
    try:
        parts, material = [], None
        for mesh, matrix in meshes:
            for primitive in document["meshes"][mesh]["primitives"]:
                if primitive.get("mode", 4) != 4:
                    raise ExportError("GLB mit anderen Primitiven als Dreiecken")
                if material is None:
                    material = primitive.get("material")
                elif primitive.get("material") != material:
                    raise ExportError("GLB mit mehreren Materialien; die Schnittstelle liefert genau eins")
                attributes = primitive["attributes"]
                if "TEXCOORD_0" not in attributes:
                    raise ExportError("GLB ohne Texturkoordinaten")
                positions = _accessor(document, binary, attributes["POSITION"])
                uv = _accessor(document, binary, attributes["TEXCOORD_0"])
                normals = _accessor(document, binary, attributes["NORMAL"]) if "NORMAL" in attributes else None
                indices = (_accessor(document, binary, primitive["indices"])[:, 0].astype(np.int64)
                           if "indices" in primitive else np.arange(len(positions)))
                parts.append((positions, normals, uv, indices.reshape(-1, 3), matrix))
    except PrintError as error:
        raise ExportError(str(error)) from error

    all_positions, all_normals, all_uv, all_faces, offset = [], [], [], [], 0
    for positions, normals, uv, faces, matrix in parts:
        world = positions @ matrix[:3, :3].T + matrix[:3, 3]
        if normals is not None:
            turned = normals @ np.linalg.inv(matrix[:3, :3])
        else:
            turned = _vertex_normals(world, faces)
        all_positions.append(world)
        all_normals.append(turned)
        all_uv.append(uv)
        all_faces.append(faces + offset)
        offset += len(positions)
    positions = np.concatenate(all_positions)
    normals = np.concatenate(all_normals)
    uv = np.concatenate(all_uv)
    faces = np.concatenate(all_faces)
    if not len(faces):
        raise ExportError("GLB ohne Flächen")
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.where(lengths > 1e-12, normals / np.maximum(lengths, 1e-12), [0.0, 1.0, 0.0])

    # Ursprung mittig unten am Hals, Maße in Metern.
    low, high = positions[:, 1].min(), positions[:, 1].max()
    if high - low <= 0:
        raise ExportError("Das Modell hat keine Höhe")
    neck = positions[positions[:, 1] <= low + NECK * (high - low)]
    centre = np.array([(neck[:, 0].min() + neck[:, 0].max()) / 2, low, (neck[:, 2].min() + neck[:, 2].max()) / 2])
    positions = (positions - centre) * (height / (high - low))

    # Texturen: Basisfarbe und Metallic-Roughness, beide in der bestellten Größe und mit Farbe bis über die Nähte.
    source = (document.get("materials") or [{}])[material] if material is not None else {}
    pbr = source.get("pbrMetallicRoughness", {})
    alpha_mode = source.get("alphaMode", "OPAQUE")
    base = _image(document, binary, pbr.get("baseColorTexture"))
    if base is None:
        factor = pbr.get("baseColorFactor", [1, 1, 1, 1])
        base = Image.new("RGBA", (texture, texture), tuple(int(round(v * 255)) for v in factor))
    base = base.convert("RGBA" if alpha_mode != "OPAQUE" else "RGB")
    rough = _image(document, binary, pbr.get("metallicRoughnessTexture"))
    metallic_factor, roughness_factor = pbr.get("metallicFactor", 1.0), pbr.get("roughnessFactor", 1.0)
    if rough is None:
        rough = Image.new("RGB", (texture, texture), (0, int(round(roughness_factor * 255)), int(round(metallic_factor * 255))))
        metallic_factor = roughness_factor = 1.0
    rough = rough.convert("RGB")
    # Erst über die Nähte malen, dann auf die bestellte Größe: sonst zöge das Verkleinern den leeren Grund wieder herein.
    masks, textures = {}, []
    for image in (base, rough):
        if image.size not in masks:
            masks[image.size] = _islands(uv, faces, image.size)
        image = Image.fromarray(_bleed(np.asarray(image), masks[image.size]), image.mode)
        if image.size != (texture, texture):
            image = image.resize((texture, texture), Image.LANCZOS)
        textures.append(_png(image))

    material_out = {"name": "Kopf", "pbrMetallicRoughness": {
        **({"baseColorFactor": pbr["baseColorFactor"]} if "baseColorFactor" in pbr and pbr.get("baseColorTexture") else {}),
        "baseColorTexture": {"index": 0}, "metallicRoughnessTexture": {"index": 1},
        "metallicFactor": metallic_factor, "roughnessFactor": roughness_factor}}
    if alpha_mode != "OPAQUE":
        material_out["alphaMode"] = alpha_mode
        if "alphaCutoff" in source:
            material_out["alphaCutoff"] = source["alphaCutoff"]
    if source.get("doubleSided"):
        material_out["doubleSided"] = True

    blobs = [positions.astype("<f4").tobytes(), normals.astype("<f4").tobytes(), uv.astype("<f4").tobytes(),
             faces.astype("<u4" if offset > 65535 else "<u2").tobytes(), *textures]
    views, body = [], b""
    for index, blob in enumerate(blobs):
        view = {"buffer": 0, "byteOffset": len(body), "byteLength": len(blob)}
        if index < 3:
            view["target"] = 34962
        elif index == 3:
            view["target"] = 34963
        views.append(view)
        body += _pad(blob)
    count = len(positions)
    gltf = {
        "asset": {"version": "2.0", "generator": "3d-o-mat", "extras": {
            **extras, "faces": int(len(faces)), "texture": texture}},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "Kopf", "mesh": 0}],
        "meshes": [{"name": "Kopf", "primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                                                    "indices": 3, "material": 0, "mode": 4}]}],
        "materials": [material_out],
        "textures": [{"sampler": 0, "source": 0}, {"sampler": 0, "source": 1}],
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}],
        "images": [{"bufferView": 4, "mimeType": "image/png"}, {"bufferView": 5, "mimeType": "image/png"}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": count, "type": "VEC3",
             "min": positions.min(0).astype("<f4").tolist(), "max": positions.max(0).astype("<f4").tolist()},
            {"bufferView": 1, "componentType": 5126, "count": count, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5126, "count": count, "type": "VEC2"},
            {"bufferView": 3, "componentType": 5125 if offset > 65535 else 5123, "count": int(faces.size), "type": "SCALAR"},
        ],
        "bufferViews": views,
        "buffers": [{"byteLength": len(body)}],
    }
    text = _pad(json.dumps(gltf, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), b" ")
    total = 12 + 8 + len(text) + 8 + len(body)
    out = (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<II", len(text), 0x4E4F534A) + text
           + struct.pack("<II", len(body), 0x004E4942) + body)
    return out, {"faces": int(len(faces)), "vertices": int(count), "texture": texture, "bytes": len(out)}


def _vertex_normals(positions: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Flächengewichtete Normalen, falls das GLB keine mitbringt."""
    a, b, c = positions[faces[:, 0]], positions[faces[:, 1]], positions[faces[:, 2]]
    cross = np.cross(b - a, c - a)
    normals = np.zeros_like(positions)
    for corner in range(3):
        np.add.at(normals, faces[:, corner], cross)
    return normals

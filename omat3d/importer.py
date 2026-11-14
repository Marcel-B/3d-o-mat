"""Eigene 3D-Figuren importieren: GLB, glTF (mit .bin und Texturen daneben) und OBJ (mit .mtl und Texturen), einzeln,
als mehrere Dateien auf einmal oder als ZIP. Heraus kommt immer ein GLB mit einem einzigen Binärteil, wie TRELLIS.2 es
liefert, damit Skelett, Bewegungen, Gelenk-Editor und Druck nichts Neues lernen müssen.

Ein GLB mit Skelett, dessen Knochen Mixamo-Namen tragen (mit oder ohne "mixamorig:"), kann gleich in die
Bewegungsbibliothek; jedes andere Netz bekommt sein Skelett wie bisher von SkinTokens.
"""

from __future__ import annotations

import base64
import io
import json
import re
import struct
import zipfile
from pathlib import PurePosixPath

import numpy as np

from .animate import AnimationError, chunks, pack

MAX_FILES = 64
MAX_TOTAL = 200 * 1024 * 1024  # auch ausgepackt: ein ZIP darf nicht mehr werden als das
MAX_TRIANGLES = 3_000_000
# Python liest Accessors roh (Druck, Gelenk-Editor, Finger): gepackte Netze kann es nicht.
COMPRESSED = {"KHR_draco_mesh_compression", "EXT_meshopt_compression", "KHR_meshopt_compression"}
IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
MAIN = (".glb", ".gltf", ".obj")
# Was die Bewegungsbibliothek mindestens braucht; Finger, Schultern und Zehen fehlen manchen Figuren, das geht auch.
LIMBS = ("Hips", "Spine", "Head", "LeftArm", "LeftForeArm", "LeftHand", "RightArm", "RightForeArm", "RightHand",
         "LeftUpLeg", "LeftLeg", "LeftFoot", "RightUpLeg", "RightLeg", "RightFoot")
MIXAMO_PREFIX = re.compile(r"^mixamorig\d*[:_]?", re.IGNORECASE)


class ImportFailed(ValueError):
    """Eine Datei, aus der keine Figur wird; die Nachricht geht so auf die Seite."""


def import_model(files: dict[str, bytes]) -> tuple[bytes, dict]:
    """files: Dateiname -> Inhalt, wie hochgeladen (ein ZIP wird ausgepackt). Gibt das GLB und was dabei
    herauskam zurück: format, name, triangles, skeleton ("mixamo", "other" oder None), notes."""
    files = _unpack(files)
    mains = [name for name in files if PurePosixPath(name).suffix.lower() in MAIN]
    if not mains:
        raise ImportFailed("Keine GLB-, glTF- oder OBJ-Datei dabei")
    if len(mains) > 1:
        raise ImportFailed(f"Eine Figur je Import, hier sind es {len(mains)}: {', '.join(sorted(mains))}")
    main = mains[0]
    suffix = PurePosixPath(main).suffix.lower()
    notes: list[str] = []
    if suffix == ".glb":
        document, binary = _read_glb(files[main])
        glb = files[main] if _single_buffer(document) else _repack(document, [binary], _siblings(files, main), notes)
    elif suffix == ".gltf":
        try:
            document = json.loads(files[main].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ImportFailed(f"{main}: kein lesbares glTF ({error})") from None
        glb = _repack(document, [], _siblings(files, main), notes)
    else:
        glb = obj_to_glb(files[main], _siblings(files, main), notes)
    document, binary = _read_glb(glb)
    _check(document)
    info = {"format": suffix[1:], "name": PurePosixPath(main).stem[:80] or "figur",
            "triangles": _triangles(document), "skeleton": skeleton_kind(document), "notes": notes}
    if info["triangles"] == 0:
        raise ImportFailed("Die Datei hat keine Dreiecke")
    if info["triangles"] > MAX_TRIANGLES:
        raise ImportFailed(f"{info['triangles']:,} Dreiecke, mehr als {MAX_TRIANGLES:,}".replace(",", "."))
    if info["skeleton"] == "other":
        notes.append("Das Skelett hat keine Mixamo-Namen; SkinTokens kann ein neues einsetzen")
    return glb, info


# --- Dateien


def _unpack(files: dict[str, bytes]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    total = 0
    for name, data in files.items():
        if PurePosixPath(name).suffix.lower() == ".zip":
            try:
                archive = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                raise ImportFailed(f"{name}: kein lesbares ZIP") from None
            for entry in archive.infolist():
                path = PurePosixPath(entry.filename)
                if entry.is_dir() or "__MACOSX" in path.parts or path.name.startswith("."):
                    continue
                total += entry.file_size
                if total > MAX_TOTAL or len(out) >= MAX_FILES:
                    raise ImportFailed("Das ZIP ist ausgepackt zu groß")
                out[entry.filename] = archive.read(entry)
        else:
            total += len(data)
            out[name] = data
    if total > MAX_TOTAL:
        raise ImportFailed("Zusammen größer als 200 MB")
    return out


def _siblings(files: dict[str, bytes], main: str) -> dict[str, bytes]:
    """Die übrigen Dateien, auffindbar über ihren Pfad relativ zur Hauptdatei und über den bloßen Namen (klein
    geschrieben): Exporte verweisen oft mit anderem Ordner oder anderer Schreibung auf ihre Texturen."""
    base = PurePosixPath(main).parent
    found: dict[str, bytes] = {}
    for name, data in files.items():
        path = PurePosixPath(name)
        try:
            found[str(path.relative_to(base)).lower()] = data
        except ValueError:
            pass
        found.setdefault(path.name.lower(), data)
    return found


def _lookup(siblings: dict[str, bytes], uri: str) -> bytes | None:
    from urllib.parse import unquote
    uri = unquote(uri).replace("\\", "/")
    while uri.startswith("./"):
        uri = uri[2:]
    return siblings.get(uri.lower()) or siblings.get(PurePosixPath(uri).name.lower())


# --- glTF


def _read_glb(data: bytes) -> tuple[dict, bytes]:
    try:
        return chunks(data)
    except (AnimationError, UnicodeDecodeError, json.JSONDecodeError, struct.error) as error:
        raise ImportFailed(f"Kein lesbares GLB: {error}") from None


def _single_buffer(document: dict) -> bool:
    buffers = document.get("buffers") or []
    images = document.get("images") or []
    return len(buffers) <= 1 and not any("uri" in b for b in buffers) and not any("uri" in i for i in images)


def _repack(document: dict, glb_binary: list[bytes], siblings: dict[str, bytes], notes: list[str]) -> bytes:
    """Alle Puffer und Bilder (Datei daneben oder data:-URI) in einen Binärteil; so wird aus jedem glTF ein GLB."""
    data = bytearray()
    starts = []
    for index, buffer in enumerate(document.get("buffers") or []):
        if "uri" in buffer:
            content = _resolve(buffer["uri"], siblings, f"Puffer {index}")
        elif index == 0 and glb_binary:
            content = glb_binary[0]
        else:
            raise ImportFailed(f"Puffer {index} ohne Daten")
        data.extend(b"\0" * (-len(data) % 8))
        starts.append(len(data))
        data.extend(content[:buffer.get("byteLength", len(content))])
    for view in document.get("bufferViews") or []:
        if not 0 <= view.get("buffer", 0) < len(starts):
            raise ImportFailed("bufferView zeigt auf keinen Puffer")
        view["byteOffset"] = view.get("byteOffset", 0) + starts[view.get("buffer", 0)]
        view["buffer"] = 0
    views = document.setdefault("bufferViews", [])
    for index, image in enumerate(document.get("images") or []):
        if "uri" not in image:
            continue
        uri = image.pop("uri")
        try:
            content = _resolve(uri, siblings, f"Bild {index}")
        except ImportFailed as error:
            # Ein fehlendes Bild kostet die Textur, nicht die Figur.
            notes.append(str(error))
            content = _blank_png()
        content, mime = _web_image(content, uri)
        data.extend(b"\0" * (-len(data) % 4))
        views.append({"buffer": 0, "byteOffset": len(data), "byteLength": len(content)})
        data.extend(content)
        image["bufferView"] = len(views) - 1
        image["mimeType"] = mime
    document["buffers"] = [{"byteLength": len(data)}] if data else []
    if not data:
        document.pop("buffers")
    return pack(document, bytes(data))


def _resolve(uri: str, siblings: dict[str, bytes], what: str) -> bytes:
    if uri.startswith("data:"):
        head, _, payload = uri.partition(",")
        try:
            return base64.b64decode(payload) if head.endswith(";base64") else payload.encode()
        except ValueError:
            raise ImportFailed(f"{what}: data-URI nicht lesbar") from None
    content = _lookup(siblings, uri)
    if content is None:
        raise ImportFailed(f"{what}: {uri} fehlt, bitte mit hochladen")
    return content


def _web_image(content: bytes, name: str) -> tuple[bytes, str]:
    """PNG und JPEG bleiben, wie sie sind; alles andere (TGA, BMP, TIFF …) wird PNG, das glTF kennt nur die zwei."""
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return content, "image/png"
    if content[:3] == b"\xff\xd8\xff":
        return content, "image/jpeg"
    from PIL import Image
    try:
        picture = Image.open(io.BytesIO(content))
        picture.load()
    except Exception:  # noqa: BLE001 – Pillow wirft je nach Format anderes
        raise ImportFailed(f"{name}: Bild nicht lesbar") from None
    out = io.BytesIO()
    picture.convert("RGBA" if "A" in picture.getbands() else "RGB").save(out, "PNG")
    return out.getvalue(), "image/png"


def _blank_png() -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (1, 1), (200, 200, 200)).save(out, "PNG")
    return out.getvalue()


def _check(document: dict) -> None:
    required = set(document.get("extensionsRequired") or [])
    if required & COMPRESSED:
        raise ImportFailed("Das Netz ist komprimiert (Draco/Meshopt); bitte ohne Kompression exportieren")
    if not document.get("meshes"):
        raise ImportFailed("Die Datei hat kein Netz")
    count = len(document.get("accessors") or [])
    for mesh in document["meshes"]:
        for primitive in mesh.get("primitives") or []:
            position = (primitive.get("attributes") or {}).get("POSITION")
            if not isinstance(position, int) or not 0 <= position < count:
                raise ImportFailed("Ein Netz ohne Punkte")


def _triangles(document: dict) -> int:
    accessors = document.get("accessors") or []
    total = 0
    for mesh in document.get("meshes") or []:
        for primitive in mesh.get("primitives") or []:
            if primitive.get("mode", 4) != 4:
                continue
            index = primitive.get("indices", primitive["attributes"]["POSITION"])
            total += accessors[index].get("count", 0) // 3
    return total


def _plain(name: str | None) -> str:
    return MIXAMO_PREFIX.sub("", name or "")


def skeleton_kind(document: dict) -> str | None:
    """"mixamo", wenn ein Skelett alle Knochen hat, die die Bewegungsbibliothek braucht; "other" für ein Skelett mit
    anderen Namen (Blender-Rigify, Unreal, bone_N …); None ohne Skelett."""
    skins = document.get("skins") or []
    if not skins:
        return None
    nodes = document.get("nodes") or []
    names = {_plain(nodes[j].get("name")) for skin in skins for j in skin.get("joints") or [] if 0 <= j < len(nodes)}
    return "mixamo" if all(limb in names for limb in LIMBS) else "other"


def without_skin(glb: bytes) -> bytes:
    """Das Netz ohne Skelett und Animationen, für SkinTokens, wenn eine Figur ein fremdes Skelett mitbringt. Die
    Knochen bleiben als leere Knoten stehen; die Gewichte fallen weg, damit niemand das alte Skelett mitliest."""
    try:
        document, binary = chunks(glb)
    except (AnimationError, UnicodeDecodeError, json.JSONDecodeError, struct.error):
        return glb  # was es ist, sagt dann SkinTokens
    if not document.get("skins"):
        return glb
    document.pop("skins", None)
    document.pop("animations", None)
    for node in document.get("nodes") or []:
        node.pop("skin", None)
    for mesh in document.get("meshes") or []:
        for primitive in mesh.get("primitives") or []:
            attributes = primitive.get("attributes") or {}
            for key in [k for k in attributes if k.startswith(("JOINTS_", "WEIGHTS_"))]:
                del attributes[key]
    return pack(document, binary)


# --- OBJ


def obj_to_glb(text: bytes, siblings: dict[str, bytes], notes: list[str]) -> bytes:
    """Ein OBJ als GLB: je Material ein Primitive, Vielecke als Fächer in Dreiecke, Texturen aus der .mtl eingebettet.
    Fehlen Normalen, rechnet three.js sie nicht von selbst nach; also hier, gemittelt über die Flächen je Punkt."""
    positions, uvs, normals, colours = [], [], [], []
    groups: dict[str, list] = {}
    material = ""
    libraries: list[str] = []
    for raw in text.decode("utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line[0] == "#":
            continue
        word, _, rest = line.partition(" ")
        parts = rest.split()
        if word == "v" and len(parts) >= 3:
            positions.append([float(p) for p in parts[:3]])
            if len(parts) >= 6:
                colours.append([float(p) for p in parts[3:6]])
        elif word == "vt" and len(parts) >= 2:
            uvs.append([float(parts[0]), float(parts[1])])
        elif word == "vn" and len(parts) >= 3:
            normals.append([float(p) for p in parts[:3]])
        elif word == "f" and len(parts) >= 3:
            corners = []
            for part in parts:
                fields = (part.split("/") + ["", ""])[:3]
                corners.append(tuple(_index(f, n) for f, n in zip(fields, (len(positions), len(uvs), len(normals)))))
            faces = groups.setdefault(material, [])
            for i in range(1, len(corners) - 1):
                faces.append((corners[0], corners[i], corners[i + 1]))
        elif word == "usemtl":
            material = rest.strip()
        elif word == "mtllib":
            libraries.append(rest.strip())
    if not positions or not any(groups.values()):
        raise ImportFailed("Das OBJ hat keine Flächen")
    positions_np = np.asarray(positions, np.float32)
    uvs_np = np.asarray(uvs, np.float32).reshape(-1, 2)
    normals_np = np.asarray(normals, np.float32).reshape(-1, 3)
    colours_np = np.asarray(colours, np.float32) if len(colours) == len(positions) else None
    materials = _mtl(libraries, siblings, notes)

    data = bytearray()
    document: dict = {"asset": {"version": "2.0", "generator": "3d-o-mat import"}, "scene": 0,
                      "scenes": [{"nodes": [0]}], "nodes": [{"name": "Figur", "mesh": 0}],
                      "meshes": [{"primitives": []}], "accessors": [], "bufferViews": [], "materials": []}
    textures: dict[str, int] = {}

    def view(array: np.ndarray, target: int | None = None) -> int:
        data.extend(b"\0" * (-len(data) % 4))
        entry = {"buffer": 0, "byteOffset": len(data), "byteLength": array.nbytes}
        if target:
            entry["target"] = target
        data.extend(array.tobytes())
        document["bufferViews"].append(entry)
        return len(document["bufferViews"]) - 1

    def accessor(array: np.ndarray, kind: str, component: int = 5126, target: int = 34962, bounds: bool = False) -> int:
        entry = {"bufferView": view(array, target), "componentType": component, "count": len(array), "type": kind}
        if bounds:
            entry.update(min=array.min(axis=0).tolist(), max=array.max(axis=0).tolist())
        document["accessors"].append(entry)
        return len(document["accessors"]) - 1

    for name, faces in groups.items():
        if not faces:
            continue
        corners = np.asarray(faces, np.int64).reshape(-1, 3)
        # Jede Kombination aus Punkt, UV und Normale wird ein eigener Punkt im glTF.
        unique, inverse = np.unique(corners, axis=0, return_inverse=True)
        inverse = inverse.reshape(-1)
        p = positions_np[_valid(unique[:, 0], len(positions_np), "Punkt")]
        attributes = {"POSITION": accessor(p, "VEC3", bounds=True)}
        if (unique[:, 2] >= 0).all() and len(normals_np):
            n = normals_np[_valid(unique[:, 2], len(normals_np), "Normale")]
        else:
            n = _smooth_normals(positions_np, corners[:, 0].reshape(-1, 3))[unique[:, 0]]
        attributes["NORMAL"] = accessor(_unit(n), "VEC3")
        if (unique[:, 1] >= 0).all() and len(uvs_np):
            uv = uvs_np[_valid(unique[:, 1], len(uvs_np), "UV")].copy()
            uv[:, 1] = 1.0 - uv[:, 1]  # OBJ zählt v von unten, glTF von oben
            attributes["TEXCOORD_0"] = accessor(uv, "VEC2")
        if colours_np is not None:
            attributes["COLOR_0"] = accessor(np.clip(colours_np[unique[:, 0]], 0, 1), "VEC3")
        large = len(unique) > 65535
        indices = inverse.astype(np.uint32 if large else np.uint16)
        primitive = {"attributes": attributes,
                     "indices": accessor(indices, "SCALAR", 5125 if large else 5123, target=34963),
                     "material": _material(document, materials.get(name, {}), name, textures, view, siblings, notes,
                                           "TEXCOORD_0" in attributes)}
        document["meshes"][0]["primitives"].append(primitive)
    if not document.get("images"):
        document.pop("textures", None)
        document.pop("samplers", None)
    document["buffers"] = [{"byteLength": len(data)}]
    return pack(document, bytes(data))


def _index(field: str, count: int) -> int:
    if not field:
        return -1
    value = int(field)
    return value - 1 if value > 0 else count + value


def _valid(index: np.ndarray, count: int, what: str) -> np.ndarray:
    if len(index) and (index.min() < 0 or index.max() >= count):
        raise ImportFailed(f"Das OBJ verweist auf einen {what}, den es nicht gibt")
    return index


def _unit(vectors: np.ndarray) -> np.ndarray:
    length = np.linalg.norm(vectors, axis=1, keepdims=True)
    return np.where(length > 1e-12, vectors / np.maximum(length, 1e-12), [0.0, 1.0, 0.0]).astype(np.float32)


def _smooth_normals(positions: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    _valid(triangles.reshape(-1), len(positions), "Punkt")
    a, b, c = (positions[triangles[:, i]] for i in range(3))
    face = np.cross(b - a, c - a)  # nach Fläche gewichtet
    out = np.zeros_like(positions)
    for i in range(3):
        np.add.at(out, triangles[:, i], face)
    return out


def _mtl(libraries: list[str], siblings: dict[str, bytes], notes: list[str]) -> dict[str, dict]:
    materials: dict[str, dict] = {}
    for library in libraries:
        content = _lookup(siblings, library)
        if content is None:
            notes.append(f"{library} fehlt, die Figur bleibt ohne Farben")
            continue
        current = None
        for raw in content.decode("utf-8", errors="replace").splitlines():
            line = raw.strip()
            word, _, rest = line.partition(" ")
            if word == "newmtl":
                current = materials.setdefault(rest.strip(), {})
            elif current is None:
                continue
            elif word == "Kd":
                current["colour"] = [float(v) for v in rest.split()[:3]]
            elif word == "d":
                current["opacity"] = float(rest.split()[0])
            elif word == "Tr":
                current["opacity"] = 1.0 - float(rest.split()[0])
            elif word == "map_Kd":
                current["texture"] = _map_name(rest)
    return materials


def _map_name(rest: str) -> str:
    """Der Dateiname hinter map_Kd, ohne Optionen wie -s 1 1 1 oder -bm 0.5 davor; Namen mit Leerzeichen bleiben ganz."""
    words = rest.split()
    takes = {"-blendu": 1, "-blendv": 1, "-bm": 1, "-boost": 1, "-cc": 1, "-clamp": 1, "-imfchan": 1, "-mm": 2,
             "-o": 3, "-s": 3, "-t": 3, "-texres": 1, "-type": 1}
    i = 0
    while i < len(words) and words[i] in takes:
        step = takes[words[i]]
        i += 1
        while step and i < len(words) and re.fullmatch(r"-?[\d.]+|on|off|[rgbmlz]", words[i]):
            i += 1
            step -= 1
    return " ".join(words[i:])


def _material(document, spec, name, textures, view, siblings, notes, has_uv) -> int:
    colour = spec.get("colour", [0.8, 0.8, 0.8])
    pbr = {"baseColorFactor": [*colour, spec.get("opacity", 1.0)], "metallicFactor": 0.0, "roughnessFactor": 0.8}
    entry = {"name": name or "Material", "pbrMetallicRoughness": pbr, "doubleSided": True}
    if spec.get("opacity", 1.0) < 1.0:
        entry["alphaMode"] = "BLEND"
    texture = spec.get("texture")
    if texture and has_uv:
        if texture not in textures:
            content = _lookup(siblings, texture)
            if content is None:
                notes.append(f"Textur {texture} fehlt, bitte mit hochladen")
                textures[texture] = -1
            else:
                content, mime = _web_image(content, texture)
                images = document.setdefault("images", [])
                images.append({"bufferView": view(np.frombuffer(content, np.uint8)), "mimeType": mime})
                document.setdefault("samplers", [{"magFilter": 9729, "minFilter": 9987}])
                document.setdefault("textures", []).append({"source": len(images) - 1, "sampler": 0})
                textures[texture] = len(document["textures"]) - 1
        if textures[texture] >= 0:
            # Mit Textur nimmt glTF die Farbe mal; die Kd einer Textur ist meist nur ein Grau aus dem Export.
            pbr["baseColorFactor"] = [1.0, 1.0, 1.0, spec.get("opacity", 1.0)]
            pbr["baseColorTexture"] = {"index": textures[texture]}
    document["materials"].append(entry)
    return len(document["materials"]) - 1

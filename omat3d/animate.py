"""Die Proben des Prüfstands als glTF-Animationen ins GLB mit Skelett, damit Godot (oder Blender) sie mitbekommt.

Die Seite rechnet die Keyframes aus (frontend/src/rig/stage.ts, bake) und schickt sie her; hier werden sie an das
vorhandene GLB angehängt: Daten hinten an den Binärteil, dazu Accessors und je Probe eine Animation. Alles andere,
Netz, Texturen, Skelett, bleibt Byte für Byte, wie SkinTokens es geschrieben hat.
"""
import json
import math
import struct

MAX_CLIPS = 12
MAX_FRAMES = 1000
SIZES = {"rotation": 4, "translation": 3}


class AnimationError(ValueError):
    pass


def _chunks(glb: bytes) -> tuple[dict, bytes]:
    if len(glb) < 20 or glb[:4] != b"glTF":
        raise AnimationError("keine GLB-Datei")
    _magic, version, length = struct.unpack_from("<4sII", glb, 0)
    if version != 2 or length > len(glb):
        raise AnimationError("GLB in unbekannter Form")
    offset, document, binary = 12, None, b""
    while offset + 8 <= length:
        size, kind = struct.unpack_from("<II", glb, offset)
        data = glb[offset + 8:offset + 8 + size]
        if kind == 0x4E4F534A:
            document = json.loads(data.decode("utf-8"))
        elif kind == 0x004E4942:
            binary = data
        offset += 8 + size
    if document is None:
        raise AnimationError("GLB ohne JSON-Teil")
    return document, binary


def _pad(data: bytes, fill: bytes) -> bytes:
    return data + fill * (-len(data) % 4)


def _check(clips, nodes: int) -> None:
    if not isinstance(clips, list) or not 0 < len(clips) <= MAX_CLIPS:
        raise AnimationError(f"1 bis {MAX_CLIPS} Proben")
    for clip in clips:
        if not isinstance(clip, dict):
            raise AnimationError("Probe ist kein Objekt")
        times = clip.get("times")
        if not isinstance(clip.get("name"), str) or not clip["name"].strip() or len(clip["name"]) > 60:
            raise AnimationError("Probe ohne Namen")
        if not isinstance(times, list) or not 2 <= len(times) <= MAX_FRAMES \
                or not all(isinstance(t, (int, float)) for t in times) or any(b <= a for a, b in zip(times, times[1:])):
            raise AnimationError(f"{clip['name']}: Zeiten müssen aufsteigen, 2 bis {MAX_FRAMES}")
        for track in clip.get("tracks") or []:
            size = SIZES.get(track.get("path"))
            node, values = track.get("node"), track.get("values")
            if size is None or not isinstance(node, int) or not 0 <= node < nodes:
                raise AnimationError(f"{clip['name']}: unbekannter Knoten oder Pfad")
            if not isinstance(values, list) or len(values) != size * len(times) \
                    or not all(isinstance(v, (int, float)) for v in values):
                raise AnimationError(f"{clip['name']}: Werte passen nicht zu den Zeiten")


def _trs(matrix: list) -> dict:
    """Ein Knoten mit "matrix" darf nicht animiert werden (glTF 2.0, 5.11.1); also in translation, rotation und
    scale zerlegen. Spaltenweise wie im glTF, ohne Scherung."""
    column = [matrix[0:3], matrix[4:7], matrix[8:11]]
    scale = [math.sqrt(sum(v * v for v in c)) or 1.0 for c in column]
    det = (column[0][0] * (column[1][1] * column[2][2] - column[2][1] * column[1][2])
           - column[1][0] * (column[0][1] * column[2][2] - column[2][1] * column[0][2])
           + column[2][0] * (column[0][1] * column[1][2] - column[1][1] * column[0][2]))
    if det < 0:
        scale[0] = -scale[0]
    (m00, m10, m20), (m01, m11, m21), (m02, m12, m22) = ([v / scale[i] for v in c] for i, c in enumerate(column))
    trace = m00 + m11 + m22
    if trace > 0:
        k = 0.5 / math.sqrt(trace + 1)
        q = [(m21 - m12) * k, (m02 - m20) * k, (m10 - m01) * k, 0.25 / k]
    elif m00 > m11 and m00 > m22:
        k = 2 * math.sqrt(1 + m00 - m11 - m22)
        q = [0.25 * k, (m01 + m10) / k, (m02 + m20) / k, (m21 - m12) / k]
    elif m11 > m22:
        k = 2 * math.sqrt(1 + m11 - m00 - m22)
        q = [(m01 + m10) / k, 0.25 * k, (m12 + m21) / k, (m02 - m20) / k]
    else:
        k = 2 * math.sqrt(1 + m22 - m00 - m11)
        q = [(m02 + m20) / k, (m12 + m21) / k, 0.25 * k, (m10 - m01) / k]
    return {"translation": list(matrix[12:15]), "rotation": q, "scale": scale}


def add_animations(glb: bytes, clips: list) -> bytes:
    """clips: [{"name", "times": [s], "tracks": [{"node", "path": "rotation"|"translation", "values": [...]}]}]."""
    document, binary = _chunks(glb)
    _check(clips, len(document.get("nodes") or []))
    if len(document.get("buffers") or []) > 1 or (document.get("buffers") and "uri" in document["buffers"][0]):
        raise AnimationError("GLB mit externen Puffern")
    for clip in clips:
        for track in clip.get("tracks") or []:
            node = document["nodes"][track["node"]]
            if "matrix" in node:
                node.update(_trs(node.pop("matrix")))
    data = bytearray(_pad(binary, b"\0"))
    views = document.setdefault("bufferViews", [])
    accessors = document.setdefault("accessors", [])
    animations = document.setdefault("animations", [])

    def accessor(values: list, kind: str, bounds: bool = False) -> int:
        offset = len(data)
        data.extend(struct.pack(f"<{len(values)}f", *values))
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(values) * 4})
        count = len(values) // {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[kind]
        entry = {"bufferView": len(views) - 1, "componentType": 5126, "count": count, "type": kind}
        if bounds:  # glTF verlangt min/max für die Zeiten
            entry.update(min=[min(values)], max=[max(values)])
        accessors.append(entry)
        return len(accessors) - 1

    for clip in clips:
        times = accessor([float(t) for t in clip["times"]], "SCALAR", bounds=True)
        samplers, channels = [], []
        for track in clip.get("tracks") or []:
            kind = "VEC4" if track["path"] == "rotation" else "VEC3"
            samplers.append({"input": times, "output": accessor([float(v) for v in track["values"]], kind),
                             "interpolation": "LINEAR"})
            channels.append({"sampler": len(samplers) - 1, "target": {"node": track["node"], "path": track["path"]}})
        if channels:
            animations.append({"name": clip["name"].strip(), "samplers": samplers, "channels": channels})

    document["buffers"] = [{"byteLength": len(data)}]
    text = _pad(json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), b" ")
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(data), 0x004E4942) + bytes(data)
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body

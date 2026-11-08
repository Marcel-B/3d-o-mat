"""Moves joints of a rigged GLB: the bind pose changes, the mesh does not.

A joint SkinTokens put in the wrong place (a knee too low, shoulders too far in) bends the mesh in the wrong spot.
Moving it means giving the node a new rest position and recomputing the inverse bind matrices of every skin, so that
the figure looks exactly as before in its rest pose and deforms around the new joint when posed. The weights stay as
SkinTokens set them: for small moves that is what one wants, for large ones they would have to be recomputed.
"""

from __future__ import annotations

import struct

import numpy as np

from .animate import AnimationError, chunks, pack
from .printout import COMPONENTS, TYPES, _matrix

MAX_MOVES = 200


class SkeletonError(ValueError):
    pass


def move_joints(glb: bytes, moves: dict[int, list[float]]) -> tuple[bytes, int]:
    """moves: node index -> new translation in the parent's space (as the node's ``translation``). Returns the GLB
    and how many joints changed. Nodes with a ``matrix`` are decomposed first, because glTF has no translation
    beside a matrix."""
    try:
        document, binary = chunks(glb)
    except AnimationError as error:
        raise SkeletonError(str(error)) from error
    nodes = document.get("nodes") or []
    joints = {j for skin in document.get("skins") or [] for j in skin.get("joints", [])}
    if not joints:
        raise SkeletonError("GLB ohne Skelett")
    if len(moves) > MAX_MOVES:
        raise SkeletonError(f"höchstens {MAX_MOVES} Gelenke auf einmal")
    changed = 0
    for index, translation in moves.items():
        if not isinstance(index, int) or index not in joints:
            raise SkeletonError(f"Knoten {index} ist kein Gelenk")
        if (not isinstance(translation, (list, tuple)) or len(translation) != 3
                or not all(isinstance(v, (int, float)) and abs(v) < 1e3 for v in translation)):
            raise SkeletonError(f"Lage von Knoten {index} unbrauchbar")
        node = nodes[index]
        if "matrix" in node:
            node.update(_decompose(node.pop("matrix")))
        new = [float(v) for v in translation]
        if np.allclose(new, node.get("translation", [0.0, 0.0, 0.0]), atol=1e-7):
            continue
        node["translation"] = new
        changed += 1
    if not changed:
        return glb, 0
    data = bytearray(binary)
    world = _world_matrices(nodes)
    for skin in document.get("skins") or []:
        _write_inverses(document, data, skin, world)
    return pack(document, bytes(data)), changed


def _world_matrices(nodes: list) -> list[np.ndarray]:
    parent = {child: i for i, node in enumerate(nodes) for child in node.get("children", [])}
    local = [_matrix(node) for node in nodes]
    world: list = [None] * len(nodes)

    def to_world(i: int) -> np.ndarray:
        if world[i] is None:
            world[i] = (to_world(parent[i]) if i in parent else np.eye(4)) @ local[i]
        return world[i]

    return [to_world(i) for i in range(len(nodes))]


def _write_inverses(document: dict, data: bytearray, skin: dict, world: list) -> None:
    """The inverse bind matrix of every joint, from its new world matrix, written over the old ones (same size)."""
    joints = skin.get("joints", [])
    inverses = np.array([np.linalg.inv(world[j]) for j in joints], np.float32)
    if "inverseBindMatrices" not in skin:
        raise SkeletonError("Skelett ohne Bindematrizen")
    accessor = document["accessors"][skin["inverseBindMatrices"]]
    if accessor.get("type") != "MAT4" or accessor.get("componentType") != 5126 or "bufferView" not in accessor:
        raise SkeletonError("Bindematrizen in unbekannter Form")
    view = document["bufferViews"][accessor["bufferView"]]
    if view.get("buffer", 0) != 0 or view.get("byteStride"):
        raise SkeletonError("Bindematrizen in unbekannter Form")
    if accessor["count"] != len(joints):
        raise SkeletonError("Bindematrizen passen nicht zu den Gelenken")
    offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    size = COMPONENTS["MAT4"] * np.dtype(TYPES[5126]).itemsize
    if offset + size * len(joints) > len(data):
        raise SkeletonError("Bindematrizen reichen über die Datei hinaus")
    # glTF stores matrices column by column; numpy row by row, so transpose before flattening.
    for k, matrix in enumerate(inverses):
        struct.pack_into("<16f", data, offset + k * size, *matrix.T.reshape(-1).tolist())
    accessor.pop("min", None)
    accessor.pop("max", None)


def _decompose(matrix: list) -> dict:
    m = np.array(matrix, float).reshape(4, 4).T
    translation = m[:3, 3]
    scale = np.linalg.norm(m[:3, :3], axis=0)
    turn = m[:3, :3] / np.where(scale == 0, 1, scale)
    trace = np.trace(turn)
    if trace > 0:
        s = np.sqrt(trace + 1.0) * 2
        q = [(turn[2, 1] - turn[1, 2]) / s, (turn[0, 2] - turn[2, 0]) / s, (turn[1, 0] - turn[0, 1]) / s, 0.25 * s]
    else:
        i = int(np.argmax(np.diag(turn)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = np.sqrt(1.0 + turn[i, i] - turn[j, j] - turn[k, k]) * 2
        q = [0.0, 0.0, 0.0, (turn[k, j] - turn[j, k]) / s]
        q[i] = 0.25 * s
        q[j] = (turn[j, i] + turn[i, j]) / s
        q[k] = (turn[k, i] + turn[i, k]) / s
    return {"translation": translation.tolist(), "rotation": [float(v) for v in q], "scale": scale.tolist()}

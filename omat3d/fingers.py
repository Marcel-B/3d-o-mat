"""Names the finger chains of a SkinTokens skeleton the Mixamo way.

SkinTokens finds the fingers (one chain of two to four bones per finger, hanging from the hand) but names only the
core of the skeleton: the chains stay ``bone_13`` or ``extra_LeftHand4``. Everything that works with fingers (the fist
sliders, Gemma's poses, the animations, Godot) looks for ``LeftHandThumb1`` and the like, so the chains are named here
by where they sit: the chain closest to the wrist is the thumb, the others count from the thumb outward (index,
middle, ring, little). One chain beside the thumb is a mitten and becomes the index finger; two become index and ring,
three index, middle and little.
"""

from __future__ import annotations

import math
import re

from .animate import AnimationError, chunks, pack

FINGERS = {1: ["Index"], 2: ["Index", "Ring"], 3: ["Index", "Middle", "Pinky"], 4: ["Index", "Middle", "Ring", "Pinky"]}
MIXAMO_FINGER = re.compile(r"^(mixamorig[:_]?)?(Left|Right)Hand(Thumb|Index|Middle|Ring|Pinky)\d$")
MAX_BONES = 4  # Mixamo: three joints and the tip


def name_fingers(glb: bytes) -> tuple[bytes, dict]:
    """Returns the GLB with the finger chains named and, per side, how many chains got a name (0 when the hand
    already had Mixamo fingers or no chains). The bytes are returned unchanged when nothing was renamed."""
    try:
        document, binary = chunks(glb)
    except AnimationError as error:
        raise FingerError(str(error)) from error
    nodes = document.get("nodes") or []
    named = {}
    for side in ("Left", "Right"):
        named[side] = _name_hand(nodes, side)
    if not any(named.values()):
        return glb, named
    return pack(document, binary), named


class FingerError(Exception):
    pass


def _name_hand(nodes: list, side: str) -> int:
    hand = next((i for i, node in enumerate(nodes) if _plain(node.get("name")) == f"{side}Hand"), None)
    if hand is None:
        return 0
    prefix = (nodes[hand].get("name") or "")[: -len(f"{side}Hand")]
    if any(MIXAMO_FINGER.match(nodes[c].get("name") or "") for c in nodes[hand].get("children", [])):
        return 0
    chains = []
    for child in nodes[hand].get("children", []):
        if "mesh" in nodes[child]:
            continue
        chain = [child]
        while len(chain) < MAX_BONES and len(nodes[chain[-1]].get("children", [])) == 1:
            chain.append(nodes[chain[-1]]["children"][0])
        chains.append(chain)
    if not chains:
        return 0
    root = {chain[0]: nodes[chain[0]].get("translation") or [0.0, 0.0, 0.0] for chain in chains}
    fingers = chains
    thumb = None
    if len(chains) >= 2:
        thumb = min(chains, key=lambda c: _length(root[c[0]]))
        fingers = [c for c in chains if c is not thumb]
        # The knuckles sit side by side across the hand; the thumb is off to one side of that row. Sorting them by
        # how far they sit toward the thumb (across the hand, not along it) gives index, middle, ring, little.
        centre = [sum(float(root[c[0]][k]) for c in fingers) / len(fingers) for k in range(3)]
        along = _unit(centre)
        toward = _without(_diff(root[thumb[0]], centre), along)
        if _length(toward) > 1e-6:
            fingers.sort(key=lambda c: -_dot(_diff(root[c[0]], centre), toward))
        else:
            fingers.sort(key=lambda c: _length(_diff(root[c[0]], root[thumb[0]])))
        _rename(nodes, thumb, f"{prefix}{side}HandThumb")
    names = FINGERS.get(min(len(fingers), 4), FINGERS[4])
    for chain, finger in zip(fingers, names):
        _rename(nodes, chain, f"{prefix}{side}Hand{finger}")
    return len(names) + (1 if thumb else 0)


def _rename(nodes: list, chain: list, stem: str) -> None:
    for k, index in enumerate(chain):
        nodes[index]["name"] = f"{stem}{k + 1}"


def _plain(name) -> str:
    return re.sub(r"^mixamorig[:_]?", "", name or "")


def _length(v: list) -> float:
    return math.sqrt(sum(float(x) * float(x) for x in v))


def _diff(a: list, b: list) -> list:
    return [float(x) - float(y) for x, y in zip(a, b)]


def _dot(a: list, b: list) -> float:
    return sum(float(x) * float(y) for x, y in zip(a, b))


def _unit(v: list) -> list:
    length = _length(v) or 1.0
    return [float(x) / length for x in v]


def _without(v: list, direction: list) -> list:
    """v without its part along direction (a unit vector)."""
    d = _dot(v, direction)
    return [x - d * y for x, y in zip(v, direction)]

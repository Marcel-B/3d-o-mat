"""Was Gemma (über die Schranke, auf oi-o-mat) für 3d-o-mat tut:

- ein Bild prüfen, bevor es 5 bis 10 Minuten in TRELLIS.2 geht: ein einzelnes Objekt, ganz im Bild, freigestellt?
- aus einer Idee Bild-Prompts machen, die ein Bildmodell zu einer guten Vorlage für TRELLIS.2 macht.

Die Bilder selbst erzeugt 3d-o-mat noch nicht; das kommt über einen eigenen Bilddienst (img-o-mat, FLUX, Krea 2,
Z-Image-Turbo), den auch Tonwerk nutzt. Bis dahin sind die Prompts zum Kopieren.
"""
import base64
import io
import json
import re

from PIL import Image

from .gate import Gate

CHECK_PROMPT = """Du prüfst ein Bild, bevor es ein Bild-zu-3D-Modell (TRELLIS.2) bekommt. Gut ist: genau ein Objekt,
vollständig im Bild, nicht abgeschnitten, nicht verdeckt, scharf, gleichmäßig beleuchtet, freigestellt oder vor
schlichtem Hintergrund, eher schräg von vorn als genau von vorn. Schlecht sind Szenen, mehrere Objekte, Text als
Hauptsache, Spiegelungen, Glas und durchsichtige Teile, Bewegungsunschärfe.
Antworte nur mit JSON: {"suitable": true|false, "object": "<was es ist, kurz>", "problems": ["<kurz, deutsch>"],
"tips": ["<kurz, deutsch>"]}"""

PROMPT_PROMPT = """Du schreibst Prompts für ein Bildmodell (FLUX). Das Bild dient als Vorlage für ein Bild-zu-3D-Modell.
Daher immer: genau ein Objekt, vollständig sichtbar und mittig, Dreiviertelansicht leicht von oben, weicher
Studio-Licht, schlichter hellgrauer Hintergrund, kein Boden-Schatten, keine weiteren Gegenstände, kein Text,
scharf, Produktfoto. Schreib die Prompts auf Englisch, je 1 bis 3 Sätze, mit Material und Farben.
Antworte nur mit JSON: {"prompts": ["…", "…", "…"], "note": "<ein kurzer deutscher Satz, was du betont hast>"}"""


def _json(text: str) -> dict:
    """Gemma antwortet manchmal mit ```json … ```; das Objekt darin."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"Gemma antwortete ohne JSON: {text[:200]}")
    return json.loads(match.group(0))


def _preview(png: bytes, side: int = 768) -> str:
    """Verkleinert, auf Weiß, als JPEG: spart Kontext und Zeit, Gemma braucht keine 1024 px mit Alpha."""
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    image.thumbnail((side, side))
    flat = Image.new("RGB", image.size, (240, 240, 240))
    flat.paste(image, mask=image.getchannel("A"))
    buffer = io.BytesIO()
    flat.save(buffer, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def check_image(gate: Gate, model: str, png: bytes) -> dict:
    answer = gate.chat(model, [{"role": "user", "content": [
        {"type": "text", "text": CHECK_PROMPT},
        {"type": "image_url", "image_url": {"url": _preview(png)}},
    ]}], max_tokens=500, temperature=0.1)
    result = _json(answer)
    return {
        "suitable": bool(result.get("suitable")),
        "object": str(result.get("object") or "")[:120],
        "problems": [str(item)[:200] for item in result.get("problems") or []][:6],
        "tips": [str(item)[:200] for item in result.get("tips") or []][:6],
    }


def image_prompts(gate: Gate, model: str, idea: str) -> dict:
    idea = idea.strip()[:1000]
    if not idea:
        raise ValueError("Keine Idee angegeben")
    answer = gate.chat(model, [
        {"role": "system", "content": PROMPT_PROMPT},
        {"role": "user", "content": idea},
    ], max_tokens=900, temperature=0.8)
    result = _json(answer)
    prompts = [str(item).strip() for item in result.get("prompts") or [] if str(item).strip()][:4]
    if not prompts:
        raise ValueError("Gemma lieferte keine Prompts")
    return {"idea": idea, "prompts": prompts, "note": str(result.get("note") or "")[:300]}

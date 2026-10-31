"""Was Gemma (über die Schranke, auf oi-o-mat) für 3d-o-mat tut:

- ein Bild prüfen, bevor es 5 bis 10 Minuten in TRELLIS.2 geht: ein einzelnes Objekt, ganz im Bild, freigestellt?
- aus einer Idee Bild-Prompts machen, die ein Bildmodell zu einer guten Vorlage für TRELLIS.2 macht.
- aus einem Satz eine Haltung für eine Figur mit Skelett machen, etwa für den 3D-Druck.

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
Antworte nur mit JSON: {"suitable": true|false, "object": "<was es ist, kurz, {sprache}>", "problems": ["<kurz, {sprache}>"],
"tips": ["<kurz, {sprache}>"]}"""

PROMPT_PROMPT = """Du schreibst Prompts für ein Bildmodell (FLUX). Das Bild dient als Vorlage für ein Bild-zu-3D-Modell.
Daher immer: genau ein Objekt, vollständig sichtbar und mittig, Dreiviertelansicht leicht von oben, weicher
Studio-Licht, schlichter hellgrauer Hintergrund, kein Boden-Schatten, keine weiteren Gegenstände, kein Text,
scharf, Produktfoto. Schreib die Prompts auf Englisch, je 1 bis 3 Sätze, mit Material und Farben.
Antworte nur mit JSON: {"prompts": ["…", "…", "…"], "note": "<ein kurzer Satz auf {sprache}, was du betont hast>"}"""

POSE_PROMPT = """Du stellst eine menschliche 3D-Figur in eine Haltung. Sie steht anfangs gerade, Arme seitlich in
T- oder A-Pose, und schaut nach vorn. Du gibst Winkel in Grad für diese Knochen an, jeweils [x, y, z]:
Hips, Spine, Spine1, Spine2, Neck, Head: x = nach vorn neigen, y = nach links drehen, z = zur linken Seite neigen.
LeftArm, RightArm (Oberarm): z = vom Körper abspreizen, 0 = hängt am Körper, 90 = waagrecht zur Seite, 170 = nach oben;
  x = nach vorn heben (90 = waagrecht nach vorn, negativ = nach hinten); y = drehen.
LeftForeArm, RightForeArm: x = Ellbogen beugen (Unterarm kommt nach vorn, 0 bis 140);
  z = Unterarm nach außen und oben schwenken (bei waagrecht abgespreiztem Oberarm: 90 = Unterarm zeigt nach oben).
LeftUpLeg, RightUpLeg: x = Bein nach vorn heben (negativ = nach hinten), z = nach außen spreizen.
LeftLeg, RightLeg: x = Knie beugen (0 bis 140). LeftFoot, RightFoot: x = Zehen heben.
Links und rechts sind aus Sicht der Figur. Lass Knochen weg, die gerade bleiben. Bleib in natürlichen Grenzen, damit
die Figur nicht knickt. Für den 3D-Druck sind ruhige Haltungen mit Armen nah am Körper und beiden Füßen am Boden am
besten; folge aber der Beschreibung. Beispiel „lässig stehen“: {"pose": {"Spine": [0, 5, 3], "Neck": [3, -8, 0],
"LeftArm": [5, 0, 8], "RightArm": [-5, 0, 10], "LeftForeArm": [15, 0, 0], "RightForeArm": [10, 0, 0],
"LeftUpLeg": [8, 0, 2], "LeftLeg": [14, 0, 0], "RightUpLeg": [0, 0, 3]}, "note": "…"}
Antworte nur mit JSON: {"pose": {"<Knochen>": [x, y, z], …}, "note": "<ein kurzer Satz auf {sprache}, was du gestellt hast>"}"""

POSE_BONES = ("Hips", "Spine", "Spine1", "Spine2", "Neck", "Head", "LeftArm", "RightArm", "LeftForeArm", "RightForeArm",
              "LeftUpLeg", "RightUpLeg", "LeftLeg", "RightLeg", "LeftFoot", "RightFoot")

# Die Oberfläche gibt es auf Deutsch und Englisch; Gemma antwortet in ihrer Sprache. Die Bild-Prompts bleiben englisch.
LANGUAGES = {"de": "Deutsch", "en": "Englisch"}


def _prompt(template: str, lang: str) -> str:
    # replace statt format: die Vorlagen enthalten JSON mit geschweiften Klammern.
    return template.replace("{sprache}", LANGUAGES.get(lang, LANGUAGES["de"]))


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


def check_image(gate: Gate, model: str, png: bytes, lang: str = "de") -> dict:
    answer = gate.chat(model, [{"role": "user", "content": [
        {"type": "text", "text": _prompt(CHECK_PROMPT, lang)},
        {"type": "image_url", "image_url": {"url": _preview(png)}},
    ]}], max_tokens=500, temperature=0.1)
    result = _json(answer)
    return {
        "suitable": bool(result.get("suitable")),
        "object": str(result.get("object") or "")[:120],
        "problems": [str(item)[:200] for item in result.get("problems") or []][:6],
        "tips": [str(item)[:200] for item in result.get("tips") or []][:6],
    }


def image_prompts(gate: Gate, model: str, idea: str, lang: str = "de") -> dict:
    idea = idea.strip()[:1000]
    if not idea:
        raise ValueError("Keine Idee angegeben")
    answer = gate.chat(model, [
        {"role": "system", "content": _prompt(PROMPT_PROMPT, lang)},
        {"role": "user", "content": idea},
    ], max_tokens=900, temperature=0.8)
    result = _json(answer)
    prompts = [str(item).strip() for item in result.get("prompts") or [] if str(item).strip()][:4]
    if not prompts:
        raise ValueError("Gemma lieferte keine Prompts")
    return {"idea": idea, "prompts": prompts, "note": str(result.get("note") or "")[:300]}


def pose_from_text(gate: Gate, model: str, text: str, lang: str = "de") -> dict:
    """Eine Haltung in den Winkeln, die stage.ts applyPose() versteht; unbekannte Knochen fallen weg, Winkel auf ±180°."""
    text = text.strip()[:500]
    if not text:
        raise ValueError("Keine Haltung beschrieben")
    answer = gate.chat(model, [
        {"role": "system", "content": _prompt(POSE_PROMPT, lang)},
        {"role": "user", "content": text},
    ], max_tokens=700, temperature=0.3)
    result = _json(answer)
    pose = {}
    for bone, angles in (result.get("pose") or {}).items():
        if bone in POSE_BONES and isinstance(angles, list) and len(angles) == 3 \
                and all(isinstance(a, (int, float)) and not isinstance(a, bool) for a in angles):
            pose[bone] = [max(-180.0, min(180.0, float(a))) for a in angles]
    if not pose:
        raise ValueError("Gemma lieferte keine Haltung")
    return {"text": text, "pose": pose, "note": str(result.get("note") or "")[:300]}

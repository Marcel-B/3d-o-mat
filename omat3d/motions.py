"""Die Bewegungsbibliothek: Aufnahmen aus zwei freien Sammlungen, die die Seite auf das Skelett einer Figur überträgt
(frontend/src/rig/retarget.ts).

- CMU Graphics Lab Motion Capture Database, in der BVH-Fassung von Bruce Hahne (T-Pose als erstes Bild, Knochennamen
  wie in MotionBuilder). Frei nutzbar, auch in verkauften Produkten; nur die Daten selbst darf man nicht verkaufen.
- Bandai Namco Research Motiondataset 1: 17 Bewegungen in bis zu 15 Stilen, CC BY-NC 4.0, also nur nichtkommerziell
  und mit Nennung.

`python -m omat3d.motions fetch <ordner>` holt die Aufnahmen aus catalogue() einmal (Docker: beim Bau des Images, siehe
Dockerfile), bringt sie auf 30 Bilder je Sekunde und schreibt sie mit index.json in den Ordner. Das erste Bild jeder
Datei ist die Ruhehaltung, an der die Seite die Knochen ausrichtet; die Bewegung beginnt beim zweiten. Bei CMU ist das
die T-Pose, die die BVH-Fassung voranstellt. Bandai Namco hat keine: Dort zeigen die Knochen ohne Drehung kreuz und
quer (die Achsen der Gelenke stecken in den Drehungen), deshalb wird jeder Aufnahme das erste Bild von REST_BANDAI
vorangestellt, ein ruhiges Stehen mit hängenden Armen. Die Website liest den Ordner nur (Library); die Aufnahmen sind kein Nutzerinhalt und liegen
deshalb nicht auf der T7. Nur Standardbibliothek: das läuft im Docker-Bau vor den Abhängigkeiten.
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

FPS = 30
# Die Quellen, auf einen Stand festgenagelt, damit ein Neubau dieselben Dateien bekommt.
SOURCES = {
    "cmu": {
        "name": "CMU Graphics Lab Motion Capture Database",
        "license": "frei, auch kommerziell (Daten nicht weiterverkaufen)",
        "licenseEn": "free, also commercially (do not resell the data)",
        "commercial": True,
        "credit": "Motion capture data from mocap.cs.cmu.edu (NSF EIA-0196217), BVH conversion by B. Hahne",
        "url": "http://mocap.cs.cmu.edu/",
        "base": "https://raw.githubusercontent.com/una-dinosauria/cmu-mocap/09a07f54f3bbb58797325f009282d0b2048a2871/data",
    },
    "bandai": {
        "name": "Bandai Namco Research Motiondataset",
        "license": "CC BY-NC 4.0, nur nichtkommerziell",
        "licenseEn": "CC BY-NC 4.0, non-commercial only",
        "commercial": False,
        "credit": "Bandai Namco Research Motiondataset (CC BY-NC 4.0), Bandai Namco Research Inc.",
        "url": "https://github.com/BandaiNamcoResearchInc/Bandai-Namco-Research-Motiondataset",
        "base": "https://raw.githubusercontent.com/BandaiNamcoResearchInc/Bandai-Namco-Research-Motiondataset/"
                "74ead3ba1ae4696404e6086233779f60de8bf9ef/dataset/Bandai-Namco-Research-Motiondataset-1/data",
    },
}

# Die Aufnahme, deren erstes Bild bei Bandai Namco als Ruhehaltung dient (alle haben dasselbe Skelett).
REST_BANDAI = "dataset-1_guide_normal_001.bvh"

CATEGORIES = {
    "walk": ("Gehen", "Walking"), "run": ("Laufen", "Running"), "jump": ("Springen", "Jumping"),
    "fight": ("Kämpfen", "Fighting"), "dance": ("Tanzen", "Dancing"), "gesture": ("Gesten", "Gestures"),
    "everyday": ("Alltag", "Everyday"), "acrobatics": ("Akrobatik", "Acrobatics"),
}

# Das Startset aus CMU: Aufnahme, deutscher und englischer Name, Kategorie. Ausgewählt nach dem Index der Sammlung.
CMU = [
    ("02_01", "Gehen", "Walk", "walk"),
    ("07_12", "Zügig gehen", "Brisk walk", "walk"),
    ("08_07", "Gehen mit großen Schritten", "Walk with long strides", "walk"),
    ("91_14", "Niedergeschlagen gehen", "Depressed walk", "walk"),
    ("91_33", "Macho-Gang", "Macho walk", "walk"),
    ("91_32", "Ängstlich gehen", "Scared walk", "walk"),
    ("91_20", "Schüchtern gehen", "Shy walk", "walk"),
    ("91_16", "Humpeln", "Limp", "walk"),
    ("91_19", "Marschieren", "March", "walk"),
    ("137_33", "Alter Mann", "Old man walk", "walk"),
    ("137_16", "Betrunken gehen", "Drunk walk", "walk"),
    ("137_42", "Kraftprotz", "Strongman walk", "walk"),
    ("104_41", "Zombie-Gang", "Zombie walk", "walk"),
    ("104_12", "Aufgeregt gehen", "Excited walk", "walk"),
    ("132_29", "Federnd gehen", "Bouncy walk", "walk"),
    ("17_03", "Schleichen", "Walk stealthily", "walk"),
    ("139_29", "Anschleichen", "Sneaking", "walk"),
    ("136_09", "Geduckt gehen", "Crouched walk", "walk"),
    ("136_25", "Rückwärts gehen", "Walk backwards", "walk"),
    ("143_40", "Seitwärts gehen", "Walk sideways", "walk"),
    ("02_03", "Joggen", "Jog", "run"),
    ("16_35", "Laufen", "Run", "run"),
    ("09_01", "Rennen", "Sprint", "run"),
    ("104_53", "Loslaufen", "Start running", "run"),
    ("143_02", "Rennen und anhalten", "Run to stop", "run"),
    ("127_25", "Rennen und drüberspringen", "Run and jump over", "run"),
    ("13_11", "Sprung nach vorn", "Forward jump", "jump"),
    ("118_01", "Sprung", "Jump", "jump"),
    ("16_03", "Hochsprung", "High jump", "jump"),
    ("91_46", "Drehsprung", "Jump turn", "jump"),
    ("49_02", "Hüpfen, auch auf einem Bein", "Jump up and down, hop on one foot", "jump"),
    ("75_15", "Weitsprünge", "Long jumps", "jump"),
    ("143_31", "Himmel und Hölle", "Hopscotch", "jump"),
    ("13_29", "Hampelmann und Kniebeugen", "Jumping jacks and squats", "jump"),
    ("13_17", "Boxen", "Boxing", "fight"),
    ("144_20", "Schlagfolge", "Punch sequence", "fight"),
    ("143_23", "Schlagen", "Punching", "fight"),
    ("135_04", "Fußtritt nach vorn", "Front kick", "fight"),
    ("143_24", "Treten", "Kicking", "fight"),
    ("75_16", "Sprungtritt", "Jump kick", "fight"),
    ("86_01", "Sprünge, Tritte und Schläge", "Jumps, kicks and punches", "fight"),
    ("02_07", "Schwertkampf", "Swordplay", "fight"),
    ("12_04", "Tai Chi", "Tai chi", "fight"),
    ("05_02", "Tanz mit Pirouette", "Dance with pirouette", "dance"),
    ("60_01", "Salsa", "Salsa", "dance"),
    ("55_02", "Lambada", "Lambada", "dance"),
    ("93_06", "Lindy Hop", "Lindy hop", "dance"),
    ("141_12", "Twist", "Twist", "dance"),
    ("90_28", "Breakdance", "Breakdance", "dance"),
    ("90_32", "Moonwalk", "Moonwalk", "dance"),
    ("143_34", "Hühnertanz", "Chicken dance", "dance"),
    ("140_06", "Stehen", "Idle", "everyday"),
    ("141_16", "Winken", "Wave hello", "gesture"),
    ("111_02", "Verbeugen", "Bow", "gesture"),
    ("13_26", "Verkehr regeln", "Direct traffic", "gesture"),
    ("143_18", "Hinsetzen und aufstehen", "Sit down and get up", "everyday"),
    ("114_05", "Auf dem Stuhl sitzen", "Sitting in a chair", "everyday"),
    ("82_05", "Auf dem Boden sitzen", "Sitting on the ground", "everyday"),
    ("115_06", "Kiste aus den Knien heben", "Pick up a box, bending knees", "everyday"),
    ("143_30", "Strecken und gähnen", "Stretch and yawn", "everyday"),
    ("113_24", "Werfen", "Throw", "everyday"),
    ("10_01", "Ball schießen", "Kick a ball", "everyday"),
    ("124_05", "Sprungwurf", "Jump shot", "everyday"),
    ("79_01", "Holz hacken", "Chop wood", "everyday"),
    ("113_19", "Treppe rauf und runter", "Walk up and down stairs", "everyday"),
    ("143_37", "Leiter rauf und runter", "Climb up and down a ladder", "everyday"),
    ("90_16", "Aufs Gesicht fallen", "Fall on face", "acrobatics"),
    ("87_03", "Rückwärtssalto", "Backflip", "acrobatics"),
    ("144_01", "Rad schlagen", "Cartwheels", "acrobatics"),
]

# Bandai Namco, Datensatz 1: Bewegung -> (deutsch, englisch, Kategorie, Anzahl Aufnahmen je Stil).
BANDAI = {
    "walk": ("Gehen", "Walk", "walk", 2), "walk-back": ("Rückwärts gehen", "Walk backwards", "walk", 1),
    "walk-left": ("Nach links gehen", "Walk left", "walk", 1), "walk-right": ("Nach rechts gehen", "Walk right", "walk", 1),
    "run": ("Laufen", "Run", "run", 1), "dash": ("Rennen", "Dash", "run", 1),
    "bow": ("Verbeugen", "Bow", "gesture", 1), "bye": ("Tschüss", "Bye", "gesture", 1),
    "byebye": ("Winken zum Abschied", "Wave goodbye", "gesture", 1), "guide": ("Den Weg zeigen", "Show the way", "gesture", 1),
}
# Nur im normalen Stil.
BANDAI_NORMAL = {
    "call": ("Rufen", "Call", "gesture", 2), "respond": ("Antworten", "Respond", "gesture", 1),
    "punch": ("Schlagen", "Punch", "fight", 2), "kick": ("Treten", "Kick", "fight", 1), "slash": ("Hieb", "Slash", "fight", 2),
    "dance-short": ("Tanz, kurz", "Dance, short", "dance", 1), "dance-long": ("Tanz, lang", "Dance, long", "dance", 1),
}
STYLES = {
    "normal": ("normal", "normal"), "happy": ("fröhlich", "happy"), "sad": ("traurig", "sad"), "angry": ("wütend", "angry"),
    "proud": ("stolz", "proud"), "not-confident": ("unsicher", "not confident"), "masculinity": ("männlich", "masculine"),
    "feminine": ("weiblich", "feminine"), "childish": ("kindlich", "childish"), "old": ("alt", "old"),
    "tired": ("müde", "tired"), "active": ("lebhaft", "active"), "musical": ("wie im Musical", "musical"),
    "giant": ("wie ein Riese", "like a giant"), "chimpira": ("wie ein Halbstarker", "like a thug"),
}


def catalogue() -> list[dict]:
    """Alle Einträge der Bibliothek, noch ohne Länge (die kommt beim Holen dazu)."""
    entries = []
    for take, de, en, category in CMU:
        subject = take.split("_")[0]
        entries.append({"id": f"cmu-{take}", "source": "cmu", "de": de, "en": en, "category": category,
                        "style": None, "path": f"{int(subject):03d}/{take}.bvh", "ref": f"CMU {take}"})

    def bandai(content, de, en, category, count, styles):
        for style in styles:
            for n in range(1, count + 1):
                suffix = f" {n}" if count > 1 else ""
                entries.append({
                    "id": f"bandai-{content}-{style}-{n:03d}", "source": "bandai", "de": de + suffix, "en": en + suffix,
                    "category": category, "style": style, "styleDe": STYLES[style][0], "styleEn": STYLES[style][1],
                    "path": f"dataset-1_{content}_{style}_{n:03d}.bvh", "ref": f"Bandai Namco {content} {style} {n:03d}",
                })

    for content, (de, en, category, count) in BANDAI.items():
        bandai(content, de, en, category, count, STYLES)
    for content, (de, en, category, count) in BANDAI_NORMAL.items():
        bandai(content, de, en, category, count, ["normal"])
    return entries


class MotionError(ValueError):
    pass


def resample(text: str, rest: str | None = None, fps: int = FPS) -> tuple[str, int, float]:
    """Eine BVH-Datei auf fps Bilder je Sekunde bringen (jedes n-te Bild; die Sammlungen haben 120 oder 30), Zahlen auf
    vier Stellen. Das erste Bild bleibt als Ruhehaltung vorn; rest ersetzt es nicht, sondern kommt davor (eine Zeile
    Kanalwerte). Gibt Text, Bilder der Bewegung (ohne Ruhehaltung) und ihre Sekunden zurück."""
    head, frames, step = _parse(text)
    if rest is not None:
        if len(rest.split()) != len(frames[0].split()):
            raise MotionError("Ruhehaltung passt nicht zum Skelett")
        frames = [rest, *frames]
    # Manche Aufnahmen (CMU 07_12) beginnen nach der Ruhehaltung mit einem Bild, in dem alles null ist; das würde die
    # Figur im ersten Bild auf den Ursprung ziehen und die Lage der Hüfte falsch messen.
    first = 1
    while first < len(frames) - 1 and all(float(v) == 0 for v in frames[first].split()):
        first += 1
    every = max(1, round(1 / (step * fps)))
    kept = [frames[0], *frames[first::every]]
    if len(kept) < 3:
        raise MotionError("zu wenige Bilder")
    short = [" ".join(_number(v) for v in frame.split()) for frame in kept]
    seconds = (len(kept) - 2) / fps
    body = f"MOTION\nFrames: {len(kept)}\nFrame Time: {1 / fps:.7f}\n" + "\n".join(short) + "\n"
    return head.rstrip() + "\n" + body, len(kept) - 1, seconds


def first_frame(text: str) -> str:
    return _parse(text)[1][0]


def _parse(text: str) -> tuple[str, list[str], float]:
    head, sep, motion = text.partition("MOTION")
    if not sep or "HIERARCHY" not in head:
        raise MotionError("keine BVH-Datei")
    lines = [line.strip() for line in motion.strip().splitlines() if line.strip()]
    count = re.match(r"Frames:\s*(\d+)", lines[0] if lines else "")
    step = re.match(r"Frame Time:\s*([0-9.eE+-]+)", lines[1] if len(lines) > 1 else "")
    if not count or not step or float(step.group(1)) <= 0:
        raise MotionError("BVH ohne Frames oder Frame Time")
    frames = lines[2:2 + int(count.group(1))]
    if not frames:
        raise MotionError("BVH ohne Bilder")
    return head, frames, float(step.group(1))


def _number(value: str) -> str:
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def fetch(folder: str | Path, only: set[str] | None = None, opener=urllib.request.urlopen, log=print) -> dict:
    """Holt die Aufnahmen (only: nur diese ids) nach folder und schreibt index.json. Schon geholte bleiben."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    clips, failed = [], []
    rest = {}

    def rest_of(source: str) -> str | None:
        if source != "bandai":
            return None
        if source not in rest:
            rest[source] = first_frame(_download(f"{SOURCES[source]['base']}/{REST_BANDAI}", opener))
        return rest[source]

    for entry in catalogue():
        if only and entry["id"] not in only:
            continue
        target = folder / f"{entry['id']}.bvh"
        try:
            if not target.exists():
                url = f"{SOURCES[entry['source']]['base']}/{entry['path']}"
                raw = _download(url, opener)
                text, _frames, _seconds = resample(raw, rest_of(entry["source"]))
                target.write_text(text, encoding="utf-8")
            _text, frames, seconds = _measure(target.read_text(encoding="utf-8"))
        except (OSError, MotionError) as error:
            failed.append(entry["id"])
            log(f"{entry['id']}: {error}")
            continue
        clips.append({key: value for key, value in entry.items() if key != "path"} | {"frames": frames, "seconds": round(seconds, 2)})
    index = {"sources": {key: {k: v for k, v in value.items() if k != "base"} for key, value in SOURCES.items()},
             "categories": {key: {"de": de, "en": en} for key, (de, en) in CATEGORIES.items()}, "clips": clips}
    (folder / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{len(clips)} Aufnahmen in {folder}" + (f", {len(failed)} fehlen: {', '.join(failed)}" if failed else ""))
    return index


def _measure(text: str) -> tuple[str, int, float]:
    """Bilder und Sekunden einer geholten Datei, ohne die Ruhehaltung vorn."""
    frames = re.search(r"Frames:\s*(\d+)", text)
    step = re.search(r"Frame Time:\s*([0-9.eE+-]+)", text)
    if not frames or not step:
        raise MotionError("BVH ohne Frames")
    return text, int(frames.group(1)) - 1, (int(frames.group(1)) - 2) * float(step.group(1))


def _download(url: str, opener, tries: int = 4) -> str:
    for attempt in range(tries):
        try:
            with opener(urllib.request.Request(url, headers={"User-Agent": "3d-o-mat"}), timeout=60) as response:
                return response.read().decode("utf-8", "replace")
        except OSError:
            if attempt == tries - 1:
                raise
            time.sleep(2 ** (attempt + 1))
    raise OSError(url)


class Library:
    """Die geholte Bibliothek: index.json und die BVH-Dateien daneben. Fehlt der Ordner, ist sie leer."""

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self._index = None

    def index(self) -> dict:
        if self._index is None:
            try:
                self._index = json.loads((self.folder / "index.json").read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {"sources": {}, "categories": {}, "clips": []}
        return self._index

    def bvh(self, clip_id: str) -> str:
        if not re.fullmatch(r"[a-z0-9_-]{1,80}", clip_id) or not any(c["id"] == clip_id for c in self.index()["clips"]):
            raise KeyError(clip_id)
        return (self.folder / f"{clip_id}.bvh").read_text(encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "fetch":
        sys.exit("python -m omat3d.motions fetch <ordner> [id …]")
    result = fetch(sys.argv[2], set(sys.argv[3:]) or None)
    sys.exit(0 if result["clips"] else 1)

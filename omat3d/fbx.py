"""FBX für Unity und Unreal: dasselbe GLB, das die Seite herunterlädt (mit oder ohne angehängte Animationen), von
Blender umgewandelt.

Blender läuft ohne Oberfläche als eigener Prozess (`blender_fbx.py`), so bleibt 3d-o-mat MIT, obwohl Blender GPL ist.
Im Docker-Image liegt Blender unter /opt/blender (Dockerfile), sonst nennt `blender` in der config.json den Pfad.
Warum Blender und nicht assimp, das FBX SDK oder ein eigener Schreiber, steht in der README (FBX).
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

TARGETS = ("unity", "unreal")
SCRIPT = Path(__file__).resolve().with_name("blender_fbx.py")
# Eine Figur mit 500 000 Punkten braucht 5 bis 10 Sekunden; das hier fängt nur einen hängenden Blender ab.
TIMEOUT = 300
MARK = "OMAT3D-FEHLER "


class FbxError(ValueError):
    pass


class FbxUnavailable(FbxError):
    pass


def find_blender(configured: str) -> str | None:
    """Der Pfad zu Blender, oder None, wenn es hier keins gibt."""
    if not configured:
        return None
    if "/" in configured:
        return configured if Path(configured).is_file() else None
    return shutil.which(configured)


def convert(glb: bytes, target: str, blender: str | None, timeout: float = TIMEOUT) -> tuple[bytes, dict]:
    """GLB -> FBX; zurück kommen die Datei und Blenders Bericht (Knochen, Takes mit Bildern, Sekunden)."""
    if target not in TARGETS:
        raise FbxError(f"Ziel muss {' oder '.join(TARGETS)} sein")
    if not blender:
        raise FbxUnavailable("FBX braucht Blender, hier ist keins installiert (siehe README, FBX)")
    with tempfile.TemporaryDirectory(prefix="omat3d-fbx-") as folder:
        source, result, report = (Path(folder) / name for name in ("modell.glb", "modell.fbx", "bericht.json"))
        source.write_bytes(glb)
        command = [blender, "-b", "--factory-startup", "--python-exit-code", "3", "--python", str(SCRIPT),
                   "--", str(source), str(result), target, str(report)]
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=folder)
        except subprocess.TimeoutExpired:
            raise FbxError(f"Blender hat nach {timeout:.0f} s nicht fertig umgewandelt") from None
        except OSError as error:
            raise FbxUnavailable(f"Blender lässt sich nicht starten: {error}") from None
        output = (done.stdout or "") + (done.stderr or "")
        if done.returncode != 0 or not result.is_file() or not report.is_file():
            marked = [line[len(MARK):] for line in output.splitlines() if line.startswith(MARK)]
            tail = marked[-1] if marked else (output.strip().splitlines() or ["keine Ausgabe"])[-1]
            raise FbxError(f"Blender konnte das GLB nicht umwandeln: {tail[:300]}")
        return result.read_bytes(), json.loads(report.read_text(encoding="utf-8"))

"""Ablage auf der T7 von oi-o-mat (auf dem mac-mini eingebunden):

    <dataDir>/.3d-o-mat                      Kennzeichen: nur wenn es da ist, gilt das Laufwerk als verbunden
    <dataDir>/images/<id>/original.<ext>     das hochgeladene Bild
    <dataDir>/images/<id>/cutout.png         freigestellt, das bekommt TRELLIS.2
    <dataDir>/images/<id>/image.json         Name, Freistellung, Prüfung durch Gemma
    <dataDir>/jobs/<id>/job.json             ein Auftrag mit Zustand und Fortschritt
    <dataDir>/jobs/<id>/model.glb            das Ergebnis
"""
import json
import os
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

MARKER = ".3d-o-mat"
_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")


class NotMounted(RuntimeError):
    pass


def new_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def valid_id(value: str) -> bool:
    return bool(_ID.match(value or ""))


def write_json(path: Path, value) -> None:
    # Erst daneben schreiben, dann umbenennen: ein Abbruch hinterlässt nie eine halbe Datei.
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class Store:
    def __init__(self, root: Path, mount_command: str = "", log=print):
        self.root = Path(root)
        self.mount_command = mount_command
        self.log = log
        self._last_mount = 0.0
        self._lock = threading.Lock()

    @property
    def mounted(self) -> bool:
        return (self.root / MARKER).is_file()

    def ensure_mounted(self) -> None:
        """Wirft NotMounted, wenn die Freigabe fehlt; versucht höchstens einmal je Minute, sie einzubinden."""
        if self.mounted:
            return
        if self.mount_command and time.monotonic() - self._last_mount > 60:
            self._last_mount = time.monotonic()
            self.log(f"Datenlaufwerk fehlt, binde es ein: {self.mount_command}")
            try:
                subprocess.run(["/bin/sh", "-c", self.mount_command], timeout=60, check=False)
            except (OSError, subprocess.TimeoutExpired) as error:
                self.log(f"Einbinden ging nicht: {error}")
        if not self.mounted:
            raise NotMounted(f"Datenlaufwerk nicht verbunden: {self.root / MARKER} fehlt "
                             "(T7 auf oi-o-mat freigegeben und auf diesem Mac eingebunden?)")

    def dir(self, kind: str, item_id: str) -> Path:
        if kind not in ("images", "jobs") or not valid_id(item_id):
            raise KeyError(item_id)
        return self.root / kind / item_id

    def create(self, kind: str) -> tuple[str, Path]:
        self.ensure_mounted()
        with self._lock:
            while True:
                item_id = new_id()
                path = self.root / kind / item_id
                if not path.exists():
                    path.mkdir(parents=True)
                    return item_id, path

    def list(self, kind: str, name: str) -> list[dict]:
        base = self.root / kind
        if not self.mounted or not base.is_dir():
            return []
        items = []
        for path in sorted(base.iterdir(), reverse=True):
            try:
                items.append(read_json(path / name))
            except (OSError, ValueError):
                continue
        return items

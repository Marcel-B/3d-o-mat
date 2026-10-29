"""Bilder malen lassen: img-o-mat (FLUX.2 Klein, Z-Image Turbo) über die Schranke, gemalt auf oi-o-mat. Jedes fertige
Bild geht denselben Weg wie ein hochgeladenes (freistellen, auf die T7) und steht dann in der Bildliste.

Ein Malauftrag lebt nur im Speicher: Er dauert eine Minute, nicht zehn wie TRELLIS.2, und was zählt, sind die Bilder,
die er hinterlässt. Nach einem Neustart sind sie da, der Auftrag nicht mehr.
"""
import threading
import time

from .gate import Gate, GateBusy, GateRefused, StreamBroken
from .store import new_id

KEEP = 20
SIZES = ("1024x1024", "1024x768", "768x1024")


class Paintings:
    def __init__(self, gate: Gate, save_image, log=print):
        """save_image(data, name, extra) legt ein Bild an, wie ein hochgeladenes, und gibt sein Dokument zurück."""
        self.gate = gate
        self.save_image = save_image
        self.log = log
        self.lock = threading.Lock()
        self.items: dict[str, dict] = {}

    def all(self) -> list[dict]:
        with self.lock:
            return sorted((dict(item) for item in self.items.values()), key=lambda item: item["id"], reverse=True)

    def get(self, painting_id: str) -> dict | None:
        with self.lock:
            item = self.items.get(painting_id)
            return dict(item) if item else None

    def start(self, prompt: str, model: str, n: int = 1, seed: int | None = None, size: str = "1024x1024") -> dict:
        item = {"id": new_id(), "prompt": prompt, "model": model, "n": n, "seed": seed, "size": size,
                "state": "running", "stage": "queued", "place": None, "fraction": None,
                "imageIds": [], "message": None, "started": time.time(), "seconds": None}
        with self.lock:
            self.items[item["id"]] = item
            for old in sorted(self.items)[:-KEEP]:
                if self.items[old]["state"] != "running":
                    del self.items[old]
        threading.Thread(target=self._run, args=(item["id"],), daemon=True, name=f"paint-{item['id']}").start()
        return dict(item)

    def _update(self, painting_id: str, **values) -> None:
        with self.lock:
            self.items[painting_id].update(values)

    def _run(self, painting_id: str) -> None:
        item = self.get(painting_id)

        def progress(name: str, data: dict) -> None:
            if name == "queued":
                self._update(painting_id, stage="queued", place=data.get("place"))
            elif name == "progress":
                self._update(painting_id, stage=data.get("stage"), place=None, fraction=data.get("fraction"))

        def image(jpeg: bytes, info: dict) -> None:
            self._update(painting_id, stage="cutout")
            painted = {"model": item["model"], "prompt": item["prompt"], "seed": info.get("seed"),
                       "revisedPrompt": info.get("revised_prompt"), "paintSeconds": info.get("paintSeconds")}
            doc = self.save_image(jpeg, item["prompt"][:80], {"original": "original.jpg", "painted": painted})
            with self.lock:
                self.items[painting_id]["imageIds"].append(doc["id"])

        try:
            self.gate.paint(item["model"], item["prompt"], item["size"], item["seed"], item["n"], progress, image)
            state, message = "done", None
        except GateRefused as error:
            state, message = "failed", f"{error} (in der Oberfläche der Schranke /gate freigeben)"
        except GateBusy as error:
            state, message = "failed", f"oi-o-mat ist beschäftigt: {error}"
        except (StreamBroken, RuntimeError, OSError) as error:
            state, message = "failed", str(error)
        if message:
            self.log(f"Malen {painting_id}: {message}")
        self._update(painting_id, state=state, message=message, stage=None,
                     seconds=round(time.time() - item["started"], 1))

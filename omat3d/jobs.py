"""Die Aufträge: Bild zu 3D und Skelett einsetzen (kind "rig", aus dem GLB eines fertigen 3D-Auftrags), einer nach dem anderen (TRELLIS.2 braucht oi-o-mat ohnehin ganz). Jeder Auftrag liegt als
job.json auf der T7 (über store.py); nach einem Neustart der App werden unterbrochene Aufträge neu eingereiht.
"""
import threading
import time
from datetime import datetime

from .gate import Gate, GateBusy, GateRefused, StreamBroken
from .store import NotMounted, Store, StoreError, new_id

OPEN = ("queued", "waiting", "running")
# Wie oft ein Auftrag nach einem abgerissenen Stream oder einer 503 neu startet, bevor er als fehlgeschlagen gilt.
MAX_BROKEN = 3
MAX_BUSY = 12


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Jobs:
    def __init__(self, store: Store, gate: Gate, model: str, defaults: dict, log=print, rig_model: str = "skintokens"):
        self.store = store
        self.gate = gate
        self.model = model
        self.rig_model = rig_model
        self.defaults = defaults
        self.log = log
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.current = None
        self.recovered = False

    # --- Ablage

    def get(self, job_id: str) -> dict:
        return self.store.read("jobs", job_id)

    def save(self, job: dict) -> dict:
        return self.store.write("jobs", job)

    def update(self, job_id: str, **changes) -> dict:
        with self.lock:
            job = self.get(job_id)
            job.update(changes)
            return self.save(job)

    def all(self) -> list[dict]:
        return self.store.list("jobs")

    # --- Aufträge

    def create(self, image: dict, options: dict) -> dict:
        self.store.ensure_mounted()
        job_id = new_id()
        job = {
            "id": job_id, "imageId": image["id"], "imageName": image.get("name"), "options": options,
            "state": "queued", "stage": None, "step": None, "total": None, "message": None,
            "created": now(), "started": None, "finished": None, "seconds": None, "glbBytes": None, "attempts": 0,
        }
        self.save(job)
        self.wake.set()
        return job

    def create_rig(self, source: dict, kind: str, seed: int) -> dict:
        """Ein Skelett für das GLB eines fertigen 3D-Auftrags; das Ergebnis ist wieder model.glb, im eigenen Auftrag."""
        self.store.ensure_mounted()
        job_id = new_id()
        job = {
            "id": job_id, "kind": "rig", "sourceJobId": source["id"], "imageId": source["imageId"],
            "imageName": source.get("imageName"), "options": {"class": kind, "seed": seed},
            "state": "queued", "stage": None, "step": None, "total": None, "message": None, "report": None,
            "created": now(), "started": None, "finished": None, "seconds": None, "glbBytes": None, "attempts": 0,
        }
        self.save(job)
        self.wake.set()
        return job

    def cancel(self, job_id: str) -> dict:
        job = self.get(job_id)
        if job["state"] != "queued":
            raise ValueError("Nur wartende Aufträge lassen sich abbrechen; ein laufender rechnet zu Ende")
        return self.update(job_id, state="canceled", finished=now())

    def recover(self) -> None:
        """Nach einem Neustart: was lief oder bei der Schranke wartete, kommt wieder in die Schlange."""
        for job in self.all():
            if job["state"] in ("waiting", "running"):
                self.update(job["id"], state="queued", stage=None, step=None, total=None,
                            message="nach einem Neustart neu eingereiht")

    # --- Arbeiter

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name="jobs", daemon=True)
        thread.start()
        threading.Thread(target=self.watch_gate, name="gate-watch", daemon=True).start()
        return thread

    def next_job(self):
        queued = [job for job in self.all() if job["state"] == "queued"]
        return min(queued, key=lambda job: job["created"]) if queued else None

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                # Erst wenn die T7 da ist, lässt sich sehen, was ein Neustart unterbrochen hat.
                if not self.recovered and self.store.mounted:
                    self.recover()
                    self.recovered = True
                job = self.next_job() if self.store.mounted else None
            except (OSError, StoreError, NotMounted) as error:
                self.log(f"Aufträge nicht lesbar: {error}")
                job = None
            if not job:
                self.wake.wait(10)
                self.wake.clear()
                continue
            self.process(job["id"])

    def process(self, job_id: str) -> None:
        job = self.update(job_id, state="waiting", started=now(), message="geht an oi-o-mat")
        self.current = job_id
        started = time.monotonic()
        broken = busy = 0
        try:
            report = None
            if job.get("kind") == "rig":
                source = self.store.get("jobs", job["sourceJobId"], "model.glb")

                def work():
                    self.update(job_id, state="running", stage="skeleton", message="SkinTokens setzt das Skelett ein")
                    return self.gate.rig(self.rig_model, source, job["options"].get("class", "humanoid"),
                                         job["options"].get("seed", 1))
            else:
                png = self.store.get("images", job["imageId"], "cutout.png")
                options = {**self.defaults, **job["options"]}

                def work():
                    return self.gate.generate_3d(self.model, png, options, on_progress=lambda *p: self.progress(job_id, *p)), None
            while True:
                self.update(job_id, attempts=job["attempts"] + broken + busy + 1)
                try:
                    glb, report = work()
                    break
                except StreamBroken as error:
                    broken += 1
                    if broken > MAX_BROKEN:
                        raise
                    self.log(f"{job_id}: {error}; Versuch {broken + 1}")
                    self.update(job_id, state="waiting", message=f"{error}; neuer Versuch")
                except GateBusy as error:
                    busy += 1
                    if busy > MAX_BUSY:
                        raise
                    self.update(job_id, state="waiting", message=f"{error}; neuer Versuch in {int(error.retry_after)} s")
                    self.stop.wait(error.retry_after)
            # launch.py schreibt daneben und benennt dann um: nie ein halbes GLB auf der T7.
            self.store.put("jobs", job_id, "model.glb", glb)
            seconds = round(time.monotonic() - started)
            self.update(job_id, state="done", finished=now(), seconds=seconds, glbBytes=len(glb), message=None, stage=None,
                        **({"report": report} if report is not None else {}))
            self.log(f"{job_id}: fertig in {seconds} s, {len(glb) // 1024} KB")
        except GateRefused as error:
            hint = " Freigeben oder installieren in der Oberfläche der Schranke (/gate)." if error.status in (403, 424) else ""
            self.update(job_id, state="failed", finished=now(), message=f"{error}.{hint}")
            self.log(f"{job_id}: abgewiesen ({error.status}): {error}")
        except Exception as error:  # noqa: BLE001 – jeder Fehler gehört in den Auftrag, nicht in den Arbeiter
            self.update(job_id, state="failed", finished=now(), message=str(error) or type(error).__name__)
            self.log(f"{job_id}: fehlgeschlagen: {error!r}")
        finally:
            self.current = None

    def progress(self, job_id: str, stage, step, total) -> None:
        self.update(job_id, state="running", stage=stage, step=step, total=total, message=None)

    def watch_gate(self) -> None:
        """Solange der Auftrag bei der Schranke wartet, steht ihr Grund im Auftrag (Tonwerk arbeitet, kein Speicher …)."""
        while not self.stop.is_set():
            self.stop.wait(5)
            job_id = self.current
            if not job_id:
                continue
            status = self.gate.status()
            if not status:
                continue
            try:
                job = self.get(job_id)
                if job["state"] != "waiting":
                    continue
                mine = [entry for entry in status.get("waiting", []) if entry.get("app") == self.gate.app]
                active = [entry for entry in status.get("running", []) if entry.get("app") == self.gate.app]
                if mine:
                    message = f"wartet bei der Schranke: {mine[0].get('held') or 'andere Anfragen davor'}"
                elif active:
                    message = f"oi-o-mat: {active[0].get('stage') or 'läuft'}"
                else:
                    continue
                if message != job.get("message"):
                    self.update(job_id, message=message)
            except (OSError, KeyError, ValueError, StoreError, NotMounted):
                continue

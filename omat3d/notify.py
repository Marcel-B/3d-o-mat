"""Eine Nachricht an Onkel Bummel (Marcels Benachrichtigungsdienst, Marcel-B/onkel-bummel), wenn ein 3D-Modell oder
ein Skelett fertig ist oder fehlschlägt, mit dem freigestellten Bild als Anhang. Ein Auftrag rechnet zehn Minuten und
mehr; so muss niemand die Seite offen halten.

    "onkelBummel": {"url": "https://onkel-bummel.example", "token": "ob_…", "topic": "3d-o-mat",
                    "click": "http://3d-o-mat:8090/"}

Ohne url wird nichts gesendet. Statt token geht tokenFile (bei jeder Nachricht neu gelesen). Gemalte Bilder meldet
img-o-mat selbst, sonst kämen sie doppelt.

Gesendet wird in einem eigenen Thread, der Arbeiter wartet nie darauf: Was nach drei Versuchen nicht ankommt, steht
nur im Log. Der Idempotency-Key ist die Kennung des Auftrags, eine Wiederholung legt keine zweite Nachricht an.
"""
import base64
import threading
import time
from pathlib import Path

import httpx

ATTEMPTS = 3
TIMEOUT = 30


class Notifier:
    def __init__(self, settings: dict | None, store=None, log=print, transport: httpx.BaseTransport | None = None):
        self.settings = settings or {}
        self.store = store
        self.log = log
        self.transport = transport
        self.pause = time.sleep

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("url"))

    def token(self) -> str | None:
        if path := self.settings.get("tokenFile"):
            try:
                if token := Path(path).read_text(encoding="utf-8").strip():
                    return token
            except OSError:
                pass
        return self.settings.get("token") or None

    def job_finished(self, job: dict) -> None:
        """Vom Arbeiter für jeden Auftrag, der fertig oder fehlgeschlagen ist; kehrt sofort zurück."""
        if self.enabled and job.get("state") in ("done", "failed"):
            threading.Thread(target=self._deliver, args=(dict(job),), daemon=True, name="onkel-bummel").start()

    def _deliver(self, job: dict) -> None:
        try:
            self.send(self.message_of(job), f"3d-o-mat-{job['id']}")
        except Exception as error:  # noqa: BLE001 – eine Nachricht darf nie den Auftrag stören
            self.log(f"Onkel Bummel: {error!r}")

    def message_of(self, job: dict) -> dict:
        rig = job.get("kind") == "rig"
        name = job.get("imageName") or job.get("imageId")
        message = {"topic": self.settings.get("topic") or "3d-o-mat"}
        if job["state"] == "done":
            seconds = job.get("seconds") or 0
            took = f"{seconds / 60:.0f} min" if seconds >= 90 else f"{seconds} s"
            size = f", {job['glbBytes'] / 1024 / 1024:.1f} MB" if job.get("glbBytes") else ""
            message.update(title=f"Skelett fertig: {name}" if rig else f"3D-Modell fertig: {name}", priority=3,
                           tags=["white_check_mark", "3d-o-mat"], message=f"In {took}{size}.")
        else:
            message.update(title=f"Skelett fehlgeschlagen: {name}" if rig else f"3D-Modell fehlgeschlagen: {name}",
                           priority=4, tags=["warning", "3d-o-mat"], message=job.get("message") or "Unbekannter Fehler.")
        if picture := self.picture(job):
            message["images"] = [{"data": base64.b64encode(picture).decode("ascii"), "filename": f"{job['imageId']}.png"}]
        if click := self.settings.get("click"):
            message["click"] = click
        return message

    def picture(self, job: dict) -> bytes | None:
        """Das freigestellte Bild, aus dem der Auftrag entstand; fehlt es (T7 weg, Bild gelöscht), geht die Nachricht ohne."""
        if not self.store or not job.get("imageId"):
            return None
        try:
            return self.store.get("images", job["imageId"], "cutout.png")
        except Exception:  # noqa: BLE001
            return None

    def send(self, message: dict, key: str) -> bool:
        token = self.token()
        if not token:
            self.log("Onkel Bummel: kein Token (onkelBummel.token oder tokenFile), Nachricht nicht gesendet.")
            return False
        url = self.settings["url"].rstrip("/") + "/api/v1/messages"
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": key}
        reason = ""
        for attempt in range(1, ATTEMPTS + 1):
            try:
                with httpx.Client(transport=self.transport, timeout=TIMEOUT) as client:
                    response = client.post(url, json=message, headers=headers)
                if response.status_code < 400:
                    return True
                # Falscher Token, falsches Topic, kaputtes Bild bleiben falsch; nur Ärger des Servers lohnt einen neuen Versuch.
                if response.status_code < 500:
                    self.log(f"Onkel Bummel: {response.status_code} {response.text[:200]}, Nachricht nicht gesendet.")
                    return False
                reason = f"{response.status_code}"
            except httpx.HTTPError as error:
                reason = str(error) or type(error).__name__
            if attempt < ATTEMPTS:
                self.pause(5 * attempt)
        self.log(f"Onkel Bummel nicht erreichbar ({reason}), Nachricht nicht gesendet.")
        return False

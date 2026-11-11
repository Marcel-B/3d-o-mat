"""Die Schnittstelle für andere Programme: aus einem Bild ein GLB, erster Nutzer Kaderwerk (ein Kopf je Spieler).
Gebaut wie img-o-mat, damit derselbe Client passt:

    POST   /v1/files                        rohe Bytes (PNG, JPEG, WebP) -> {"sha256"}; 201 neu, 200 schon da
    GET    /v1/files/<sha256>               die Datei (Bilder mit ihrem Typ, GLB als model/gltf-binary)
    POST   /v1/models3d/generations         Auftrag anlegen, kehrt sofort zurück (202)
    GET    /v1/models3d/generations         die letzten 100 Aufträge der Schnittstelle, neueste zuerst
    GET    /v1/models3d/generations/<id>    Stand, Fortschritt, Ergebnis
    DELETE /v1/models3d/generations/<id>    einen wartenden Auftrag herausnehmen
    GET    /v1/busy                         {"busy": true|false}

`Authorization: Bearer <App>` nennt nur den Absender, angemeldet wird nicht. Fehler kommen als
{"error": {"message": …}}. Die Aufträge laufen in derselben Schlange wie die der Seite (jobs.py), also nie zwei
zugleich. Dateien liegen auf der T7 unter files/<sha256>/ (store/launch.py), nicht im Container. Den Speicher mit
img-o-mat zu teilen ginge nicht: dessen Dateien liegen in seinem eigenen Container.
"""
import hashlib
import json
import re
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from . import cutout, export
from .jobs import OPEN, now, order
from .store import NotMounted, StoreError, valid_id

SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_FILE = 40 * 1024 * 1024
TEXTURES = (512, 1024, 2048, 4096)
FACES = (2000, 500_000)
HEIGHTS = (0.05, 3.0)
SEED_MAX = 2**31 - 1
LISTED = 100
PREFIX = "job_"
# Grobe Stufen für progress: TRELLIS.2 meldet seine Stufen mit Namen (wie JobList.tsx sie liest), dazu Schritt und
# Anzahl; jede Stufe füllt ihren Bereich.
BANDS = [
    (re.compile(r"sparse|structure"), 0.10, 0.30),
    (re.compile(r"shape|geometry|mesh"), 0.30, 0.55),
    (re.compile(r"tex|paint|material|bake"), 0.55, 0.85),
    (re.compile(r"decimat|simplif|export|glb"), 0.85, 0.90),
]


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def sniff(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"glTF":
        return "model/gltf-binary"
    return None


def error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": "3d_o_mat", "code": status}}, status_code=status)


class Models3d:
    """Die Endpunkte und was jobs.py für einen Auftrag der Schnittstelle braucht (prepare, finish)."""

    def __init__(self, config, store, save_image, log=print):
        self.config = config
        self.store = store
        self.save_image = save_image
        self.log = log
        self.jobs = None

    # --- Dateien auf der T7, nach ihrem SHA-256

    def put_file(self, data: bytes, media_type: str, app: str | None = None) -> tuple[str, bool]:
        digest = hashlib.sha256(data).hexdigest()
        if self.has_file(digest):
            return digest, False
        self.store.put("files", digest, "data", data)
        info = {"sha256": digest, "type": media_type, "bytes": len(data), "created": now(), "app": app}
        self.store.put("files", digest, "file.json", json.dumps(info).encode())
        return digest, True

    def file_info(self, digest: str) -> dict | None:
        try:
            return json.loads(self.store.get("files", digest, "file.json"))
        except FileNotFoundError:
            return None
        except StoreError as failure:
            if "unzulässig" in str(failure):
                raise StoreError("store/launch.py auf oi-o-mat kennt noch keine Dateien; dort install-store.sh noch "
                                 "einmal ausführen") from failure
            raise

    def has_file(self, digest: str) -> bool:
        return self.file_info(digest) is not None

    # --- für jobs.py

    def prepare(self, job_id: str, job: dict) -> bytes:
        """Freistellen (oder nicht) und als Bild ablegen, damit die Seite den Auftrag mit Vorschau zeigt."""
        data = self.store.get("files", job["file"], "data")
        info = self.file_info(job["file"]) or {}
        suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(info.get("type"), "bin")
        method = self.config.cutout if job["api"]["removeBackground"] else "none"
        name = job["api"].get("label") or "Kopf"
        image = self.save_image(data, name[:80], {"original": f"original.{suffix}", "sha256": job["file"],
                                                  "app": job["api"].get("app")}, method)
        self.jobs.update(job_id, imageId=image["id"], imageName=name)
        return self.store.get("images", image["id"], "cutout.png")

    def finish(self, job: dict, glb: bytes) -> tuple[bytes, dict]:
        """Das GLB in die Form, die die Schnittstelle verspricht (export.py), und als Datei nach seinem SHA-256."""
        options = job["options"]
        extras = {"model": self.config.trellis_model, "seed": options["seed"], "image": job["file"],
                  "creator": job["api"].get("creator"), "label": job["api"].get("label"),
                  "height": job["api"]["height"]}
        out, info = export.finish(glb, options["texture_size"], job["api"]["height"], extras)
        digest, _ = self.put_file(out, "model/gltf-binary", job["api"].get("app"))
        return out, {"file": digest, **info}

    # --- Ansicht eines Auftrags

    def positions(self) -> dict[str, int]:
        queued = sorted((job for job in self.jobs.all() if job["state"] == "queued"), key=order)
        return {job["id"]: index + 1 for index, job in enumerate(queued)}

    @staticmethod
    def progress(job: dict) -> float:
        state, stage = job["state"], job.get("stage") or ""
        if state == "done":
            return 1.0
        if state in ("queued", "canceled", "failed"):
            return 0.0
        if stage == "cutout":
            return 0.05
        if stage == "export":
            return 0.92
        for pattern, start, end in BANDS:
            if pattern.search(stage):
                part = (job.get("step") or 0) / job["total"] if job.get("total") else 0.0
                return round(start + (end - start) * min(max(part, 0.0), 1.0), 3)
        return 0.10

    def view(self, job: dict, positions: dict | None = None) -> dict:
        positions = self.positions() if positions is None else positions
        state = job["state"]
        status = {"waiting": "running"}.get(state, state)
        return {
            "id": PREFIX + job["id"], "status": status, "position": positions.get(job["id"], 0),
            "progress": self.progress(job), "seed": job["options"].get("seed"),
            "error": job.get("message") if state == "failed" else None,
            "message": job.get("message") if state in OPEN else None,
            "result": job.get("result") if state == "done" else None,
            "creator": job["api"].get("creator"), "label": job["api"].get("label"),
            "faces": job["options"].get("decimation_target"), "texture": job["options"].get("texture_size"),
            "created": job["created"], "finished": job.get("finished"),
        }

    def job(self, api_id: str) -> dict:
        internal = api_id.removeprefix(PREFIX)
        if not api_id.startswith(PREFIX) or not valid_id(internal):
            raise ApiError(404, f"Kein Auftrag {api_id}")
        self.store.ensure_mounted()
        try:
            job = self.jobs.get(internal)
        except KeyError:
            raise ApiError(404, f"Kein Auftrag {api_id}") from None
        if job.get("kind") != "api":
            raise ApiError(404, f"Kein Auftrag {api_id}")
        return job

    # --- Anfragen prüfen

    @staticmethod
    def parse(body, app: str | None) -> dict:
        if not isinstance(body, dict):
            raise ApiError(400, "Der Body muss ein JSON-Objekt sein")
        unknown = sorted(set(body) - {"image", "faces", "texture", "remove_background", "seed", "creator", "label",
                                      "height"})
        if unknown:
            raise ApiError(400, f"Unbekannte Felder: {', '.join(unknown)}")
        image = body.get("image")
        if not isinstance(image, str) or not SHA256.match(image):
            raise ApiError(400, "image: der SHA-256 einer Datei aus POST /v1/files (64 Zeichen 0-9a-f)")

        def number(name, default, low, high, kind=int):
            value = body.get(name, default)
            if value is None and name == "seed":
                return None
            if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind is int and value != int(value)):
                raise ApiError(400, f"{name}: eine Zahl von {low} bis {high}")
            if not low <= value <= high:
                raise ApiError(400, f"{name}: erlaubt sind {low} bis {high}, nicht {value}")
            return kind(value)

        faces = number("faces", 30000, *FACES)
        texture = body.get("texture", 2048)
        if isinstance(texture, bool) or texture not in TEXTURES:
            raise ApiError(400, f"texture: {', '.join(map(str, TEXTURES))}")
        seed = number("seed", None, 0, SEED_MAX)
        height = number("height", 0.25, *HEIGHTS, kind=float)
        remove = body.get("remove_background", True)
        if not isinstance(remove, bool):
            raise ApiError(400, "remove_background: true oder false")
        texts = {}
        for name in ("creator", "label"):
            value = body.get(name)
            if value is not None and (not isinstance(value, str) or len(value) > 200):
                raise ApiError(400, f"{name}: ein Text bis 200 Zeichen")
            texts[name] = value
        seed = secrets.randbelow(SEED_MAX + 1) if seed is None else seed
        return {"file": image,
                "options": {"texture_size": int(texture), "decimation_target": faces, "seed": seed},
                "api": {"app": app, "creator": texts["creator"] or app, "label": texts["label"],
                        "removeBackground": remove, "height": height}}

    # --- Endpunkte

    def add_routes(self, app: FastAPI) -> None:
        def caller(request: Request) -> str | None:
            header = request.headers.get("authorization", "")
            return header[7:].strip()[:64] or None if header.lower().startswith("bearer ") else None

        @app.post("/v1/files")
        async def upload(request: Request):
            declared = request.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_FILE:
                raise ApiError(413, "Datei größer als 40 MB")
            data = await request.body()
            if not data:
                raise ApiError(400, "Leerer Body; die Datei geht als rohe Bytes (application/octet-stream)")
            if len(data) > MAX_FILE:
                raise ApiError(413, "Datei größer als 40 MB")
            media_type = sniff(data)
            if media_type not in ("image/png", "image/jpeg", "image/webp"):
                raise ApiError(415, "Nur PNG, JPEG oder WebP")
            self.store.ensure_mounted()
            digest, created = self.put_file(data, media_type, caller(request))
            return JSONResponse({"sha256": digest}, status_code=201 if created else 200)

        @app.get("/v1/files/{digest}")
        def download(digest: str):
            if not SHA256.match(digest):
                raise ApiError(400, "Ein SHA-256 hat 64 Zeichen 0-9a-f")
            self.store.ensure_mounted()
            info = self.file_info(digest)
            if info is None:
                raise ApiError(404, f"Keine Datei {digest}")
            try:
                data = self.store.get("files", digest, "data")
            except FileNotFoundError:
                raise ApiError(404, f"Keine Datei {digest}") from None
            return Response(data, media_type=info.get("type") or "application/octet-stream",
                            headers={"cache-control": "private, max-age=31536000, immutable"})

        @app.post("/v1/models3d/generations")
        async def create(request: Request):
            try:
                body = json.loads(await request.body() or b"null")
            except ValueError:
                raise ApiError(400, "Kein gültiges JSON") from None
            fields = self.parse(body, caller(request))
            self.store.ensure_mounted()
            if not self.has_file(fields["file"]):
                raise ApiError(404, f"Keine Datei {fields['file']}; erst mit POST /v1/files hochladen")
            info = self.file_info(fields["file"])
            if not str(info.get("type", "")).startswith("image/"):
                raise ApiError(400, "image: das ist kein Bild")
            job = self.jobs.create_api(fields)
            view = self.view(job)
            return JSONResponse({key: view[key] for key in ("id", "status", "position", "seed")}, status_code=202)

        @app.get("/v1/models3d/generations")
        def listing():
            mine = sorted((job for job in self.jobs.all() if job.get("kind") == "api"),
                          key=order, reverse=True)[:LISTED]
            positions = self.positions()
            return {"data": [self.view(job, positions) for job in mine]}

        @app.get("/v1/models3d/generations/{api_id}")
        def one(api_id: str):
            return self.view(self.job(api_id))

        @app.delete("/v1/models3d/generations/{api_id}")
        def remove(api_id: str):
            job = self.job(api_id)
            if job["state"] in ("waiting", "running"):
                raise ApiError(409, "Der Auftrag rechnet schon und darf zu Ende rechnen")
            if job["state"] != "queued":
                raise ApiError(409, f"Der Auftrag wartet nicht mehr ({job['state']})")
            try:
                job = self.jobs.cancel(job["id"])
            except ValueError as failure:
                raise ApiError(409, str(failure)) from failure
            return self.view(job)

        @app.get("/v1/busy")
        def busy():
            waiting = sum(1 for job in self.jobs.all() if job["state"] == "queued")
            running = self.jobs.current
            return {"busy": bool(running or waiting), "running": PREFIX + running if running else None,
                    "waiting": waiting}

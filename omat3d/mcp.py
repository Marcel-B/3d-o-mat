"""MCP-Server unter /mcp, damit Agents (Claude Code, Claude Desktop, andere MCP-Clients) mit 3d-o-mat arbeiten können:
Bild hochladen oder malen lassen, prüfen, in 3D umwandeln, Stand abfragen, Skelett einsetzen, als GLB, 3MF, STL und
(sobald es die FBX-Route gibt) FBX holen.

Transport ist „Streamable HTTP“ ohne Sitzung: jede Nachricht ist ein POST mit JSON-RPC, die Antwort kommt als JSON
zurück (nie als SSE-Stream; die Werkzeuge warten höchstens ein paar Minuten und melden dann den Stand). Damit braucht
der Client nichts zu installieren: `claude mcp add --transport http 3d-o-mat https://3d.idsrv.info/mcp`.

Die Werkzeuge rufen die eigene Schnittstelle (/api und /v1) im selben Prozess über ASGI auf, statt in jobs.py oder
store.py zu greifen. So gelten dieselben Prüfungen und Fehlermeldungen wie für die Seite, und eine neue Route
(etwa model.fbx) braucht hier nur einen Eintrag. Dateien gehen nicht durch das Gespräch des Agents: Ergebnisse kommen
als Download-Adresse, die er mit curl holt; eine Druckdatei, die nur ein POST liefert, liegt dafür kurz im Speicher
(Downloads). Wie die übrige Schnittstelle gehört /mcp nur ins Heimnetz und ins Tailnet; angemeldet wird nicht.
"""
import asyncio
import base64
import binascii
import io
import json
import secrets
import threading
import time
from urllib.parse import quote, urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .jobs import OPEN
from .models3d import PREFIX, Models3d

# Neueste zuerst; der Client bekommt seine Version, wenn wir sie kennen, sonst unsere neueste.
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER = {"name": "3d-o-mat", "title": "3d-o-mat", "version": "1.0.0"}
FBX_ROUTE = "/api/jobs/{job_id}/model.fbx"
MAX_WAIT = 240
POLL = 2.0
PREVIEW = 512
LISTED = 20

INSTRUCTIONS = """3d-o-mat turns a picture into a textured 3D model (GLB with PBR textures) using TRELLIS.2 on a Mac in
Marcel's home network, can add a skeleton with Mixamo bone names (SkinTokens) and makes files for 3D printing.

Typical flow:
1. Get a picture: upload_image (small files as base64; for files on disk use the curl command in its description),
   or paint_images (img-o-mat) and wait with get_painting. write_image_prompts suggests good prompts for an idea.
2. Optional: check_image asks Gemma whether the picture suits 3D (one whole object, sharp, no glass).
3. create_model starts a job. Jobs run one at a time and take several minutes; get_job with wait_seconds waits.
4. When done: export_model gives a download URL (GLB, 3MF/STL for printing, FBX where available). Download it with
   curl -o; the files are large and do not belong in the conversation.
5. Optional: add_skeleton rigs a finished model (new job, about a minute); its GLB then has Mixamo bones.

Animations from the motion library are retargeted in the browser and are not available here; open the web page for
those. Server messages are partly in German."""


class ToolError(Exception):
    """Ein Fehler, den der Agent lesen soll (isError im Ergebnis), kein Fehler des Protokolls."""


class Downloads:
    """Dateien, die nur ein POST erzeugt (Druckdatei), für kurze Zeit unter /mcp/downloads/<token>/<name>."""
    KEEP_SECONDS = 3600
    MAX_ITEMS = 8
    MAX_BYTES = 400 * 1024 * 1024

    def __init__(self):
        self.lock = threading.Lock()
        self.items: dict[str, dict] = {}

    def put(self, data: bytes, name: str, media_type: str) -> str:
        token = secrets.token_urlsafe(12)
        with self.lock:
            self.items[token] = {"data": data, "name": name, "type": media_type, "at": time.time()}
            self._trim()
        return token

    def get(self, token: str) -> dict | None:
        with self.lock:
            self._trim()
            return self.items.get(token)

    def _trim(self) -> None:
        cutoff = time.time() - self.KEEP_SECONDS
        for token in [t for t, item in self.items.items() if item["at"] < cutoff]:
            del self.items[token]
        while self.items and (len(self.items) > self.MAX_ITEMS
                              or sum(len(i["data"]) for i in self.items.values()) > self.MAX_BYTES):
            del self.items[min(self.items, key=lambda t: self.items[t]["at"])]


def base_url(request: Request) -> str:
    """Die Adresse, unter der der Agent 3d-o-mat erreicht (hinter dem Proxy von 3d.idsrv.info die öffentliche)."""
    scheme = request.headers.get("x-forwarded-proto", "").split(",")[0].strip() or request.url.scheme
    host = request.headers.get("x-forwarded-host", "").split(",")[0].strip() or request.headers.get("host") \
        or request.url.netloc
    return f"{scheme}://{host}"


def foreign_origin(request: Request) -> bool:
    """Gegen DNS-Rebinding (verlangt die MCP-Spezifikation): ein Browser von einer fremden Seite schickt Origin mit."""
    origin = request.headers.get("origin")
    if not origin:
        return False
    try:
        netloc = urlsplit(origin).netloc
    except ValueError:
        return True
    allowed = {request.headers.get("host"), request.headers.get("x-forwarded-host", "").split(",")[0].strip()}
    return netloc not in allowed


def text_of(value) -> list[dict]:
    return [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=1)}]


def schema(properties: dict, required: tuple = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


JOB_ID = {"type": "string", "description": "Job id from create_model or list_jobs (e.g. 20261118-011500-ab12)"}
IMAGE_ID = {"type": "string", "description": "Image id from upload_image, list_images or get_painting"}
LANG = {"type": "string", "enum": ["de", "en"], "default": "en", "description": "Language of Gemma's answer"}


class Mcp:
    def __init__(self, app: FastAPI):
        self.app = app
        self.downloads = Downloads()
        self.tools = {tool["name"]: tool for tool in self._tools()}

    # --- die eigene Schnittstelle im selben Prozess

    async def call(self, method: str, path: str, *, raw: bool = False, app_name: str = "mcp", **kwargs):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://3d-o-mat", timeout=None) as client:
            response = await client.request(method, path, headers={"authorization": f"Bearer {app_name}"}, **kwargs)
        if response.status_code >= 400:
            raise ToolError(f"{response.status_code}: {self._message(response)}")
        return response if raw else response.json()

    @staticmethod
    def _message(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:500] or response.reason_phrase
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            return str(error.get("message"))
        if error:
            return str(error)
        detail = body.get("detail") if isinstance(body, dict) else body
        if isinstance(detail, list):  # Prüfung durch FastAPI: [{loc, msg}, …]
            return "; ".join(f"{'.'.join(map(str, d.get('loc', [])[1:]))}: {d.get('msg')}" for d in detail)
        return str(detail)

    def has_route(self, path: str) -> bool:
        return any(getattr(route, "path", None) == path for route in self.app.routes)

    # --- Ansichten: knapp, mit dem, was ein Agent als Nächstes braucht

    def job_view(self, job: dict, base: str, fbx: bool | None) -> dict:
        done = job["state"] == "done"
        kind = job.get("kind") or "model"
        skeleton = kind == "rig" or (kind == "import" and (job.get("import") or {}).get("skeleton") == "mixamo")
        view = {
            "id": job["id"], "kind": kind, "state": job["state"], "stage": job.get("stage"),
            "progress": Models3d.progress(job) if kind != "import" else 1.0,
            "message": job.get("message"), "imageId": job.get("imageId"), "name": job.get("imageName"),
            "options": job.get("options"), "created": job.get("created"), "finished": job.get("finished"),
            "seconds": job.get("seconds"), "skeleton": skeleton,
        }
        if kind == "api":
            view["apiId"] = PREFIX + job["id"]
            view["app"] = (job.get("api") or {}).get("app")
        if kind == "rig":
            view["sourceJobId"] = job.get("sourceJobId")
            view["fingers"] = job.get("fingers")
            report = job.get("report") or {}
            view["humanoid"] = report.get("humanoid") if isinstance(report, dict) else None
        if done:
            view["bytes"] = job.get("glbBytes")
            view["downloads"] = {"glb": f"{base}/api/jobs/{job['id']}/model.glb?download=true"}
            if job.get("result"):
                view["downloads"]["glbByHash"] = f"{base}/v1/files/{job['result']['file']}"
            if fbx:
                for target in ("unity", "unreal"):
                    view["downloads"][f"fbx_{target}"] = f"{base}/api/jobs/{job['id']}/model.fbx?target={target}"
            view["next"] = ("export_model for GLB/3MF/STL" + ("/FBX" if fbx else "")
                            + ("" if skeleton else "; add_skeleton to rig it"))
        return view

    @staticmethod
    def image_view(doc: dict) -> dict:
        check = doc.get("check")
        view = {"id": doc["id"], "name": doc.get("name"), "created": doc.get("created")}
        if doc.get("painted"):
            view["painted"] = {k: doc["painted"].get(k) for k in ("model", "prompt", "seed")}
        if doc.get("app"):
            view["app"] = doc["app"]
        if check:
            view["check"] = check
        cutout = doc.get("cutout") or {}
        if cutout:
            view["cutout"] = {k: cutout.get(k) for k in ("method", "subject") if k in cutout}
        return view

    async def fbx_state(self) -> bool | None:
        """None: diese Fassung kennt keine FBX-Route; sonst, ob Blender da ist (status.fbx, ohne Angabe ja)."""
        if not self.has_route(FBX_ROUTE):
            return None
        try:
            status = await self.call("GET", "/api/status")
        except ToolError:
            return True
        return bool(status.get("fbx", True))

    async def job(self, job_id: str) -> dict:
        job_id = str(job_id or "").strip().removeprefix(PREFIX)
        if not job_id:
            raise ToolError("job_id fehlt")
        return await self.call("GET", f"/api/jobs/{quote(job_id, safe='')}")

    # --- die Werkzeuge

    def _tools(self) -> list[dict]:
        read = {"readOnlyHint": True, "openWorldHint": False}
        write = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        return [
            {"name": "get_status", "title": "Status of 3d-o-mat", "annotations": read, "handler": self.get_status,
             "description": "Whether 3d-o-mat can work right now: storage reachable, model gate reachable, which "
                            "models are approved, how many jobs are running or waiting, whether FBX export exists.",
             "inputSchema": schema({})},
            {"name": "list_images", "title": "List pictures", "annotations": read, "handler": self.list_images,
             "description": "The pictures 3d-o-mat has (uploaded, painted or from the /v1 API), newest first, with "
                            "id, name, Gemma's check and how they were painted.",
             "inputSchema": schema({"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": LISTED}})},
            {"name": "show_image", "title": "Show a picture", "annotations": read, "handler": self.show_image,
             "description": "Returns a picture so you can look at it: the cut-out version TRELLIS.2 gets (default) "
                            "or the original, scaled down to at most 512 px.",
             "inputSchema": schema({"image_id": IMAGE_ID,
                                    "kind": {"type": "string", "enum": ["cutout", "original"], "default": "cutout"}},
                                   ("image_id",))},
            {"name": "upload_image", "title": "Upload a picture", "annotations": write, "handler": self.upload_image,
             "description": "Upload a picture (PNG, JPEG or WebP, base64) for 3D. 3d-o-mat removes the background and "
                            "crops it square. Base64 costs many tokens: for a file on disk run instead\n"
                            "  curl -sS -F file=@picture.png <base>/api/images\n"
                            "(<base> is this server's address without /mcp); the JSON answer's id is the image_id.",
             "inputSchema": schema({"data_base64": {"type": "string", "description": "The file's bytes, base64"},
                                    "filename": {"type": "string", "description": "e.g. robot.png; names the picture"}},
                                   ("data_base64",))},
            {"name": "check_image", "title": "Check a picture with Gemma", "annotations": write,
             "handler": self.check_image,
             "description": "Gemma (vision model) judges whether the cut-out picture suits 3D: a single whole "
                            "object, sharp, nothing transparent, with tips. Takes up to a minute.",
             "inputSchema": schema({"image_id": IMAGE_ID, "lang": LANG}, ("image_id",))},
            {"name": "write_image_prompts", "title": "Prompts for a picture", "annotations": write,
             "handler": self.write_image_prompts,
             "description": "Gemma writes English image prompts from an idea, made for a good 3D source picture "
                            "(one object, fully in frame, plain background). Use one with paint_images.",
             "inputSchema": schema({"idea": {"type": "string", "maxLength": 1000}, "lang": LANG}, ("idea",))},
            {"name": "paint_images", "title": "Paint pictures", "annotations": write, "handler": self.paint_images,
             "description": "img-o-mat paints 1 to 4 pictures from a prompt (about a minute). Each finished picture is "
                            "cut out and becomes an image for create_model. Returns a painting id for get_painting.",
             "inputSchema": schema({
                 "prompt": {"type": "string", "maxLength": 2000},
                 "model": {"type": "string", "description": "Image model; get_status lists them, default the first"},
                 "n": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
                 "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
                 "size": {"type": "string", "enum": ["1024x1024", "1024x768", "768x1024"], "default": "1024x1024"}},
                 ("prompt",))},
            {"name": "get_painting", "title": "State of a painting", "annotations": read, "handler": self.get_painting,
             "description": "State of a paint_images request and the image ids it produced. wait_seconds waits until "
                            "it is finished (at most 240).",
             "inputSchema": schema({"painting_id": {"type": "string"},
                                    "wait_seconds": {"type": "integer", "minimum": 0, "maximum": MAX_WAIT,
                                                     "default": 0}}, ("painting_id",))},
            {"name": "create_model", "title": "Picture to 3D", "annotations": write, "handler": self.create_model,
             "description": "Start turning a picture into a textured 3D model (TRELLIS.2). Give image_id (a picture "
                            "of 3d-o-mat) or image_sha256 (a file uploaded raw with curl --data-binary @file "
                            "<base>/v1/files; that path gives a game-ready GLB: one mesh, origin bottom centre, scaled "
                            "to height). Jobs run one after another and take about 3 to 15 minutes; returns the job, "
                            "wait with get_job.",
             "inputSchema": schema({
                 "image_id": IMAGE_ID,
                 "image_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                 "texture_size": {"type": "integer", "enum": [512, 1024, 2048, 4096],
                                  "description": "Texture edge length; default 2048"},
                 "faces": {"type": "integer", "minimum": 2000, "maximum": 2000000,
                           "description": "Target face count after simplifying (image_id: 10000 or more; "
                                          "image_sha256: up to 500000, default 30000)"},
                 "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
                 "resolution": {"type": "integer", "enum": [512, 1024, 1536], "description": "image_id only"},
                 "steps": {"type": "integer", "minimum": 1, "maximum": 50, "description": "image_id only"},
                 "height": {"type": "number", "minimum": 0.05, "maximum": 3,
                            "description": "image_sha256 only: model height in metres, default 0.25"},
                 "remove_background": {"type": "boolean", "description": "image_sha256 only, default true"},
                 "label": {"type": "string", "maxLength": 200, "description": "image_sha256 only: shown name"}})},
            {"name": "get_job", "title": "State of a job", "annotations": read, "handler": self.get_job,
             "description": "State of a job (queued, waiting, running, done, failed, canceled) with progress and, when "
                            "done, download URLs. wait_seconds waits until it is no longer queued or running (at most "
                            "240; call again if it is still running).",
             "inputSchema": schema({"job_id": JOB_ID,
                                    "wait_seconds": {"type": "integer", "minimum": 0, "maximum": MAX_WAIT,
                                                     "default": 0}}, ("job_id",))},
            {"name": "list_jobs", "title": "List jobs", "annotations": read, "handler": self.list_jobs,
             "description": "Jobs newest first: picture to 3D (kind model, or api from /v1), skeletons (rig) and "
                            "imported figures (import).",
             "inputSchema": schema({
                 "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": LISTED},
                 "state": {"type": "string", "enum": ["queued", "waiting", "running", "done", "failed", "canceled"]}})},
            {"name": "cancel_job", "title": "Cancel a waiting job",
             "annotations": {**write, "destructiveHint": True}, "handler": self.cancel_job,
             "description": "Takes a queued job out of the queue. A running job always finishes.",
             "inputSchema": schema({"job_id": JOB_ID}, ("job_id",))},
            {"name": "add_skeleton", "title": "Add a skeleton", "annotations": write, "handler": self.add_skeleton,
             "description": "SkinTokens rigs a finished model: bones and weights, for humanoid with Mixamo bone names "
                            "(and named fingers), so Mixamo animations fit in Godot, Unity or Blender. Creates a new "
                            "job (about a minute); if it cannot find a human, it tries the next seeds by itself.",
             "inputSchema": schema({
                 "job_id": JOB_ID,
                 "kind": {"type": "string", "enum": ["humanoid", "quadruped", "custom"], "default": "humanoid"},
                 "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647, "default": 1}}, ("job_id",))},
            {"name": "export_model", "title": "Export a model", "annotations": write, "handler": self.export_model,
             "description": "A download URL for a finished job: glb (as made, with skeleton if rigged), 3mf or stl "
                            "for the slicer (millimetres, one closed body, rest pose, takes up to half a minute), "
                            "fbx for Unity or Unreal where this 3d-o-mat has it. Fetch it with curl -o <file> <url>.",
             "inputSchema": schema({
                 "job_id": JOB_ID,
                 "format": {"type": "string", "enum": ["glb", "3mf", "stl", "fbx"], "default": "glb"},
                 "height_mm": {"type": "number", "minimum": 20, "maximum": 400, "default": 120,
                               "description": "3mf/stl: printed height"},
                 "base": {"type": "boolean", "default": True, "description": "3mf/stl: round base"},
                 "target": {"type": "string", "enum": ["unity", "unreal"], "default": "unity",
                            "description": "fbx: axes and scale for this engine"}}, ("job_id",))},
        ]

    async def get_status(self, args: dict, base: str):
        status = await self.call("GET", "/api/status")
        busy = await self.call("GET", "/v1/busy")
        jobs = await self.call("GET", "/api/jobs")
        running = [job["id"] for job in jobs if job["state"] in ("waiting", "running")]
        gate = status.get("gate") or {}
        return {
            "storage": status.get("storage"),
            "gate": {"reachable": gate.get("reachable"), "registered": gate.get("registered"),
                     "held": gate.get("held"), "tonwerk": gate.get("tonwerk"), "models": gate.get("models")},
            "models": status.get("models"),
            "paintModels": [m.get("id") for m in (status.get("paint") or {}).get("models", [])],
            "queue": {"running": running, "waiting": sum(1 for job in jobs if job["state"] == "queued"),
                      "busy": busy.get("busy")},
            "fbx": await self.fbx_state(),
            "web": f"{base}/",
        }

    async def list_images(self, args: dict, base: str):
        images = await self.call("GET", "/api/images")
        images.sort(key=lambda doc: doc.get("created") or doc["id"], reverse=True)
        return [self.image_view(doc) for doc in images[:int(args.get("limit") or LISTED)]]

    async def show_image(self, args: dict, base: str):
        from PIL import Image  # erst hier: nur dieses Werkzeug braucht Pillow

        kind = args.get("kind") or "cutout"
        if kind not in ("cutout", "original"):
            raise ToolError("kind: cutout oder original")
        image_id = quote(str(args.get("image_id") or ""), safe="")
        response = await self.call("GET", f"/api/images/{image_id}/{kind}", raw=True)
        try:
            picture = Image.open(io.BytesIO(response.content))
            picture.thumbnail((PREVIEW, PREVIEW))
            buffer = io.BytesIO()
            picture.save(buffer, "PNG")
        except Exception as error:  # noqa: BLE001 – Pillow wirft je nach Format anderes
            raise ToolError(f"Bild nicht lesbar: {error}") from error
        return [{"type": "image", "data": base64.b64encode(buffer.getvalue()).decode(), "mimeType": "image/png"},
                {"type": "text", "text": f"{kind} of {args.get('image_id')}, {picture.width}x{picture.height}"}]

    async def upload_image(self, args: dict, base: str):
        try:
            data = base64.b64decode(str(args.get("data_base64") or "").split(",")[-1], validate=True)
        except (binascii.Error, ValueError):
            raise ToolError("data_base64 ist kein gültiges Base64") from None
        if not data:
            raise ToolError("data_base64 ist leer")
        name = str(args.get("filename") or "bild.png")[:120]
        doc = await self.call("POST", "/api/images", files={"file": (name, data, "application/octet-stream")})
        return {**self.image_view(doc), "next": "check_image (optional), then create_model with this image_id"}

    async def check_image(self, args: dict, base: str):
        image_id = quote(str(args.get("image_id") or ""), safe="")
        doc = await self.call("POST", f"/api/images/{image_id}/check", params={"lang": args.get("lang") or "en"})
        return {"id": doc["id"], "check": doc.get("check")}

    async def write_image_prompts(self, args: dict, base: str):
        return await self.call("POST", "/api/prompts", json={"idea": args.get("idea"), "lang": args.get("lang") or "en"})

    async def paint_images(self, args: dict, base: str):
        body = {key: args[key] for key in ("prompt", "model", "n", "seed", "size") if args.get(key) is not None}
        painting = await self.call("POST", "/api/paint", json=body)
        return {**self.painting_view(painting), "next": "get_painting with wait_seconds"}

    @staticmethod
    def painting_view(painting: dict) -> dict:
        return {key: painting.get(key) for key in ("id", "state", "stage", "place", "fraction", "model", "prompt",
                                                    "n", "seed", "imageIds", "message", "seconds")}

    async def get_painting(self, args: dict, base: str):
        painting_id = str(args.get("painting_id") or "")
        until = time.monotonic() + min(int(args.get("wait_seconds") or 0), MAX_WAIT)
        while True:
            found = next((p for p in await self.call("GET", "/api/paint") if p["id"] == painting_id), None)
            if found is None:
                raise ToolError(f"Kein Malauftrag {painting_id} (sie leben nur bis zum Neustart; die Bilder bleiben "
                                "in list_images)")
            if found["state"] != "running" or time.monotonic() >= until:
                return self.painting_view(found)
            await asyncio.sleep(POLL)

    async def create_model(self, args: dict, base: str):
        image_id, digest = args.get("image_id"), args.get("image_sha256")
        if bool(image_id) == bool(digest):
            raise ToolError("Genau eins von image_id und image_sha256 angeben")
        if digest:
            only = [key for key in ("resolution", "steps") if args.get(key) is not None]
            if only:
                raise ToolError(f"{', '.join(only)} geht nur mit image_id")
            body = {"image": digest, "creator": "mcp"}
            for ours, theirs in (("texture_size", "texture"), ("faces", "faces"), ("seed", "seed"),
                                 ("height", "height"), ("remove_background", "remove_background"),
                                 ("label", "label")):
                if args.get(ours) is not None:
                    body[theirs] = args[ours]
            created = await self.call("POST", "/v1/models3d/generations", json=body)
            job = await self.job(created["id"])
        else:
            only = [key for key in ("height", "remove_background", "label") if args.get(key) is not None]
            if only:
                raise ToolError(f"{', '.join(only)} geht nur mit image_sha256")
            options = {"texture_size": args.get("texture_size"), "decimation_target": args.get("faces"),
                       "resolution": args.get("resolution"), "steps": args.get("steps"), "seed": args.get("seed")}
            job = await self.call("POST", "/api/jobs", json={
                "imageId": image_id, "options": {k: v for k, v in options.items() if v is not None}})
        return {**self.job_view(job, base, None), "next": "get_job with wait_seconds"}

    async def get_job(self, args: dict, base: str):
        until = time.monotonic() + min(int(args.get("wait_seconds") or 0), MAX_WAIT)
        while True:
            job = await self.job(args.get("job_id"))
            if job["state"] not in OPEN or time.monotonic() >= until:
                return self.job_view(job, base, await self.fbx_state() if job["state"] == "done" else None)
            await asyncio.sleep(POLL)

    async def list_jobs(self, args: dict, base: str):
        jobs = await self.call("GET", "/api/jobs")
        if args.get("state"):
            jobs = [job for job in jobs if job["state"] == args["state"]]
        jobs.sort(key=lambda job: (job.get("created") or "", job["id"]), reverse=True)
        fbx = await self.fbx_state()
        return [self.job_view(job, base, fbx) for job in jobs[:int(args.get("limit") or LISTED)]]

    async def cancel_job(self, args: dict, base: str):
        job = await self.job(args.get("job_id"))
        job = await self.call("POST", f"/api/jobs/{job['id']}/cancel")
        return self.job_view(job, base, None)

    async def add_skeleton(self, args: dict, base: str):
        source = await self.job(args.get("job_id"))
        job = await self.call("POST", f"/api/jobs/{source['id']}/rig", json={
            "class": args.get("kind") or "humanoid", "seed": 1 if args.get("seed") is None else args["seed"]})
        return {**self.job_view(job, base, None), "next": "get_job with wait_seconds"}

    async def export_model(self, args: dict, base: str):
        job = await self.job(args.get("job_id"))
        if job["state"] != "done":
            raise ToolError(f"Der Auftrag ist noch nicht fertig ({job['state']})")
        fmt = args.get("format") or "glb"
        if fmt == "glb":
            url = f"{base}/api/jobs/{job['id']}/model.glb?download=true"
            return {"format": "glb", "url": url, "bytes": job.get("glbBytes"), "curl": f"curl -sS -o model.glb '{url}'"}
        if fmt == "fbx":
            fbx = await self.fbx_state()
            if fbx is None:
                raise ToolError("Diese Fassung von 3d-o-mat kann noch kein FBX; GLB nehmen oder später noch einmal")
            if not fbx:
                raise ToolError("FBX braucht Blender im Container, und das fehlt dort gerade")
            target = args.get("target") or "unity"
            if target not in ("unity", "unreal"):
                raise ToolError("target: unity oder unreal")
            url = f"{base}/api/jobs/{job['id']}/model.fbx?target={target}"
            return {"format": "fbx", "target": target, "url": url,
                    "curl": f"curl -sS -o model-{target}.fbx '{url}'",
                    "note": "Converted on download with Blender; may take a little while."}
        if fmt not in ("3mf", "stl"):
            raise ToolError("format: glb, 3mf, stl oder fbx")
        body = {"height": args.get("height_mm") or 120, "base": args.get("base", True) is not False, "format": fmt}
        response = await self.call("POST", f"/api/jobs/{job['id']}/print", json=body, raw=True)
        name = f"{job['id']}-druck.{fmt}"
        token = self.downloads.put(response.content, name, response.headers.get("content-type", "application/octet-stream"))
        url = f"{base}/mcp/downloads/{token}/{name}"
        try:
            info = json.loads(response.headers.get("x-print-info") or "null")
        except ValueError:
            info = None
        return {"format": fmt, "url": url, "bytes": len(response.content), "info": info,
                "curl": f"curl -sS -o {name} '{url}'", "expires": "in an hour or after 8 newer files"}

    # --- JSON-RPC

    async def handle(self, message, base: str):
        """Eine Nachricht -> Antwort, oder None für eine Benachrichtigung."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                    "error": {"code": -32600, "message": "Invalid Request"}}
        if "id" not in message:
            return None  # notifications/initialized, notifications/cancelled, …
        method, params, ident = message["method"], message.get("params") or {}, message["id"]

        def ok(result):
            return {"jsonrpc": "2.0", "id": ident, "result": result}

        def fail(code, text):
            return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": text}}

        if method == "initialize":
            wanted = params.get("protocolVersion")
            return ok({"protocolVersion": wanted if wanted in PROTOCOLS else PROTOCOLS[0],
                       "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER,
                       "instructions": f"{INSTRUCTIONS}\n\nAddress: {base}"})
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": [{key: tool[key] for key in ("name", "title", "description", "inputSchema",
                                                              "annotations")} for tool in self.tools.values()]})
        if method == "tools/call":
            tool = self.tools.get(params.get("name"))
            if tool is None:
                return fail(-32602, f"Unknown tool: {params.get('name')}")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return fail(-32602, "arguments must be an object")
            unknown = sorted(set(arguments) - set(tool["inputSchema"]["properties"]))
            missing = [key for key in tool["inputSchema"]["required"] if arguments.get(key) in (None, "")]
            try:
                if unknown or missing:
                    raise ToolError("; ".join(filter(None, [
                        unknown and f"unbekannt: {', '.join(unknown)}", missing and f"fehlt: {', '.join(missing)}"])))
                result = await tool["handler"](arguments, base)
            except ToolError as error:
                return ok({"content": [{"type": "text", "text": str(error)}], "isError": True})
            except (TypeError, ValueError) as error:
                return ok({"content": [{"type": "text", "text": f"Ungültige Angabe: {error}"}], "isError": True})
            if isinstance(result, list) and result and isinstance(result[0], dict) and "type" in result[0] \
                    and result[0]["type"] in ("image", "text"):
                return ok({"content": result, "isError": False})
            answer = {"content": text_of(result), "isError": False}
            if isinstance(result, dict):
                answer["structuredContent"] = result
            return ok(answer)
        if method in ("resources/list", "prompts/list"):
            return ok({method.split("/")[0]: []})
        return fail(-32601, f"Method not found: {method}")

    def add_routes(self, app: FastAPI) -> None:
        @app.post("/mcp")
        async def mcp(request: Request):
            if foreign_origin(request):
                return JSONResponse({"error": "fremde Origin"}, status_code=403)
            try:
                message = json.loads(await request.body() or b"null")
            except ValueError:
                return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                                    status_code=400)
            base = base_url(request)
            if isinstance(message, list):  # Stapel (Protokoll 2025-03-26)
                answers = [a for a in [await self.handle(m, base) for m in message] if a is not None]
                return JSONResponse(answers) if answers else Response(status_code=202)
            answer = await self.handle(message, base)
            return JSONResponse(answer) if answer is not None else Response(status_code=202)

        @app.get("/mcp")
        def mcp_stream():
            # Keine Nachrichten vom Server an den Client, also kein SSE-Stream (so erlaubt die Spezifikation 405).
            return Response(status_code=405, headers={"allow": "POST"})

        @app.delete("/mcp")
        def mcp_end():
            return Response(status_code=405, headers={"allow": "POST"})

        @app.get("/mcp/downloads/{token}/{name}")
        def download(token: str, name: str):
            item = self.downloads.get(token)
            if item is None or item["name"] != name:
                return JSONResponse({"error": "Abgelaufen; export_model noch einmal aufrufen"}, status_code=404)
            return Response(item["data"], media_type=item["type"],
                            headers={"content-disposition": f'attachment; filename="{name}"'})


def add_routes(app: FastAPI) -> Mcp:
    server = Mcp(app)
    server.add_routes(app)
    app.state.mcp = server
    return server

"""Die Website: eine JSON-Schnittstelle unter /api und die React-Oberfläche (frontend/dist) unter /. Dateien gehen über
store.py auf die T7 von oi-o-mat; hier bleibt nur, was gerade im Speicher ist."""
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import assist, cutout
from .config import Config
from .gate import Gate, GateBusy, GateRefused, register_with_gate
from .jobs import OPEN, Jobs
from .store import NotMounted, Store, StoreError, new_id

MAX_UPLOAD = 40 * 1024 * 1024
FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "dist"
TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


class Options(BaseModel):
    """Was der Auftrag an /v1/3d/generations mitgibt; ohne Angabe gilt defaults aus der Einstellung."""
    texture_size: int | None = Field(None, description="512, 1024, 2048 oder 4096")
    decimation_target: int | None = Field(None, ge=10_000, le=2_000_000)
    resolution: int | None = Field(None, description="512, 1024 oder 1536")
    steps: int | None = Field(None, ge=1, le=50)
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
    roughness_scale: float | None = Field(None, ge=0.05, le=2)
    clearcoat: float | None = Field(None, ge=0, le=1)


class JobRequest(BaseModel):
    imageId: str
    options: Options = Options()


class PromptRequest(BaseModel):
    idea: str = Field(..., min_length=1, max_length=1000)


def make_store(config: Config, log=print) -> Store:
    spec = config.store
    return Store(ssh=spec.get("ssh"), key=spec.get("key"), folder=spec.get("folder"),
                 known_hosts=spec.get("knownHosts"), log=log)


def create_app(config: Config, gate: Gate | None = None, store: Store | None = None, register: bool = True,
               log=print) -> FastAPI:
    store = store or make_store(config, log)
    gate = gate or Gate(config.gate_url, config.app_name, config.request_timeout_minutes)
    jobs = Jobs(store, gate, config.trellis_model, config.defaults, log)

    @asynccontextmanager
    async def lifespan(_app):
        if register and config.gate:
            threading.Thread(target=register_with_gate, args=({**config.gate, "url": config.gate_url, "app": config.app_name}, log),
                             kwargs={"stop": jobs.stop}, daemon=True).start()
        jobs.start()
        if config.cutout in ("auto", "rembg"):
            threading.Thread(target=cutout.warm, args=(config.rembg_model, log), daemon=True).start()
        yield
        jobs.stop.set()
        jobs.wake.set()

    app = FastAPI(title="3d-o-mat", lifespan=lifespan)
    app.state.jobs = jobs
    app.state.store = store

    @app.exception_handler(NotMounted)
    async def not_mounted(_request: Request, error: NotMounted):
        return JSONResponse({"error": str(error)}, status_code=503)

    @app.exception_handler(StoreError)
    async def store_error(_request: Request, error: StoreError):
        return JSONResponse({"error": f"Ablage auf oi-o-mat: {error}"}, status_code=502)

    @app.exception_handler(GateRefused)
    async def refused(_request: Request, error: GateRefused):
        return JSONResponse({"error": f"{error} (in der Oberfläche der Schranke /gate freigeben)"}, status_code=409)

    @app.exception_handler(GateBusy)
    async def busy(_request: Request, error: GateBusy):
        return JSONResponse({"error": f"oi-o-mat ist beschäftigt: {error}"}, status_code=503)

    def image(image_id: str) -> dict:
        store.ensure_mounted()
        try:
            return store.read("images", image_id)
        except KeyError:
            raise HTTPException(404, "Kein solches Bild") from None

    def job(job_id: str) -> dict:
        store.ensure_mounted()
        try:
            return store.read("jobs", job_id)
        except KeyError:
            raise HTTPException(404, "Kein solcher Auftrag") from None

    def file(kind: str, item: str, name: str, media_type: str, filename: str | None = None) -> Response:
        try:
            data = store.get(kind, item, name)
        except FileNotFoundError:
            raise HTTPException(404, "Datei fehlt auf der T7") from None
        headers = {"cache-control": "private, max-age=86400"}
        if filename:
            headers["content-disposition"] = f'attachment; filename="{filename}"'
        return Response(data, media_type=media_type, headers=headers)

    # --- Zustand

    @app.get("/api/status")
    def status():
        storage = store.check()
        gate_status = gate.status()
        mine = (gate_status or {}).get("apps", {}).get(config.app_name)
        models = {m.get("model"): {"state": m.get("state"), "approved": m.get("approved"), "backend": m.get("backend")}
                  for m in (mine or {}).get("models", [])}
        return {
            "storage": {"where": store.name, "mounted": bool(storage.get("mounted")), "root": storage.get("root"),
                        "freeGb": storage.get("freeGb"), "error": storage.get("error")},
            "rembg": cutout.rembg_available(),
            "cutout": config.cutout,
            "models": {"trellis": config.trellis_model, "assist": config.assist_model},
            "defaults": config.defaults,
            "gate": {
                "url": config.gate_url,
                "reachable": gate_status is not None,
                "tonwerk": (gate_status or {}).get("tonwerk"),
                "registered": bool(mine and mine.get("known")),
                "held": (mine or {}).get("held"),
                "models": models,
            },
            "imageGeneration": None,  # kommt mit dem Bilddienst (img-o-mat)
        }

    # --- Bilder

    @app.post("/api/images")
    async def upload(file_: UploadFile = File(..., alias="file")):
        store.ensure_mounted()
        data = await file_.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "Bild größer als 40 MB")
        try:
            # Im Thread: das Freistellen dauert Sekunden, die Seite fragt derweil weiter nach dem Stand.
            png, info = await run_in_threadpool(cutout.cut, data, config.cutout, config.rembg_model)
        except Exception as error:  # noqa: BLE001 – Pillow wirft je nach Format anderes
            raise HTTPException(400, f"Kein lesbares Bild: {error}") from error
        image_id = new_id()
        suffix = re.sub(r"[^a-z0-9]", "", Path(file_.filename or "").suffix.lower())[:5]
        suffix = suffix if suffix in TYPES else "bin"
        store.put("images", image_id, f"original.{suffix}", data)
        store.put("images", image_id, "cutout.png", png)
        doc = {"id": image_id, "name": Path(file_.filename or "bild").stem[:80], "original": f"original.{suffix}",
               "cutout": info, "check": None, "created": image_id}
        return store.write("images", doc)

    @app.get("/api/images")
    def images():
        return store.list("images")

    @app.get("/api/images/{image_id}/{kind}")
    def image_file(image_id: str, kind: str):
        doc = image(image_id)
        if kind == "cutout":
            return file("images", image_id, "cutout.png", "image/png")
        if kind == "original":
            name = doc["original"]
            return file("images", image_id, name, TYPES.get(name.rsplit(".", 1)[-1], "application/octet-stream"))
        raise HTTPException(404, "original oder cutout")

    @app.post("/api/images/{image_id}/check")
    def check(image_id: str):
        doc = image(image_id)
        doc["check"] = assist.check_image(gate, config.assist_model, store.get("images", image_id, "cutout.png"))
        return store.write("images", doc)

    @app.delete("/api/images/{image_id}")
    def delete_image(image_id: str):
        image(image_id)
        if any(j["imageId"] == image_id and j["state"] in OPEN for j in jobs.all()):
            raise HTTPException(409, "Für dieses Bild läuft noch ein Auftrag")
        store.delete("images", image_id)
        return {"deleted": image_id}

    # --- Prompts (Bilderzeugung folgt über img-o-mat)

    @app.post("/api/prompts")
    def prompts(request: PromptRequest):
        try:
            return assist.image_prompts(gate, config.assist_model, request.idea)
        except ValueError as error:
            raise HTTPException(502, str(error)) from error

    # --- Aufträge

    @app.post("/api/jobs")
    def create_job(request: JobRequest):
        doc = image(request.imageId)
        options = request.options.model_dump(exclude_none=True)
        if options.get("texture_size") not in (None, 512, 1024, 2048, 4096):
            raise HTTPException(422, "texture_size: 512, 1024, 2048 oder 4096")
        if options.get("resolution") not in (None, 512, 1024, 1536):
            raise HTTPException(422, "resolution: 512, 1024 oder 1536")
        return jobs.create(doc, options)

    @app.get("/api/jobs")
    def list_jobs():
        return jobs.all()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return job(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        job(job_id)
        try:
            return jobs.cancel(job_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str):
        if job(job_id)["state"] in OPEN:
            raise HTTPException(409, "Erst abbrechen oder fertig werden lassen")
        store.delete("jobs", job_id)
        return {"deleted": job_id}

    @app.get("/api/jobs/{job_id}/model.glb")
    def model(job_id: str, download: bool = False):
        doc = job(job_id)
        if doc["state"] != "done":
            raise HTTPException(404, "Noch kein Modell")
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", doc.get("imageName") or "modell").strip("-") or "modell"
        return file("jobs", job_id, "model.glb", "model/gltf-binary", f"{name}-{job_id}.glb" if download else None)

    if FRONTEND.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")

    return app

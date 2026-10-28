"""Die Website: eine JSON-Schnittstelle unter /api und die React-Oberfläche (frontend/dist) unter /."""
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import assist, cutout
from .config import Config
from .gate import Gate, GateBusy, GateRefused, register_with_gate
from .jobs import OPEN, Jobs
from .store import NotMounted, Store, read_json, write_json

MAX_UPLOAD = 40 * 1024 * 1024
FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "dist"


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


def create_app(config: Config, gate: Gate | None = None, register: bool = True, log=print) -> FastAPI:
    store = Store(config.data_path, config.mount_command, log)
    gate = gate or Gate(config.gate_url, config.app_name, config.request_timeout_minutes)
    jobs = Jobs(store, gate, config.trellis_model, config.defaults, log)

    @asynccontextmanager
    async def lifespan(_app):
        if register and config.gate:
            threading.Thread(target=register_with_gate, args=({**config.gate, "url": config.gate_url, "app": config.app_name}, log),
                             kwargs={"stop": jobs.stop}, daemon=True).start()
        try:
            store.ensure_mounted()
            jobs.recover()
        except NotMounted as error:
            log(str(error))
        jobs.start()
        yield
        jobs.stop.set()
        jobs.wake.set()

    app = FastAPI(title="3d-o-mat", lifespan=lifespan)
    app.state.jobs = jobs
    app.state.store = store

    @app.exception_handler(NotMounted)
    async def not_mounted(_request: Request, error: NotMounted):
        return JSONResponse({"error": str(error)}, status_code=503)

    @app.exception_handler(GateRefused)
    async def refused(_request: Request, error: GateRefused):
        return JSONResponse({"error": f"{error} (in der Oberfläche der Schranke /gate freigeben)"}, status_code=409)

    @app.exception_handler(GateBusy)
    async def busy(_request: Request, error: GateBusy):
        return JSONResponse({"error": f"oi-o-mat ist beschäftigt: {error}"}, status_code=503)

    def image_dir(image_id: str) -> Path:
        store.ensure_mounted()
        try:
            path = store.dir("images", image_id)
        except KeyError:
            raise HTTPException(404, "Kein solches Bild") from None
        if not (path / "image.json").is_file():
            raise HTTPException(404, "Kein solches Bild")
        return path

    def job_dir(job_id: str) -> Path:
        store.ensure_mounted()
        try:
            path = store.dir("jobs", job_id)
        except KeyError:
            raise HTTPException(404, "Kein solcher Auftrag") from None
        if not (path / "job.json").is_file():
            raise HTTPException(404, "Kein solcher Auftrag")
        return path

    # --- Zustand

    @app.get("/api/status")
    def status():
        gate_status = gate.status()
        mine = (gate_status or {}).get("apps", {}).get(config.app_name)
        models = {m.get("model"): {"state": m.get("state"), "approved": m.get("approved"), "backend": m.get("backend")}
                  for m in (mine or {}).get("models", [])}
        return {
            "dataDir": str(config.data_path),
            "mounted": store.mounted,
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
    async def upload(file: UploadFile = File(...)):
        store.ensure_mounted()
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "Bild größer als 40 MB")
        try:
            png, info = cutout.cut(data, config.cutout, config.rembg_model)
        except Exception as error:  # noqa: BLE001 – Pillow wirft je nach Format anderes
            raise HTTPException(400, f"Kein lesbares Bild: {error}") from error
        image_id, path = store.create("images")
        suffix = re.sub(r"[^a-z0-9]", "", Path(file.filename or "").suffix.lower())[:5] or "bin"
        (path / f"original.{suffix}").write_bytes(data)
        (path / "cutout.png").write_bytes(png)
        image = {"id": image_id, "name": Path(file.filename or "bild").stem[:80], "original": f"original.{suffix}",
                 "cutout": info, "check": None, "created": image_id}
        write_json(path / "image.json", image)
        return image

    @app.get("/api/images")
    def images():
        return store.list("images", "image.json")

    @app.get("/api/images/{image_id}/{kind}")
    def image_file(image_id: str, kind: str):
        path = image_dir(image_id)
        if kind == "cutout":
            return FileResponse(path / "cutout.png", media_type="image/png")
        if kind == "original":
            return FileResponse(path / read_json(path / "image.json")["original"])
        raise HTTPException(404, "original oder cutout")

    @app.post("/api/images/{image_id}/check")
    def check(image_id: str):
        path = image_dir(image_id)
        result = assist.check_image(gate, config.assist_model, (path / "cutout.png").read_bytes())
        image = read_json(path / "image.json")
        image["check"] = result
        write_json(path / "image.json", image)
        return image

    @app.delete("/api/images/{image_id}")
    def delete_image(image_id: str):
        path = image_dir(image_id)
        if any(job["imageId"] == image_id and job["state"] in OPEN for job in jobs.all()):
            raise HTTPException(409, "Für dieses Bild läuft noch ein Auftrag")
        _remove(path)
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
        path = image_dir(request.imageId)
        options = request.options.model_dump(exclude_none=True)
        if options.get("texture_size") not in (None, 512, 1024, 2048, 4096):
            raise HTTPException(422, "texture_size: 512, 1024, 2048 oder 4096")
        if options.get("resolution") not in (None, 512, 1024, 1536):
            raise HTTPException(422, "resolution: 512, 1024 oder 1536")
        return jobs.create(read_json(path / "image.json"), options)

    @app.get("/api/jobs")
    def list_jobs():
        return jobs.all()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job_dir(job_id)
        return jobs.get(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        job_dir(job_id)
        try:
            return jobs.cancel(job_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str):
        path = job_dir(job_id)
        if jobs.get(job_id)["state"] in OPEN:
            raise HTTPException(409, "Erst abbrechen oder fertig werden lassen")
        _remove(path)
        return {"deleted": job_id}

    @app.get("/api/jobs/{job_id}/model.glb")
    def model(job_id: str, download: bool = False):
        path = job_dir(job_id)
        glb = path / "model.glb"
        if not glb.is_file():
            raise HTTPException(404, "Noch kein Modell")
        job = jobs.get(job_id)
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", job.get("imageName") or "modell").strip("-") or "modell"
        return FileResponse(glb, media_type="model/gltf-binary", filename=f"{name}-{job_id}.glb" if download else None,
                            content_disposition_type="attachment" if download else "inline")

    if FRONTEND.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")

    return app


def _remove(path: Path) -> None:
    for child in sorted(path.rglob("*"), reverse=True):
        child.rmdir() if child.is_dir() else child.unlink()
    path.rmdir()

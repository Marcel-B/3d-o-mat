"""Eine TRELLIS-Figur aufbereiten, als Schritt der Schnittstelle unter /v1: aus einer .blend mit Körper (ohne Kopf) und
Kopf (mit langem Hals) wird eine saubere und eine verkleinerte Figur für das Rigging (prep/figur_aufbereiten.py).
Gerechnet wird in Blender auf oi-o-mat, im Aufbereitungs-Dienst hinter der Schranke (prep/server.py); hier wird nur
geprüft, eingereiht und abgelegt.

    POST   /v1/files                          die .blend als rohe Bytes -> {"sha256"} (wie Bilder, bis 400 MB)
    POST   /v1/figures/preparations           Auftrag anlegen, kehrt sofort zurück (202)
    GET    /v1/figures/preparations           die letzten 100 Aufbereitungen, neueste zuerst
    GET    /v1/figures/preparations/<id>      Stand, Bericht, Ergebnis
    DELETE /v1/figures/preparations/<id>      eine wartende herausnehmen
    POST   /v1/figures/players                Spielerfigur: Kopf (GLB aus /v1/models3d, mit Hals) auf den Teamkörper
                                              (body, Eingabe-.blend) setzen, aufbereiten, Skelett der Teamfigur (rig,
                                              .blend mit Armature) übertragen; Stand und Ergebnis wie oben (prep_…)

Das Ergebnis sind Dateien unter /v1/files/<sha256>: glb (ein Mesh, ein Material, Atlas, ohne Skelett), blend (die
Arbeitsdatei mit 1_Original, 2_Sauber, 3_Klein), preview (PNG, drei Ansichten), report (der JSON-Bericht), auf Wunsch
fbx (ZIP mit FBX und Texturordner). Exit-Code 2 des Skripts heißt: die Rückseiten-Prüfung aus 14 Richtungen ist nicht
bestanden. Dann steht der Auftrag als failed da, mit Grund, aber die Dateien sind zum Ansehen trotzdem da
(`result.passed` ist false). Ein bestandener Auftrag steht auf der Seite als Figur und kann ein Skelett bekommen.
Bei einer Spielerfigur kommen fbx (die Figur mit 65 Knochen, Material Figur_<name>) und textures (ZIP mit
<name>-farbe.png und <name>-normal.png) dazu, so wie Iron Rush sie unter Resources/Animationen/<name>/ erwartet.
"""
import json
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .jobs import OPEN, order
from .models3d import SHA256, ApiError
from .store import valid_id

PREFIX = "prep_"
LISTED = 100
TEXTURES = (512, 1024, 2048, 4096)
NAME = re.compile(r"^[^\x00-\x1f]{1,63}$")
PLAYER = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
# Die Grenzen wie in prep/server.py, damit ein falscher Wert gleich hier abgewiesen wird und nicht erst nach der Schlange.
NUMBERS = {"height": (0.3, 3.0, float), "body_triangles": (2000, 300000, int), "head_triangles": (2000, 200000, int),
           "head_voxels": (100, 800, int)}
FIELDS = {"blend", "creator", "label", "texture", "skin_color", "body", "head", "retouch", "check", "fbx", *NUMBERS}
PLAYER_FIELDS = {"name", "head", "body", "rig", "creator", "label", "texture", "skin_color", "check", *NUMBERS}
# Kennzahlen aus dem Bericht, die die Antwort oben zeigt; der ganze Bericht steht unter report.
SUMMARY = ("dreiecke_klein_gesamt", "kopf_modus", "kopf_volumen_gefuellt", "pruefung_rueckseiten_pixel",
           "pruefung_bestanden", "laufzeit_sekunden")


def parse(body, app: str | None) -> dict:
    if not isinstance(body, dict):
        raise ApiError(400, "Der Body muss ein JSON-Objekt sein")
    unknown = sorted(set(body) - FIELDS)
    if unknown:
        raise ApiError(400, f"Unbekannte Felder: {', '.join(unknown)}")
    blend = body.get("blend")
    if not isinstance(blend, str) or not SHA256.match(blend):
        raise ApiError(400, "blend: der SHA-256 einer .blend aus POST /v1/files (64 Zeichen 0-9a-f)")
    options = {"height": 1.89}
    for name, (low, high, kind) in NUMBERS.items():
        value = body.get(name, options.get(name))
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind is int and value != int(value)):
            raise ApiError(400, f"{name}: eine Zahl von {low} bis {high}")
        if not low <= value <= high:
            raise ApiError(400, f"{name}: erlaubt sind {low} bis {high}, nicht {value}")
        options[name] = kind(value)
    if "texture" in body:
        if isinstance(body["texture"], bool) or body["texture"] not in TEXTURES:
            raise ApiError(400, f"texture: {', '.join(map(str, TEXTURES))}")
        options["texture"] = int(body["texture"])
    if body.get("skin_color") is not None:
        skin = body["skin_color"]
        if (not isinstance(skin, list) or len(skin) != 3
                or any(isinstance(c, bool) or not isinstance(c, (int, float)) or not 0 <= c <= 1 for c in skin)):
            raise ApiError(400, "skin_color: drei Zahlen von 0 bis 1 (r, g, b)")
        options["skin_color"] = [float(c) for c in skin]
    for name in ("body", "head"):
        if body.get(name) is not None:
            if not isinstance(body[name], str) or not NAME.match(body[name]):
                raise ApiError(400, f"{name}: der Name eines Objekts in der .blend (bis 63 Zeichen)")
            options[name] = body[name]
    if body.get("retouch") is not None:
        boxes = body["retouch"]
        if not isinstance(boxes, list) or not 0 < len(boxes) <= 50 or not all(isinstance(b, dict) for b in boxes):
            raise ApiError(400, "retouch: eine Liste von 1 bis 50 Quadern")
        for box in boxes:
            for corner in ("lo", "hi"):
                point = box.get(corner)
                if (not isinstance(point, list) or len(point) != 3
                        or any(isinstance(c, bool) or not isinstance(c, (int, float)) for c in point)):
                    raise ApiError(400, f"retouch: jeder Quader braucht {corner} mit drei Zahlen")
        options["retouch"] = boxes
    for name, default in (("check", True), ("fbx", False)):
        value = body.get(name, default)
        if not isinstance(value, bool):
            raise ApiError(400, f"{name}: true oder false")
        options[name] = value
    texts = {}
    for name in ("creator", "label"):
        value = body.get(name)
        if value is not None and (not isinstance(value, str) or len(value) > 200):
            raise ApiError(400, f"{name}: ein Text bis 200 Zeichen")
        texts[name] = value
    return {"file": blend, "options": options,
            "api": {"app": app, "creator": texts["creator"] or app, "label": texts["label"]}}


def parse_player(body, app: str | None) -> dict:
    """Wie parse, aber: name, head (GLB), body (.blend des Teams) und rig (.blend der Teamfigur mit Skelett)."""
    if not isinstance(body, dict):
        raise ApiError(400, "Der Body muss ein JSON-Objekt sein")
    unknown = sorted(set(body) - PLAYER_FIELDS)
    if unknown:
        raise ApiError(400, f"Unbekannte Felder: {', '.join(unknown)}")
    name = body.get("name")
    if not isinstance(name, str) or not PLAYER.match(name):
        raise ApiError(400, "name: Kleinbuchstaben, Ziffern und -, wie der Ordner im Spiel (z. B. azari)")
    for key in ("head", "rig"):
        if not isinstance(body.get(key), str) or not SHA256.match(body[key]):
            raise ApiError(400, f"{key}: der SHA-256 einer Datei aus POST /v1/files (64 Zeichen 0-9a-f)")
    # Die Objektnamen gibt es hier nicht: body ist die Teamvorlage mit Koerper_ohne_Kopf und Kopf_langer_Hals.
    rest = {key: value for key, value in body.items() if key not in ("name", "head", "body", "rig")}
    fields = parse({**rest, "blend": body.get("body")}, app)
    fields["options"].pop("fbx", None)   # die Spielerfigur ist immer eine FBX
    fields["player"] = {"name": name, "head": body["head"], "rig": body["rig"]}
    fields["api"]["label"] = fields["api"]["label"] or name.upper()
    return fields


class Figures:
    """Die Endpunkte und was jobs.py für eine Aufbereitung braucht (finish)."""

    def __init__(self, config, store, models3d, log=print):
        self.config = config
        self.store = store
        self.models3d = models3d
        self.log = log
        self.jobs = None

    # --- für jobs.py

    def finish(self, job: dict, payload: dict) -> tuple[bytes, dict, str | None]:
        """Die Dateien des Dienstes nach ihrem SHA-256 ablegen. Zurück: das GLB (wird model.glb des Auftrags), das
        Ergebnis für die Antwort und der Grund, wenn die Prüfung nicht bestanden ist (Exit-Code 2), sonst None."""
        app = job["api"].get("app")
        files = payload["files"]
        report = payload.get("report") or {}
        result = {"files": {}}
        player = bool(job.get("player"))
        for key, media_type in (("glb", "model/gltf-binary"), ("blend", "application/x-blender"),
                                ("preview", "image/png"),
                                ("fbx", "application/octet-stream" if player else "application/zip"),
                                ("textures", "application/zip")):
            if files.get(key):
                result["files"][key], _ = self.models3d.put_file(files[key], media_type, app)
        result["files"]["report"], _ = self.models3d.put_file(
            json.dumps(report, ensure_ascii=False, indent=2).encode(), "application/json", app)
        result.update({"passed": payload.get("passed"), "exit_code": payload.get("exitCode"),
                       "bytes": len(files["glb"]), **{key: report[key] for key in SUMMARY if key in report}})
        failure = None
        if payload.get("exitCode") == 2 or payload.get("passed") is False:
            pixels = report.get("pruefung_rueckseiten_pixel")
            failure = ("Prüfung nicht bestanden: " + (f"{pixels} Pixel Rückseite sichtbar (erlaubt 200)"
                                                      if pixels is not None else "Rückseiten sichtbar")
                       + "; Dateien zum Ansehen unter result.files")
        return files["glb"], {**result, "report": report}, failure

    # --- Ansicht

    def positions(self) -> dict[str, int]:
        queued = sorted((job for job in self.jobs.all() if job["state"] == "queued"), key=order)
        return {job["id"]: index + 1 for index, job in enumerate(queued)}

    def view(self, job: dict, positions: dict | None = None) -> dict:
        positions = self.positions() if positions is None else positions
        state = job["state"]
        result = job.get("result")
        return {
            "id": PREFIX + job["id"], "status": {"waiting": "running"}.get(state, state),
            "position": positions.get(job["id"], 0),
            "progress": {"done": 1.0, "running": 0.5}.get(state, 0.0) if state != "failed" else 0.0,
            "error": job.get("message") if state == "failed" else None,
            "message": job.get("message") if state in OPEN else None,
            "passed": (result or {}).get("passed"),
            # Auch bei failed, wenn das Skript durchlief und nur die Prüfung scheiterte: zum Ansehen.
            "result": result if state in ("done", "failed") and result else None,
            "options": {key: value for key, value in job["options"].items() if key != "retouch"},
            "blend": job["file"], "creator": job["api"].get("creator"), "label": job["api"].get("label"),
            "player": job.get("player"),
            "created": job["created"], "finished": job.get("finished"), "seconds": job.get("seconds"),
            # Der Auftrag auf der Seite (/api/jobs/<id>): dort ansehen und ein Skelett einsetzen.
            "job": job["id"],
        }

    def job(self, api_id: str) -> dict:
        internal = api_id.removeprefix(PREFIX)
        if not api_id.startswith(PREFIX) or not valid_id(internal):
            raise ApiError(404, f"Keine Aufbereitung {api_id}")
        self.store.ensure_mounted()
        try:
            job = self.jobs.get(internal)
        except KeyError:
            raise ApiError(404, f"Keine Aufbereitung {api_id}") from None
        if job.get("kind") != "prep":
            raise ApiError(404, f"Keine Aufbereitung {api_id}")
        return job

    # --- Endpunkte

    def add_routes(self, app: FastAPI, caller) -> None:
        @app.post("/v1/figures/preparations")
        async def create(request: Request):
            try:
                body = json.loads(await request.body() or b"null")
            except ValueError:
                raise ApiError(400, "Kein gültiges JSON") from None
            fields = parse(body, caller(request))
            self.store.ensure_mounted()
            info = self.models3d.file_info(fields["file"])
            if info is None:
                raise ApiError(404, f"Keine Datei {fields['file']}; erst mit POST /v1/files hochladen")
            if info.get("type") != "application/x-blender":
                raise ApiError(400, "blend: das ist keine .blend-Datei")
            job = self.jobs.create_prep(fields)
            view = self.view(job)
            return JSONResponse({key: view[key] for key in ("id", "status", "position")}, status_code=202)

        @app.post("/v1/figures/players")
        async def create_player(request: Request):
            try:
                body = json.loads(await request.body() or b"null")
            except ValueError:
                raise ApiError(400, "Kein gültiges JSON") from None
            fields = parse_player(body, caller(request))
            self.store.ensure_mounted()
            wanted = {"body": (fields["file"], "application/x-blender"),
                      "rig": (fields["player"]["rig"], "application/x-blender"),
                      "head": (fields["player"]["head"], "model/gltf-binary")}
            for key, (digest, media_type) in wanted.items():
                info = self.models3d.file_info(digest)
                if info is None:
                    raise ApiError(404, f"{key}: keine Datei {digest}; erst mit POST /v1/files hochladen")
                if info.get("type") != media_type:
                    raise ApiError(400, f"{key}: erwartet {media_type}, nicht {info.get('type')}")
            job = self.jobs.create_prep(fields)
            view = self.view(job)
            return JSONResponse({key: view[key] for key in ("id", "status", "position")}, status_code=202)

        @app.get("/v1/figures/preparations")
        def listing():
            mine = sorted((job for job in self.jobs.all() if job.get("kind") == "prep"), key=order, reverse=True)[:LISTED]
            positions = self.positions()
            return {"data": [self.view(job, positions) for job in mine]}

        @app.get("/v1/figures/preparations/{api_id}")
        def one(api_id: str):
            return self.view(self.job(api_id))

        @app.delete("/v1/figures/preparations/{api_id}")
        def remove(api_id: str):
            job = self.job(api_id)
            if job["state"] != "queued":
                raise ApiError(409, "Die Aufbereitung rechnet schon und darf zu Ende rechnen"
                               if job["state"] in ("waiting", "running") else f"Sie wartet nicht mehr ({job['state']})")
            return self.view(self.jobs.cancel(job["id"]))

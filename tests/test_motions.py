"""Die Bewegungsbibliothek: Aufnahmen holen, auf 30 Bilder je Sekunde bringen, auflisten und ausliefern."""
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from omat3d import motions
from omat3d.app import create_app
from omat3d.config import Config
from omat3d.gate import Gate
from omat3d.store import Store


def bvh(frames: int, frame_time: float) -> str:
    """Eine kleine BVH-Datei: Hüfte mit Lage und Drehung, ein Bein; Bild i hebt die Hüfte auf i."""
    lines = "\n".join(f"0 {i} 0 0 0 {i * 0.123456789}" + " 0 0 0" for i in range(frames))
    return ("HIERARCHY\nROOT Hips\n{\n OFFSET 0 0 0\n CHANNELS 6 Xposition Yposition Zposition Zrotation Yrotation Xrotation\n"
            " JOINT LeftUpLeg\n {\n  OFFSET 1 -1 0\n  CHANNELS 3 Zrotation Yrotation Xrotation\n"
            "  End Site\n  {\n   OFFSET 0 -1 0\n  }\n }\n}\n"
            f"MOTION\nFrames: {frames}\nFrame Time: {frame_time}\n{lines}\n")


def test_resample_keeps_the_rest_frame_and_takes_every_fourth_of_120():
    text, frames, seconds = motions.resample(bvh(121, 1 / 120))
    body = text.split("MOTION")[1].strip().splitlines()
    # Bild 0 (die T-Pose der CMU-Fassung) bleibt vorn als Ruhehaltung, dann jedes vierte: 1, 5, 9 …
    assert frames == 30 and body[0] == "Frames: 31" and body[1] == "Frame Time: 0.0333333"
    assert [body[i].split()[1] for i in (2, 3, 4)] == ["0", "1", "5"] and body[4].split()[5] == "0.6173"
    assert seconds == pytest.approx(29 / 30) and text.startswith("HIERARCHY")


def test_a_rest_frame_goes_in_front_and_must_fit():
    text, frames, _seconds = motions.resample(bvh(10, 1 / 30), rest="9 9 9 0 0 0 0 0 0")
    body = text.split("MOTION")[1].strip().splitlines()
    assert frames == 10 and body[2] == "9 9 9 0 0 0 0 0 0" and body[3].split()[1] == "0"
    assert motions.first_frame(bvh(3, 1 / 30)).split()[1] == "0"
    with pytest.raises(motions.MotionError, match="Ruhehaltung"):
        motions.resample(bvh(10, 1 / 30), rest="1 2 3")
    for junk in ["kein bvh", "HIERARCHY\nMOTION\nFrames: 1\nFrame Time: 0.03\n0 0 0\n", "HIERARCHY\nMOTION\n"]:
        with pytest.raises(motions.MotionError):
            motions.resample(junk)


def test_catalogue_names_both_sources_once():
    entries = motions.catalogue()
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)) and all(e["category"] in motions.CATEGORIES for e in entries)
    bandai = [e for e in entries if e["source"] == "bandai"]
    # Datensatz 1 hat 175 Aufnahmen; alle Stile sind übersetzt.
    assert len(bandai) == 175 and all(e["style"] in motions.STYLES for e in bandai)
    assert {e["source"] for e in entries} == set(motions.SOURCES)
    assert motions.SOURCES["bandai"]["commercial"] is False and motions.SOURCES["cmu"]["commercial"] is True


def test_fetch_writes_index_and_survives_a_missing_file(tmp_path):
    asked = []

    def opener(request, timeout):
        asked.append(request.full_url)
        if "02_01" in request.full_url or "walk_sad" in request.full_url:
            return io.BytesIO(bvh(13, 1 / 120).encode())
        raise OSError("404")

    logged = []
    index = motions.fetch(tmp_path, {"cmu-02_01", "bandai-walk-sad-001"}, opener=opener, log=logged.append)
    # Bandai braucht seine Ruhehaltung aus REST_BANDAI; ohne die fehlt die Aufnahme, der Rest geht trotzdem.
    assert [c["id"] for c in index["clips"]] == ["cmu-02_01"] and index["clips"][0]["frames"] == 3
    assert "base" not in index["sources"]["cmu"] and index["categories"]["walk"]["de"] == "Gehen"
    assert "1 fehlen: bandai-walk-sad-001" in logged[-1]
    rest = (motions.SOURCES["bandai"]["base"] + "/" + motions.REST_BANDAI)
    assert rest in asked
    assert json.loads((tmp_path / "index.json").read_text())["clips"][0]["source"] == "cmu"
    # Schon geholte Aufnahmen werden nicht noch einmal geladen.
    asked.clear()
    motions.fetch(tmp_path, {"cmu-02_01"}, opener=opener, log=logged.append)
    assert asked == []


def test_library_is_listed_and_served(tmp_path):
    folder = tmp_path / "motions"
    motions.fetch(folder, {"cmu-02_01"}, opener=lambda request, timeout: io.BytesIO(bvh(9, 1 / 120).encode()),
                  log=lambda *_: None)
    config = Config(trellis_model="trellis", assist_model="gemma", motions_dir=str(folder))
    gate = Gate("http://gate", "3d-o-mat", transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    app = create_app(config, gate=gate, store=Store(folder=str(tmp_path), log=lambda *_: None), register=False,
                     log=lambda *_: None)
    with TestClient(app) as client:
        listed = client.get("/api/motions").json()
        assert [c["id"] for c in listed["clips"]] == ["cmu-02_01"] and listed["sources"]["bandai"]["commercial"] is False
        clip = client.get("/api/motions/cmu-02_01.bvh")
        assert clip.status_code == 200 and clip.text.startswith("HIERARCHY") and "max-age" in clip.headers["cache-control"]
        for wrong in ["cmu-99_99", "..%2Findex", "CMU-02_01"]:
            assert client.get(f"/api/motions/{wrong}.bvh").status_code == 404


def test_without_library_the_list_is_empty(tmp_path):
    assert motions.Library(tmp_path / "fehlt").index()["clips"] == []

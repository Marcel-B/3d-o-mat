"""Die Einstellung von 3d-o-mat: eine JSON-Datei (config.json, Pfad auch über OMAT3D_CONFIG), Vorlage config.example.json.

Die Website läuft in einem Container auf Proxmox und hält selbst keine Daten. Gerechnet wird auf oi-o-mat, nur über
die LLM-Schranke (gate.url), und die Dateien liegen auf der T7 von oi-o-mat, erreicht per SSH (store, siehe store.py).
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.json"


@dataclass
class Config:
    listen: str = "0.0.0.0"
    port: int = 8090
    # Wo die Dateien liegen: {"ssh": "marcel@192.168.2.122", "key": "/ssh/id_ed25519", "knownHosts": "/ssh/known_hosts"}
    # für die T7 von oi-o-mat, oder {"folder": "/pfad"} für einen eigenen Ordner (Tests, alles auf einem Mac).
    store: dict = field(default_factory=lambda: {"folder": "./data"})
    # Die Anmeldung bei der LLM-Schranke, so wie sie PUT /gate/apps/<app> bekommt, dazu url und app.
    gate: dict = field(default_factory=dict)
    # LM-Studio- bzw. MLX-Serve-Keys aus gate.models.
    trellis_model: str = "ddalcu/TRELLIS.2-4B-MLX-Serve-8bit"
    assist_model: str = "gemma-4-26b-a4b-it-qat-mlx"
    # Skelett und Gewichte (SkinTokens, Rig-Dienst auf oi-o-mat, Server "rig" der Schranke).
    rig_model: str = "skintokens"
    # Was an /v1/3d/generations geht, wenn der Auftrag nichts anderes sagt.
    defaults: dict = field(default_factory=lambda: {"texture_size": 2048, "decimation_target": 300000})
    # Freistellen: "auto" (rembg, wenn installiert, sonst einfarbiger Hintergrund), "rembg", "flood" oder "none".
    cutout: str = "auto"
    rembg_model: str = "isnet-general-use"
    # So lange darf eine Anfrage an die Schranke dauern, Warten in ihrer Schlange eingeschlossen (dort bis 60 min).
    request_timeout_minutes: int = 75

    @property
    def gate_url(self) -> str:
        return self.gate.get("url", "http://192.168.2.122:1235").rstrip("/")

    @property
    def app_name(self) -> str:
        return self.gate.get("app", "3d-o-mat")



_KEYS = {
    "listen": "listen", "port": "port", "store": "store", "gate": "gate",
    "trellisModel": "trellis_model", "assistModel": "assist_model", "rigModel": "rig_model", "defaults": "defaults", "cutout": "cutout",
    "rembgModel": "rembg_model", "requestTimeoutMinutes": "request_timeout_minutes",
}


def load(path: str | os.PathLike | None = None) -> Config:
    path = Path(path or os.environ.get("OMAT3D_CONFIG") or DEFAULT_PATH)
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    unknown = sorted(set(raw) - set(_KEYS))
    if unknown:
        raise ValueError(f"{path}: unbekannte Einstellungen {', '.join(unknown)}")
    config = Config(**{_KEYS[key]: value for key, value in raw.items()})
    if "defaults" in raw:
        config.defaults = {**Config().defaults, **raw["defaults"]}
    return config

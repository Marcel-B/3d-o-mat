"""Die Einstellung von 3d-o-mat: eine JSON-Datei (config.json, Pfad auch über OMAT3D_CONFIG), Vorlage config.example.json.

Die App läuft auf mac-mini-von-marcel. Gerechnet wird auf oi-o-mat, nur über die LLM-Schranke (gate.url), und die
Daten liegen auf der T7 von oi-o-mat, auf dem mac-mini per Dateifreigabe eingebunden (dataDir).
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
    # Ordner auf der T7 von oi-o-mat, auf dem mac-mini eingebunden. Darin muss die Datei .3d-o-mat liegen, sonst gilt
    # das Laufwerk als nicht verbunden und die App speichert nichts (sie schriebe sonst auf die interne Platte).
    data_dir: str = "/Volumes/3d-o-mat"
    # Befehl, der die Freigabe einbindet, wenn sie fehlt (etwa nach einem Neustart); leer heißt: nur warten.
    mount_command: str = ""
    # Die Anmeldung bei der LLM-Schranke, so wie sie PUT /gate/apps/<app> bekommt, dazu url und app.
    gate: dict = field(default_factory=dict)
    # LM-Studio- bzw. MLX-Serve-Keys aus gate.models.
    trellis_model: str = "TRELLIS.2-4B-MLX-Serve-8bit"
    assist_model: str = "gemma-4-26b-a4b-it-qat-mlx"
    # Was an /v1/3d/generations geht, wenn der Auftrag nichts anderes sagt.
    defaults: dict = field(default_factory=lambda: {"texture_size": 2048, "decimation_target": 300000})
    # Freistellen: "auto" (rembg, wenn installiert, sonst einfarbiger Hintergrund), "rembg", "flood" oder "none".
    cutout: str = "auto"
    rembg_model: str = "birefnet-general"
    # So lange darf eine Anfrage an die Schranke dauern, Warten in ihrer Schlange eingeschlossen (dort bis 60 min).
    request_timeout_minutes: int = 75

    @property
    def gate_url(self) -> str:
        return self.gate.get("url", "http://192.168.2.122:1235").rstrip("/")

    @property
    def app_name(self) -> str:
        return self.gate.get("app", "3d-o-mat")

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)


_KEYS = {
    "listen": "listen", "port": "port", "dataDir": "data_dir", "mountCommand": "mount_command", "gate": "gate",
    "trellisModel": "trellis_model", "assistModel": "assist_model", "defaults": "defaults", "cutout": "cutout",
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

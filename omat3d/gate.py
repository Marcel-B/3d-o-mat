"""Alles, was 3d-o-mat auf oi-o-mat rechnen lässt, geht durch die LLM-Schranke (scripts-Repo, docs/llm-gate.md):
Gemma in LM Studio für Prompts und die Bildprüfung, TRELLIS.2 in MLX-Serve für die 3D-Umwandlung, SkinTokens
fürs Skelett und img-o-mat (FLUX.2 Klein, Z-Image Turbo) zum Malen. Die Schranke
lässt Tonwerk vor, reiht nach Priorität ein, lädt und entlädt die Modelle. Diese App lädt nie selbst etwas.

Regeln der Schranke: 403, 424 und 507 sind Einstellungsfehler (nicht wiederholen), 503 heißt Retry-After abwarten,
502 und ein abgerissener Stream heißen erneut versuchen.
"""
import base64
import json
import time
import urllib.parse
import urllib.request

import httpx


class GateRefused(RuntimeError):
    """403, 424, 507: Modell nicht freigegeben, nicht installiert oder zu groß; erst in /gate beheben."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class GateBusy(RuntimeError):
    """503 (zu lange gewartet, verworfen, kein Platz) oder 502: später erneut."""

    def __init__(self, message: str, retry_after: float):
        super().__init__(message)
        self.retry_after = retry_after


class StreamBroken(RuntimeError):
    """Die Antwort brach ab, etwa weil der Pausenwächter das Modell für Tonwerk entlud: erneut versuchen."""


def register_with_gate(gate: dict, log=print, stop=None):
    """Der Python-Adapter aus scripts/examples/adapters/llm_gate.py: anmelden, bis die Schranke antwortet."""
    body = {key: value for key, value in gate.items() if key not in ("url", "app")}
    url = f"{gate['url'].rstrip('/')}/gate/apps/{urllib.parse.quote(gate['app'])}"
    delay = 10
    while not (stop and stop.is_set()):
        try:
            request = urllib.request.Request(url, data=json.dumps(body).encode(), method="PUT",
                                             headers={"content-type": "application/json"})
            with urllib.request.urlopen(request, timeout=15):
                pass
            log(f"Bei der LLM-Schranke angemeldet; Modelle freigeben unter {gate['url']}/gate")
            return
        except OSError as error:
            log(f"LLM-Schranke nicht erreichbar ({error}); neuer Versuch in {delay} s")
            time.sleep(delay)
            delay = min(delay * 2, 600)


def _message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error")
        return error.get("message") if isinstance(error, dict) else str(error or payload)
    except ValueError:
        return response.text[:300] or f"HTTP {response.status_code}"


def _check(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    message = _message(response)
    if response.status_code in (403, 424, 507):
        raise GateRefused(response.status_code, message)
    if response.status_code in (502, 503):
        retry = response.headers.get("retry-after")
        raise GateBusy(message, float(retry) if retry and retry.isdigit() else 60.0)
    raise RuntimeError(f"HTTP {response.status_code}: {message}")


class Gate:
    def __init__(self, url: str, app: str, timeout_minutes: int = 75, transport=None):
        self.url = url.rstrip("/")
        self.app = app
        self.timeout = httpx.Timeout(timeout_minutes * 60, connect=10)
        self.transport = transport

    def _client(self, timeout=None) -> httpx.Client:
        return httpx.Client(base_url=self.url, timeout=timeout or self.timeout, transport=self.transport,
                            headers={"authorization": f"Bearer {self.app}"})

    def chat(self, model: str, messages: list, max_tokens: int = 800, temperature: float = 0.4, json_mode=False) -> str:
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        with self._client() as client:
            response = client.post("/v1/chat/completions", json=body)
        _check(response)
        return response.json()["choices"][0]["message"]["content"]

    def generate_3d(self, model: str, png: bytes, options: dict, on_progress=None) -> bytes:
        """POST /v1/3d/generations mit stream: Fortschritt an on_progress(stage, step, total), zurück kommt das GLB."""
        body = {**options, "model": model, "image": base64.b64encode(png).decode(), "stream": True}
        with self._client() as client, client.stream("POST", "/v1/3d/generations", json=body) as response:
            if response.status_code >= 400:
                response.read()
                _check(response)
            buffer = ""
            try:
                for chunk in response.iter_text():
                    buffer += chunk.replace("\r\n", "\n")
                    while "\n\n" in buffer:
                        block, buffer = buffer.split("\n\n", 1)
                        data = "".join(line[5:].strip() for line in block.split("\n") if line.startswith("data:"))
                        if not data or data == "[DONE]":
                            continue
                        event = json.loads(data)
                        if event.get("type") == "progress" and on_progress:
                            on_progress(event.get("stage"), event.get("step"), event.get("total"))
                        elif event.get("type") == "error":
                            raise RuntimeError(f"TRELLIS.2: {event.get('message')}")
                        elif event.get("type") == "complete":
                            if event.get("format") not in (None, "glb"):
                                raise RuntimeError(f"TRELLIS.2 lieferte {event.get('format')} statt GLB")
                            return base64.b64decode(event["data"])
            except httpx.TransportError as error:
                raise StreamBroken(f"Verbindung abgerissen: {error}") from error
        raise StreamBroken("Die Antwort endete ohne Ergebnis (Modell entladen?)")

    def rig(self, model: str, glb: bytes, kind: str = "humanoid", seed: int = 1) -> tuple[bytes, dict]:
        """POST /v1/rig (Rig-Dienst auf oi-o-mat, rig/server.py): zurück kommen das GLB mit Skelett und der Bericht."""
        body = {"model": model, "glb": base64.b64encode(glb).decode(), "class": kind, "seed": seed}
        try:
            with self._client() as client:
                response = client.post("/v1/rig", json=body)
        except httpx.TransportError as error:
            raise StreamBroken(f"Verbindung abgerissen: {error}") from error
        _check(response)
        payload = response.json()
        if payload.get("format") != "glb" or not payload.get("data"):
            raise RuntimeError("Der Rig-Dienst lieferte kein GLB")
        return base64.b64decode(payload["data"]), payload.get("report") or {}

    def paint(self, model: str, prompt: str, size: str = "1024x1024", seed: int | None = None, n: int = 1,
              on_progress=None, on_image=None) -> int:
        """POST /v1/images/generations mit stream (img-o-mat hinter der Schranke): Fortschritt an
        on_progress(event, data) für queued und progress, jedes fertige Bild an on_image(jpeg, info). Zurück: Anzahl."""
        body = {"model": model, "prompt": prompt, "size": size, "n": n, "stream": True}
        if seed is not None:
            body["seed"] = seed
        count = 0
        try:
            with self._client() as client, client.stream("POST", "/v1/images/generations", json=body) as response:
                if response.status_code >= 400:
                    response.read()
                    _check(response)
                buffer = ""
                for chunk in response.iter_text():
                    buffer += chunk.replace("\r\n", "\n")
                    while "\n\n" in buffer:
                        block, buffer = buffer.split("\n\n", 1)
                        name = next((line[6:].strip() for line in block.split("\n") if line.startswith("event:")), "")
                        data = "".join(line[5:].strip() for line in block.split("\n") if line.startswith("data:"))
                        if not name or not data:
                            continue
                        event = json.loads(data)
                        if name == "image":
                            count += 1
                            if on_image:
                                on_image(base64.b64decode(event["b64_json"]), event)
                        elif name == "error":
                            error = event.get("error") or {}
                            code, message = error.get("code"), error.get("message") or "unbekannter Fehler"
                            # 422: der Inhaltsfilter des Modells hielt das Bild zurück; das ändert nur ein anderer Prompt.
                            raise RuntimeError(f"img-o-mat: {message}" if code != 422 else f"Vom Inhaltsfilter zurückgehalten: {message}")
                        elif name == "done":
                            return count
                        elif on_progress:
                            on_progress(name, event)
        except httpx.TransportError as error:
            raise StreamBroken(f"Verbindung abgerissen: {error}") from error
        if count:
            return count
        raise StreamBroken("Die Antwort endete ohne Bild")

    def status(self) -> dict | None:
        """Der Zustand der Schranke (GET /gate/status), oder None, wenn sie nicht antwortet."""
        try:
            with self._client(timeout=httpx.Timeout(8)) as client:
                response = client.get("/gate/status")
            return response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

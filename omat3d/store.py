"""Where 3d-o-mat keeps its files: on the T7 of oi-o-mat, reached over SSH. The website (in its Proxmox container)
holds nothing of its own but a list of what is there, kept in memory.

On oi-o-mat the website's key may run only store/launch.py (install-store.sh writes it into authorized_keys), which
knows five words: check, put, get, list, delete. Without "ssh" in the config the website runs the same script itself
on a folder of its own ("folder"), as the tests do and as a single Mac would.

    <folder>/.3d-o-mat                      marker: only with it does the folder count as there
    <folder>/images/<id>/original.<ext>     the upload
    <folder>/images/<id>/cutout.png         cut out, what TRELLIS.2 gets
    <folder>/images/<id>/image.json         name, cutout, Gemma's check
    <folder>/jobs/<id>/job.json             a job with its state and progress
    <folder>/jobs/<id>/model.glb            the result
"""
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

LAUNCH = Path(__file__).resolve().parent.parent / "store" / "launch.py"
_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
# How long a "check" counts; the page asks for the status every few seconds.
CHECK_SECONDS = 20
# Pictures the page shows again and again (thumbnails, the cutout) are kept here up to this size.
CACHE_BYTES = 96 * 1024 * 1024
DOCUMENTS = {"images": "image.json", "jobs": "job.json"}


class NotMounted(RuntimeError):
    pass


class StoreError(RuntimeError):
    pass


def new_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def valid_id(value: str) -> bool:
    return bool(_ID.match(value or ""))


class Store:
    def __init__(self, ssh: str | None = None, key: str | None = None, folder: str | None = None,
                 known_hosts: str | None = None, log=print):
        if not ssh and not folder:
            raise ValueError("store braucht ssh (oi-o-mat) oder folder (eigener Ordner)")
        self.ssh, self.key, self.folder, self.known_hosts, self.log = ssh, key, folder, known_hosts, log
        self._lock = threading.Lock()
        self._checked: tuple[float, dict] | None = None
        self._index: dict[str, OrderedDict] = {}
        self._cache: OrderedDict[tuple, bytes] = OrderedDict()
        self._cached_bytes = 0

    @property
    def name(self) -> str:
        return self.ssh or self.folder

    # --- the link

    def command(self, *words: str) -> list[str]:
        if not self.ssh:
            return [sys.executable, str(LAUNCH), *words]
        options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                   # The first connection learns oi-o-mat's host key; a changed one later is refused.
                   "-o", "StrictHostKeyChecking=accept-new",
                   "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4"]
        if self.known_hosts:
            options += ["-o", f"UserKnownHostsFile={self.known_hosts}"]
        if self.key:
            options += ["-i", os.path.expanduser(self.key)]
        # The key may only run launch.py; the words after the first are what it reads.
        return ["ssh", *options, self.ssh, "3d-o-mat " + " ".join(shlex.quote(word) for word in words)]

    def run(self, *words: str, data: bytes | None = None, timeout: float = 120) -> bytes:
        environment = dict(os.environ)
        if not self.ssh:
            environment["OMAT3D_STORE_FOLDER"] = self.folder
        try:
            done = subprocess.run(self.command(*words), input=data or b"", capture_output=True, timeout=timeout,
                                  env=environment)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise StoreError(f"{self.name} antwortet nicht: {error}") from error
        if done.returncode != 0:
            lines = done.stderr.decode(errors="replace").strip().splitlines()
            message = lines[-1] if lines else f"Exit {done.returncode}"
            if "T7 nicht verbunden" in message:
                self._checked = None
                raise NotMounted(message.removeprefix("Error: "))
            if done.returncode == 4:
                raise FileNotFoundError(message)
            # 255 is ssh's own failure: no connection, key refused.
            raise StoreError(f"{self.name}: {message}")
        return done.stdout

    # --- state of the T7

    def check(self, fresh: bool = False) -> dict:
        with self._lock:
            if not fresh and self._checked and time.monotonic() - self._checked[0] < CHECK_SECONDS:
                return self._checked[1]
        try:
            state = {**json.loads(self.run("check", timeout=30)), "error": None}
        except (StoreError, ValueError) as error:
            state = {"mounted": False, "root": None, "freeGb": None, "error": str(error)}
        with self._lock:
            if self._checked and self._checked[1].get("error") != state["error"] and state["error"]:
                self.log(state["error"])
            # Gone and back may be another T7 or a restored one: read the lists anew.
            if not state["mounted"]:
                self._index.clear()
            self._checked = (time.monotonic(), state)
        return state

    @property
    def mounted(self) -> bool:
        return bool(self.check().get("mounted"))

    def ensure_mounted(self) -> None:
        state = self.check()
        if not state.get("mounted"):
            raise NotMounted(state.get("error") or f"T7 nicht verbunden: .3d-o-mat fehlt in {state.get('root')}")

    # --- files

    def put(self, kind: str, item: str, name: str, data: bytes) -> None:
        self.run("put", kind, item, name, data=data, timeout=600)
        self._remember(kind, item, name, data)

    def get(self, kind: str, item: str, name: str) -> bytes:
        with self._lock:
            cached = self._cache.get((kind, item, name))
            if cached is not None:
                self._cache.move_to_end((kind, item, name))
                return cached
        data = self.run("get", kind, item, name, timeout=600)
        self._remember(kind, item, name, data)
        return data

    def _remember(self, kind: str, item: str, name: str, data: bytes) -> None:
        if len(data) > CACHE_BYTES // 8:
            return
        with self._lock:
            old = self._cache.pop((kind, item, name), None)
            self._cached_bytes += len(data) - (len(old) if old else 0)
            self._cache[(kind, item, name)] = data
            while self._cached_bytes > CACHE_BYTES:
                _, dropped = self._cache.popitem(last=False)
                self._cached_bytes -= len(dropped)

    # --- documents (image.json, job.json), kept in memory and written through

    def documents(self, kind: str) -> OrderedDict:
        with self._lock:
            if kind in self._index:
                return self._index[kind]
        listed = json.loads(self.run("list", kind, DOCUMENTS[kind], timeout=120))
        with self._lock:
            self._index[kind] = OrderedDict((doc["id"], doc) for doc in listed if isinstance(doc, dict) and "id" in doc)
            return self._index[kind]

    def list(self, kind: str) -> list[dict]:
        try:
            self.ensure_mounted()
            return [dict(doc) for doc in self.documents(kind).values()]
        except (NotMounted, StoreError):
            return []

    def read(self, kind: str, item: str) -> dict:
        if not valid_id(item):
            raise KeyError(item)
        doc = self.documents(kind).get(item)
        if doc is None:
            raise KeyError(item)
        return dict(doc)

    def write(self, kind: str, doc: dict) -> dict:
        self.run("put", kind, doc["id"], DOCUMENTS[kind], data=json.dumps(doc, ensure_ascii=False, indent=2).encode())
        index = self.documents(kind)
        with self._lock:
            known = doc["id"] in index
            index[doc["id"]] = dict(doc)
            if not known:
                index.move_to_end(doc["id"], last=False)
        return doc

    def delete(self, kind: str, item: str) -> None:
        if not valid_id(item):
            raise KeyError(item)
        self.run("delete", kind, item)
        index = self.documents(kind)
        with self._lock:
            index.pop(item, None)
            for key in [key for key in self._cache if key[:2] == (kind, item)]:
                self._cached_bytes -= len(self._cache.pop(key))


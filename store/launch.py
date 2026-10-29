"""3d-o-mat's storage on oi-o-mat: the one command the website (in its Proxmox container) may run here, over SSH.

    launch.py check                     is the T7 there: {"mounted", "root", "freeGb"}
    launch.py put <kind> <id> <name>    stdin into <root>/<kind>/<id>/<name> (written beside, then renamed)
    launch.py get <kind> <id> <name>    that file on stdout
    launch.py list <kind> <name>        every <root>/<kind>/*/<name> (a JSON file) as one JSON list
    launch.py delete <kind> <id>        <root>/<kind>/<id> with everything in it

kind is "images" or "jobs", id looks like 20261029-001500-ab12, name like cutout.png: nothing else can be named, so
the key reaches nothing outside the folder. Over SSH the website's key may run only this script (install-store.sh
writes restrict,command="/usr/bin/python3 <root>/store/launch.py --ssh" into ~/.ssh/authorized_keys): sshd runs it
whatever was asked and leaves that in SSH_ORIGINAL_COMMAND, of which the words after the first count.

The folder is <root of store.json>, by default /Volumes/T7/3d-o-mat. Only with the file .3d-o-mat in it does it count
as there, so an unmounted T7 never fills the internal disk. Plain Python 3.9, macOS's own /usr/bin/python3: keep
`from __future__ import annotations` and no newer syntax.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(os.environ.get("OMAT3D_STORE_ROOT") or os.path.dirname(HERE), "store.json")
MARKER = ".3d-o-mat"
KINDS = ("images", "jobs")
ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class Refused(Exception):
    pass


def root() -> str:
    # Where the website runs the script itself (tests, one Mac for everything), it names the folder directly.
    if os.environ.get("OMAT3D_STORE_FOLDER"):
        return os.path.abspath(os.environ["OMAT3D_STORE_FOLDER"])
    try:
        with open(SETTINGS) as file:
            folder = json.load(file).get("root")
    except FileNotFoundError:
        folder = None
    return os.path.abspath(os.path.expanduser(folder or "/Volumes/T7/3d-o-mat"))


def mounted(folder: str) -> bool:
    return os.path.isfile(os.path.join(folder, MARKER))


def place(folder: str, kind: str, item: str, name: str | None = None) -> str:
    if kind not in KINDS or not ID.match(item) or (name is not None and (not NAME.match(name) or ".." in name)):
        raise Refused(f"unzulässig: {kind} {item} {name or ''}".strip())
    if not mounted(folder):
        raise Refused(f"T7 nicht verbunden: {os.path.join(folder, MARKER)} fehlt")
    path = os.path.join(folder, kind, item)
    return os.path.join(path, name) if name else path


def check(folder: str) -> int:
    free = None
    if mounted(folder):
        stats = os.statvfs(folder)
        free = round(stats.f_bavail * stats.f_frsize / 1e9, 1)
    print(json.dumps({"mounted": mounted(folder), "root": folder, "freeGb": free}))
    return 0


def put(folder: str, kind: str, item: str, name: str) -> int:
    target = place(folder, kind, item, name)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".put-")
    try:
        with os.fdopen(handle, "wb") as out:
            shutil.copyfileobj(sys.stdin.buffer, out, 1 << 20)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    return 0


def get(folder: str, kind: str, item: str, name: str) -> int:
    source = place(folder, kind, item, name)
    if not os.path.isfile(source):
        print(f"fehlt: {kind}/{item}/{name}", file=sys.stderr)
        return 4
    with open(source, "rb") as file:
        shutil.copyfileobj(file, sys.stdout.buffer, 1 << 20)
    return 0


def listing(folder: str, kind: str, name: str) -> int:
    if kind not in KINDS or not NAME.match(name) or not name.endswith(".json"):
        raise Refused(f"unzulässig: list {kind} {name}")
    if not mounted(folder):
        raise Refused(f"T7 nicht verbunden: {os.path.join(folder, MARKER)} fehlt")
    base = os.path.join(folder, kind)
    items = []
    for item in sorted(os.listdir(base), reverse=True) if os.path.isdir(base) else []:
        if not ID.match(item):
            continue
        try:
            with open(os.path.join(base, item, name)) as file:
                items.append(json.load(file))
        except (OSError, ValueError):
            continue
    json.dump(items, sys.stdout)
    return 0


def delete(folder: str, kind: str, item: str) -> int:
    path = place(folder, kind, item)
    if os.path.isdir(path):
        shutil.rmtree(path)
    return 0


def main() -> int:
    arguments = sys.argv[1:]
    if arguments[:1] == ["--ssh"]:
        # "3d-o-mat put images <id> cutout.png": the first word is only a name, the rest the command.
        try:
            arguments = shlex.split(os.environ.get("SSH_ORIGINAL_COMMAND", ""))[1:]
        except ValueError:
            arguments = []
    command, rest = (arguments[0], arguments[1:]) if arguments else ("", [])
    folder = root()
    commands = {"check": (check, 0), "put": (put, 3), "get": (get, 3), "list": (listing, 2), "delete": (delete, 2)}
    if command not in commands or len(rest) != commands[command][1]:
        print("Aufruf: launch.py check | put|get <kind> <id> <name> | list <kind> <name> | delete <kind> <id>",
              file=sys.stderr)
        return 2
    try:
        return commands[command][0](folder, *rest)
    except Refused as refused:
        print(f"Error: {refused}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())

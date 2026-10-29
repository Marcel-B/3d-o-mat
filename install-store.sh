#!/bin/sh
# On oi-o-mat: sets up 3d-o-mat's storage on the T7. The website (in its Proxmox container) reaches it over SSH with a
# key that may run store/launch.py and nothing else; there is no service running here and no port open.
#
#   ./install-store.sh                       the folder on the T7, the script, store.json
#   ./install-store.sh --key "<public key>"  also let that key (the website's) run store/launch.py, and nothing else
#
# Settings through the environment: FOLDER (/Volumes/T7/3d-o-mat). "Entfernte Anmeldung" (System Settings > General >
# Sharing) must be on for the website to come in.
set -eu

ROOT="$HOME/Library/Application Support/3d-o-mat"
HERE="$(cd "$(dirname "$0")" && pwd)"
FOLDER="${FOLDER:-/Volumes/T7/3d-o-mat}"
KEY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --key) KEY="$2"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unbekannt: $1" >&2; exit 2 ;;
  esac
done

[ -x /usr/bin/python3 ] && /usr/bin/python3 -c 'import sys' 2>/dev/null || {
  echo "/usr/bin/python3 fehlt: xcode-select --install" >&2; exit 1; }
case "$FOLDER" in
  /Volumes/*) [ -d "$(echo "$FOLDER" | cut -d/ -f1-3)" ] || { echo "$(echo "$FOLDER" | cut -d/ -f1-3) ist nicht eingehängt." >&2; exit 1; } ;;
esac

mkdir -p "$FOLDER" "$ROOT/store"
# The marker: only with it does launch.py write here, so an unmounted T7 never fills the internal disk.
touch "$FOLDER/.3d-o-mat"
cp "$HERE/store/launch.py" "$ROOT/store/launch.py"
/usr/bin/python3 -c 'import json, sys; json.dump({"root": sys.argv[1]}, open(sys.argv[2], "w"), indent=2)' "$FOLDER" "$ROOT/store.json"
echo "Ablage: $FOLDER"

if [ -n "$KEY" ]; then
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  touch "$HOME/.ssh/authorized_keys" && chmod 600 "$HOME/.ssh/authorized_keys"
  # One line, tagged, replaced on every run: the key may run launch.py only, without a terminal or forwarding.
  grep -v ' 3d-o-mat-website$' "$HOME/.ssh/authorized_keys" > "$HOME/.ssh/authorized_keys.tmp" || true
  set -- $KEY
  printf 'restrict,command="/usr/bin/python3 \\"%s/store/launch.py\\" --ssh" %s %s 3d-o-mat-website\n' "$ROOT" "$1" "$2" >> "$HOME/.ssh/authorized_keys.tmp"
  mv "$HOME/.ssh/authorized_keys.tmp" "$HOME/.ssh/authorized_keys"
  echo "Schlüssel der Website eingetragen: darf nur store/launch.py starten."
fi

/usr/bin/python3 "$ROOT/store/launch.py" check
echo "Fertig. Prüfe, dass Entfernte Anmeldung (Systemeinstellungen > Allgemein > Teilen) an ist."

#!/bin/sh
# On oi-o-mat: sets up the prep service (figur_aufbereiten.py: clean, shrink and re-bake a TRELLIS figure in Blender) for
# the LLM gate. It makes a Python 3.13 venv with bpy 5.3, numpy, scipy and scikit-image on the T7, copies prep/ and
# writes prep.json; the gate starts the service when a request needs it, nothing runs here in between.
#
#   ./install-prep.sh            set up or update
#
# Settings through the environment: HOME_DIR (/Volumes/T7/figur-aufbereiten), PORT (11241), GATE (http://127.0.0.1:1235),
# PYTHON313 (python3.13 from PATH or Homebrew). bpy 5.3 exists only for Python 3.13.
set -eu

ROOT="$HOME/Library/Application Support/3d-o-mat"
HERE="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${HOME_DIR:-/Volumes/T7/figur-aufbereiten}"
PORT="${PORT:-11241}"
GATE="${GATE:-http://127.0.0.1:1235}"
MEMORY="${MEMORY:-10}"

case "${1:-}" in
  -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

[ -x /usr/bin/python3 ] || { echo "/usr/bin/python3 fehlt: xcode-select --install" >&2; exit 1; }
case "$HOME_DIR" in
  /Volumes/*) [ -d "$(echo "$HOME_DIR" | cut -d/ -f1-3)" ] || { echo "$(echo "$HOME_DIR" | cut -d/ -f1-3) ist nicht eingehängt." >&2; exit 1; } ;;
esac

PY="${PYTHON313:-}"
[ -n "$PY" ] || PY="$(command -v python3.13 2>/dev/null || true)"
[ -n "$PY" ] || for candidate in /opt/homebrew/bin/python3.13 /usr/local/bin/python3.13; do
  [ -x "$candidate" ] && PY="$candidate" && break
done
[ -n "$PY" ] || { echo "Python 3.13 fehlt (bpy 5.3 gibt es nur dafür): brew install python@3.13" >&2; exit 1; }

mkdir -p "$HOME_DIR" "$ROOT/prep"
[ -x "$HOME_DIR/venv/bin/python" ] || "$PY" -m venv "$HOME_DIR/venv"
"$HOME_DIR/venv/bin/pip" install -q --upgrade pip
"$HOME_DIR/venv/bin/pip" install -q "bpy==5.3.*" numpy scipy scikit-image
"$HOME_DIR/venv/bin/python" -c 'import bpy, scipy, skimage; print("bpy", bpy.app.version_string)'

cp "$HERE/prep/server.py" "$HERE/prep/figur_aufbereiten.py" "$HERE/prep/kopf_aufsetzen.py" "$HERE/prep/rig_uebertragen.py" "$HERE/prep/finger_trennen.py" "$HERE/prep/uv_trennen.py" "$ROOT/prep/"
/usr/bin/python3 -c 'import json, sys; json.dump({"python": sys.argv[1], "port": int(sys.argv[2]), "memoryGb": float(sys.argv[3])}, open(sys.argv[4], "w"), indent=2)' \
  "$HOME_DIR/venv/bin/python" "$PORT" "$MEMORY" "$ROOT/prep.json"
echo "Aufbereitung: $ROOT/prep/server.py, Port $PORT, Python $HOME_DIR/venv/bin/python"

START="/usr/bin/python3 '$ROOT/prep/server.py' >> '$HOME/Library/Logs/3d-o-mat-prep.log' 2>&1"
BODY=$(/usr/bin/python3 -c 'import json, sys; print(json.dumps({"backends": {"prep": {"type": "tool", "url": "http://127.0.0.1:" + sys.argv[1], "memoryGb": float(sys.argv[3]), "start": sys.argv[2]}}}))' "$PORT" "$START" "$MEMORY")
if curl -fsS -X PATCH "$GATE/gate/settings" -H 'content-type: application/json' -d "$BODY" >/dev/null 2>&1; then
  echo "Bei der LLM-Schranke als Server \"prep\" (Typ tool) eingetragen; sie startet ihn bei Bedarf."
else
  echo "Schranke unter $GATE nicht erreichbar; später eintragen mit:" >&2
  echo "  curl -X PATCH $GATE/gate/settings -H 'content-type: application/json' -d '$BODY'" >&2
fi
echo "Fertig. In $GATE/gate bei 3d-o-mat das Modell figur-aufbereiten freigeben."

#!/bin/sh
# On oi-o-mat: sets up the rig service (skeleton and skin weights with SkinTokens) for the LLM gate. It builds the
# Apple-Silicon port of SkinTokens on the T7 if it is not there, fetches its weights, copies rig/server.py and writes
# rig.json; the gate starts the service when a request needs it, nothing runs here in between.
#
#   ./install-rig.sh            set up or update
#
# Settings through the environment: HOME_DIR (/Volumes/T7/skintokens), PORT (11240), GATE (http://127.0.0.1:1235).
# Needs Rust (rustup) to build. The built skintokens binary is GPL-3.0 (its shape encoder); 3d-o-mat only runs it.
set -eu

ROOT="$HOME/Library/Application Support/3d-o-mat"
HERE="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${HOME_DIR:-/Volumes/T7/skintokens}"
PORT="${PORT:-11240}"
GATE="${GATE:-http://127.0.0.1:1235}"
REPO="https://github.com/chris-straka/skintokens.git"

case "${1:-}" in
  -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

[ -x /usr/bin/python3 ] || { echo "/usr/bin/python3 fehlt: xcode-select --install" >&2; exit 1; }
case "$HOME_DIR" in
  /Volumes/*) [ -d "$(echo "$HOME_DIR" | cut -d/ -f1-3)" ] || { echo "$(echo "$HOME_DIR" | cut -d/ -f1-3) ist nicht eingehängt." >&2; exit 1; } ;;
esac

mkdir -p "$HOME_DIR" "$ROOT/rig"
if [ ! -x "$HOME_DIR/src/target/release/skintokens" ]; then
  command -v cargo >/dev/null 2>&1 || [ -x "$HOME/.cargo/bin/cargo" ] || {
    echo "Rust fehlt: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y" >&2; exit 1; }
  PATH="$HOME/.cargo/bin:$PATH"
  [ -d "$HOME_DIR/src/.git" ] || git clone "$REPO" "$HOME_DIR/src"
  (cd "$HOME_DIR/src" && cargo build --release)
fi
(cd "$HOME_DIR/src" && SKINTOKENS_HOME="$HOME_DIR" ./fetch-weights.sh)

cp "$HERE/rig/server.py" "$ROOT/rig/server.py"
/usr/bin/python3 -c 'import json, sys; json.dump({"binary": sys.argv[1], "home": sys.argv[2], "port": int(sys.argv[3]), "memoryGb": 8}, open(sys.argv[4], "w"), indent=2)' \
  "$HOME_DIR/src/bin/skintokens" "$HOME_DIR" "$PORT" "$ROOT/rig.json"
echo "Rig-Dienst: $ROOT/rig/server.py, Port $PORT, SkinTokens in $HOME_DIR"

START="/usr/bin/python3 '$ROOT/rig/server.py' >> '$HOME/Library/Logs/3d-o-mat-rig.log' 2>&1"
BODY=$(/usr/bin/python3 -c 'import json, sys; print(json.dumps({"backends": {"rig": {"type": "tool", "url": "http://127.0.0.1:" + sys.argv[1], "memoryGb": 8, "start": sys.argv[2]}}}))' "$PORT" "$START")
if curl -fsS -X PATCH "$GATE/gate/settings" -H 'content-type: application/json' -d "$BODY" >/dev/null 2>&1; then
  echo "Bei der LLM-Schranke als Server \"rig\" (Typ tool) eingetragen; sie startet ihn bei Bedarf."
else
  echo "Schranke unter $GATE nicht erreichbar; später eintragen mit:" >&2
  echo "  curl -X PATCH $GATE/gate/settings -H 'content-type: application/json' -d '$BODY'" >&2
fi
echo "Fertig. In $GATE/gate bei 3d-o-mat das Modell skintokens freigeben."

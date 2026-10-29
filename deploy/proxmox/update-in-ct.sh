#!/usr/bin/env bash
# Auto-Update im 3d-o-mat-Container (Proxmox-LXC): holt main und baut neu, aber nur, wenn kein Auftrag wartet oder
# rechnet. Der systemd-Timer 3d-o-mat-update ruft es alle 5 Minuten auf (3d-o-mat-lxc.sh autoupdate legt ihn an);
# das Log steht im Journal (journalctl -u 3d-o-mat-update).
#
# Nur auf dem Branch main, nur vorwärts (git pull --ff-only). Ein Stand, dessen Bau fehlschlug, wird nicht alle fünf
# Minuten neu versucht, erst der nächste Commit. Der ganze Text steht in { }, damit bash ihn vor dem Ausführen ganz
# liest: der git pull darf diese Datei ändern, während sie läuft.
{
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/3d-o-mat}"
STATE_DIR=/var/lib/3d-o-mat-update
API="${API:-http://127.0.0.1:8090}"
export GIT_TERMINAL_PROMPT=0

mkdir -p "$STATE_DIR"
cd "$APP_DIR"
branch=$(git symbolic-ref --short HEAD 2>/dev/null || true)
if [ "$branch" != main ]; then
  echo "Checkout steht auf ${branch:-keinem Branch}, nicht main; nichts getan."
  exit 0
fi
git fetch -q origin main
head=$(git rev-parse HEAD)
remote=$(git rev-parse origin/main)
if [ "$head" = "$remote" ] || git merge-base --is-ancestor "$remote" "$head"; then
  rm -f "$STATE_DIR/waiting"
  exit 0
fi
if [ "$(cat "$STATE_DIR/failed" 2>/dev/null)" = "$remote" ]; then
  exit 0
fi

# Neu bauen startet die App neu; ein laufender Auftrag finge dann von vorn an. Antwortet die App nicht, darf es.
if jobs=$(curl -fsS --max-time 15 "$API/api/jobs" 2>/dev/null); then
  # grep findet ohne offenen Auftrag nichts und endet mit 1; das ist hier kein Fehler.
  open=$({ printf '%s' "$jobs" | grep -oE '"state" *: *"(queued|waiting|running)"' || true; } | wc -l)
  if [ "$open" -gt 0 ]; then
    if [ "$(cat "$STATE_DIR/waiting" 2>/dev/null)" != "$remote" ]; then
      echo "${remote:0:7} wartet: $open Auftrag/Aufträge offen."
      echo "$remote" > "$STATE_DIR/waiting"
    fi
    exit 0
  fi
fi
rm -f "$STATE_DIR/waiting"

echo "Update ${head:0:7} -> ${remote:0:7}:"
git log --format='    %h %s' "$head..$remote"
if git pull -q --ff-only origin main && docker compose up -d --build --remove-orphans; then
  docker image prune -f > /dev/null || true
  rm -f "$STATE_DIR/failed"
  echo "${remote:0:7} läuft."
else
  echo "$remote" > "$STATE_DIR/failed"
  echo "${remote:0:7} fehlgeschlagen; nächster Versuch mit dem nächsten Commit oder 3d-o-mat-lxc.sh update."
  exit 1
fi
exit 0
}

#!/usr/bin/env bash
# Auto-Update im 3d-o-mat-Container (Proxmox-LXC): holt main und startet das fertige Image dazu (GitHub Actions baut
# es, start-image.sh holt es), aber nur, wenn kein Auftrag wartet oder rechnet. Der systemd-Timer 3d-o-mat-update ruft
# es alle 5 Minuten auf (3d-o-mat-lxc.sh autoupdate legt ihn an); das Log steht im Journal (journalctl -u 3d-o-mat-update).
#
# Nur auf dem Branch main, nur vorwärts (git pull --ff-only). Gibt es das Image noch nicht, versucht es der nächste
# Lauf wieder. Ein Stand, dessen Start fehlschlug, wird nicht alle fünf Minuten neu versucht, erst der nächste Commit. Der ganze Text steht in { }, damit bash ihn vor dem Ausführen ganz
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
# Welches Image läuft (start-image.sh schreibt es in .env); nach einem git pull ohne Image ist es noch das alte.
running=$(sed -n 's/^OMAT3D_IMAGE=.*:sha-//p' .env 2>/dev/null || true)
if { [ "$head" = "$remote" ] || git merge-base --is-ancestor "$remote" "$head"; } && [ "$running" = "$head" ]; then
  rm -f "$STATE_DIR/waiting"
  exit 0
fi
if [ "$head" = "$remote" ] || git merge-base --is-ancestor "$remote" "$head"; then
  remote=$head
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

if [ "$head" != "$remote" ]; then
  echo "Update ${head:0:7} -> ${remote:0:7}:"
  git log --format='    %h %s' "$head..$remote"
  git pull -q --ff-only origin main
fi
set +e
"$APP_DIR/deploy/proxmox/start-image.sh" 2>&1
code=$?
set -e
if [ "$code" -eq 0 ]; then
  rm -f "$STATE_DIR/failed" "$STATE_DIR/image"
  echo "${remote:0:7} läuft."
elif [ "$code" -eq 3 ]; then
  # GitHub Actions baut noch: nächster Lauf. Nur einmal je Stand ins Log.
  if [ "$(cat "$STATE_DIR/image" 2>/dev/null)" != "$remote" ]; then
    echo "${remote:0:7}: Image noch nicht da, nächster Versuch in 5 Minuten."
    echo "$remote" > "$STATE_DIR/image"
  fi
else
  echo "$remote" > "$STATE_DIR/failed"
  echo "${remote:0:7} fehlgeschlagen; nächster Versuch mit dem nächsten Commit oder 3d-o-mat-lxc.sh update."
  exit 1
fi
exit 0
}

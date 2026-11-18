#!/usr/bin/env bash
# Startet 3d-o-mat im Container mit dem fertigen Image zum Stand des Checkouts. GitHub Actions baut es bei jedem Push
# auf main (.github/workflows/image.yml) und legt es als ghcr.io/marcel-b/3d-o-mat:sha-<commit> ab; hier wird nur
# geholt, nicht gebaut. Welches Image läuft, steht in .env (OMAT3D_IMAGE), die docker compose liest.
#
#   start-image.sh               Image zum Stand holen und starten; Exit 3, wenn es das Image noch nicht gibt
#   start-image.sh --warten 20   bis zu 20 Minuten auf das Image warten (der Bau dauert einige Minuten)
#   start-image.sh --bauen       ohne GitHub: hier bauen wie früher (braucht Platz und Zeit)
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/3d-o-mat}"
REGISTRY="${REGISTRY:-ghcr.io/marcel-b/3d-o-mat}"
warten=0
bauen=false
while [ $# -gt 0 ]; do
  case $1 in
    --warten) warten=$2; shift 2 ;;
    --bauen) bauen=true; shift ;;
    *) echo "unbekannt: $1" >&2; exit 2 ;;
  esac
done

cd "$APP_DIR"
sha=$(git rev-parse HEAD)
if $bauen; then
  image="3d-o-mat:lokal-${sha:0:12}"
  OMAT3D_IMAGE="$image" docker compose build
else
  image="$REGISTRY:sha-$sha"
  ende=$(( $(date +%s) + warten * 60 ))
  until docker pull -q "$image" > /dev/null 2>&1; do
    if [ "$(date +%s)" -ge "$ende" ]; then
      # Beim Auto-Update (ohne --warten) ist das normal und meldet update-in-ct.sh selbst, einmal je Stand.
      [ "$warten" -gt 0 ] && echo "Image $image gibt es noch nicht (GitHub Actions baut noch, oder der Bau schlug fehl)." >&2
      exit 3
    fi
    sleep 30
  done
fi
printf 'OMAT3D_IMAGE=%s\n' "$image" > .env
docker compose up -d --no-build --remove-orphans
docker image prune -f > /dev/null || true
echo "läuft: $image"

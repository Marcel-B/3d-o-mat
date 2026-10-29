#!/usr/bin/env bash
# 3d-o-mat in einem Proxmox-LXC-Container mit Docker installieren oder aktualisieren.
# Läuft in der Shell des Proxmox-VE-Hosts (als root). Im Container liegt nur die Website. Die Dateien (Bilder, GLB)
# liegen auf der T7 von oi-o-mat und gehen per SSH dorthin, mit einem Schlüssel, der auf oi-o-mat nur
# store/launch.py starten darf. Gerechnet wird auf oi-o-mat über die LLM-Schranke. Aufruf: --help, Einzelheiten im README.

set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/Marcel-B/3d-o-mat.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
APP_DIR=/opt/3d-o-mat

CT_HOSTNAME="${CT_HOSTNAME:-3d-o-mat}"
CORES="${CORES:-4}"
MEMORY="${MEMORY:-4096}"
DISK="${DISK:-16}"
BRIDGE="${BRIDGE:-vmbr0}"
IP="${IP:-dhcp}"
GATEWAY="${GATEWAY:-}"
OI_HOST="${OI_HOST:-192.168.2.122}"
OI_USER="${OI_USER:-marcel}"
GATE_URL="${GATE_URL:-http://$OI_HOST:1235}"

NICHT_FRAGEN="${NONINTERACTIVE:-0}"

info() { printf '\e[1;34m==>\e[0m %s\n' "$*" >&2; }
ok() { printf '\e[1;32m ok\e[0m %s\n' "$*" >&2; }
warn() { printf '\e[1;33m !!\e[0m %s\n' "$*" >&2; }
abbruch() {
    printf '\e[1;31mFehler:\e[0m %s\n' "$*" >&2
    exit 1
}
trap 'abbruch "Befehl in Zeile $LINENO fehlgeschlagen: $BASH_COMMAND"' ERR

if [[ -f ${BASH_SOURCE[0]:-} ]]; then
    AUFRUF="bash $(realpath "${BASH_SOURCE[0]}")"
else
    AUFRUF="bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Marcel-B/3d-o-mat/main/deploy/proxmox/3d-o-mat-lxc.sh)\" --"
fi

hilfe() {
    cat <<EOF
3d-o-mat in einem Proxmox-LXC-Container installieren oder aktualisieren (auf dem PVE-Host als root).

Aufruf: $AUFRUF [-y] [anlegen | app <CTID> | schluessel <CTID> | update <CTID> | pruefen <CTID>]

  (ohne)            alles der Reihe nach: anlegen, app
  anlegen           Schritt 1: Container anlegen (oder den namens $CT_HOSTNAME nehmen) und Docker installieren
  app <CTID>        Schritt 2: 3d-o-mat holen, config.json und SSH-Schlüssel anlegen, bauen und starten;
                    am Ende steht der Befehl für oi-o-mat (install-store.sh --key …)
  schluessel <CTID> den öffentlichen Schlüssel der Website noch einmal zeigen
  update <CTID>     git pull und neu bauen; config.json und Schlüssel bleiben
  pruefen <CTID>    App, Ablage auf oi-o-mat und Schranke prüfen
  -y                ohne Rückfragen, Werte aus Umgebungsvariablen

Variablen (Vorgabe): CTID (nächste freie bzw. Container namens 3d-o-mat), CT_HOSTNAME (3d-o-mat), CORES (4),
MEMORY (4096), DISK (16), BRIDGE (vmbr0), IP (dhcp, sonst z. B. 192.168.2.50/24), GATEWAY, CT_STORAGE,
TEMPLATE_STORAGE, OI_HOST (192.168.2.122), OI_USER (marcel), GATE_URL (http://OI_HOST:1235), REPO_URL,
REPO_BRANCH (main).

Auf oi-o-mat einmal: ./install-store.sh --key "<Schlüssel aus app>" im Checkout von 3d-o-mat.
EOF
}

frage() {
    local var=$1 text=$2 eingabe
    [[ $NICHT_FRAGEN == 1 ]] && return 0
    read -rp "$text [${!var}]: " eingabe
    [[ -n $eingabe ]] && printf -v "$var" '%s' "$eingabe"
    return 0
}

pruefe_host() {
    [[ $EUID -eq 0 ]] || abbruch "Bitte als root in der Proxmox-Shell ausführen."
    command -v pct >/dev/null && command -v pveam >/dev/null \
        || abbruch "pct/pveam nicht gefunden. Das Skript muss auf einem Proxmox-VE-Host laufen."
}

storage_mit() {
    local inhalt=$1 bevorzugt=${2:-} liste
    liste=$(pvesm status -content "$inhalt" 2>/dev/null | awk 'NR>1 && $3=="active" {print $1}')
    if [[ -n $bevorzugt ]] && grep -qx "$bevorzugt" <<<"$liste"; then
        echo "$bevorzugt"
    else
        head -n1 <<<"$liste"
    fi
}

im_ct() { pct exec "$CTID" -- env DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 "$@"; }

# Der Container mit dem Namen CT_HOSTNAME, falls es ihn gibt.
ct_nach_name() { pct list | awk -v name="$CT_HOSTNAME" 'NR > 1 && $NF == name { print $1; exit }'; }

# --- Schritt 1: Container

waehle_vorlage() {
    local version name
    info "Suche Debian-Vorlage"
    pveam update >/dev/null 2>&1 || warn "pveam update fehlgeschlagen, nutze vorhandene Liste"
    for version in ${DEBIAN_VERSION:-12 13}; do
        name=$(pveam available --section system 2>/dev/null | awk '{print $2}' \
            | grep -E "^debian-${version}-standard_.*amd64" | sort -V | tail -n1 || true)
        if [[ -n $name ]]; then
            VORLAGE=$name
            break
        fi
    done
    [[ -n ${VORLAGE:-} ]] || abbruch "Keine Debian-Vorlage gefunden (pveam available --section system)."
    if pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -q "$VORLAGE"; then
        ok "Vorlage $VORLAGE schon vorhanden"
    else
        info "Lade $VORLAGE nach $TEMPLATE_STORAGE"
        pveam download "$TEMPLATE_STORAGE" "$VORLAGE" >/dev/null
    fi
}

warte_auf_netz() {
    local _
    for _ in $(seq 1 30); do
        im_ct getent hosts github.com >/dev/null 2>&1 && return 0
        sleep 2
    done
    abbruch "Container $CTID hat kein Netz (DNS für github.com schlägt fehl). Bridge/IP prüfen."
}

anlegen() {
    info "Schritt 1: Container"
    local vorhanden
    vorhanden=$(ct_nach_name)
    if [[ -n $vorhanden ]]; then
        CTID=$vorhanden
        ok "Container $CTID ($CT_HOSTNAME) gibt es schon"
    else
        CTID="${CTID:-$(pvesh get /cluster/nextid)}"
        CT_STORAGE="${CT_STORAGE:-$(storage_mit rootdir local-lvm)}"
        TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-$(storage_mit vztmpl local)}"
        frage CTID "Container-ID"
        frage CT_HOSTNAME "Name"
        frage IP "IP (dhcp oder z. B. 192.168.2.50/24)"
        if [[ $IP != dhcp ]]; then frage GATEWAY "Gateway"; fi
        frage MEMORY "RAM in MB (rembg braucht etwa 2 GB)"
        frage DISK "Platte in GB (Docker-Abbild und rembg-Modell, keine Nutzerdaten)"
        waehle_vorlage
        local netz="name=eth0,bridge=$BRIDGE,ip=$IP"
        [[ $IP != dhcp && -n $GATEWAY ]] && netz+=",gw=$GATEWAY"
        info "Lege Container $CTID ($CT_HOSTNAME) an"
        # Unprivilegiert; Docker darin braucht nesting und keyctl.
        pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$VORLAGE" \
            --hostname "$CT_HOSTNAME" --description "3d-o-mat: Bild zu 3D, https://github.com/Marcel-B/3d-o-mat" \
            --memory "$MEMORY" --swap 1024 --cores "$CORES" \
            --rootfs "$CT_STORAGE:$DISK" \
            --net0 "$netz" \
            --unprivileged 1 --features nesting=1,keyctl=1 \
            --onboot 1 >/dev/null
        ok "Container $CTID angelegt"
    fi
    [[ $(pct status "$CTID" | awk '{print $2}') == running ]] || pct start "$CTID"
    warte_auf_netz
    info "Installiere Docker im Container"
    im_ct bash -c 'apt-get update -qq && apt-get install -y -qq ca-certificates curl git >/dev/null
        command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh >/dev/null
        systemctl enable --now docker >/dev/null'
    ok "Docker $(im_ct docker --version | awk '{print $3}' | tr -d ,)"
}

# --- Schritt 2: App

schluessel() {
    im_ct cat "$APP_DIR/ssh/id_ed25519.pub" 2>/dev/null || abbruch "Noch kein Schlüssel: erst $AUFRUF app $CTID"
}

app() {
    info "Schritt 2: 3d-o-mat in Container $CTID"
    if im_ct test -d "$APP_DIR/.git"; then
        # Auch auf den Zweig, den REPO_BRANCH nennt, wenn der Checkout auf einem anderen steht.
        im_ct git -C "$APP_DIR" fetch -q origin "$REPO_BRANCH"
        im_ct git -C "$APP_DIR" checkout -q -B "$REPO_BRANCH" FETCH_HEAD
    else
        im_ct git clone -q --branch "$REPO_BRANCH" "$REPO_URL" "$APP_DIR"
    fi
    ok "Stand $(im_ct git -C "$APP_DIR" log -1 --format='%h %s') ($REPO_BRANCH)"
    if ! im_ct test -f "$APP_DIR/store/launch.py"; then
        abbruch "Dieser Stand kennt die Ablage per SSH noch nicht; REPO_BRANCH=proxmox-skript setzen, bis sie auf main ist."
    fi
    if ! im_ct test -f "$APP_DIR/config.json"; then
        frage OI_HOST "Adresse von oi-o-mat im Heimnetz"
        frage OI_USER "Benutzer auf oi-o-mat"
        GATE_URL="http://$OI_HOST:1235"
        frage GATE_URL "Adresse der LLM-Schranke"
        # Auf dem Host gebaut (dort gibt es python3, in der Debian-Vorlage nicht sicher) und in den Container geschoben.
        local datei
        datei=$(mktemp)
        im_ct cat "$APP_DIR/config.docker.json" | python3 -c '
import json, sys
config = json.load(sys.stdin)
config["gate"]["url"] = sys.argv[1]
config.pop("dataDir", None)
config.pop("mountCommand", None)
config["store"] = {"ssh": sys.argv[2], "key": "/ssh/id_ed25519", "knownHosts": "/ssh/known_hosts"}
print(json.dumps(config, indent=2, ensure_ascii=False))' "$GATE_URL" "$OI_USER@$OI_HOST" >"$datei"
        pct push "$CTID" "$datei" "$APP_DIR/config.json"
        rm -f "$datei"
        ok "config.json: Ablage $OI_USER@$OI_HOST, Schranke $GATE_URL (ändern: $APP_DIR/config.json, dann update)"
    fi
    if ! im_ct test -f "$APP_DIR/ssh/id_ed25519"; then
        im_ct bash -c "install -d -m 700 '$APP_DIR/ssh' && ssh-keygen -q -t ed25519 -N '' -C 3d-o-mat-website -f '$APP_DIR/ssh/id_ed25519'"
        ok "SSH-Schlüssel der Website angelegt ($APP_DIR/ssh)"
    fi
    info "Baue und starte (beim ersten Mal einige Minuten)"
    im_ct bash -c "cd '$APP_DIR' && docker compose up -d --build --remove-orphans" >/dev/null
    pruefen || true
    echo >&2
    info "Jetzt auf oi-o-mat, im Checkout von 3d-o-mat (git clone $REPO_URL), einmal:"
    printf '\n  ./install-store.sh --key "%s"\n\n' "$(schluessel)" >&2
    info "Danach: $AUFRUF pruefen $CTID"
}

update() {
    info "Aktualisiere 3d-o-mat in Container $CTID"
    im_ct git -C "$APP_DIR" fetch -q origin "$REPO_BRANCH"
    im_ct git -C "$APP_DIR" checkout -q -B "$REPO_BRANCH" FETCH_HEAD
    ok "Stand $(im_ct git -C "$APP_DIR" log -1 --format='%h %s') ($REPO_BRANCH)"
    im_ct bash -c "cd '$APP_DIR' && docker compose up -d --build --remove-orphans && docker image prune -f" >/dev/null
    pruefen
}

pruefen() {
    info "Prüfe"
    local status=""
    for _ in $(seq 1 30); do
        status=$(im_ct curl -fsS --max-time 30 "http://127.0.0.1:8090/api/status" 2>/dev/null) && break
        sleep 3
    done
    [[ -n $status ]] || abbruch "Die App antwortet nicht: pct exec $CTID -- bash -c 'cd $APP_DIR && docker compose logs --tail 50'"
    python3 -c '
import json, sys
s = json.loads(sys.argv[1]); g = s["gate"]; t = s["storage"]
def line(good, text): print(("\033[1;32m ok\033[0m " if good else "\033[1;33m !!\033[0m ") + text)
if t["mounted"]:
    line(True, "Ablage: " + t["where"] + ":" + str(t["root"]) + ", " + str(t["freeGb"]) + " GB frei")
else:
    line(False, "Ablage nicht erreichbar: " + str(t["error"] or ".3d-o-mat fehlt") + " (install-store.sh auf oi-o-mat gelaufen?)")
line(s["rembg"], "rembg da" if s["rembg"] else "rembg fehlt, nur einfarbige Hintergründe")
line(g["reachable"], "Schranke erreichbar (" + g["url"] + ")" if g["reachable"] else "Schranke antwortet nicht: " + g["url"])
if g["reachable"]:
    line(g["registered"], "bei der Schranke angemeldet" if g["registered"] else "noch nicht angemeldet (passiert beim Start, etwas warten)")
    for name, m in g["models"].items():
        line(bool(m.get("approved")), name + ": " + str(m.get("state")) + ", " + ("freigegeben" if m.get("approved") else "in /gate freigeben"))
' "$status" >&2
    local adresse
    adresse=$(im_ct hostname -I | awk '{print $1}')
    echo >&2
    ok "3d-o-mat: http://$adresse:8090   (Schranke: $GATE_URL/gate)"
}

# --- Ablauf

pruefe_host
SCHRITT=alles
while [[ $# -gt 0 ]]; do
    case $1 in
        -h | --help) hilfe; exit 0 ;;
        -y) NICHT_FRAGEN=1 ;;
        anlegen) SCHRITT=$1 ;;
        app | schluessel | update | pruefen)
            SCHRITT=$1
            if [[ $# -ge 2 && $2 =~ ^[0-9]+$ ]]; then
                CTID=$2
                shift
            else
                CTID=$(ct_nach_name)
            fi
            [[ -n ${CTID:-} ]] || abbruch "$1 braucht die Container-ID"
            ;;
        *) hilfe; abbruch "Unbekannt: $1" ;;
    esac
    shift
done

case $SCHRITT in
    anlegen) anlegen; echo >&2; ok "Weiter mit: $AUFRUF app $CTID" ;;
    app) app ;;
    schluessel) schluessel ;;
    update) update ;;
    pruefen) pruefen ;;
    alles) anlegen; app ;;
esac

# 3d-o-mat

Aus einem Bild ein 3D-Modell: Bild hochladen, freistellen lassen, optional von Gemma prüfen lassen, in 3D umwandeln, drehbar ansehen, als GLB herunterladen. Ohne Bild hilft Gemma mit Bild-Prompts aus einer Idee. Die Bilder daraus erzeugt später der Bilddienst img-o-mat.

| Teil | Wo | Was |
| --- | --- | --- |
| Website und API | Proxmox-LXC mit Docker | Python (FastAPI) mit React-Oberfläche, Port 8090; Freistellen mit rembg auf der CPU; hält keine Daten |
| Rechnen | oi-o-mat, nur über die [LLM-Schranke](https://github.com/Marcel-B/scripts/blob/main/docs/llm-gate.md) | TRELLIS.2 in MLX-Serve (Bild zu 3D), Gemma 4 26B in LM Studio (Prüfen, Prompts) |
| Dateien | T7 an oi-o-mat, `/Volumes/T7/3d-o-mat`, per SSH | Original, Freistellung, Aufträge, GLB |

```
Browser ──► 3d-o-mat (Proxmox, :8090) ──ssh──► oi-o-mat: store/launch.py ──► /Volumes/T7/3d-o-mat
                     │
                     └──► LLM-Schranke (oi-o-mat:1235) ──► MLX-Serve (TRELLIS.2), LM Studio (Gemma)
```

Die Website erreicht die T7 wie img-o-mat seinen Maler: per SSH, mit einem eigenen Schlüssel, der auf oi-o-mat nur `store/launch.py` starten darf. Das Skript kennt fünf Wörter (`check`, `put`, `get`, `list`, `delete`) und nur Pfade der Form `images|jobs/<id>/<name>`. Dafür läuft auf oi-o-mat kein Dienst und ist kein Port offen. Die Liste der Bilder und Aufträge hält die Website im Speicher und liest sie beim Start von der T7.

Die App lädt und entlädt nie selbst ein Modell. Sie meldet sich bei der Schranke als `3d-o-mat` an. Die Schranke lässt Tonwerk vor, reiht nach Priorität ein, startet MLX-Serve bei Bedarf und entlädt TRELLIS.2 nach 2 Minuten Leerlauf wieder. Lizenz: MIT.

## So läuft ein Auftrag

1. **Hochladen:** Ein Bild hochladen (PNG, JPEG, WebP). Die App stellt es frei:
   - Hat es schon Transparenz, bleibt die.
   - Sonst entfernt rembg (BiRefNet) den Hintergrund.
   - Ohne rembg wird nur ein einfarbiger Hintergrund entfernt.

   Danach wird das Bild quadratisch auf das Objekt zugeschnitten. TRELLIS.2 braucht so ein freigestelltes Bild, weil es selbst keinen Hintergrund entfernt.
2. **Prüfen (optional):** „Mit Gemma prüfen“ beantwortet: ein einzelnes Objekt, vollständig, scharf, ohne Glas? Dazu kommen Tipps.
3. **Umwandeln:** „In 3D umwandeln“ legt einen Auftrag an. Aufträge laufen nacheinander und gehen gestreamt an `/v1/3d/generations` der Schranke. Den Fortschritt zeigt die Liste.
   - Wartet der Auftrag bei der Schranke, steht ihr Grund dabei, zum Beispiel „Tonwerk arbeitet (songs)“.
   - 503 wird nach `Retry-After` wiederholt, ein abgerissener Stream bis zu dreimal.
   - 403 und 424 bedeuten, dass das Modell in `/gate` noch freigegeben werden muss.
4. **Ergebnis:** Das GLB mit PBR-Texturen liegt auf der T7. Die Seite zeigt es mit `<model-viewer>`, und es lässt sich herunterladen.

Ist die T7 nicht da (keine Datei `.3d-o-mat` im Ordner) oder oi-o-mat nicht erreichbar, nimmt die Website nichts an und zeigt „T7 nicht erreichbar“. So landet nie etwas auf der internen Platte von oi-o-mat.

## Einrichten

### 1. Proxmox: Container und Website

Das Skript `deploy/proxmox/3d-o-mat-lxc.sh` läuft in der Shell des Proxmox-Hosts als root, Schritt für Schritt:

```sh
curl -fsSLo /root/3d-o-mat-lxc.sh https://raw.githubusercontent.com/Marcel-B/3d-o-mat/main/deploy/proxmox/3d-o-mat-lxc.sh
bash /root/3d-o-mat-lxc.sh anlegen       # 1. unprivilegierter Debian-Container mit Docker
bash /root/3d-o-mat-lxc.sh app <CTID>    # 2. Repo holen, config.json und SSH-Schlüssel anlegen, bauen, starten
```

Am Ende von `app` steht der Befehl für oi-o-mat mit dem öffentlichen Schlüssel der Website. Später gibt es diese Befehle:
- `update <CTID>` holt den neuen Stand und baut neu.
- `pruefen <CTID>` zeigt den Zustand von Ablage, Schranke und Freigaben.
- `schluessel <CTID>` zeigt den Schlüssel noch einmal.

`--help` nennt die Variablen, zum Beispiel `IP=192.168.2.50/24 GATEWAY=192.168.2.1` für eine feste Adresse.

Im Container liegt die App unter `/opt/3d-o-mat`, ihre Einstellung in `config.json` (aus `config.docker.json`). Der Schlüssel liegt in `ssh/`. Nutzerdaten gibt es dort keine.

### 2. oi-o-mat: Ablage auf der T7

„Entfernte Anmeldung“ muss an sein (Systemeinstellungen → Allgemein → Teilen). Dann im Checkout von 3d-o-mat:

```sh
git clone https://github.com/Marcel-B/3d-o-mat.git ~/repos/3d-o-mat && cd ~/repos/3d-o-mat
./install-store.sh --key "ssh-ed25519 AAAA… 3d-o-mat-website"
```

Das Skript legt `/Volumes/T7/3d-o-mat` mit der Kennzeichen-Datei `.3d-o-mat` an. Es kopiert `store/launch.py` nach `~/Library/Application Support/3d-o-mat` und trägt den Schlüssel so in `~/.ssh/authorized_keys` ein, dass er nur dieses Skript starten darf (`restrict,command=…`). Ein zweiter Lauf ersetzt die Zeile. Danach auf Proxmox `bash /root/3d-o-mat-lxc.sh pruefen`.

### 3. oi-o-mat: Schranke und MLX-Serve

Die Schranke braucht den Stand mit mehreren Servern (scripts #19, gemergt). MLX-Serve wird einmal eingetragen:

```sh
curl -X PATCH http://127.0.0.1:1235/gate/settings -H 'content-type: application/json' \
  -d '{"backends":{"mlx-serve":{"type":"mlx-serve","url":"http://127.0.0.1:11234","start":"/Volumes/T7/mlx-3d/start.sh"}}}'
curl -s http://127.0.0.1:11234/v1/models | grep -o '"id":"[^"]*"'
```

Die zweite Zeile funktioniert nur, wenn MLX-Serve läuft. Sie zeigt den Namen, unter dem MLX-Serve TRELLIS.2 führt. Genau dieser Name muss in `config.json` bei `trellisModel` und in `gate.models` stehen. Die Vorlage nimmt `TRELLIS.2-4B-MLX-Serve-8bit` an. Liegt das Modell nicht im Modellordner von MLX-Serve, kommt der absolute Pfad als `source` dazu.

TRELLIS.2 braucht beim Rechnen deutlich mehr Speicher als im Leerlauf. Die Vorlage schätzt `ramGb: 18`. Damit das neben den anderen Apps geht, sollte `ramBudgetGb` der Schranke mindestens 18 sein (für immich-tagger ohnehin nötig). Den echten Spitzenwert beim ersten Lauf in der Aktivitätsanzeige ablesen und `ramGb` anpassen.

### 4. Freigeben

Beim ersten Start meldet sich die App bei der Schranke an. In `http://192.168.2.122:1235/gate` dann bei **3d-o-mat** TRELLIS.2 freigeben, Gemma ebenfalls freigeben oder installieren. Danach ist die Website unter `http://<container>:8090` erreichbar. Beim ersten Freistellen lädt rembg sein Modell (etwa 1 GB) in das Volume `rembg-cache`.

### Statt Proxmox: auf einem Mac

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-rembg.txt
(cd frontend && npm ci && npm run build)
cp config.example.json config.json
.venv/bin/python -m omat3d
```

`config.example.json` nimmt `"store": {"folder": "/Volumes/T7/3d-o-mat"}`. Ohne `ssh` ruft die Website `store/launch.py` selbst auf, mit demselben Kennzeichen `.3d-o-mat`.

## Einstellungen (`config.json`)

| Feld | Bedeutung |
| --- | --- |
| `store` | `{"ssh": "marcel@192.168.2.122", "key": "/ssh/id_ed25519", "knownHosts": "/ssh/known_hosts"}` für die T7 von oi-o-mat, oder `{"folder": "…"}` für einen eigenen Ordner |
| `gate` | Anmeldung bei der Schranke: `url`, `app`, `note`, `priority`, `holdFor`, `models` (siehe docs/llm-gate.md im scripts-Repo) |
| `trellisModel`, `assistModel` | welche der angemeldeten Modelle für 3D und für Gemma |
| `defaults` | Vorgaben für `/v1/3d/generations`: `texture_size`, `decimation_target`, auch `resolution`, `steps` |
| `cutout` | `auto`, `rembg`, `flood` oder `none` |
| `rembgModel` | z. B. `birefnet-general` (gut, etwa 1 GB) oder `u2net` (kleiner) |

## Entwickeln

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
cd frontend && npm run dev
```

`npm run dev` läuft auf Port 5173 und leitet `/api` an Port 8090 weiter. Die Tests spielen den ganzen Weg ohne oi-o-mat durch: Hochladen, Freistellen, Auftrag, Stream mit Abbruch und 503, Ablehnung, fehlende T7, Gemma, unzulässige Pfade. Die Schranke ist dabei ein `httpx.MockTransport`. Die Ablage läuft einmal direkt und einmal über `tests/fake/ssh`, das wie der erzwungene Befehl von sshd arbeitet.

## Später

- **Bilder erzeugen:** über img-o-mat (FLUX, Krea 2, Z-Image-Turbo), den auch Tonwerk nutzt. Dann führt der Prompt-Teil direkt zu Bildern zur Auswahl.
- Pixal3D für mehrere Ansichten desselben Objekts (MLX-Serve kann es, `images` statt `image`).

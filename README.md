# 3d-o-mat

Aus einem Bild ein 3D-Modell: Bild hochladen, freistellen lassen, optional von Gemma prüfen lassen, in 3D umwandeln, drehbar ansehen, als GLB herunterladen. Ohne Bild hilft Gemma mit Bild-Prompts aus einer Idee. Die Bilder daraus erzeugt später der Bilddienst img-o-mat.

| Teil | Wo | Was |
| --- | --- | --- |
| Website und API | Proxmox-LXC mit Docker (oder ein Mac, siehe unten) | Python (FastAPI) mit React-Oberfläche, Port 8090; Freistellen mit rembg auf der CPU |
| Rechnen | oi-o-mat, nur über die [LLM-Schranke](https://github.com/Marcel-B/scripts/blob/main/docs/llm-gate.md) | TRELLIS.2 in MLX-Serve (Bild zu 3D), Gemma 4 26B in LM Studio (Prüfen, Prompts) |
| Daten | T7 an oi-o-mat, Ordner `3d-o-mat`, per SMB eingebunden | Bilder, Freistellungen, Aufträge, GLB-Dateien |

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

Ohne verbundene T7, also ohne die Datei `.3d-o-mat` im Datenordner, nimmt die App nichts an und zeigt „T7 nicht verbunden“. So schreibt sie nie aus Versehen auf die Platte des Containers.

## Einrichten

### 1. oi-o-mat: Ordner und Freigabe

```sh
mkdir -p /Volumes/T7/3d-o-mat && touch /Volumes/T7/3d-o-mat/.3d-o-mat
```

Dann in den Systemeinstellungen unter **Allgemein → Teilen → Dateifreigabe** den Ordner `/Volumes/T7/3d-o-mat` hinzufügen. Lesen und Schreiben bekommt nur `marcel`, Gäste haben keinen Zugriff. Bei den Optionen muss SMB an sein.

### 2. oi-o-mat: Schranke und MLX-Serve

Die Schranke braucht den Stand mit mehreren Servern (scripts PR #19). MLX-Serve wird einmal eingetragen:

```sh
curl -X PATCH http://127.0.0.1:1235/gate/settings -H 'content-type: application/json' \
  -d '{"backends":{"mlx-serve":{"type":"mlx-serve","url":"http://127.0.0.1:11234","start":"/Volumes/T7/mlx-3d/start.sh"}}}'
curl -s http://127.0.0.1:11234/v1/models | grep -o '"id":"[^"]*"'
```

Die zweite Zeile funktioniert nur, wenn MLX-Serve läuft. Sie zeigt den Namen, unter dem MLX-Serve TRELLIS.2 führt. Genau dieser Name muss in `config.json` bei `trellisModel` und in `gate.models` stehen. Die Vorlage nimmt `TRELLIS.2-4B-MLX-Serve-8bit` an. Liegt das Modell nicht im Modellordner von MLX-Serve, kommt der absolute Pfad als `source` dazu.

TRELLIS.2 braucht beim Rechnen deutlich mehr Speicher als im Leerlauf. Die Vorlage schätzt `ramGb: 18`. Damit das neben den anderen Apps geht, sollte `ramBudgetGb` der Schranke mindestens 18 sein (für immich-tagger ohnehin nötig). Den echten Spitzenwert beim ersten Lauf in der Aktivitätsanzeige ablesen und `ramGb` anpassen.

### 3. Proxmox: Freigabe einbinden und Container

Auf dem Proxmox-Host:

```sh
apt install cifs-utils
printf 'username=marcel\npassword=…\n' > /root/.smb-oi-o-mat && chmod 600 /root/.smb-oi-o-mat
mkdir -p /mnt/oi-o-mat-3d
# uid/gid 100000: root eines unprivilegierten LXC
echo '//192.168.2.122/3d-o-mat /mnt/oi-o-mat-3d cifs credentials=/root/.smb-oi-o-mat,uid=100000,gid=100000,iocharset=utf8,_netdev,x-systemd.automount 0 0' >> /etc/fstab
systemctl daemon-reload && mount /mnt/oi-o-mat-3d && ls -a /mnt/oi-o-mat-3d
pct set <lxc-id> -mp0 /mnt/oi-o-mat-3d,mp=/mnt/3d-o-mat
```

Im LXC:

```sh
git clone https://github.com/Marcel-B/3d-o-mat.git && cd 3d-o-mat
cp config.docker.json config.json
docker compose up -d --build
docker compose logs -f
```

Beim ersten Start meldet sich die App bei der Schranke an. In `http://192.168.2.122:1235/gate` dann bei **3d-o-mat** TRELLIS.2 freigeben, Gemma ebenfalls freigeben oder installieren. Danach ist die Website unter `http://<lxc>:8090` erreichbar. Beim ersten Freistellen lädt rembg sein Modell (etwa 1 GB) in das Volume `rembg-cache`.

Aktualisieren: `git pull && docker compose up -d --build`.

### Statt Proxmox: auf einem Mac

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-rembg.txt
(cd frontend && npm ci && npm run build)
cp config.example.json config.json
.venv/bin/python -m omat3d
```

`config.example.json` bindet die Freigabe über `mountCommand` selbst ein (`osascript … mount volume`, das Passwort kommt aus dem Schlüsselbund), wenn sie fehlt. Sie erscheint dann unter `/Volumes/3d-o-mat`.

## Einstellungen (`config.json`)

| Feld | Bedeutung |
| --- | --- |
| `dataDir` | Datenordner, darin muss `.3d-o-mat` liegen |
| `mountCommand` | Befehl, der die Freigabe einbindet, wenn sie fehlt (höchstens einmal je Minute) |
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

`npm run dev` läuft auf Port 5173 und leitet `/api` an Port 8090 weiter. Die Tests spielen den ganzen Weg ohne oi-o-mat durch: Hochladen, Freistellen, Auftrag, Stream mit Abbruch und 503, Ablehnung, fehlende T7, Gemma. Die Schranke ist dabei ein `httpx.MockTransport`.

## Später

- **Bilder erzeugen:** über img-o-mat (FLUX, Krea 2, Z-Image-Turbo), den auch Tonwerk nutzt. Dann führt der Prompt-Teil direkt zu Bildern zur Auswahl.
- Pixal3D für mehrere Ansichten desselben Objekts (MLX-Serve kann es, `images` statt `image`).

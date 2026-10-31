# 3d-o-mat

Aus einem Bild ein 3D-Modell: Bild hochladen, freistellen lassen, optional von Gemma prüfen lassen, in 3D umwandeln, drehbar ansehen, als GLB oder als 3MF für den 3D-Drucker herunterladen; eine Figur lässt sich vorher in eine Haltung stellen. Ohne Bild schreibt Gemma aus einer Idee Bild-Prompts, und img-o-mat malt sie.

| Teil | Wo | Was |
| --- | --- | --- |
| Website und API | Proxmox-LXC mit Docker | Python (FastAPI) mit React-Oberfläche, Port 8090; Freistellen mit rembg auf der CPU; hält keine Daten |
| Rechnen | oi-o-mat, nur über die [LLM-Schranke](https://github.com/Marcel-B/scripts/blob/main/docs/llm-gate.md) | TRELLIS.2 in MLX-Serve (Bild zu 3D), SkinTokens im Rig-Dienst (Skelett einsetzen), Gemma 4 26B in LM Studio (Prüfen, Prompts), img-o-mat mit FLUX.2 Klein 4B, Z-Image Turbo oder Krea 2 Turbo (Bilder malen) |
| Dateien | T7 an oi-o-mat, `/Volumes/T7/3d-o-mat`, per SSH | Original, Freistellung, Aufträge, GLB |

```
Browser ──► 3d-o-mat (Proxmox, :8090) ──ssh──► oi-o-mat: store/launch.py ──► /Volumes/T7/3d-o-mat
                     │
                     └──► LLM-Schranke (oi-o-mat:1235) ──► MLX-Serve (TRELLIS.2), LM Studio (Gemma), Rig-Dienst (SkinTokens),
                                                      img-o-mat (malt auf oi-o-mat)
```

Die Website erreicht die T7 wie img-o-mat seinen Maler: per SSH, mit einem eigenen Schlüssel, der auf oi-o-mat nur `store/launch.py` starten darf. Das Skript kennt fünf Wörter (`check`, `put`, `get`, `list`, `delete`) und nur Pfade der Form `images|jobs/<id>/<name>`. Dafür läuft auf oi-o-mat kein Dienst und ist kein Port offen. Die Liste der Bilder und Aufträge hält die Website im Speicher und liest sie beim Start von der T7.

Die Oberfläche gibt es auf Deutsch und Englisch, dazu hell, dunkel oder nach dem System; beides lässt sich oben rechts umschalten und bleibt im Browser gespeichert (`localStorage`: `lang`, `theme`). Ohne Wahl folgt die Sprache dem Browser. Gemma prüft und schreibt in der Sprache der Oberfläche; die Bild-Prompts bleiben englisch. Meldungen der Python-App (Fehler, Stand eines Auftrags) sind noch deutsch.

Die App lädt und entlädt nie selbst ein Modell. Sie meldet sich bei der Schranke als `3d-o-mat` an. Die Schranke lässt Tonwerk vor, reiht nach Priorität ein, startet MLX-Serve bei Bedarf und entlädt TRELLIS.2 nach 2 Minuten Leerlauf wieder. Lizenz: MIT.

## So läuft ein Auftrag

1. **Hochladen:** Ein Bild hochladen (PNG, JPEG, WebP). Die App stellt es frei:
   - Hat es schon Transparenz, bleibt die.
   - Sonst entfernt rembg (ISNet) den Hintergrund.
   - Ohne rembg wird nur ein einfarbiger Hintergrund entfernt.

   Danach wird das Bild quadratisch auf das Objekt zugeschnitten. TRELLIS.2 braucht so ein freigestelltes Bild, weil es selbst keinen Hintergrund entfernt.
   Kein Bild zur Hand? Unter „Malen lassen“ eine Idee eingeben: „Prompts schreiben“ lässt Gemma daraus englische Prompts für eine gute Vorlage machen (ein Objekt, ganz im Bild, schlichter Hintergrund), „malen“ schickt einen davon an img-o-mat, „Direkt malen“ den eingegebenen Text. Gemalt wird über die Schranke auf oi-o-mat mit FLUX.2 Klein 4B, Z-Image Turbo (beide Apache 2.0, ohne Inhaltsfilter) oder Krea 2 Turbo (Krea 2 Community License; img-o-mat prüft jedes Bild mit dem Inhaltsfilter, braucht beim Malen etwa 22 GB), 1 bis 4 Bilder. Hält der Filter ein Bild zurück, steht das als Fehler beim Malauftrag. Jedes fertige Bild wird wie ein hochgeladenes freigestellt und steht dann in der Bildliste, mit Modell, Seed und Prompt. Das Original behält img-o-mats XMP-Vermerk, dass es KI-generiert ist.
2. **Prüfen (optional):** „Mit Gemma prüfen“ beantwortet: ein einzelnes Objekt, vollständig, scharf, ohne Glas? Dazu kommen Tipps.
3. **Umwandeln:** „In 3D umwandeln“ legt einen Auftrag an. Aufträge laufen nacheinander und gehen gestreamt an `/v1/3d/generations` der Schranke. Den Fortschritt zeigt die Liste.
   - Wartet der Auftrag bei der Schranke, steht ihr Grund dabei, zum Beispiel „Tonwerk arbeitet (songs)“.
   - 503 wird nach `Retry-After` wiederholt, ein abgerissener Stream bis zu dreimal.
   - 403 und 424 bedeuten, dass das Modell in `/gate` noch freigegeben werden muss.
4. **Ergebnis:** Das GLB mit PBR-Texturen liegt auf der T7. Die Seite zeigt es mit `<model-viewer>`, und es lässt sich herunterladen.

5. **Skelett einsetzen (optional):** Unter einem fertigen Modell setzt „Skelett einsetzen“ Knochen und Gewichte ein. Dafür läuft SkinTokens auf oi-o-mat, etwa 35 Sekunden und höchstens 8 GB. Das Ergebnis ist ein eigener Auftrag. Das Skelett hängt vom Seed ab. Kann SkinTokens es nicht als Mensch benennen (etwa: keine Brust mit Armen auf beiden Seiten gefunden), probiert der Rig-Dienst die nächsten Seeds, bis zu drei je Auftrag (`humanoidTries` in `rig.json`). Ein neuer Versuch auf der Seite fängt bei den nächsten drei an.
   - Bei „Mensch“ tragen die Knochen Mixamo-Namen. Animationen von Mixamo (Without Skin) passen dann direkt, etwa in Godot oder Unity.
   - Der Viewer zeigt die Figur mit Proben (Gehen, Schlag, Tritt, Arme heben, Kniebeuge, Umfallen), Schiebern für einzelne Knochen und dem Befund, welche Mixamo-Knochen gefunden wurden.
   - „GLB mit Proben herunterladen“ hängt die sechs Proben als glTF-Animationen an das GLB (30 Bilder je Sekunde, eine Runde je Probe, mit der eingestellten Stärke). In Godot erscheinen sie im `AnimationPlayer` als „Gehen“, „Tritt“, „Umfallen“ usw., in Blender als Aktionen. Netz, Texturen und Skelett bleiben unverändert; nur bewegte Knoten, die als Matrix gespeichert sind, werden in Lage, Drehung und Größe zerlegt, weil glTF das für Animationen verlangt. Die Proben sind Prüfbewegungen; für ein Spiel passen Mixamo-Animationen über Godots Retargeting besser.

6. **Für den 3D-Druck (optional):** Unter jedem fertigen Modell macht „Für den 3D-Druck“ eine Datei für den Slicer: 3MF (Bambu Studio, PrusaSlicer, OrcaSlicer, Cura) oder STL, in Millimetern, mit wählbarer Höhe (20 bis 400 mm) und auf Wunsch mit rundem Sockel. Gerechnet wird im Container auf der CPU (`omat3d/printout.py`), ohne Modell auf oi-o-mat:
   - Bei einem Modell mit Skelett zählt die Haltung, die der Viewer gerade zeigt. Die Seite schickt die Drehung jedes Knochens; der Server bewegt die Punkte mit den Gewichten aus SkinTokens mit (Linear Blend Skinning, wie three.js) und stellt den tiefsten Punkt auf den Boden.
   - Sind alle Teile geschlossen, verschmilzt [manifold3d](https://github.com/elalish/manifold) sie zu einem Körper. Sonst wird die Figur über ein Raster (Höhe / 550, also etwa 0,2 mm bei 120 mm und 0,36 mm bei 200 mm) neu umhüllt: Das Raster entscheidet nur, was innen und außen ist; die neue Oberfläche liegt im genauen Abstand von gut einer halben Zelle zur alten, damit sie glatt bleibt und keine Treppen zeigt. Löcher bis gut 1 mm schließen sich, hauchdünne Flächen werden etwa eine Zelle dick. Mit Beispielfiguren dauerte das 15 bis 25 Sekunden und brauchte gut 1 GB; es rechnet immer nur eine Datei auf einmal.
   - Gedruckt wird nur die Form. Was TRELLIS.2 nur in die Textur gemalt hat (Augen, Muster, oft auch eine Brille), fehlt. Finger und Riemen unter etwa 0,8 mm brechen beim FDM-Druck leicht; für Figuren um 10 bis 15 cm ist Resin besser.

   **Haltung:** Im Viewer mit Skelett gibt es unter „Haltung“ fertige Haltungen (Arme runter, Lässig stehen, Heldenpose, Schritt, Winken, Arm hoch). Oder man beschreibt eine, etwa „steht lässig, rechte Hand winkt“, und Gemma setzt Winkel für 16 Mixamo-Knochen (`/api/pose`); einen Oberarm gibt Gemma als Richtung an (wie hoch, wohin), Finger und Hände gibt es nicht. Danach lässt sich jeder Knochen mit den Schiebern nachstellen; die Schieber drehen von der gewählten Haltung aus und merken sich ihre Werte je Knochen. Die Füße bleiben auf dem Gitter. Starke Winkel (Ellbogen, Knie über etwa 100°) knicken das Netz sichtbar, ruhige Haltungen mit Armen nah am Körper drucken sich am besten.

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
- `autoupdate <CTID> [on|off|status|run]` schaltet das Auto-Update im Container (`app` schaltet es ein): ein systemd-Timer holt alle 5 Minuten `main` und baut neu, aber nur, wenn kein Auftrag wartet oder rechnet. Ein Stand, dessen Bau fehlschlug, wird erst mit dem nächsten Commit wieder versucht. Log: `pct exec <CTID> -- journalctl -u 3d-o-mat-update`.
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

Die zweite Zeile funktioniert nur, wenn MLX-Serve läuft. Sie zeigt den Namen, unter dem MLX-Serve TRELLIS.2 führt. Genau dieser Name muss in `config.json` bei `trellisModel` und in `gate.models` stehen. Die Vorlage nimmt `ddalcu/TRELLIS.2-4B-MLX-Serve-8bit` an. Liegt das Modell nicht im Modellordner von MLX-Serve, kommt der absolute Pfad als `source` dazu.

TRELLIS.2 braucht beim Rechnen mehr Speicher als im Leerlauf. Auf oi-o-mat (M6, 32 GB) zeigte die GPU beim ersten Lauf (Auflösung 1024, Textur 2048) höchstens etwa 9 GB, danach 7 GB, bis die Schranke es entlud. Die Vorlage meldet deshalb `ramGb: 12` an, mit Puffer für die Textur-Stufe.

### 4. oi-o-mat: Rig-Dienst (Skelett einsetzen)

Rust muss da sein (`curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y`). Dann im Checkout von 3d-o-mat:

```sh
./install-rig.sh
```

Das Skript erledigt:
- Es baut die Apple-Silicon-Fassung von SkinTokens ([chris-straka/skintokens](https://github.com/chris-straka/skintokens), Rust mit Metal) unter `/Volumes/T7/skintokens`, falls sie dort noch nicht liegt.
- Es lädt die offiziellen Gewichte (1,1 GB, mit Prüfsumme).
- Es kopiert `rig/server.py` nach `~/Library/Application Support/3d-o-mat` und trägt den Dienst bei der Schranke als Server `rig` vom Typ `tool` ein. Die Schranke startet ihn bei der ersten Anfrage.
- Danach in `/gate` bei 3d-o-mat das Modell `skintokens` freigeben.

**Lizenzen:**
- Code und Gewichte von SkinTokens stehen unter MIT, das eingebaute Qwen3-0.6B unter Apache-2.0.
- Das gebaute `skintokens`-Programm ist GPL-3.0, weil sein Teil zum Einlesen der Form aus Michelangelo stammt. 3d-o-mat ruft es nur als eigenes Programm auf und bleibt MIT.
- Die erzeugten Skelette und Gewichte sind Ausgaben des Modells und kein GPL-Code.
- Für die Druckdatei nutzt die Website manifold3d (Apache-2.0) und scikit-image (BSD-3-Clause).

Gemessen auf oi-o-mat (M6, 32 GB) mit einer TRELLIS-Figur (226.000 Punkte): 34,5 s, höchstens 8,3 GB.

### 5. Freigeben

Beim ersten Start meldet sich die App bei der Schranke an. In `http://192.168.2.122:1235/gate` dann bei **3d-o-mat** TRELLIS.2, SkinTokens und die Bildmodelle (`klein-4b`, `z-image-turbo`, `krea-2-turbo`) freigeben, Gemma ebenfalls freigeben oder installieren. Danach ist die Website unter `http://<container>:8090` erreichbar. Beim Start lädt die App das rembg-Modell (etwa 170 MB) einmal in das Volume `rembg-cache`.

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
| `trellisModel`, `rigModel`, `assistModel` | welche der angemeldeten Modelle für 3D, fürs Skelett und für Gemma |
| `paintModels` | die Bildmodelle von img-o-mat zur Auswahl, das erste ist die Vorgabe (`klein-4b`, `z-image-turbo`, `krea-2-turbo`). Fehlt eines dieser Modelle in `gate.models`, meldet die App es trotzdem an; so reicht bei einer älteren `config.json` ein Update |
| `defaults` | Vorgaben für `/v1/3d/generations`: `texture_size`, `decimation_target`, auch `resolution`, `steps` |
| `cutout` | `auto`, `rembg`, `flood` oder `none` |
| `rembgModel` | Vorgabe `isnet-general-use`: auf einem 12-MP-Foto 9 s und 1,5 GB RAM. `birefnet-general` stellt etwas feiner frei, braucht aber 115 s und 9,5 GB, mehr als der Container hat |

## Entwickeln

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
cd frontend && npm run dev
```

`npm run dev` läuft auf Port 5173 und leitet `/api` an Port 8090 weiter. Die Tests spielen den ganzen Weg ohne oi-o-mat durch: Hochladen, Freistellen, Auftrag, Stream mit Abbruch und 503, Ablehnung, fehlende T7, Gemma, Malen samt Inhaltsfilter, Haltung von Gemma, Druckdatei, unzulässige Pfade. `tests/test_printout.py` backt die Haltung eines kleinen GLB mit zwei Knochen ein und prüft auf beiden Wegen, dass die Druckdatei wasserdicht ist. Die Schranke ist dabei ein `httpx.MockTransport`. Die Ablage läuft einmal direkt und einmal über `tests/fake/ssh`, das wie der erzwungene Befehl von sshd arbeitet.

## Später

- Pixal3D für mehrere Ansichten desselben Objekts (MLX-Serve kann es, `images` statt `image`).

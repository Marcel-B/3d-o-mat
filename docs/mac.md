# 3d-o-mat auf einem Mac einrichten

Diese Anleitung bringt die Website von 3d-o-mat auf einem Mac zum Laufen, ohne Proxmox und ohne Docker: zum Entwickeln, zum Ausprobieren oder als Dauerbetrieb auf oi-o-mat selbst. Gerechnet wird weiter über die [LLM-Schranke](https://github.com/Marcel-B/scripts/blob/main/docs/llm-gate.md) auf oi-o-mat. Auf dem Mac laufen nur die Python-App, die gebaute Oberfläche und das Freistellen mit rembg.

Es gibt zwei Wege, die sich nur in der `config.json` unterscheiden:

| Weg | Ablage (`store`) | Schranke (`gate.url`) |
| --- | --- | --- |
| **A: ein Mac im Heimnetz** (Entwickeln, Ausprobieren) | ein Ordner auf dem Mac | `http://192.168.2.122:1235` |
| **B: alles auf oi-o-mat** (statt des Proxmox-Containers) | `/Volumes/T7/3d-o-mat` | `http://127.0.0.1:1235` |

Für beide gilt: Die Schranke, MLX-Serve mit TRELLIS.2, der Rig-Dienst und img-o-mat sind auf oi-o-mat schon eingerichtet (README, Abschnitte 3 bis 5). Ohne sie startet die Website trotzdem, nimmt Bilder an und stellt sie frei; Prüfen, Malen, 3D und Skelett gehen dann nicht.

## 1. Werkzeuge

Gebraucht werden Git, Python 3.12 und Node 22, am einfachsten über [Homebrew](https://brew.sh):

```sh
xcode-select --install          # Git und die Kommandozeilenwerkzeuge, falls noch nicht da
brew install python@3.12 node@22
```

macOS' eigenes `/usr/bin/python3` (3.9) reicht für die App nicht, sie braucht mindestens 3.10; der Container nimmt 3.12. Nur `store/launch.py` und `rig/server.py` bleiben bei 3.9, weil sie auf oi-o-mat mit dem Python von macOS laufen. Node braucht es nur zum Bauen der Oberfläche (Vite 7 verlangt Node 20.19 oder neuer). Falls `node@22` nicht im `PATH` landet, sagt `brew info node@22`, welche Zeile in `~/.zprofile` gehört.

## 2. Repo holen und bauen

```sh
git clone https://github.com/Marcel-B/3d-o-mat.git ~/repos/3d-o-mat && cd ~/repos/3d-o-mat
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-rembg.txt
(cd frontend && npm ci && npm run build)
```

- Die virtuelle Umgebung `.venv` hält alle Python-Pakete im Checkout; nichts wird systemweit installiert.
- `requirements-rembg.txt` bringt rembg mit onnxruntime für das Freistellen von Fotos. Ohne das Paket stellt die App nur einfarbige Hintergründe frei (`cutout: "auto"` merkt das von selbst).
- `npm run build` legt die Oberfläche in `frontend/dist`. Von dort liefert sie die Python-App unter `/` aus. Nach jedem `git pull`, der etwas in `frontend/` ändert, noch einmal bauen.

## 3. Ablage anlegen

Die App legt Bilder und Aufträge nur in einen Ordner, in dem die Kennzeichen-Datei `.3d-o-mat` liegt. Fehlt sie, zeigt die Seite „T7 nicht erreichbar“ und nimmt nichts an.

**Weg A**, ein eigener Ordner:

```sh
mkdir -p ~/3d-o-mat-daten && touch ~/3d-o-mat-daten/.3d-o-mat
```

**Weg B**, die T7 an oi-o-mat: `./install-store.sh` (ohne `--key`) legt `/Volumes/T7/3d-o-mat` samt `.3d-o-mat` an. Die T7 ist für macOS ein Wechseldatenträger. Beim ersten Zugriff fragt macOS nach; bis jemand „Erlauben“ klickt, hängt der Zugriff ohne Fehlermeldung. Unter Systemeinstellungen → Datenschutz & Sicherheit → Dateien und Ordner muss beim Terminal (oder dem Programm, das die App startet) „Wechseldatenträger“ an sein.

## 4. Einstellung

```sh
cp config.example.json config.json
```

Dann in `config.json` (sie steht in `.gitignore` und bleibt nur lokal) anpassen:

- **Weg A:** `"store": {"folder": "/Users/<du>/3d-o-mat-daten"}`. Ein absoluter Pfad ist am sichersten; `~` wird nicht aufgelöst. `gate.url` bleibt `http://192.168.2.122:1235`.
- **Weg B:** `store` bleibt `{"folder": "/Volumes/T7/3d-o-mat"}`, `gate.url` wird `http://127.0.0.1:1235`.

Mit `folder` startet die Website `store/launch.py` selbst, ohne SSH. Die übrigen Felder stehen in der Tabelle im README. Eine andere Datei nimmt die App über `OMAT3D_CONFIG=/pfad/zur/config.json`.

## 5. Starten

```sh
.venv/bin/python -m omat3d
```

Die Website läuft dann unter `http://localhost:8090`, im Heimnetz auch unter der Adresse des Macs (`listen: "0.0.0.0"`; nur für diesen Mac `"127.0.0.1"`). Beim ersten Freistellen lädt rembg sein Modell (etwa 170 MB) nach `~/.u2net`; das dauert einmal etwas.

Was beim Start passiert:

- Die App meldet sich bei der Schranke als `3d-o-mat` an. Ist die Schranke nicht erreichbar, steht im Terminal „LLM-Schranke nicht erreichbar … neuer Versuch in 10 s“, und die App versucht es weiter.
- Nach der ersten Anmeldung steht 3d-o-mat in `http://192.168.2.122:1235/gate` mit „nicht frei“. Dort TRELLIS.2, SkinTokens, Gemma und die Bildmodelle freigeben. Bis dahin bekommt jede Anfrage 403. Meldet sich ein zweiter Mac mit demselben Namen `3d-o-mat` an, gelten dieselben Freigaben; einen eigenen Eintrag gibt `gate.app`, etwa `"3d-o-mat-dev"`.
- `http://localhost:8090/api/status` zeigt in einem Blick, ob die Ablage da ist (`storage.mounted`), ob rembg geladen ist und ob die Schranke erreichbar ist und die Modelle frei sind. Den Zustand der Ablage prüft die App höchstens alle 20 Sekunden neu.

Beenden mit Ctrl-C. Was lief oder bei der Schranke wartete, kommt beim nächsten Start wieder in die Schlange.

## 6. Entwickeln

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest          # alle Tests, ohne oi-o-mat und ohne Netz
cd frontend && npm run dev          # Oberfläche mit Neuladen auf http://localhost:5173
```

`npm run dev` leitet `/api` an Port 8090 weiter, die Python-App muss also daneben laufen. Für Änderungen an der Oberfläche reicht das; `npm run build` braucht es erst wieder, wenn die Seite über Port 8090 kommen soll.

## Wenn es hakt

| Zeichen | Ursache und Abhilfe |
| --- | --- |
| `TypeError: unsupported operand type(s) for \|` beim Start | Die venv ist mit macOS' Python 3.9 angelegt. `.venv` löschen und mit `python3.12` neu anlegen (Schritt 2). |
| „T7 nicht erreichbar“ | `.3d-o-mat` fehlt im Ordner aus `store.folder`, oder der Pfad stimmt nicht. Nach dem Anlegen bis zu 20 Sekunden warten. |
| Zugriff auf `/Volumes/T7` hängt | macOS wartet auf „Erlauben“ für den Wechseldatenträger (Schritt 3). |
| Seite leer oder 404 unter `/` | `frontend/dist` fehlt: `(cd frontend && npm ci && npm run build)`. |
| 403 oder 424 bei Prüfen, Malen oder 3D | Modell in `/gate` für 3d-o-mat noch nicht freigegeben oder nicht installiert. |
| `address already in use` | Port 8090 ist belegt, etwa von einer zweiten Instanz. `port` in `config.json` ändern; `npm run dev` erwartet dann aber weiter 8090 (`frontend/vite.config.ts`). |
| Fotos werden schlecht freigestellt | rembg fehlt (`/api/status` zeigt `"rembg": false`): `.venv/bin/pip install -r requirements-rembg.txt`. |

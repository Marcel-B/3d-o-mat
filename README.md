# 3d-o-mat

Aus einem Bild ein 3D-Modell: Bild hochladen, freistellen lassen, optional von Gemma prüfen lassen, in 3D umwandeln, drehbar ansehen, als GLB, als FBX für Unity und Unreal oder als 3MF für den 3D-Drucker herunterladen; eine Figur lässt sich vorher in eine Haltung stellen. Ohne Bild schreibt Gemma aus einer Idee Bild-Prompts, und img-o-mat malt sie.

| Teil | Wo | Was |
| --- | --- | --- |
| Website und API | Proxmox-LXC mit Docker | Python (FastAPI) mit React-Oberfläche, Port 8090; Freistellen mit rembg und FBX mit Blender auf der CPU; hält keine Daten |
| Rechnen | oi-o-mat, nur über die [LLM-Schranke](https://github.com/Marcel-B/scripts/blob/main/docs/llm-gate.md) | TRELLIS.2 in MLX-Serve (Bild zu 3D), SkinTokens im Rig-Dienst (Skelett einsetzen), Blender im Aufbereitungs-Dienst (Figur aufbereiten), Gemma 4 26B in LM Studio (Prüfen, Prompts), img-o-mat mit FLUX.2 Klein 4B, Z-Image Turbo oder Krea 2 Turbo (Bilder malen) |
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

5. **Skelett einsetzen (optional):** Unter einem fertigen Modell setzt „Skelett einsetzen“ Knochen und Gewichte ein. Dafür läuft SkinTokens auf oi-o-mat, etwa 35 Sekunden und höchstens 8 GB. Das Ergebnis ist ein eigener Auftrag. Das Skelett hängt vom Seed ab. SkinTokens findet die Finger als Ketten von Knochen an der Hand (je nach Figur zwei bis fünf je Hand), benennt aber nur den Rumpf; 3d-o-mat gibt den Ketten danach Mixamo-Namen (`omat3d/fingers.py`): die Kette am nächsten zum Handgelenk ist der Daumen, die anderen zählen von ihm weg (Zeige-, Mittel-, Ring-, kleiner Finger; eine einzelne Kette ist ein Fäustling und wird zum Zeigefinger). Ein Skelett von früher bekommt die Namen beim ersten Lesen und wird so auf die T7 zurückgeschrieben (`fingers` im Auftrag zählt sie). Damit greifen Faust-Schieber, Gemma und die Namen in Godot. Kann SkinTokens es nicht als Mensch benennen (etwa: keine Brust mit Armen auf beiden Seiten gefunden), probiert der Rig-Dienst die nächsten Seeds, bis zu drei je Auftrag (`humanoidTries` in `rig.json`). Ein neuer Versuch auf der Seite fängt bei den nächsten drei an.
   - Bei „Mensch“ tragen die Knochen Mixamo-Namen. Animationen von Mixamo (Without Skin) passen dann direkt, etwa in Godot oder Unity.
   - Der Viewer zeigt die Figur mit Proben (Gehen, Schlag mit Faust, Tritt, Drehtritt, Arme heben, Kniebeuge, Umfallen), Schiebern für einzelne Knochen dem Befund, welche Mixamo-Knochen gefunden wurden, und der Bewegungsbibliothek (siehe unten).
   - „GLB mit Proben herunterladen“ hängt die sieben Proben als glTF-Animationen an das GLB (30 Bilder je Sekunde, eine Runde je Probe, mit der eingestellten Stärke). In Godot erscheinen sie im `AnimationPlayer` als „Gehen“, „Tritt“, „Umfallen“ usw., in Blender als Aktionen. Netz, Texturen und Skelett bleiben unverändert; nur bewegte Knoten, die als Matrix gespeichert sind, werden in Lage, Drehung und Größe zerlegt, weil glTF das für Animationen verlangt. Die Proben sind gerechnete Prüfbewegungen; für ein Spiel sind die Aufnahmen aus der Bibliothek besser.
   - **Animationen aus der Bibliothek:** 244 echte Bewegungsaufnahmen (Motion Capture) aus zwei freien Sammlungen, nach Quelle, Art (Gehen, Laufen, Springen, Kämpfen, Tanzen, Gesten, Alltag, Akrobatik), Stil und Text zu suchen. Ein Klick spielt die Aufnahme auf der eigenen Figur ab; die Kamera geht mit, wenn die Figur losläuft. Einstellbar sind Tempo, Anfang und Ende, „An Ort und Stelle“ (die Hüfte bleibt über dem Startpunkt, wie für ein Spiel mit eigener Steuerung) und Spiegeln (links und rechts getauscht). „Füße festhalten“ (an) sucht in der Aufnahme, wann ein Fuß auf dem Boden steht, und hält ihn dort mit zwei Knochen (Oberschenkel, Schienbein) fest, statt ihn rutschen zu lassen; kein Fuß sinkt dabei unter den Boden. „Schleife“ blendet die letzten Zehntelsekunden weich in das erste Bild über, damit eine wiederholte Animation nicht springt. „Faust“ (bei Figuren mit Fingerknochen) schließt beide Hände während der Aufnahme, denn die Aufnahmen selbst bringen kaum Fingerbewegung mit (CMU je Hand Daumen und einen Finger, Bandai Namco keine). „Armabstand“ (−15° bis +30°) dreht die Arme um die Schulter vom Körper weg, wenn die Hände einer kräftigen Figur in Bauch oder Hüfte stecken. „Root Motion (Godot)“ beim Herunterladen legt die waagrechte Bewegung auf die Armature statt auf die Hüfte; in Godot zeigt `root_motion_track` des `AnimationTree` dann auf diese Spur. „Zur Auswahl“ sammelt bis zu 12, „GLB mit … Animationen herunterladen“ hängt sie wie die Proben an (je höchstens 33 s). Jede Animation trägt in `extras` Quelle, Aufnahme und Lizenz.
     - Übertragen wird auf der Seite (`frontend/src/rig/retarget.ts`): Die Knochen der Aufnahme werden auf Mixamo-Namen abgebildet, jede Drehung wird relativ zur Ruhehaltung in den Achsen der Szene übertragen und die Hüftbahn mit dem Verhältnis der Beinlängen skaliert. Bei Armen und Beinen wird vorher die Ruhehaltung angeglichen (Richtung zum Kind in beiden Skeletten; so passt eine T-Pose-Aufnahme auch auf eine Figur in A-Pose). Rumpf, Hals, Kopf und Füße behalten dagegen ihre Grundhaltung und bekommen nur die Änderung; Hals und Kopf folgen der Linie vom Hals- zum Kopfgelenk der Aufnahme. Denn wo SkinTokens diese Gelenke setzt, sagt nichts über die Haltung, und die Kopfdrehung der CMU-Aufnahmen ist gegen ihre Nullpose um gut 20 Grad gekippt; früher lehnten Figuren deshalb den Kopf zurück und standen auf den Hacken. Schaut die Figur in eine andere Richtung als die Aufnahme, wird die Aufnahme um die Hochachse gedreht.
     - **CMU Graphics Lab Motion Capture Database** (69 ausgewählte Aufnahmen, BVH-Fassung von B. Hahne): frei nutzbar, auch in verkauften Spielen; nur die Daten selbst darf man nicht verkaufen.
     - **Bandai Namco Research Motiondataset 1** (alle 175 Aufnahmen: Gehen, Laufen, Gesten in bis zu 15 Stilen wie müde, fröhlich, alt, dazu Schlag, Tritt, Tanz): **CC BY-NC 4.0, nur nichtkommerziell** und mit Nennung. Die Liste markiert sie mit „NC“, die Auswahl warnt.
     - Noch nicht dabei: Füße, die bei großen Unterschieden im Körperbau leicht rutschen, festhalten; Hände, die bei kräftigen Figuren in den Körper gehen, nach außen schieben; Schleifen ohne Sprung am Ende.

6. **Für den 3D-Druck (optional):** Unter jedem fertigen Modell macht „Für den 3D-Druck“ eine Datei für den Slicer: 3MF (Bambu Studio, PrusaSlicer, OrcaSlicer, Cura) oder STL, in Millimetern, mit wählbarer Höhe (20 bis 400 mm) und auf Wunsch mit rundem Sockel. Gerechnet wird im Container auf der CPU (`omat3d/printout.py`), ohne Modell auf oi-o-mat:
   - Bei einem Modell mit Skelett zählt die Haltung, die der Viewer gerade zeigt. Die Seite schickt die Drehung jedes Knochens; der Server bewegt die Punkte mit den Gewichten aus SkinTokens mit (Linear Blend Skinning, wie three.js) und stellt den tiefsten Punkt auf den Boden.
   - Sind alle Teile geschlossen, verschmilzt [manifold3d](https://github.com/elalish/manifold) sie zu einem Körper. Sonst wird die Figur über ein Raster (Höhe / 550, also etwa 0,2 mm bei 120 mm und 0,36 mm bei 200 mm) neu umhüllt: Das Raster entscheidet nur, was innen und außen ist; die neue Oberfläche liegt im genauen Abstand von gut einer halben Zelle zur alten, damit sie glatt bleibt und keine Treppen zeigt. Löcher bis gut 1 mm schließen sich, hauchdünne Flächen werden etwa eine Zelle dick. Mit Beispielfiguren dauerte das 15 bis 25 Sekunden und brauchte gut 1 GB; es rechnet immer nur eine Datei auf einmal.
   - Gedruckt wird nur die Form. Was TRELLIS.2 nur in die Textur gemalt hat (Augen, Muster, oft auch eine Brille), fehlt. Finger und Riemen unter etwa 0,8 mm brechen beim FDM-Druck leicht; für Figuren um 10 bis 15 cm ist Resin besser.

   **Skelett anpassen:** Sitzt ein Gelenk falsch (ein Knie zu tief, die Schultern zu eng), lässt es sich unter „Skelett anpassen“ verschieben: „Gelenke verschieben“ zeigt einen Marker je Knochen, ein Klick wählt ihn (oder die Liste), ein Griff zieht ihn im Raum; „Auf die andere Seite spiegeln“ überträgt die Lage auf das Gegenstück, „Verwerfen“ nimmt alles zurück. „Skelett speichern“ schickt die neuen Lagen an `POST /api/jobs/<id>/skeleton`; der Server setzt sie als `translation` der Knoten und rechnet die Bindematrizen des Skins neu (`omat3d/skeleton.py`), sodass das Netz in der Grundhaltung genau so bleibt und sich erst beim Bewegen an der neuen Stelle beugt. Die Gewichte bleiben die von SkinTokens; für kleine Korrekturen reicht das, bei großen Verschiebungen wandern Punkte mit dem falschen Knochen mit. Das Original liegt als `skeleton-original.glb` neben dem Modell, `skeletonEdited` im Auftrag zählt die Verschiebungen, und „Original-Skelett wiederherstellen“ (`POST …/skeleton/reset`) holt es zurück.

   **Haltung:** Im Viewer mit Skelett gibt es unter „Haltung“ fertige Haltungen (Arme runter, Lässig stehen, Heldenpose, Schritt, Winken, Arm hoch). Oder man beschreibt eine, etwa „steht lässig, rechte Hand winkt“, und Gemma setzt Winkel für 16 Mixamo-Knochen (`/api/pose`); einen Oberarm gibt Gemma als Richtung an (wie hoch, wohin). Hat das Skelett Fingerknochen, gibt es dazu „Faust links“ und „Faust rechts“ als Schieber, und Gemma kann sie ebenfalls schließen, etwa bei „rechte Faust zum Himmel“. Die Heldenpose ballt beide Fäuste. Danach lässt sich jeder Knochen mit den Schiebern nachstellen; die Schieber drehen von der gewählten Haltung aus und merken sich ihre Werte je Knochen. Die Füße bleiben auf dem Gitter. Starke Winkel (Ellbogen, Knie über etwa 100°) knicken das Netz sichtbar, ruhige Haltungen mit Armen nah am Körper drucken sich am besten.

## FBX für Unity und Unreal

Unter jedem fertigen Modell stehen neben „GLB herunterladen“ die Knöpfe „FBX für Unity“ und „FBX für Unreal“. Bei den Proben und der Auswahl aus der Bibliothek wählt ein Feld vor dem Knopf das Format: GLB (Godot, Blender) oder FBX für Unity bzw. Unreal. Die Knöpfe erscheinen nur, wenn der Container Blender hat (`/api/status` meldet `fbx`).

- **Wie:** Erst entsteht dasselbe GLB wie beim GLB-Knopf (mit den angehängten Animationen), dann wandelt Blender es um (`omat3d/fbx.py` startet `blender -b … --python omat3d/blender_fbx.py`). Blender läuft als eigener Prozess und teilt sich das Schloss mit der Druckdatei, damit nie beides auf einmal rechnet. Eine Figur mit 490.000 Punkten braucht 5 bis 10 Sekunden und knapp 1 GB Speicher.
- **Was ankommt:** Netz, UV, die Grundfarbe-Textur (eingebettet), alle Knochen mit ihren Mixamo-Namen (ohne zusätzliche Endknochen, damit Unitys Humanoid-Avatar sie zuordnet), die Gewichte und jede Animation als eigener Take mit 30 Bildern je Sekunde. In Unity heißen die Clips `Armature|Gehen` usw., in Unreal wird jeder eine Animation Sequence. FBX kennt kein PBR: Metallic und Roughness gehen verloren, im Spiel setzt man ohnehin ein eigenes Material.
- **Maßstab und Achsen:** Y oben, Gesicht nach −Z. „Unity“ schreibt Meter, die Figur kommt mit Maßstab 1 an; „Unreal“ schreibt Zentimeter, und das Armature-Objekt heißt `Armature`, damit Unreal keinen zusätzlichen Wurzelknochen anlegt. Root Motion: Die Hüfte bewegt sich wie im GLB; Unity rechnet beim Humanoid Root Motion daraus. Den Schalter „Root Motion (Godot)“ braucht es für FBX nicht.
- **Schnittstelle:** `GET /api/jobs/<id>/model.fbx?target=unity|unreal` für jedes fertige Modell (mit oder ohne Skelett), `POST /api/jobs/<id>/animated.fbx?target=…` mit denselben `clips` wie `animated.glb`. Der Header `x-fbx-info` nennt Knochen, Takes mit Bildern und die Dauer. Ohne Blender antworten beide mit 503, scheitert Blender, mit 502 und Blenders Meldung.
- **Warum Blender:** Geprüft wurden auch assimp (Animationen tausendfach zu lang, Netz in Punkte, Linien und Dreiecke zerlegt), das Autodesk FBX SDK (eigene Lizenz, darf nicht einfach ins Image), JavaScript (three.js liest FBX, schreibt es aber nicht) und ein eigener Schreiber (viel Arbeit, prüfen ließe er sich nur in Unity und Unreal selbst). Blenders FBX-Exporter ist der, mit dem die meisten Figuren aus freier Software in Unity und Unreal landen.
- **Blender:** im Image die offizielle LTS-Fassung unter `/opt/blender` (`BLENDER` im Dockerfile), sonst über `blender` in der `config.json`.

## Eigene Figur importieren

Eine fertige Figur von außen (aus Blender, Mixamo, einem Asset-Shop oder ein GLB von img-o-mat) kommt über „Eigene Figur importieren“ (`POST /api/jobs/import`, Feld `files`, mehrfach) herein. Sie wird sofort ein fertiger Auftrag mit der Marke „importiert“, ohne oi-o-mat, und kann danach alles, was ein Modell aus einem Bild kann: ansehen, herunterladen, drucken, Skelett einsetzen (`omat3d/importer.py`).

- **Formate:** GLB, glTF (mit `.bin` und Texturen daneben oder als data-URI) und OBJ (mit `.mtl` und Texturen). Die Dateien gehen auf einmal hoch oder als ZIP; Ordner im ZIP und andere Schreibung der Texturnamen stören nicht. Zusammen höchstens 200 MB und 3 Millionen Dreiecke. FBX geht noch nicht, bitte vorher in Blender als GLB exportieren.
- **Was daraus wird:** immer ein GLB mit einem Binärteil. Ein GLB bleibt Byte für Byte, wie es ist; ein glTF wird mit seinen Dateien zusammengepackt. Ein OBJ bekommt je Material eine Primitive, Vielecke werden Dreiecke, fehlende Normalen werden aus den Flächen gerechnet, Farbe (`Kd`), Deckkraft (`d`) und Textur (`map_Kd`) kommen aus der `.mtl`. Texturen, die glTF nicht kennt (TGA, BMP, TIFF), werden PNG. Fehlt eine Textur, kommt die Figur ohne sie und die Seite sagt, welche fehlt.
- **Skelett:** Hat die Figur ein Skelett mit Mixamo-Namen (mit oder ohne `mixamorig:`, mindestens Hüfte, Wirbelsäule, Kopf, Arme, Hände, Beine und Füße), öffnet sie gleich im Viewer mit Skelett: Bibliothek, Haltungen, Gelenk-Editor und Druck in Haltung gehen sofort. Ein Skelett mit anderen Namen (Rigify, Unreal) bleibt im GLB, die Seite bietet „Skelett einsetzen“ an; SkinTokens bekommt dann nur das Netz, ohne das alte Skelett.
- **Nicht angenommen:** Draco- oder Meshopt-komprimierte Netze, weil Druck und Gelenk-Editor die Punkte direkt lesen. Bitte ohne Kompression exportieren.

Ist die T7 nicht da (keine Datei `.3d-o-mat` im Ordner) oder oi-o-mat nicht erreichbar, nimmt die Website nichts an und zeigt „T7 nicht erreichbar“. So landet nie etwas auf der internen Platte von oi-o-mat.

## Schnittstelle für andere Programme (`/v1`)

Andere Programme lassen 3d-o-mat aus einem Bild ein GLB machen, erster Nutzer ist Kaderwerk (ein texturierter Kopf je Spieler). Die Schnittstelle ist wie die von img-o-mat gebaut: Pfade unter `/v1`, JSON rein und raus, `Authorization: Bearer <App>` nennt nur den Absender (keine Anmeldung), Fehler kommen als `{"error": {"message": "…"}}` mit passendem HTTP-Code. Sie gehört nur ins Heimnetz und ins Tailnet.

Kaderwerk erreicht sie unter derselben Adresse wie die Seite: **`https://3d.idsrv.info`** (nur intern, über das Tailnet), im Heimnetz auch direkt `http://192.168.2.75:8090`. Gerechnet wird wie bei der Seite über die Schranke auf oi-o-mat mit TRELLIS.2 (`trellisModel`, Vorgabe `ddalcu/TRELLIS.2-4B-MLX-Serve-8bit`), in derselben Schlange wie die Aufträge der Seite: nie zwei zugleich. Auf der Seite stehen diese Aufträge mit dem Absender in der Liste.

| Endpunkt | Was |
| --- | --- |
| `POST /v1/files` | Bild als rohe Bytes (PNG, JPEG, WebP, bis 40 MB) → `{"sha256": "…"}`, 201 neu, 200 schon da |
| `GET /v1/files/<sha256>` | die Datei: Bilder mit ihrem Typ, GLB als `model/gltf-binary` |
| `POST /v1/models3d/generations` | Auftrag anlegen, kehrt sofort zurück: 202 `{"id": "job_…", "status": "queued", "position": 3, "seed": 1234}` |
| `GET /v1/models3d/generations/<id>` | `status` (`queued`, `running`, `done`, `failed`, nach DELETE `canceled`), `position` (0, sobald er rechnet), `progress` (0 bis 1 in Stufen: Freistellen, Form, Textur, Export), `seed`, `error`, bei `done` `result`: `{"file": "<sha256 der GLB>", "bytes", "faces", "vertices", "texture"}` |
| `DELETE /v1/models3d/generations/<id>` | nimmt einen wartenden Auftrag heraus; ein laufender rechnet zu Ende (409) |
| `GET /v1/models3d/generations` | `{"data": [...]}`, die letzten 100 Aufträge der Schnittstelle, neueste zuerst |
| `GET /v1/busy` | `{"busy": true\|false, "running": "job_…"\|null, "waiting": 2}`; busy, solange ein Auftrag rechnet oder wartet |

Felder des Auftrags (nur `image` ist Pflicht; unbekannte Felder werden abgewiesen):

| Feld | Vorgabe | Bedeutung |
| --- | --- | --- |
| `image` | | SHA-256 aus `POST /v1/files` |
| `faces` | 30000 | Ziel-Flächenzahl nach dem Vereinfachen, 2000 bis 500000 (geht als `decimation_target` an TRELLIS.2) |
| `texture` | 2048 | Kantenlänge der Texturen: 512, 1024, 2048 oder 4096 |
| `remove_background` | true | vorher freistellen wie beim Hochladen auf der Seite (rembg); mit false geht das Bild, wie es ist (hat es Transparenz, zählt die) |
| `cutout` | Einstellung `cutout` | wie freigestellt wird: `auto`, `rembg`, `flood` (einfarbiger Grund), `head` (rembg und Fläche zusammen, für Köpfe mit nacktem Hals: rembg allein schneidet Hals und Schultern ab, die Fläche allein läuft bei heller Haut durch eine dünne Spur ins Gesicht) oder `none` |
| `tolerance` | 40 | für `flood` und `head`: wie weit eine Farbe vom Grund abweichen darf (5 bis 120); bei Haut nah am Grau lieber 25 |
| `seed` | gewürfelt | 0 bis 2147483647; der benutzte steht in der Antwort |
| `height` | 0.25 | Höhe des Modells in Metern (0,05 bis 3) |
| `creator`, `label` | Absender, leer | nur zur Anzeige, `label` wird auf der Seite der Name |

Das GLB:
- glTF 2.0 binär, genau ein Mesh mit einer Primitive (POSITION, NORMAL, TEXCOORD_0, Indizes), kein Skelett, keine Animation.
- PBR mit Basisfarbe-Textur und Metallic-Roughness-Textur, beide PNG in der Kantenlänge `texture`.
- Y oben, das Gesicht nach +Z, Maße in Metern (auf `height` skaliert). Der Ursprung liegt mittig unten am Hals: in der Mitte des untersten Querschnitts, nicht der ganzen Hülle, damit eine Nase ihn nicht nach vorn schiebt.
- In `asset.extras`: `model`, `seed`, `faces`, `texture`, `image` (SHA-256 des Eingabebilds), dazu `creator`, `label`, `height`.
- Der UV-Atlas ist der von TRELLIS.2. Gegen Farbe, die an den Nähten ausläuft, bekommt jedes Texel außerhalb der Inseln die Farbe des nächsten Texels innerhalb, also mehr als 4 Pixel Rand. Den Atlas selbst packt 3d-o-mat nicht neu; TRELLIS.2 zerlegt ihn in viele kleine Inseln, eine große zusammenhängende fürs Gesicht gibt es so noch nicht (siehe „Später“).

Nach einem Neustart laufen wartende Aufträge weiter; einer, der gerade rechnete, steht als `failed` mit dem Grund „durch einen Neustart von 3d-o-mat abgebrochen“. Bilder und GLBs liegen auf der T7 unter `files/<sha256>/`. Dafür muss `store/launch.py` auf oi-o-mat auf dem Stand sein: nach dem Update einmal `./install-store.sh --key "…"` im Checkout auf oi-o-mat. Ein alter Stand antwortet mit 502 und genau diesem Hinweis.

Der ganze Ablauf mit `curl`:

```sh
URL=https://3d.idsrv.info
AUTH="Authorization: Bearer kaderwerk"

# 1. Bild hochladen
SHA=$(curl -sS -X POST "$URL/v1/files" -H "$AUTH" -H 'content-type: application/octet-stream' \
  --data-binary @kopf.png | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha256"])')

# 2. Auftrag anlegen
JOB=$(curl -sS -X POST "$URL/v1/models3d/generations" -H "$AUTH" -H 'content-type: application/json' \
  -d "{\"image\": \"$SHA\", \"faces\": 30000, \"texture\": 2048, \"seed\": 1234, \"creator\": \"kaderwerk\", \"label\": \"HOCHOFEN 9 / KLEIN\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

# 3. Stand abfragen, bis done oder failed
while :; do
  STATE=$(curl -sS "$URL/v1/models3d/generations/$JOB" -H "$AUTH")
  echo "$STATE"
  case "$STATE" in *'"status":"done"'*|*'"status":"failed"'*|*'"status":"canceled"'*) break;; esac
  sleep 10
done

# 4. GLB holen
GLB=$(echo "$STATE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["file"])')
curl -sS -o kopf.glb "$URL/v1/files/$GLB" -H "$AUTH"
```

## Figur aufbereiten (`/v1/figures/preparations`)

Eine TRELLIS-Figur, die in Blender aus zwei Teilen zusammengesetzt wurde (Körper ohne Kopf, Kopf mit langem Hals, Z oben), wird hier für das Rigging fertig gemacht: Innenhaut weg, Öffnungen zu, Kopf als geschlossene Fläche neu aufgebaut und mit geschütztem Gesicht verkleinert, neu abgewickelt, Farbe, Metall/Rauheit und Normal Map gebacken, dann aus 14 Richtungen auf sichtbare Rückseiten geprüft. Das macht `prep/figur_aufbereiten.py` in Blender (bpy 5.3) auf oi-o-mat, im Aufbereitungs-Dienst `prep/server.py` hinter der Schranke (Server `prep`, Typ `tool`, Modell `figur-aufbereiten`); im Container wäre dafür zu wenig Speicher. Der Auftrag läuft in derselben Schlange wie alle anderen.

| Endpunkt | Was |
| --- | --- |
| `POST /v1/files` | die `.blend` als rohe Bytes (bis 400 MB) → `{"sha256": "…"}` |
| `POST /v1/figures/preparations` | Auftrag anlegen → 202 `{"id": "prep_…", "status": "queued", "position": 1}` |
| `GET /v1/figures/preparations/<id>` | `status`, `passed`, `error`, `result` (Dateien und Kennzahlen, mit dem ganzen Bericht unter `result.report`), `job` (der Auftrag auf der Seite) |
| `GET /v1/figures/preparations` | `{"data": [...]}`, die letzten 100, neueste zuerst |
| `DELETE /v1/figures/preparations/<id>` | eine wartende herausnehmen |

Felder (nur `blend` ist Pflicht): `height` (Meter, Vorgabe 1,89; Füße auf dem Boden), `body_triangles` (30000), `head_triangles` (17500), `texture` (2048, je Teil; der Atlas ist doppelt so breit), `head_voxels` (390), `skin_color` `[r, g, b]` 0 bis 1 (bei Helm oder dunkler Haut, sonst wird der Kopf gleichmäßig verkleinert und `kopf_modus` sagt das), `body` und `head` (Objektnamen, sonst gilt das höhere Objekt als Kopf), `retouch` (Quader für dunkle Flecken der Körpertextur, siehe Kopf von `prep/figur_aufbereiten.py`), `check` (false lässt Prüfung und Vorschau weg), `fbx` (true schreibt zusätzlich eine FBX, etwa für Mixamo), `creator`, `label`.

Das Ergebnis liegt unter `/v1/files/<sha256>`, die Schlüssel in `result.files`:
- `glb`: die kleine Figur als ein Mesh mit einem Material, Atlas aus Körper und Kopf, kein Skelett.
- `blend`: die Arbeitsdatei mit den Collections `1_Original`, `2_Sauber`, `3_Klein`.
- `preview`: PNG mit drei Ansichten; `report`: der JSON-Bericht des Skripts.
- `fbx`: ZIP mit `figur.fbx` und dem Ordner `figur_texturen` (FBX kennt keine kombinierte Metall/Rauheit-Textur).

So wird der Exit-Code ausgewertet:
- **0:** `done`, `passed` true (oder null mit `check: false`).
- **2:** Prüfung nicht bestanden (über 200 Pixel Rückseite sichtbar). Der Auftrag steht als `failed` da, `error` nennt die Pixel, aber `result` mit allen Dateien ist zum Ansehen da.
- **1 mit eigener Meldung:** das Skript lehnt die Eingabe ab (etwa nicht ein oder zwei Mesh-Objekte); `failed`, der Grund steht in `error`. Ein Absturz steht ebenso da, mit der letzten Zeile.

### Spielerfigur in einem Auftrag (`/v1/figures/players`)

Für ein Team mit einem Körper und Köpfen je Spieler (Iron Rush): `POST /v1/figures/players` mit `name` (Ordnername im Spiel, Kleinbuchstaben), `head` (das GLB eines Kopfes mit Hals aus `/v1/models3d/generations`, am besten mit `"cutout": "head"`), `body` (die Eingabe-.blend des Teams mit `Koerper_ohne_Kopf` und `Kopf_langer_Hals` als Vorlage für Größe und Sitz) und `rig` (die gerigte Teamfigur als .blend: Armature mit Mixamo-Namen und Netz). Dazu dieselben Felder wie oben außer `fbx`. Der Dienst setzt den Kopf an die Stelle des alten (`prep/kopf_aufsetzen.py`: Maßstab aus Kinn bis Scheitel, Halsbreite und Schädelbreite, Schultern unterhalb des Kinns weg), bereitet auf und überträgt Skelett und Gewichte der Teamfigur (`prep/rig_uebertragen.py`: nächste Fläche, alles am Kopf, was nicht zum alten Kopf passt, hängt am Head-Knochen). Stand und Ergebnis kommen unter `/v1/figures/preparations/prep_…`, dazu `result.files.fbx` (die Figur mit Skelett, Material `Figur_<name>`) und `result.files.textures` (ZIP mit `<name>-farbe.png`, `<name>-normal.png`). Fällt die Prüfung durch (Exit-Code 2), wird nicht gerigged.

Auf der Seite steht die fertige Figur als „aufbereitet“ in der Liste; „Skelett einsetzen“ geht wie bei jedem Modell. Mit klein-apose-1 (66 MB `.blend`, 452.000 + 161.000 Dreiecke) dauerte ein Lauf auf dem Mac mini knapp 3 Minuten und ergab 46.911 Dreiecke, 11 Pixel Rückseite.

```sh
SHA=$(curl -sS -X POST "$URL/v1/files" -H "$AUTH" -H 'content-type: application/octet-stream' \
  --data-binary @figur.blend | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha256"])')
curl -sS -X POST "$URL/v1/figures/preparations" -H "$AUTH" -H 'content-type: application/json' \
  -d "{\"blend\": \"$SHA\", \"height\": 1.89, \"fbx\": true, \"label\": \"KLEIN\"}"
# dann GET /v1/figures/preparations/prep_… bis done oder failed, Dateien aus result.files holen
```

## Für Agents: MCP (`/mcp`)

3d-o-mat ist auch ein MCP-Server, damit Agents wie Claude Code damit arbeiten können: ein Bild hochladen oder malen lassen, prüfen, in 3D umwandeln, auf das Ergebnis warten, ein Skelett einsetzen und das Modell als GLB, 3MF, STL oder FBX (für Unity und Unreal) holen. Er läuft im selben Container wie die Seite, unter derselben Adresse, und braucht auf dem Rechner des Agents nichts außer dem Eintrag. Wie `/v1` gehört er nur ins Heimnetz und ins Tailnet; angemeldet wird nicht.

**In Claude Code einbinden** (auf einem Rechner im Heimnetz oder im Tailnet):

```sh
claude mcp add --transport http --scope user 3d-o-mat https://3d.idsrv.info/mcp
claude mcp list   # 3d-o-mat: https://3d.idsrv.info/mcp (HTTP) - ✓ Connected
```

`--scope user` macht ihn in allen Projekten verfügbar; ohne gilt er nur im aktuellen Ordner. Für ein einzelnes Projekt, das ihn mit allen teilt, die es auschecken, geht auch eine `.mcp.json` im Repo:

```json
{ "mcpServers": { "3d-o-mat": { "type": "http", "url": "https://3d.idsrv.info/mcp" } } }
```

Im Heimnetz geht auch `http://192.168.2.75:8090/mcp`. In Claude Code zeigt `/mcp` die Werkzeuge. Danach reicht etwa: „Mach aus `~/Bilder/roboter.png` ein 3D-Modell mit Skelett und leg das GLB in `assets/`.“ Claude Desktop und andere Clients, die nur stdio können, binden ihn über die Brücke [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) ein (`"command": "npx", "args": ["mcp-remote", "https://3d.idsrv.info/mcp"]`). Cloud-Sitzungen (Claude Code im Web) erreichen 3d-o-mat nicht, weil es nur intern erreichbar ist.

| Werkzeug | Was |
| --- | --- |
| `get_status` | Ablage, Schranke, freigegebene Modelle, laufende und wartende Aufträge, ob FBX geht |
| `list_images`, `show_image` | die Bilder; `show_image` gibt das freigestellte Bild (oder das Original, höchstens 512 px) zum Ansehen zurück |
| `upload_image` | ein Bild als Base64 hochladen und freistellen |
| `check_image` | Gemma prüft, ob das Bild für 3D taugt |
| `write_image_prompts`, `paint_images`, `get_painting` | Prompts von Gemma, Bilder malen mit img-o-mat, warten, bis sie da sind |
| `create_model` | Auftrag Bild zu 3D, entweder mit `image_id` (wie die Seite: `texture_size`, `faces`, `seed`, `resolution`, `steps`) oder mit `image_sha256` aus `POST /v1/files` (wie Kaderwerk: ein Mesh, Ursprung unten mittig, `height` in Metern) |
| `get_job` | Stand, Fortschritt und, wenn fertig, die Download-Adressen; `wait_seconds` (bis 240) wartet |
| `list_jobs`, `cancel_job` | Aufträge, neueste zuerst; einen wartenden herausnehmen |
| `add_skeleton` | SkinTokens setzt ein Skelett ein (`humanoid` mit Mixamo-Namen, `quadruped`, `custom`), als neuer Auftrag |
| `export_model` | Download-Adresse für `glb`, `3mf` oder `stl` (für den Slicer, in Ruhehaltung, `height_mm`, `base`) und `fbx` (`target` `unity` oder `unreal`) |

Wie es gebaut ist (`omat3d/mcp.py`):
- Transport ist Streamable HTTP ohne Sitzung: jede Nachricht ein `POST /mcp`, die Antwort JSON (Protokoll 2024-11-05 bis 2025-11-25). `GET /mcp` gibt 405, denn der Server schickt nichts von sich aus. Eine Anfrage mit fremdem `Origin` (ein Browser auf einer anderen Seite) wird mit 403 abgewiesen.
- Die Werkzeuge rufen `/api` und `/v1` im selben Prozess auf. Es gelten also dieselben Prüfungen, Fehlermeldungen (teils noch deutsch) und dieselbe Schlange wie für die Seite; was ein Agent anlegt, steht dort in der Liste, Aufträge über `image_sha256` mit dem Absender `mcp`.
- Dateien gehen nicht durch das Gespräch: Ergebnisse kommen als Adresse mit dem passenden `curl -o`-Befehl. Eine Druckdatei, die nur ein POST liefert, liegt dafür eine Stunde (höchstens die letzten acht) im Speicher unter `/mcp/downloads/<token>/<name>`. Ein Bild von der Platte lädt der Agent am besten selbst hoch, statt es als Base64 zu schicken: `curl -F file=@bild.png https://3d.idsrv.info/api/images` (die `id` der Antwort ist die `image_id`) oder roh an `/v1/files` (gibt den `sha256`).
- Die Adressen in den Antworten bauen sich aus `Host` und `X-Forwarded-Proto`/`X-Forwarded-Host`; der Proxy vor 3d.idsrv.info muss sie weitergeben, damit `https://` darin steht.
- FBX bietet `export_model` an, sobald 3d-o-mat die Route `GET /api/jobs/<id>/model.fbx` hat und `/api/status` kein `"fbx": false` meldet (Blender fehlt).
- Getestet wird mit `tests/test_mcp.py`: MCP mit `/mcp` (initialize, Werkzeuge, Fehler, fremde Origin) und der Weg eines Agents mit nachgestellter Schranke: hochladen, ansehen, prüfen, malen, in 3D, warten, Skelett, GLB und 3MF holen, FBX nur mit Route und Blender.
- Noch nicht über MCP: Animationen aus der Bibliothek und die Proben (sie werden auf der Seite übertragen, `animated.glb` bekommt fertige Keyframes), Haltungen, der Gelenk-Editor, eigene Figuren importieren und Löschen.

## Einrichten

### 1. Proxmox: Container und Website

Das Skript `deploy/proxmox/3d-o-mat-lxc.sh` läuft in der Shell des Proxmox-Hosts als root, Schritt für Schritt:

```sh
curl -fsSLo /root/3d-o-mat-lxc.sh https://raw.githubusercontent.com/Marcel-B/3d-o-mat/main/deploy/proxmox/3d-o-mat-lxc.sh
bash /root/3d-o-mat-lxc.sh anlegen       # 1. unprivilegierter Debian-Container mit Docker
bash /root/3d-o-mat-lxc.sh app <CTID>    # 2. Repo holen, config.json und SSH-Schlüssel anlegen, bauen, starten
```

Am Ende von `app` steht der Befehl für oi-o-mat mit dem öffentlichen Schlüssel der Website. Später gibt es diese Befehle:
- `update <CTID>` holt den neuen Stand und das fertige Image dazu. Gebaut wird nicht im Container, sondern bei jedem Push auf `main` von GitHub Actions (`.github/workflows/image.yml`, erst die Tests, dann das Image nach `ghcr.io/marcel-b/3d-o-mat:sha-<commit>` und `:main`); `deploy/proxmox/start-image.sh` holt es und schreibt in `.env`, welches läuft. Das Update wartet bis 20 Minuten auf das Image. `BAUEN=1 bash /root/3d-o-mat-lxc.sh update <CTID>` baut wie früher im Container (braucht gut 10 GB Platz).
- `pruefen <CTID>` zeigt den Zustand von Ablage, Schranke und Freigaben.
- `autoupdate <CTID> [on|off|status|run]` schaltet das Auto-Update im Container (`app` schaltet es ein): ein systemd-Timer holt alle 5 Minuten `main` und startet das Image dazu, aber nur, wenn kein Auftrag wartet oder rechnet. Ist das Image noch nicht gebaut, versucht es der nächste Lauf wieder; ein Stand, dessen Start fehlschlug, erst mit dem nächsten Commit. Log: `pct exec <CTID> -- journalctl -u 3d-o-mat-update`.
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
- Für FBX liegt Blender (GPL-2.0 oder neuer) im Image. 3d-o-mat startet es als eigenes Programm und gibt ihm nur Dateien, importiert also nichts davon und bleibt MIT; `omat3d/blender_fbx.py` läuft in Blender selbst. Wer das Image weitergibt, gibt Blender mit seiner Lizenz weiter.
- Die Bewegungsbibliothek bringt keine Daten ins Repo; sie wird beim Bau geholt. CMU: frei, auch kommerziell, nicht weiterverkaufen (mocap.cs.cmu.edu, gefördert von NSF EIA-0196217). Bandai Namco Research Motiondataset: CC BY-NC 4.0.

Gemessen auf oi-o-mat (M6, 32 GB) mit einer TRELLIS-Figur (226.000 Punkte): 34,5 s, höchstens 8,3 GB.

### 4b. oi-o-mat: Aufbereitungs-Dienst (Figur aufbereiten)

Python 3.13 muss da sein (`brew install python@3.13`), weil es bpy 5.3 nur dafür gibt. Dann im Checkout:

```sh
./install-prep.sh
```

Das Skript legt unter `/Volumes/T7/figur-aufbereiten/venv` ein Python mit bpy 5.3, numpy, scipy und scikit-image an (etwa 1 GB), kopiert die Skripte aus `prep/` nach `~/Library/Application Support/3d-o-mat`, schreibt `prep.json` und trägt den Dienst bei der Schranke als Server `prep` vom Typ `tool` ein (Port 11241, `MEMORY=10` GB angemeldet). Danach in `/gate` bei 3d-o-mat das Modell `figur-aufbereiten` freigeben. Log: `~/Library/Logs/3d-o-mat-prep.log`.

### 5. Freigeben

Beim ersten Start meldet sich die App bei der Schranke an. In `http://192.168.2.122:1235/gate` dann bei **3d-o-mat** TRELLIS.2, SkinTokens und die Bildmodelle (`klein-4b`, `z-image-turbo`, `krea-2-turbo`) freigeben, Gemma ebenfalls freigeben oder installieren. Danach ist die Website unter `http://<container>:8090` erreichbar. Beim Start lädt die App das rembg-Modell (etwa 170 MB) einmal in das Volume `rembg-cache`.

### Statt Proxmox: auf einem Mac

Die ganze Anleitung steht in [docs/mac.md](docs/mac.md): Werkzeuge (Python 3.12, Node 22), Bauen, Ablage, Einstellung, Freigaben und was tun, wenn es hakt. Kurz:

```sh
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-rembg.txt
(cd frontend && npm ci && npm run build)
.venv/bin/python -m omat3d.motions fetch motions-data   # Bewegungsbibliothek, etwa 50 MB, einmal
cp config.example.json config.json
mkdir -p ~/3d-o-mat-daten && touch ~/3d-o-mat-daten/.3d-o-mat   # dann store.folder auf diesen Ordner setzen
.venv/bin/python -m omat3d
```

Im Container holt der Bau des Images die Bewegungsbibliothek (`/app/motions-data`); sie ist kein Nutzerinhalt und liegt deshalb nicht auf der T7. Neu geholt wird sie nur, wenn sich `omat3d/motions.py` ändert.

`config.example.json` nimmt `"store": {"folder": "/Volumes/T7/3d-o-mat"}`. Ohne `ssh` ruft die Website `store/launch.py` selbst auf, mit demselben Kennzeichen `.3d-o-mat`; macOS' eigenes Python 3.9 reicht für die App nicht.

## Einstellungen (`config.json`)

| Feld | Bedeutung |
| --- | --- |
| `store` | `{"ssh": "marcel@192.168.2.122", "key": "/ssh/id_ed25519", "knownHosts": "/ssh/known_hosts"}` für die T7 von oi-o-mat, oder `{"folder": "…"}` für einen eigenen Ordner |
| `gate` | Anmeldung bei der Schranke: `url`, `app`, `note`, `priority`, `holdFor`, `models` (siehe docs/llm-gate.md im scripts-Repo) |
| `trellisModel`, `rigModel`, `assistModel`, `prepModel` | welche der angemeldeten Modelle für 3D, fürs Skelett, für Gemma und fürs Aufbereiten einer Figur (`figur-aufbereiten`) |
| `paintModels` | die Bildmodelle von img-o-mat zur Auswahl, das erste ist die Vorgabe (`klein-4b`, `z-image-turbo`, `krea-2-turbo`). Fehlt eines dieser Modelle in `gate.models`, meldet die App es trotzdem an; so reicht bei einer älteren `config.json` ein Update |
| `defaults` | Vorgaben für `/v1/3d/generations`: `texture_size`, `decimation_target`, auch `resolution`, `steps` |
| `cutout` | `auto`, `rembg`, `flood` oder `none` |
| `onkelBummel` | Nachricht an [Onkel Bummel](https://github.com/Marcel-B/onkel-bummel), wenn ein 3D-Modell oder Skelett fertig ist oder fehlschlägt, mit dem freigestellten Bild: `{"url": "https://onkel-bummel.<tailnet>.ts.net", "token": "ob_…", "topic": "3d-o-mat", "click": "http://3d-o-mat:8090/"}`. Den Token (Publish-Token für das Topic) legst du in Onkel Bummel unter Verwaltung → Publish-Tokens an; statt `token` geht `tokenFile`. Ohne `url` kommt keine Nachricht. Gemalte Bilder meldet img-o-mat selbst |
| `motionsDir` | wo die Bewegungsbibliothek liegt; Vorgabe `motions-data` neben `omat3d`, im Image `/app/motions-data` |
| `blender` | Blender für den FBX-Export: ein Name im `PATH` oder ein Pfad. Vorgabe `blender`, im Image `/usr/local/bin/blender`; auf einem Mac etwa `/Applications/Blender.app/Contents/MacOS/Blender`. Leer schaltet FBX ab |
| `rembgModel` | Vorgabe `isnet-general-use`: auf einem 12-MP-Foto 9 s und 1,5 GB RAM. `birefnet-general` stellt etwas feiner frei, braucht aber 115 s und 9,5 GB, mehr als der Container hat |

## Entwickeln

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
cd frontend && npm run dev
```

`npm run dev` läuft auf Port 5173 und leitet `/api` an Port 8090 weiter. Die Tests spielen den ganzen Weg ohne oi-o-mat durch: Hochladen, Freistellen, Auftrag, Stream mit Abbruch und 503, Ablehnung, fehlende T7, Gemma, Malen samt Inhaltsfilter, Haltung von Gemma, Druckdatei, unzulässige Pfade. `tests/test_models3d.py` spielt die Schnittstelle unter `/v1` mit einem nachgestellten TRELLIS.2 durch: hochladen, Auftrag, Stand, GLB holen und prüfen (ein Mesh, Ursprung am Hals, Texturen, `extras`), Fehler bei falschem SHA-256 und unzulässigen Werten, Abbrechen und Neustart. `tests/test_importer.py` importiert OBJ mit Material und Textur, glTF mit Dateien daneben und als ZIP, GLB mit Mixamo- und fremdem Skelett, und lehnt ab, was keine Figur wird. `tests/test_motions.py` prüft das Holen und Umrechnen der Bibliothek (mit nachgestellten Downloads) und ihre Schnittstelle. `tests/test_printout.py` backt die Haltung eines kleinen GLB mit zwei Knochen ein und prüft auf beiden Wegen, dass die Druckdatei wasserdicht ist. `tests/test_figures.py` spielt die Aufbereitung mit einem nachgestellten `figur_aufbereiten.py` durch, den Dienst als eigenen Prozess und hinter `/v1/figures/preparations`: Dateien, Bericht, Exit-Code 2 und 1, Absturz, unzulässige Werte, Abbrechen. `tests/test_fbx.py` prüft den Aufruf von Blender mit `tests/fake/blender` und die Wege unter `/api`; ist ein echtes Blender da (`OMAT3D_TEST_BLENDER=/pfad/zu/blender` oder `blender` im `PATH`), wandelt es eine kleine Figur mit einer Animation um, liest das FBX mit Blender wieder und prüft Knochen, Gewichte, Take, Länge und Größe. Die Schranke ist dabei ein `httpx.MockTransport`. Die Ablage läuft einmal direkt und einmal über `tests/fake/ssh`, das wie der erzwungene Befehl von sshd arbeitet.

## Später

- Import: FBX (über das Blender, das für den FBX-Export schon im Image liegt) und eine Zuordnung für Skelette mit anderen Namen (Rigify, Unreal), damit auch die gleich in die Bibliothek können.

- Schnittstelle: den UV-Atlas neu packen (etwa mit xatlas) und die Texturen darauf umbacken, damit das Gesicht eine große zusammenhängende Insel bekommt.
- MCP: Animationen aus der Bibliothek, dafür müsste das Übertragen (`retarget.ts`) auch auf dem Server laufen.
- Pixal3D für mehrere Ansichten desselben Objekts (MLX-Serve kann es, `images` statt `image`).

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

// Die Oberfläche auf Deutsch und Englisch. Die Wahl steht in localStorage.lang, sonst entscheidet der Browser.
// Meldungen der Python-App (Fehler, Stand eines Auftrags) kommen weiter auf Deutsch.

export type Lang = 'de' | 'en';

const de = {
  locale: 'de-DE',
  tagline: 'Aus einem Bild ein 3D-Modell. Gerechnet von TRELLIS.2 auf oi-o-mat.',
  settings: { language: 'Sprache', theme: 'Farbschema', system: 'System', light: 'Hell', dark: 'Dunkel' },

  status: {
    noAnswer: 'App antwortet nicht',
    t7Free: (gb: number) => `T7 · ${gb} GB frei`,
    t7Missing: 'T7 nicht erreichbar',
    gateUp: 'Schranke erreichbar',
    gateDown: 'Schranke antwortet nicht',
    tonwerkUnknown: 'Tonwerk ?',
    tonwerkIdle: 'Tonwerk still',
    tonwerkBusy: (reasons: string) => `Tonwerk: ${reasons}`,
    reasons: {
      songs: 'Songs', transcription: 'Transkription', lyrics: 'Songtext', voices: 'Stimmen', speech: 'Sprache',
      images: 'Bilder', video: 'Video', ace: 'ACE-Step', singing: 'Singen',
    } as Record<string, string>,
    notRegistered: 'nicht angemeldet',
    trellisMissing: 'TRELLIS fehlt',
    trellisNotApproved: 'TRELLIS nicht freigegeben',
    approveAt: (url: string) => `In ${url}/gate freigeben`,
  },

  image: {
    title: 'Bild',
    cutting: 'Freistellen …',
    choose: 'Bild wählen oder hierher ziehen',
    advice: 'Ein einzelnes Objekt, ganz im Bild, am besten vor schlichtem Hintergrund oder schon freigestellt (PNG mit Transparenz).',
    methods: {
      alpha: 'hatte schon Transparenz',
      rembg: 'freigestellt mit rembg',
      flood: 'einfarbigen Hintergrund entfernt',
      none: 'unverändert',
    },
    original: 'Original',
    cutout: 'Freigestellt',
    goesTo: 'geht an TRELLIS.2',
    paintedWith: 'Gemalt mit',
    quote: (text: string) => `„${text}“`,
    seed: 'Seed',
    checking: 'Gemma prüft …',
    recheck: 'Neu prüfen',
    check: 'Mit Gemma prüfen',
    checkHint: 'Prüft, ob das Bild ein gutes Ausgangsbild ist. Kann warten, wenn Tonwerk arbeitet.',
    suitable: 'Geeignet',
    unsuitable: 'Eher nicht geeignet',
    tips: 'Tipps',
    convertTitle: 'Umwandeln',
    texture: 'Textur',
    faces: 'Flächen',
    resolution: 'Auflösung',
    preset: (value: string) => `Vorgabe (${value})`,
    modelPreset: 'Vorgabe des Modells',
    lowMemory: '512 (wenig Speicher, falls das Paket es hat)',
    random: 'zufällig',
    starting: 'Startet …',
    convert: 'In 3D umwandeln',
    convertHint: 'Dauert auf oi-o-mat etwa 5 bis 10 Minuten, Warten auf Tonwerk nicht eingerechnet.',
    deleting: 'Löscht …',
    confirmDelete: (name: string) => `${name} löschen?`,
    delete: 'Bild löschen',
  },

  paint: {
    title: 'Noch kein Bild? Malen lassen',
    placeholder: 'Idee oder Prompt, z. B. ein alter Messing-Kompass mit aufgeklapptem Deckel',
    writing: 'Gemma schreibt …',
    write: 'Prompts schreiben',
    direct: 'Direkt malen',
    model: 'Bildmodell',
    count: 'Anzahl',
    images: (n: number) => `${n} ${n === 1 ? 'Bild' : 'Bilder'}`,
    hint: 'Gemma macht aus der Idee englische Prompts für eine gute Vorlage (ein Objekt, ganz im Bild, schlichter Hintergrund). '
      + 'Gemalt wird mit img-o-mat auf oi-o-mat, etwa eine Minute je Bild.',
    notApproved: (model: string) => `${model} ist in der Schranke noch nicht freigegeben.`,
    stages: {
      queued: 'wartet', sending: 'schickt', loading: 'lädt das Modell', painting: 'malt', checking: 'prüft', cutout: 'stellt frei',
    } as Record<string, string>,
    place: (n: number) => ` (Platz ${n})`,
    paint: 'malen',
    copy: 'kopieren',
    copied: 'kopiert',
    copyFailed: 'Kopieren geht nur über https oder localhost; den Text bitte markieren.',
  },

  viewer: {
    model: 'Modell',
    rigged: 'Modell mit Skelett',
    alt: '3D-Modell',
    appears: 'Das Modell erscheint hier, sobald der Auftrag fertig ist.',
    none: 'Noch kein Modell ausgewählt.',
    downloadRigged: 'GLB mit Skelett herunterladen',
    download: 'GLB herunterladen',
    computed: (time: string) => ` · ${time} gerechnet`,
    riggedHint: ' · Knochen mit Mixamo-Namen, Animationen von Mixamo passen direkt',
    plainHint: ' · GLB mit PBR-Texturen, öffnet in Blender, Godot, Unity, Windows-3D-Viewer, Vorschau',
    toRigged: 'Zum Modell mit Skelett',
    starting: 'Startet …',
    retry: 'Nochmal mit anderen Seeds',
    rig: 'Skelett einsetzen',
    kind: 'Art der Figur',
    humanoid: 'Mensch (Mixamo-Namen)',
    quadruped: 'Vierbeiner',
    custom: 'Sonstiges',
    retryHint: 'Hat SkinTokens kein Menschen-Skelett gefunden, hilft oft ein anderer Seed; sonst „Sonstiges“ (Knochen ohne Mixamo-Namen, die Proben bewegen dann nichts).',
    rigHint: 'SkinTokens auf oi-o-mat, etwa eine halbe Minute, bei einem schwierigen Modell bis zu drei Versuche. Danach lässt sich die Figur hier bewegen.',
  },

  rig: {
    // Die Namen der Proben stehen auch als Animationen im GLB.
    clips: { walk: 'Gehen', punch: 'Schlag', kick: 'Tritt', roundhouse: 'Drehtritt', arms: 'Arme heben', squat: 'Kniebeuge', fall: 'Umfallen' },
    loadFailed: (message: string) => `Das Modell ließ sich nicht laden: ${message}`,
    exportFailed: (message: string) => `Export ging nicht: ${message}`,
    loading: 'Lädt das Modell mit Skelett …',
    strength: 'Stärke',
    byHand: 'Knochen von Hand drehen',
    bone: 'Knochen',
    restPose: 'Grundhaltung',
    skeleton: 'Skelett',
    wireframe: 'Drahtgitter',
    facts: (bones: number, points: string, core: string) => `${bones} Knochen · ${points} Punkte · Mixamo-Kern ${core}`,
    packing: 'Packt …',
    download: 'GLB mit Proben herunterladen',
    downloadHint: 'Die sieben Proben als Animationen (30 Bilder je Sekunde, Stärke wie eingestellt), in Godot im AnimationPlayer.',
    missing: (names: string) => `Ohne Mixamo-Namen: ${names}. Proben bewegen diese Teile nicht.`,
    pose: 'Haltung',
    poses: { down: 'Arme runter', relaxed: 'Lässig stehen', hero: 'Heldenpose', stride: 'Schritt', wave: 'Winken', cheer: 'Arm hoch' },
    describe: 'Haltung beschreiben, z. B. „steht lässig, rechte Hand winkt“',
    ask: 'Gemma stellt die Figur',
    asking: 'Gemma überlegt …',
    poseFailed: (message: string) => `Keine Haltung: ${message}`,
    poseHint: 'Danach mit „Knochen von Hand drehen“ nachstellen. Die Füße bleiben auf dem Boden.',
  },

  print: {
    title: 'Für den 3D-Druck',
    height: 'Höhe (mm)',
    base: 'Sockel',
    download: 'Druckdatei herunterladen',
    computing: 'Rechnet …',
    failed: (message: string) => `Druckdatei ging nicht: ${message}`,
    result: (size: string, volume: string, closed: boolean) =>
      `${size} mm, ${volume} cm³ · ${closed ? 'Teile verschmolzen' : 'über ein Raster geschlossen'}, wasserdicht`,
    hintPosed: 'Die Figur in der Haltung, die du gerade siehst, wasserdicht und auf Wunsch mit Sockel. 3MF für Bambu Studio, PrusaSlicer, OrcaSlicer, Cura. Nur Form, ohne Farben; Details aus der Textur fehlen.',
    hint: 'Das Modell wasserdicht und auf Wunsch mit Sockel. 3MF für Bambu Studio, PrusaSlicer, OrcaSlicer, Cura. Nur Form, ohne Farben; Details aus der Textur fehlen.',
  },

  jobs: {
    title: 'Aufträge',
    empty: 'Noch keine. Ein Bild wählen und „In 3D umwandeln“.',
    states: {
      queued: 'in der Schlange', waiting: 'wartet auf oi-o-mat', running: 'rechnet', done: 'fertig', failed: 'fehlgeschlagen',
      canceled: 'abgebrochen',
    },
    stages: { coarse: 'Grobform', shape: 'Form', texture: 'Textur', finish: 'Abschluss', skeleton: 'Skelett und Gewichte' },
    since: (time: string) => `seit ${time}`,
    skeleton: 'Skelett',
    cancel: 'abbrechen',
    delete: 'löschen',
    confirmDelete: 'Auftrag samt Modell löschen?',
  },
};

export type Texts = typeof de;

const en: Texts = {
  locale: 'en-GB',
  tagline: 'A 3D model from a picture. Computed by TRELLIS.2 on oi-o-mat.',
  settings: { language: 'Language', theme: 'Colour scheme', system: 'System', light: 'Light', dark: 'Dark' },

  status: {
    noAnswer: 'App not responding',
    t7Free: (gb: number) => `T7 · ${gb} GB free`,
    t7Missing: 'T7 not reachable',
    gateUp: 'Gate reachable',
    gateDown: 'Gate not responding',
    tonwerkUnknown: 'Tonwerk ?',
    tonwerkIdle: 'Tonwerk idle',
    tonwerkBusy: (reasons: string) => `Tonwerk: ${reasons}`,
    reasons: {
      songs: 'songs', transcription: 'transcription', lyrics: 'lyrics', voices: 'voices', speech: 'speech',
      images: 'images', video: 'video', ace: 'ACE-Step', singing: 'singing',
    },
    notRegistered: 'not registered',
    trellisMissing: 'TRELLIS missing',
    trellisNotApproved: 'TRELLIS not approved',
    approveAt: (url: string) => `Approve in ${url}/gate`,
  },

  image: {
    title: 'Picture',
    cutting: 'Cutting out …',
    choose: 'Choose a picture or drop it here',
    advice: 'A single object, fully in frame, ideally on a plain background or already cut out (PNG with transparency).',
    methods: {
      alpha: 'already had transparency',
      rembg: 'cut out with rembg',
      flood: 'plain background removed',
      none: 'unchanged',
    },
    original: 'Original',
    cutout: 'Cut out',
    goesTo: 'goes to TRELLIS.2',
    paintedWith: 'Painted with',
    quote: (text: string) => `“${text}”`,
    seed: 'seed',
    checking: 'Gemma is checking …',
    recheck: 'Check again',
    check: 'Check with Gemma',
    checkHint: 'Checks whether the picture is a good starting point. May wait while Tonwerk is working.',
    suitable: 'Suitable',
    unsuitable: 'Probably not suitable',
    tips: 'Tips',
    convertTitle: 'Convert',
    texture: 'Texture',
    faces: 'Faces',
    resolution: 'Resolution',
    preset: (value: string) => `Default (${value})`,
    modelPreset: 'Model default',
    lowMemory: '512 (less memory, if the package has it)',
    random: 'random',
    starting: 'Starting …',
    convert: 'Convert to 3D',
    convertHint: 'Takes about 5 to 10 minutes on oi-o-mat, not counting time spent waiting for Tonwerk.',
    deleting: 'Deleting …',
    confirmDelete: (name: string) => `Delete ${name}?`,
    delete: 'Delete picture',
  },

  paint: {
    title: 'No picture yet? Have one painted',
    placeholder: 'Idea or prompt, e.g. an old brass compass with its lid open',
    writing: 'Gemma is writing …',
    write: 'Write prompts',
    direct: 'Paint directly',
    model: 'Image model',
    count: 'Number',
    images: (n: number) => `${n} ${n === 1 ? 'picture' : 'pictures'}`,
    hint: 'Gemma turns the idea into English prompts for a good template (one object, fully in frame, plain background). '
      + 'img-o-mat paints on oi-o-mat, about a minute per picture.',
    notApproved: (model: string) => `${model} is not yet approved in the gate.`,
    stages: {
      queued: 'waiting', sending: 'sending', loading: 'loading the model', painting: 'painting', checking: 'checking',
      cutout: 'cutting out',
    },
    place: (n: number) => ` (place ${n})`,
    paint: 'paint',
    copy: 'copy',
    copied: 'copied',
    copyFailed: 'Copying only works over https or on localhost; please select the text instead.',
  },

  viewer: {
    model: 'Model',
    rigged: 'Model with skeleton',
    alt: '3D model',
    appears: 'The model appears here as soon as the job is done.',
    none: 'No model selected yet.',
    downloadRigged: 'Download GLB with skeleton',
    download: 'Download GLB',
    computed: (time: string) => ` · computed in ${time}`,
    riggedHint: ' · bones with Mixamo names, Mixamo animations fit directly',
    plainHint: ' · GLB with PBR textures, opens in Blender, Godot, Unity, Windows 3D Viewer, Preview',
    toRigged: 'Go to the model with skeleton',
    starting: 'Starting …',
    retry: 'Try again with other seeds',
    rig: 'Add a skeleton',
    kind: 'Kind of figure',
    humanoid: 'Human (Mixamo names)',
    quadruped: 'Four-legged',
    custom: 'Other',
    retryHint: 'If SkinTokens found no human skeleton, another seed often helps; otherwise choose “Other” (bones without Mixamo names, so the test moves do nothing).',
    rigHint: 'SkinTokens on oi-o-mat, about half a minute, up to three attempts for a difficult model. Afterwards you can move the figure here.',
  },

  rig: {
    clips: { walk: 'Walk', punch: 'Punch', kick: 'Kick', roundhouse: 'Roundhouse kick', arms: 'Raise arms', squat: 'Squat', fall: 'Fall over' },
    loadFailed: (message: string) => `The model could not be loaded: ${message}`,
    exportFailed: (message: string) => `Export failed: ${message}`,
    loading: 'Loading the model with skeleton …',
    strength: 'Strength',
    byHand: 'Turn bones by hand',
    bone: 'Bone',
    restPose: 'Rest pose',
    skeleton: 'Skeleton',
    wireframe: 'Wireframe',
    facts: (bones: number, points: string, core: string) => `${bones} bones · ${points} vertices · Mixamo core ${core}`,
    packing: 'Packing …',
    download: 'Download GLB with test moves',
    downloadHint: 'The seven test moves as animations (30 frames per second, at the strength set), in Godot in the AnimationPlayer.',
    missing: (names: string) => `Without Mixamo names: ${names}. The test moves do not move these parts.`,
    pose: 'Pose',
    poses: { down: 'Arms down', relaxed: 'Relaxed', hero: 'Hero pose', stride: 'Stride', wave: 'Wave', cheer: 'Arm up' },
    describe: 'Describe a pose, e.g. “standing relaxed, right hand waving”',
    ask: 'Let Gemma pose it',
    asking: 'Gemma is thinking …',
    poseFailed: (message: string) => `No pose: ${message}`,
    poseHint: 'Then fine-tune with “Turn bones by hand”. The feet stay on the ground.',
  },

  print: {
    title: 'For 3D printing',
    height: 'Height (mm)',
    base: 'Base',
    download: 'Download print file',
    computing: 'Computing …',
    failed: (message: string) => `Print file failed: ${message}`,
    result: (size: string, volume: string, closed: boolean) =>
      `${size} mm, ${volume} cm³ · ${closed ? 'parts merged' : 'closed over a grid'}, watertight`,
    hintPosed: 'The figure in the pose you see, watertight and with a base if you like. 3MF for Bambu Studio, PrusaSlicer, OrcaSlicer, Cura. Shape only, no colours; details from the texture are missing.',
    hint: 'The model, watertight and with a base if you like. 3MF for Bambu Studio, PrusaSlicer, OrcaSlicer, Cura. Shape only, no colours; details from the texture are missing.',
  },

  jobs: {
    title: 'Jobs',
    empty: 'None yet. Choose a picture and “Convert to 3D”.',
    states: {
      queued: 'queued', waiting: 'waiting for oi-o-mat', running: 'computing', done: 'done', failed: 'failed', canceled: 'cancelled',
    },
    stages: { coarse: 'Coarse shape', shape: 'Shape', texture: 'Texture', finish: 'Finishing', skeleton: 'Skeleton and weights' },
    since: (time: string) => `for ${time}`,
    skeleton: 'Skeleton',
    cancel: 'cancel',
    delete: 'delete',
    confirmDelete: 'Delete the job and its model?',
  },
};

const texts: Record<Lang, Texts> = { de, en };

function initial(): Lang {
  try {
    const stored = localStorage.getItem('lang');
    if (stored === 'de' || stored === 'en') return stored;
  } catch { /* Speicher gesperrt: dann eben der Browser */ }
  return navigator.languages?.some(l => l.toLowerCase().startsWith('de')) ? 'de' : 'en';
}

type Context = { lang: Lang; t: Texts; setLang: (lang: Lang) => void };
const LangContext = createContext<Context>({ lang: 'de', t: de, setLang: () => {} });

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setState] = useState<Lang>(initial);
  useEffect(() => { document.documentElement.lang = lang; }, [lang]);
  // Gespeichert wird nur eine ausdrückliche Wahl; sonst folgt die Seite weiter dem Browser.
  const setLang = (next: Lang) => {
    setState(next);
    try { localStorage.setItem('lang', next); } catch { /* siehe oben */ }
  };
  return <LangContext.Provider value={{ lang, t: texts[lang], setLang }}>{children}</LangContext.Provider>;
}

export const useLang = () => useContext(LangContext);
export const useT = () => useContext(LangContext).t;

// Die Schnittstelle der Python-App (omat3d/app.py).

// Die Bewegungsbibliothek (omat3d/motions.py): Aufnahmen aus CMU und Bandai Namco, mit Herkunft und Lizenz.
export type MotionSource = { name: string; license: string; licenseEn: string; commercial: boolean; credit: string; url: string };
export type MotionClip = {
  id: string; source: string; de: string; en: string; category: string; style: string | null;
  styleDe?: string; styleEn?: string; ref: string; frames: number; seconds: number;
};
export type MotionIndex = {
  sources: Record<string, MotionSource>; categories: Record<string, { de: string; en: string }>; clips: MotionClip[];
};

export type Check = { suitable: boolean; object: string; problems: string[]; tips: string[] };

export type Image = {
  id: string;
  name: string;
  original: string;
  cutout: { method: 'alpha' | 'rembg' | 'flood' | 'none'; width: number; height: number; subject: number; note: string | null };
  check: Check | null;
  painted?: { model: string; prompt: string; seed: number | null; revisedPrompt: string | null };
};

export type Options = {
  texture_size?: number;
  decimation_target?: number;
  resolution?: number;
  steps?: number;
  seed?: number;
};

export type Job = {
  id: string;
  kind?: 'rig' | 'api' | 'import';
  // Eigene Figuren (omat3d/importer.py): skeleton "mixamo" geht gleich in die Bibliothek, "other" bekommt eins von SkinTokens.
  import?: { format: 'glb' | 'gltf' | 'obj'; triangles: number; skeleton: 'mixamo' | 'other' | null; notes: string[] };
  // Aufträge der Schnittstelle (/v1): wer bestellt hat und unter welchem Namen.
  api?: { app: string | null; creator: string | null; label: string | null };
  sourceJobId?: string;
  report?: { joints_raw?: number; naming?: string; weights?: { verts?: number } } | null;
  // Wie viele Gelenke des Skeletts von Hand verschoben wurden (0 oder fehlt: das Skelett von SkinTokens).
  skeletonEdited?: number;
  fingers?: Record<string, number>;
  // Bei Aufträgen der Schnittstelle erst gesetzt, wenn das Bild freigestellt ist.
  imageId: string | null;
  imageName: string | null;
  options: Options;
  state: 'queued' | 'waiting' | 'running' | 'done' | 'failed' | 'canceled';
  stage: string | null;
  step: number | null;
  total: number | null;
  message: string | null;
  created: string;
  started: string | null;
  finished: string | null;
  seconds: number | null;
  glbBytes: number | null;
  attempts: number;
};

export type Status = {
  storage: { where: string; mounted: boolean; root: string | null; freeGb: number | null; error: string | null };
  rembg: boolean;
  cutout: string;
  models: { trellis: string; assist: string; rig: string; paint: string[] };
  defaults: Options;
  gate: {
    url: string;
    reachable: boolean;
    tonwerk: { reachable: boolean; idle: boolean; reasons: string[] } | null;
    registered: boolean;
    held: string | null;
    models: Record<string, { state: string; approved: boolean; backend?: string }>;
  };
  paint: { models: { id: string; state: string | null; approved: boolean }[]; sizes: string[] };
};

export type Painting = {
  id: string;
  prompt: string;
  model: string;
  n: number;
  state: 'running' | 'done' | 'failed';
  stage: 'queued' | 'sending' | 'loading' | 'painting' | 'checking' | 'cutout' | null;
  place: number | null;
  fraction: number | null;
  imageIds: string[];
  message: string | null;
  seconds: number | null;
};

export type Prompts = { idea: string; prompts: string[]; note: string };

export type PoseAnswer = { text: string; pose: Record<string, [number, number, number]>; note: string };

export type PrintOptions = { pose?: Record<string, [number, number, number, number]>; height: number; base: boolean; format: '3mf' | 'stl' };
// Wie die Druckdatei entstand: closed = Teile verschmolzen, voxels = über ein Raster geschlossen.
export type PrintInfo = { method: 'closed' | 'voxels'; triangles: number; volumeCm3: number; sizeMm: [number, number, number] };

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { 'content-type': 'application/json' };
  }
  const response = await fetch(path, init);
  const json = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = json.error ?? json.detail;
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((d: { msg: string }) => d.msg).join(', ') : `HTTP ${response.status}`);
  }
  return json as T;
}

export const api = {
  status: () => call<Status>('GET', '/api/status'),
  images: () => call<Image[]>('GET', '/api/images'),
  upload: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return call<Image>('POST', '/api/images', form);
  },
  // Gemma antwortet in der Sprache der Oberfläche.
  check: (id: string, lang: string) => call<Image>('POST', `/api/images/${id}/check?lang=${lang}`),
  deleteImage: (id: string) => call('DELETE', `/api/images/${id}`),
  prompts: (idea: string, lang: string) => call<Prompts>('POST', '/api/prompts', { idea, lang }),
  paint: (prompt: string, model: string, n: number) => call<Painting>('POST', '/api/paint', { prompt, model, n }),
  paintings: () => call<Painting[]>('GET', '/api/paint'),
  jobs: () => call<Job[]>('GET', '/api/jobs'),
  createJob: (imageId: string, options: Options) => call<Job>('POST', '/api/jobs', { imageId, options }),
  importFigure: (files: File[]) => {
    const form = new FormData();
    for (const file of files) form.append('files', file, file.name);
    return call<Job>('POST', '/api/jobs/import', form);
  },
  rigJob: (id: string, kind: string, seed = 1) => call<Job>('POST', `/api/jobs/${id}/rig`, { class: kind, seed }),
  // Die Proben als Animationen im GLB; zurück kommt die Datei.
  motions: () => fetch('/api/motions').then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() as Promise<MotionIndex>; }),
  motionBvh: (id: string) => fetch(`/api/motions/${id}.bvh`).then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); }),
  animatedGlb: async (id: string, clips: unknown[]) => {
    const response = await fetch(`/api/jobs/${id}/animated.glb`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ clips }),
    });
    if (!response.ok) {
      const json = await response.json().catch(() => ({}));
      throw new Error(typeof json.detail === 'string' ? json.detail : `HTTP ${response.status}`);
    }
    const name = /filename="([^"]+)"/.exec(response.headers.get('content-disposition') ?? '')?.[1] ?? `${id}-proben.glb`;
    return { blob: await response.blob(), name };
  },
  // Gelenke des Skeletts verschieben (Knoten -> neue Lage) und das Original wiederherstellen.
  moveJoints: (id: string, moves: Record<string, [number, number, number]>) =>
    call<{ moved: number; skeletonEdited: number }>('POST', `/api/jobs/${id}/skeleton`, { moves }),
  resetSkeleton: (id: string) => call<{ skeletonEdited: number }>('POST', `/api/jobs/${id}/skeleton/reset`, {}),
  // Die Figur als Datei für den 3D-Drucker, in der Haltung, die der Viewer gerade zeigt.
  printFile: async (id: string, options: PrintOptions) => {
    const response = await fetch(`/api/jobs/${id}/print`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(options),
    });
    if (!response.ok) {
      const json = await response.json().catch(() => ({}));
      throw new Error(typeof json.detail === 'string' ? json.detail : `HTTP ${response.status}`);
    }
    const name = /filename="([^"]+)"/.exec(response.headers.get('content-disposition') ?? '')?.[1] ?? `${id}.${options.format}`;
    const info = JSON.parse(response.headers.get('x-print-info') ?? 'null') as PrintInfo | null;
    return { blob: await response.blob(), name, info };
  },
  pose: (text: string, lang: string) => call<PoseAnswer>('POST', '/api/pose', { text, lang }),
  cancelJob: (id: string) => call<Job>('POST', `/api/jobs/${id}/cancel`),
  deleteJob: (id: string) => call('DELETE', `/api/jobs/${id}`),
};

// Wie omat3d/app.py has_skeleton: ein Skelett von SkinTokens oder ein importiertes mit Mixamo-Namen.
export const hasSkeleton = (job: Job | null | undefined) =>
  job?.kind === 'rig' || (job?.kind === 'import' && job.import?.skeleton === 'mixamo');

export const cutoutUrl = (id: string) => `/api/images/${id}/cutout`;
export const originalUrl = (id: string) => `/api/images/${id}/original`;
export const modelUrl = (id: string, download = false) => `/api/jobs/${id}/model.glb${download ? '?download=true' : ''}`;

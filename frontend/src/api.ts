// Die Schnittstelle der Python-App (omat3d/app.py).

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
  kind?: 'rig';
  sourceJobId?: string;
  report?: { joints_raw?: number; naming?: string; weights?: { verts?: number } } | null;
  imageId: string;
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
  check: (id: string) => call<Image>('POST', `/api/images/${id}/check`),
  deleteImage: (id: string) => call('DELETE', `/api/images/${id}`),
  prompts: (idea: string) => call<Prompts>('POST', '/api/prompts', { idea }),
  paint: (prompt: string, model: string, n: number) => call<Painting>('POST', '/api/paint', { prompt, model, n }),
  paintings: () => call<Painting[]>('GET', '/api/paint'),
  jobs: () => call<Job[]>('GET', '/api/jobs'),
  createJob: (imageId: string, options: Options) => call<Job>('POST', '/api/jobs', { imageId, options }),
  rigJob: (id: string, kind: string) => call<Job>('POST', `/api/jobs/${id}/rig`, { class: kind }),
  // Die Proben als Animationen im GLB; zurück kommt die Datei.
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
  cancelJob: (id: string) => call<Job>('POST', `/api/jobs/${id}/cancel`),
  deleteJob: (id: string) => call('DELETE', `/api/jobs/${id}`),
};

export const cutoutUrl = (id: string) => `/api/images/${id}/cutout`;
export const originalUrl = (id: string) => `/api/images/${id}/original`;
export const modelUrl = (id: string, download = false) => `/api/jobs/${id}/model.glb${download ? '?download=true' : ''}`;

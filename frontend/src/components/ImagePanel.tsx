import { useRef, useState } from 'react';
import { api, cutoutUrl, originalUrl, type Image, type Job, type Options, type Status } from '../api';
import { useLang, useT } from '../i18n';

type Props = {
  status: Status | null;
  images: Image[];
  selected: string | null;
  onSelect: (id: string | null) => void;
  onChanged: () => void;
  onStarted: (job: Job) => void;
};

export function ImagePanel({ status, images, selected, onSelect, onChanged, onStarted }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const image = images.find(i => i.id === selected) ?? null;
  const { image: t } = useT();

  async function run<T>(what: string, task: () => Promise<T>): Promise<T | undefined> {
    setBusy(what);
    setError(null);
    try {
      return await task();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function upload(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    const created = await run(t.cutting, () => api.upload(file));
    if (created) {
      onChanged();
      onSelect(created.id);
    }
    if (input.current) input.current.value = '';
  }

  return (
    <div className="card">
      <h2><span className="step">1</span> {t.title}</h2>
      <label
        className={`drop ${dragging ? 'over' : ''} ${status && !status.storage.mounted ? 'disabled' : ''}`}
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => { e.preventDefault(); setDragging(false); upload(e.dataTransfer.files); }}
      >
        <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" onChange={e => upload(e.target.files)} />
        <strong>{busy === t.cutting ? busy : t.choose}</strong>
        <span className="muted">{t.advice}</span>
      </label>
      {error && <div className="error">{error}</div>}

      {images.length > 0 && (
        <div className="thumbs">
          {images.map(i => (
            <button key={i.id} className={`thumb ${i.id === selected ? 'active' : ''}`} onClick={() => onSelect(i.id)} title={i.name}>
              <img src={cutoutUrl(i.id)} alt={i.name} loading="lazy" />
            </button>
          ))}
        </div>
      )}

      {image && <ImageDetail key={image.id} image={image} status={status} busy={busy} run={run} onChanged={onChanged} onStarted={onStarted} onDeleted={() => onSelect(null)} />}
    </div>
  );
}

type DetailProps = {
  image: Image;
  status: Status | null;
  busy: string | null;
  run: <T>(what: string, task: () => Promise<T>) => Promise<T | undefined>;
  onChanged: () => void;
  onStarted: (job: Job) => void;
  onDeleted: () => void;
};

function ImageDetail({ image, status, busy, run, onChanged, onStarted, onDeleted }: DetailProps) {
  const defaults = status?.defaults ?? {};
  const [options, setOptions] = useState<Options>({});
  const set = (key: keyof Options, value: string) =>
    setOptions(o => ({ ...o, [key]: value === '' ? undefined : Number(value) }));
  const check = image.check;
  const { lang, t: { locale, image: t } } = useLang();

  return (
    <div className="detail">
      <div className="pair">
        <figure><img src={originalUrl(image.id)} alt={t.original} /><figcaption>{t.original}</figcaption></figure>
        <figure className="checker"><img src={cutoutUrl(image.id)} alt={t.cutout} /><figcaption>{t.goesTo} · {t.methods[image.cutout.method]}</figcaption></figure>
      </div>
      {image.cutout.note && <div className="hint">{image.cutout.note}</div>}
      {image.painted && (
        <div className="muted small painted">{t.paintedWith} {image.painted.model}{image.painted.seed !== null && <>, {t.seed} {image.painted.seed}</>}: {t.quote(image.painted.prompt)}</div>
      )}

      <div className="row">
        <button onClick={() => run(t.checking, () => api.check(image.id, lang)).then(r => r && onChanged())} disabled={!!busy}>
          {busy === t.checking ? busy : check ? t.recheck : t.check}
        </button>
        <span className="muted small">{t.checkHint}</span>
      </div>
      {check && (
        <div className={`verdict ${check.suitable ? 'ok' : 'warn'}`}>
          <strong>{check.suitable ? t.suitable : t.unsuitable}</strong>{check.object && <> · {check.object}</>}
          {check.problems.length > 0 && <ul>{check.problems.map(p => <li key={p}>{p}</li>)}</ul>}
          {check.tips.length > 0 && <div className="muted small">{t.tips}: {check.tips.join(' · ')}</div>}
        </div>
      )}

      <h2><span className="step">2</span> {t.convertTitle}</h2>
      <div className="options">
        <label>{t.texture}
          <select value={options.texture_size ?? ''} onChange={e => set('texture_size', e.target.value)}>
            <option value="">{t.preset(String(defaults.texture_size ?? 2048))}</option>
            {[1024, 2048, 4096].map(v => <option key={v} value={v}>{v} px</option>)}
          </select>
        </label>
        <label>{t.faces}
          <select value={options.decimation_target ?? ''} onChange={e => set('decimation_target', e.target.value)}>
            <option value="">{t.preset((defaults.decimation_target ?? 300000).toLocaleString(locale))}</option>
            {[50000, 150000, 300000, 600000].map(v => <option key={v} value={v}>{v.toLocaleString(locale)}</option>)}
          </select>
        </label>
        <label>{t.resolution}
          <select value={options.resolution ?? ''} onChange={e => set('resolution', e.target.value)}>
            <option value="">{t.modelPreset}</option>
            <option value="512">{t.lowMemory}</option>
            <option value="1024">1024</option>
          </select>
        </label>
        <label>Seed
          <input type="number" min={0} placeholder={t.random} value={options.seed ?? ''} onChange={e => set('seed', e.target.value)} />
        </label>
      </div>
      <div className="row">
        <button
          className="primary"
          disabled={!!busy || !status?.storage.mounted}
          onClick={() => run(t.starting, () => api.createJob(image.id, options)).then(job => job && onStarted(job))}
        >{t.convert}</button>
        <span className="muted small">{t.convertHint}</span>
        <button className="link danger" disabled={!!busy}
          onClick={() => confirm(t.confirmDelete(image.name)) && run(t.deleting, () => api.deleteImage(image.id)).then(r => { if (r) { onDeleted(); onChanged(); } })}
        >{t.delete}</button>
      </div>
    </div>
  );
}

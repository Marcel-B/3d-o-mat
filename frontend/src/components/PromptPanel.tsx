import { useEffect, useRef, useState } from 'react';
import { api, type Painting, type Prompts, type Status } from '../api';
import { useLang } from '../i18n';

const labels: Record<string, string> = { 'klein-4b': 'FLUX.2 Klein 4B', 'z-image-turbo': 'Z-Image Turbo', 'krea-2-turbo': 'Krea 2 Turbo' };

type Props = { status: Status | null; onPainted: (imageIds: string[]) => void };

// Kein Bild? Gemma schreibt aus einer Idee Prompts für eine gute TRELLIS-Vorlage, img-o-mat malt sie auf oi-o-mat
// (über die Schranke), und die Bilder landen freigestellt in der Bildliste.
export function PromptPanel({ status, onPainted }: Props) {
  const [idea, setIdea] = useState('');
  const [result, setResult] = useState<Prompts | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const [model, setModel] = useState('');
  const [count, setCount] = useState(1);
  const [paintings, setPaintings] = useState<Painting[]>([]);
  const seen = useRef(new Set<string>());
  const painted = useRef(onPainted);
  const { lang, t: { paint: t } } = useLang();
  painted.current = onPainted;

  const models = status?.paint.models ?? [];
  const chosen = models.find(m => m.id === model) ?? models[0];
  const running = paintings.some(p => p.state === 'running');

  // Solange gemalt wird, nach dem Stand fragen; ein fertiges Bild wird gleich ausgewählt.
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      api.paintings().then(list => {
        setPaintings(list);
        for (const p of list) {
          if (p.state === 'done' && p.imageIds.length && !seen.current.has(p.id)) {
            seen.current.add(p.id);
            painted.current(p.imageIds);
          }
        }
      }, () => {});
    }, 1500);
    return () => clearInterval(timer);
  }, [running]);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.prompts(idea, lang));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function paint(prompt: string) {
    if (!chosen) return;
    setError(null);
    try {
      const started = await api.paint(prompt, chosen.id, count);
      setPaintings(list => [started, ...list.filter(p => p.id !== started.id)]);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function copy(text: string, index: number) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(index);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      setError(t.copyFailed);
    }
  }

  const visible = paintings.filter(p => p.state !== 'done').slice(0, 4);
  return (
    <div className="card">
      <h2>{t.title}</h2>
      <textarea
        rows={2}
        placeholder={t.placeholder}
        value={idea}
        onChange={e => setIdea(e.target.value)}
      />
      <div className="row">
        <button onClick={generate} disabled={busy || !idea.trim()}>{busy ? t.writing : t.write}</button>
        <button onClick={() => paint(idea.trim())} disabled={!idea.trim() || !chosen}>{t.direct}</button>
        {models.length > 0 && (
          <>
            <select aria-label={t.model} value={chosen?.id ?? ''} onChange={e => setModel(e.target.value)}>
              {models.map(m => <option key={m.id} value={m.id}>{labels[m.id] ?? m.id}</option>)}
            </select>
            <select aria-label={t.count} value={count} onChange={e => setCount(Number(e.target.value))}>
              {[1, 2, 3, 4].map(n => <option key={n} value={n}>{t.images(n)}</option>)}
            </select>
          </>
        )}
      </div>
      <div className="muted small">
        {t.hint}
        {chosen && !chosen.approved && <> <span className="warn">{t.notApproved(labels[chosen.id] ?? chosen.id)}</span></>}
      </div>
      {error && <div className="error">{error}</div>}
      {visible.map(p => (
        <div key={p.id} className="painting">
          <div className="small" title={p.prompt}>{p.prompt}</div>
          {p.state === 'running' ? (
            <div className="progress">
              {p.stage === 'painting' && p.fraction !== null && <div className="bar" style={{ width: `${Math.round(p.fraction * 100)}%` }} />}
              <span>{labels[p.model] ?? p.model} {t.stages[p.stage ?? ''] ?? p.stage ?? '…'}{p.stage === 'queued' && p.place ? t.place(p.place) : ''}</span>
              <span className="count">{p.stage === 'painting' && p.fraction !== null ? `${Math.round(p.fraction * 100)} %` : ''}</span>
            </div>
          ) : (
            <div className="error">{p.message}</div>
          )}
        </div>
      ))}
      {result && (
        <ol className="prompts">
          {result.prompts.map((prompt, index) => (
            <li key={prompt}>
              <p>{prompt}</p>
              <button className="link" onClick={() => paint(prompt)} disabled={!chosen}>{t.paint}</button>
              {' · '}
              <button className="link" onClick={() => copy(prompt, index)}>{copied === index ? t.copied : t.copy}</button>
            </li>
          ))}
        </ol>
      )}
      {result?.note && <div className="muted small">{result.note}</div>}
    </div>
  );
}

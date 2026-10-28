import { useState } from 'react';
import { api, type Prompts } from '../api';

// Aus einer Idee Bild-Prompts für eine gute TRELLIS-Vorlage. Das Bild selbst erzeugt später der Bilddienst
// (img-o-mat: FLUX, Krea 2, Z-Image-Turbo); bis dahin werden die Prompts kopiert.
export function PromptPanel() {
  const [idea, setIdea] = useState('');
  const [result, setResult] = useState<Prompts | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<number | null>(null);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.prompts(idea));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function copy(text: string, index: number) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(index);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      setError('Kopieren geht nur über https oder localhost; den Text bitte markieren.');
    }
  }

  return (
    <div className="card">
      <h2>Noch kein Bild? Prompt aus einer Idee</h2>
      <textarea
        rows={2}
        placeholder="z. B. ein alter Messing-Kompass mit aufgeklapptem Deckel"
        value={idea}
        onChange={e => setIdea(e.target.value)}
      />
      <div className="row">
        <button onClick={generate} disabled={busy || !idea.trim()}>{busy ? 'Gemma schreibt …' : 'Prompts schreiben'}</button>
        <span className="muted small">Bilder daraus erzeugen kommt mit dem Bilddienst (img-o-mat). Bis dahin: Prompt kopieren, Bild erzeugen, hier hochladen.</span>
      </div>
      {error && <div className="error">{error}</div>}
      {result && (
        <ol className="prompts">
          {result.prompts.map((prompt, index) => (
            <li key={prompt}>
              <p>{prompt}</p>
              <button className="link" onClick={() => copy(prompt, index)}>{copied === index ? 'kopiert' : 'kopieren'}</button>
            </li>
          ))}
        </ol>
      )}
      {result?.note && <div className="muted small">{result.note}</div>}
    </div>
  );
}

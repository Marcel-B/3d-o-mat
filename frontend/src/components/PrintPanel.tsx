import { useState } from 'react';
import { api, type PrintInfo, type PrintOptions } from '../api';
import { useLang } from '../i18n';

// Die Druckdatei (3MF oder STL): Höhe, Sockel, Format. pose liefert die Haltung des Viewers, wenn es ein Skelett gibt.
export function PrintPanel({ jobId, pose }: { jobId: string; pose?: () => PrintOptions['pose'] }) {
  const { t: { locale, print: t } } = useLang();
  const [height, setHeight] = useState(120);
  const [base, setBase] = useState(true);
  const [format, setFormat] = useState<'3mf' | 'stl'>('3mf');
  const [busy, setBusy] = useState(false);
  const [info, setInfo] = useState<PrintInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setBusy(true); setError(null); setInfo(null);
    try {
      const result = await api.printFile(jobId, { pose: pose?.(), height, base, format });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(result.blob);
      link.download = result.name;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 10000);
      setInfo(result.info);
    } catch (e) {
      setError(t.failed((e as Error).message));
    } finally {
      setBusy(false);
    }
  }

  const number = (value: number) => value.toLocaleString(locale, { maximumFractionDigits: 1 });
  return (
    <details className="print">
      <summary>{t.title}</summary>
      <div className="row tight">
        <label className="small">{t.height}
          <input id="print-height" type="number" min={20} max={400} step={5} value={height}
            onChange={e => setHeight(Math.min(400, Math.max(20, Number(e.target.value) || 120)))} />
        </label>
        <label className="small"><input id="print-base" type="checkbox" checked={base} onChange={e => setBase(e.target.checked)} /> {t.base}</label>
        <select id="print-format" value={format} onChange={e => setFormat(e.target.value as '3mf' | 'stl')} aria-label="Format">
          <option value="3mf">3MF</option>
          <option value="stl">STL</option>
        </select>
        <button type="button" onClick={download} disabled={busy}>{busy ? t.computing : t.download}</button>
      </div>
      {info && <div className="small muted">{t.result(info.sizeMm.map(number).join(' × '), number(info.volumeCm3), info.method === 'closed')}</div>}
      <div className="small muted">{pose ? t.hintPosed : t.hint}</div>
      {error && <div className="error">{error}</div>}
    </details>
  );
}

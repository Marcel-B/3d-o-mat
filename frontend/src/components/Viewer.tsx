import '@google/model-viewer';
import { useState } from 'react';
import { api, modelUrl, type Job } from '../api';
import { RigViewer } from '../rig/RigViewer';
import { PrintPanel } from './PrintPanel';
import { useT } from '../i18n';

declare module 'react' {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace JSX {
    interface IntrinsicElements {
      'model-viewer': React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
        src?: string;
        alt?: string;
        'camera-controls'?: boolean;
        'auto-rotate'?: boolean;
        'shadow-intensity'?: string;
        exposure?: string;
        'environment-image'?: string;
      };
    }
  }
}

const size = (bytes: number) => (bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.round(bytes / 1e3)} KB`);

const seconds = (s: number) => (s < 90 ? `${s} s` : `${Math.round(s / 60)} min`);

type Props = { job: Job | null; jobs: Job[]; scheme: 'light' | 'dark'; onRigStarted: (job: Job) => void };

export function Viewer({ job, jobs, scheme, onRigStarted }: Props) {
  const { viewer: t } = useT();
  const ready = job?.state === 'done';
  const rigged = job?.kind === 'rig';
  const [kind, setKind] = useState('humanoid');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Schon ein Skelett für dieses Modell in Arbeit oder fertig? Dann zeigt der Knopf darauf, statt ein zweites zu starten.
  const existing = job && !rigged ? jobs.find(j => j.kind === 'rig' && j.sourceJobId === job.id && j.state !== 'failed' && j.state !== 'canceled') : undefined;
  // Derselbe Seed gibt dasselbe Skelett. Der Rig-Dienst probiert je Auftrag drei Seeds; ein neuer Versuch nach einem
  // Fehlschlag fängt bei den nächsten drei an.
  const failed = job && !rigged ? jobs.filter(j => j.kind === 'rig' && j.sourceJobId === job.id && j.state === 'failed').length : 0;

  async function rig() {
    if (!job) return;
    if (existing) return onRigStarted(existing);
    setBusy(true); setError(null);
    try {
      onRigStarted(await api.rigJob(job.id, kind, 1 + 3 * failed));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card viewer">
      <h2><span className="step">3</span> {rigged ? t.rigged : t.model}</h2>
      {ready && rigged ? <RigViewer key={job.id} url={modelUrl(job.id)} jobId={job.id} scheme={scheme} /> : (
      <div className="stage">
        {ready ? (
          <model-viewer key={job.id} src={modelUrl(job.id)} alt={job.imageName ?? t.alt} camera-controls auto-rotate
            shadow-intensity="1" exposure="1" environment-image="neutral" />
        ) : (
          <div className="empty muted">{job ? t.appears : t.none}</div>
        )}
      </div>
      )}
      {ready && (
        <div className="row">
          <a className="button primary" href={modelUrl(job.id, true)}>{rigged ? t.downloadRigged : t.download}</a>
          <span className="muted small">
            {job.glbBytes ? size(job.glbBytes) : ''}{job.seconds !== null ? t.computed(seconds(job.seconds)) : ''}
            {rigged ? t.riggedHint : t.plainHint}
          </span>
        </div>
      )}
      {ready && !rigged && <PrintPanel key={job.id} jobId={job.id} />}
      {ready && !rigged && (
        <div className="row rig-start">
          <button type="button" onClick={rig} disabled={busy}>{existing ? t.toRigged : busy ? t.starting : failed ? t.retry : t.rig}</button>
          {!existing && (
            <select id="rig-class" value={kind} onChange={e => setKind(e.target.value)} aria-label={t.kind}>
              <option value="humanoid">{t.humanoid}</option>
              <option value="quadruped">{t.quadruped}</option>
              <option value="custom">{t.custom}</option>
            </select>
          )}
          <span className="muted small">
            {failed ? t.retryHint : t.rigHint}
          </span>
        </div>
      )}
      {error && <div className="error">{error}</div>}
    </div>
  );
}

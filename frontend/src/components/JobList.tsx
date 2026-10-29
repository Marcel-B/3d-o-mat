import { api, cutoutUrl, type Job } from '../api';

const states: Record<Job['state'], string> = {
  queued: 'in der Schlange',
  waiting: 'wartet auf oi-o-mat',
  running: 'rechnet',
  done: 'fertig',
  failed: 'fehlgeschlagen',
  canceled: 'abgebrochen',
};
const tones: Record<Job['state'], string> = { queued: 'muted', waiting: 'warn', running: 'ok', done: 'ok', failed: 'bad', canceled: 'muted' };

// TRELLIS.2 meldet Stufen wie "trellis-shape-1024"; lesbar: "Form (1024)". Unbekannte bleiben, wie sie sind.
const stages: [RegExp, string][] = [
  [/sparse|structure/, 'Grobform'],
  [/shape|geometry|mesh/, 'Form'],
  [/tex|paint|material|bake/, 'Textur'],
  [/decimat|simplif|export|glb/, 'Abschluss'],
];
function stageLabel(stage: string | null) {
  if (!stage) return '';
  const found = stages.find(([pattern]) => pattern.test(stage));
  const size = /(\d{3,4})$/.exec(stage)?.[1];
  return found ? `${found[1]}${size ? ` (${size})` : ''}` : stage;
}

function duration(job: Job) {
  if (job.seconds !== null) return `${Math.floor(job.seconds / 60)}:${String(job.seconds % 60).padStart(2, '0')} min`;
  if (job.started) {
    const s = Math.max(0, Math.round((Date.now() - Date.parse(job.started)) / 1000));
    return `seit ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')} min`;
  }
  return '';
}

type Props = { jobs: Job[]; selected: string | null; onSelect: (id: string) => void; onChanged: () => void };

export function JobList({ jobs, selected, onSelect, onChanged }: Props) {
  async function act(task: () => Promise<unknown>) {
    try {
      await task();
    } catch (e) {
      alert((e as Error).message);
    }
    onChanged();
  }

  return (
    <div className="card">
      <h2>Aufträge</h2>
      {jobs.length === 0 && <div className="muted">Noch keine. Ein Bild wählen und „In 3D umwandeln“.</div>}
      <ul className="jobs">
        {jobs.map(job => (
          <li key={job.id} className={job.id === selected ? 'active' : ''} onClick={() => onSelect(job.id)}>
            <img src={cutoutUrl(job.imageId)} alt="" />
            <div className="grow">
              <div className="row tight">
                <strong>{job.imageName ?? job.imageId}</strong>
                <span className={`pill ${tones[job.state]}`}>{states[job.state]}</span>
                <span className="muted small">{duration(job)}</span>
              </div>
              {job.state === 'running' && job.total ? (
                <div className="progress" title={`${job.stage} ${job.step}/${job.total}`}>
                  <div className="bar" style={{ width: `${Math.round(((job.step ?? 0) / job.total) * 100)}%` }} />
                  <span>{stageLabel(job.stage)}</span>
                  <span className="count">{job.step}/{job.total}</span>
                </div>
              ) : job.state === 'running' && job.stage ? <div className="muted small">{stageLabel(job.stage)}</div> : null}
              {job.message && <div className={`small ${job.state === 'failed' ? 'bad' : 'muted'}`}>{job.message}</div>}
            </div>
            <div className="actions" onClick={e => e.stopPropagation()}>
              {job.state === 'queued' && <button className="link" onClick={() => act(() => api.cancelJob(job.id))}>abbrechen</button>}
              {['done', 'failed', 'canceled'].includes(job.state) && (
                <button className="link danger" onClick={() => confirm('Auftrag samt Modell löschen?') && act(() => api.deleteJob(job.id))}>löschen</button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

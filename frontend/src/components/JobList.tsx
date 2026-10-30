import { api, cutoutUrl, type Job } from '../api';
import { useT, type Texts } from '../i18n';

const tones: Record<Job['state'], string> = { queued: 'muted', waiting: 'warn', running: 'ok', done: 'ok', failed: 'bad', canceled: 'muted' };

// TRELLIS.2 meldet Stufen wie "trellis-shape-1024"; lesbar: "Form (1024)". Unbekannte bleiben, wie sie sind.
const stages: [RegExp, keyof Texts['jobs']['stages']][] = [
  [/sparse|structure/, 'coarse'],
  [/shape|geometry|mesh/, 'shape'],
  [/tex|paint|material|bake/, 'texture'],
  [/decimat|simplif|export|glb/, 'finish'],
  [/skeleton/, 'skeleton'],
];
function stageLabel(stage: string | null, t: Texts['jobs']) {
  if (!stage) return '';
  const found = stages.find(([pattern]) => pattern.test(stage));
  const size = /(\d{3,4})$/.exec(stage)?.[1];
  return found ? `${t.stages[found[1]]}${size ? ` (${size})` : ''}` : stage;
}

const minutes = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')} min`;

function duration(job: Job, t: Texts['jobs']) {
  if (job.seconds !== null) return minutes(job.seconds);
  if (job.started) return t.since(minutes(Math.max(0, Math.round((Date.now() - Date.parse(job.started)) / 1000))));
  return '';
}

type Props = { jobs: Job[]; selected: string | null; onSelect: (id: string) => void; onChanged: () => void };

export function JobList({ jobs, selected, onSelect, onChanged }: Props) {
  const { jobs: t } = useT();
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
      <h2>{t.title}</h2>
      {jobs.length === 0 && <div className="muted">{t.empty}</div>}
      <ul className="jobs">
        {jobs.map(job => (
          <li key={job.id} className={job.id === selected ? 'active' : ''} onClick={() => onSelect(job.id)}>
            <img src={cutoutUrl(job.imageId)} alt="" />
            <div className="grow">
              <div className="row tight">
                <strong>{job.imageName ?? job.imageId}</strong>
                {job.kind === 'rig' && <span className="pill muted">{t.skeleton}</span>}
                <span className={`pill ${tones[job.state]}`}>{t.states[job.state]}</span>
                <span className="muted small">{duration(job, t)}</span>
              </div>
              {job.state === 'running' && job.total ? (
                <div className="progress" title={`${job.stage} ${job.step}/${job.total}`}>
                  <div className="bar" style={{ width: `${Math.round(((job.step ?? 0) / job.total) * 100)}%` }} />
                  <span>{stageLabel(job.stage, t)}</span>
                  <span className="count">{job.step}/{job.total}</span>
                </div>
              ) : job.state === 'running' && job.stage ? <div className="muted small">{stageLabel(job.stage, t)}</div> : null}
              {job.message && <div className={`small ${job.state === 'failed' ? 'bad' : 'muted'}`}>{job.message}</div>}
            </div>
            <div className="actions" onClick={e => e.stopPropagation()}>
              {job.state === 'queued' && <button className="link" onClick={() => act(() => api.cancelJob(job.id))}>{t.cancel}</button>}
              {['done', 'failed', 'canceled'].includes(job.state) && (
                <button className="link danger" onClick={() => confirm(t.confirmDelete) && act(() => api.deleteJob(job.id))}>{t.delete}</button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

import '@google/model-viewer';
import { modelUrl, type Job } from '../api';

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

export function Viewer({ job }: { job: Job | null }) {
  const ready = job?.state === 'done';
  return (
    <div className="card viewer">
      <h2><span className="step">3</span> Modell</h2>
      <div className="stage">
        {ready ? (
          <model-viewer key={job.id} src={modelUrl(job.id)} alt={job.imageName ?? '3D-Modell'} camera-controls auto-rotate
            shadow-intensity="1" exposure="1" environment-image="neutral" />
        ) : (
          <div className="empty muted">{job ? 'Das Modell erscheint hier, sobald der Auftrag fertig ist.' : 'Noch kein Modell ausgewählt.'}</div>
        )}
      </div>
      {ready && (
        <div className="row">
          <a className="button primary" href={modelUrl(job.id, true)}>GLB herunterladen</a>
          <span className="muted small">
            {job.glbBytes ? size(job.glbBytes) : ''} · {job.seconds !== null ? `${job.seconds < 90 ? `${job.seconds} s` : `${Math.round(job.seconds / 60)} min`} gerechnet` : ''} · GLB mit PBR-Texturen, öffnet in Blender, Godot, Unity, Windows-3D-Viewer, Vorschau
          </span>
        </div>
      )}
    </div>
  );
}

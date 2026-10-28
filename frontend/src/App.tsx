import { useCallback, useEffect, useState } from 'react';
import { api, type Image, type Job, type Status } from './api';
import { StatusBar } from './components/StatusBar';
import { ImagePanel } from './components/ImagePanel';
import { PromptPanel } from './components/PromptPanel';
import { JobList } from './components/JobList';
import { Viewer } from './components/Viewer';

function usePoll<T>(load: () => Promise<T>, ms: number): [T | null, string | null, () => void] {
  const [value, setValue] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(() => {
    load().then(v => { setValue(v); setError(null); }, e => setError(e.message));
  }, [load]);
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, ms);
    return () => clearInterval(timer);
  }, [refresh, ms]);
  return [value, error, refresh];
}

export default function App() {
  const [status, statusError] = usePoll<Status>(api.status, 5000);
  const [images, , refreshImages] = usePoll<Image[]>(api.images, 30000);
  const [jobs, , refreshJobs] = usePoll<Job[]>(api.jobs, 2000);
  const [selectedImage, setSelectedImage] = useState<string | null>(null);
  const [selectedJob, setSelectedJob] = useState<string | null>(null);

  // Ein frisch fertiger Auftrag zeigt sich von selbst, solange man nichts anderes ausgewählt hat.
  useEffect(() => {
    if (selectedJob || !jobs) return;
    const done = jobs.find(job => job.state === 'done');
    if (done) setSelectedJob(done.id);
  }, [jobs, selectedJob]);

  const job = jobs?.find(j => j.id === selectedJob) ?? null;

  return (
    <div className="page">
      <header className="top">
        <div className="brand">
          <svg viewBox="0 0 32 32" aria-hidden="true"><path d="M16 3 28 10v12l-12 7-12-7V10z" /><path d="M16 3v26M4 10l12 7 12-7" className="edge" /></svg>
          <div>
            <h1>3d-o-mat</h1>
            <p>Aus einem Bild ein 3D-Modell. Gerechnet von TRELLIS.2 auf oi-o-mat.</p>
          </div>
        </div>
        <StatusBar status={status} error={statusError} />
      </header>

      <main className="grid">
        <section className="column">
          <ImagePanel
            status={status}
            images={images ?? []}
            selected={selectedImage}
            onSelect={setSelectedImage}
            onChanged={refreshImages}
            onStarted={newJob => { refreshJobs(); setSelectedJob(newJob.id); }}
          />
          <PromptPanel />
        </section>
        <section className="column">
          <Viewer job={job} />
          <JobList jobs={jobs ?? []} selected={selectedJob} onSelect={setSelectedJob} onChanged={refreshJobs} />
        </section>
      </main>
    </div>
  );
}

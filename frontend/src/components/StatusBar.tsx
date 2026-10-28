import type { Status } from '../api';

const reasons: Record<string, string> = {
  songs: 'Songs', transcription: 'Transkription', lyrics: 'Songtext', voices: 'Stimmen', speech: 'Sprache',
  images: 'Bilder', video: 'Video', ace: 'ACE-Step', singing: 'Singen',
};

function Pill({ tone, label, title }: { tone: 'ok' | 'warn' | 'bad' | 'muted'; label: string; title?: string }) {
  return <span className={`pill ${tone}`} title={title}>{label}</span>;
}

export function StatusBar({ status, error }: { status: Status | null; error: string | null }) {
  if (error) return <div className="status"><Pill tone="bad" label="App antwortet nicht" title={error} /></div>;
  if (!status) return <div className="status"><Pill tone="muted" label="…" /></div>;
  const { gate } = status;
  const tonwerk = gate.tonwerk;
  const trellis = gate.models[status.models.trellis];
  return (
    <div className="status">
      <Pill tone={status.mounted ? 'ok' : 'bad'} label={status.mounted ? 'T7 verbunden' : 'T7 nicht verbunden'} title={status.dataDir} />
      <Pill tone={gate.reachable ? 'ok' : 'bad'} label={gate.reachable ? 'Schranke erreichbar' : 'Schranke antwortet nicht'} title={gate.url} />
      {tonwerk && (
        <Pill
          tone={!tonwerk.reachable ? 'muted' : tonwerk.idle ? 'ok' : 'warn'}
          label={!tonwerk.reachable ? 'Tonwerk ?' : tonwerk.idle ? 'Tonwerk still' : `Tonwerk: ${tonwerk.reasons.map(r => reasons[r] ?? r).join(', ')}`}
        />
      )}
      {gate.reachable && (
        <Pill
          tone={!trellis ? 'bad' : !trellis.approved ? 'warn' : 'ok'}
          label={!gate.registered ? 'nicht angemeldet' : !trellis ? 'TRELLIS fehlt' : !trellis.approved ? 'TRELLIS nicht freigegeben' : `TRELLIS ${trellis.state}`}
          title={!trellis?.approved ? `In ${gate.url}/gate freigeben` : status.models.trellis}
        />
      )}
    </div>
  );
}

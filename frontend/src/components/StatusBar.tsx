import type { Status } from '../api';
import { useT } from '../i18n';

function Pill({ tone, label, title }: { tone: 'ok' | 'warn' | 'bad' | 'muted'; label: string; title?: string }) {
  return <span className={`pill ${tone}`} title={title}>{label}</span>;
}

export function StatusBar({ status, error }: { status: Status | null; error: string | null }) {
  const { status: t } = useT();
  if (error) return <div className="status"><Pill tone="bad" label={t.noAnswer} title={error} /></div>;
  if (!status) return <div className="status"><Pill tone="muted" label="…" /></div>;
  const { gate } = status;
  const tonwerk = gate.tonwerk;
  const trellis = gate.models[status.models.trellis];
  return (
    <div className="status">
      <Pill
        tone={status.storage.mounted ? 'ok' : 'bad'}
        label={status.storage.mounted ? (status.storage.freeGb !== null ? t.t7Free(Math.round(status.storage.freeGb)) : 'T7') : t.t7Missing}
        title={status.storage.error ?? `${status.storage.where}:${status.storage.root ?? ''}`}
      />
      <Pill tone={gate.reachable ? 'ok' : 'bad'} label={gate.reachable ? t.gateUp : t.gateDown} title={gate.url} />
      {tonwerk && (
        <Pill
          tone={!tonwerk.reachable ? 'muted' : tonwerk.idle ? 'ok' : 'warn'}
          label={!tonwerk.reachable ? t.tonwerkUnknown : tonwerk.idle ? t.tonwerkIdle : t.tonwerkBusy(tonwerk.reasons.map(r => t.reasons[r] ?? r).join(', '))}
        />
      )}
      {gate.reachable && (
        <Pill
          tone={!trellis ? 'bad' : !trellis.approved ? 'warn' : 'ok'}
          label={!gate.registered ? t.notRegistered : !trellis ? t.trellisMissing : !trellis.approved ? t.trellisNotApproved : `TRELLIS ${trellis.state}`}
          title={!trellis?.approved ? t.approveAt(gate.url) : status.models.trellis}
        />
      )}
    </div>
  );
}

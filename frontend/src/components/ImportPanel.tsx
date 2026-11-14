import { useRef, useState } from 'react';
import { api, type Job, type Status } from '../api';
import { useT } from '../i18n';

// Eine eigene Figur (GLB, glTF, OBJ, mit Texturen oder als ZIP) wird sofort ein fertiger Auftrag, ohne oi-o-mat.
type Props = { status: Status | null; onImported: (job: Job) => void };

export function ImportPanel({ status, onImported }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const { figure: t } = useT();

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true); setError(null);
    try {
      onImported(await api.importFigure(Array.from(files)));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = '';
    }
  }

  return (
    <div className="card">
      <h2>{t.title}</h2>
      <label
        className={`drop ${dragging ? 'over' : ''} ${(status && !status.storage.mounted) || busy ? 'disabled' : ''}`}
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => { e.preventDefault(); setDragging(false); upload(e.dataTransfer.files); }}
      >
        <input ref={input} type="file" multiple
          accept=".glb,.gltf,.bin,.obj,.mtl,.zip,.png,.jpg,.jpeg,.tga,.bmp,.tif,.tiff,.webp,model/gltf-binary,model/gltf+json"
          onChange={e => upload(e.target.files)} />
        <strong>{busy ? t.reading : t.choose}</strong>
        <span className="muted">{t.advice}</span>
      </label>
      {error && <div className="error">{error}</div>}
    </div>
  );
}

import { useEffect, useState } from 'react';
import { fbxAvailable, type ExportFormat } from '../api';
import { useT } from '../i18n';

// Ob der Container Blender hat; bis die Antwort da ist, gibt es nur GLB.
export function useFbx() {
  const [ok, setOk] = useState(false);
  useEffect(() => {
    let live = true;
    fbxAvailable().then(v => { if (live) setOk(v); });
    return () => { live = false; };
  }, []);
  return ok;
}

// GLB oder FBX für Unity bzw. Unreal; ohne Blender im Container nichts.
export function FormatSelect({ id, value, onChange }: { id: string; value: ExportFormat; onChange: (f: ExportFormat) => void }) {
  const { exportFormat: t } = useT();
  if (!useFbx()) return null;
  return (
    <select id={id} className="format" value={value} onChange={e => onChange(e.target.value as ExportFormat)} aria-label={t.label} title={t.hint}>
      <option value="glb">{t.glb}</option>
      <option value="unity">{t.unity}</option>
      <option value="unreal">{t.unreal}</option>
    </select>
  );
}

export const short = (format: ExportFormat) => (format === 'glb' ? 'GLB' : 'FBX');

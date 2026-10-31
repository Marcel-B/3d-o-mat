import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { POSES, RigStage, type Clip, type Facts, type PoseName } from './stage';
import { PrintPanel } from '../components/PrintPanel';
import { useLang } from '../i18n';

const order: Clip[] = ['walk', 'punch', 'kick', 'roundhouse', 'arms', 'squat', 'fall'];
const poses = Object.keys(POSES) as PoseName[];

// Ein GLB mit Skelett ansehen und bewegen: Proben, Knochen von Hand, Skelett und Drahtgitter.
export function RigViewer({ url, jobId, scheme }: { url: string; jobId: string; scheme: 'light' | 'dark' }) {
  const { lang, t: { locale, rig: t } } = useLang();
  // Die Proben heißen im GLB so wie auf der Seite.
  const clips = order.map(clip => [clip, t.clips[clip]] as [Clip, string]);
  const loadFailed = useRef(t.loadFailed);
  loadFailed.current = t.loadFailed;
  const canvas = useRef<HTMLCanvasElement>(null);
  const stage = useRef<RigStage | null>(null);
  const [facts, setFacts] = useState<Facts | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [clip, setClip] = useState<Clip | null>(null);
  const [amp, setAmp] = useState(1);
  const [bone, setBone] = useState(0);
  const [angles, setAngles] = useState<[number, number, number]>([0, 0, 0]);
  // Was die Schieber je Knochen eingestellt haben, damit ein Wechsel des Knochens nichts verliert.
  const turned = useRef(new Map<number, [number, number, number]>());
  const [pose, setPose] = useState<PoseName | null>(null);
  const [describe, setDescribe] = useState('');
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [poseError, setPoseError] = useState<string | null>(null);
  const [skeleton, setSkeleton] = useState(true);
  const [wire, setWire] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  useEffect(() => {
    if (!canvas.current) return;
    const color = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#0f766e';
    const s = new RigStage(canvas.current, color);
    stage.current = s;
    setFacts(null); setError(null); setClip(null);
    s.load(url).then(f => {
      setFacts(f);
      const arm = s.bones.indexOf(s.find('LeftArm')!);
      setBone(arm >= 0 ? arm : 0);
    }, e => setError(loadFailed.current((e as Error).message)));
    return () => s.dispose();
  }, [url]);

  // Die Knochen in der Akzentfarbe des Farbschemas, auch wenn es umschaltet.
  useEffect(() => {
    const color = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
    if (color) stage.current?.setBoneColor(color);
  }, [scheme, facts]);

  useEffect(() => { if (stage.current) stage.current.amplitude = amp; }, [amp]);
  useEffect(() => { stage.current?.showSkeleton(skeleton); }, [skeleton, facts]);
  useEffect(() => { stage.current?.wireframe(wire); }, [wire, facts]);

  function play(next: Clip) {
    const value = clip === next ? null : next;
    setClip(value);
    setAngles([0, 0, 0]); turned.current.clear(); setPose(null); setNote(null);
    stage.current?.setClip(value);
  }

  function turn(axis: number, value: number) {
    const next = [...angles] as [number, number, number];
    next[axis] = value;
    setAngles(next);
    turned.current.set(bone, next);
    setClip(null);
    stage.current?.rotate(bone, next);
  }

  function pick(index: number) {
    setBone(index);
    setAngles(turned.current.get(index) ?? [0, 0, 0]);
  }

  function reset() {
    setClip(null); setAngles([0, 0, 0]); turned.current.clear(); setPose(null); setNote(null);
    stage.current?.setClip(null);
  }

  // Eine fertige Haltung oder eine von Gemma; die Schieber fangen danach wieder bei 0 an.
  function hold(name: PoseName | null, angles = name ? POSES[name] : null) {
    if (!angles) return;
    setClip(null); setAngles([0, 0, 0]); turned.current.clear(); setPose(name);
    stage.current?.applyPose(angles);
  }

  async function ask() {
    if (!describe.trim()) return;
    setAsking(true); setPoseError(null); setNote(null);
    try {
      const answer = await api.pose(describe, lang);
      hold(null, answer.pose);
      setNote(answer.note);
    } catch (e) {
      setPoseError(t.poseFailed((e as Error).message));
    } finally {
      setAsking(false);
    }
  }

  // Alle Proben mit der eingestellten Stärke als Animationen ins GLB, für Godot (AnimationPlayer) oder Blender.
  async function download() {
    if (!stage.current) return;
    setExporting(true); setExportError(null);
    try {
      const { blob, name } = await api.animatedGlb(jobId, stage.current.bake(clips));
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = name;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    } catch (e) {
      setExportError(t.exportFailed((e as Error).message));
    } finally {
      setExporting(false);
    }
  }

  const missing = facts?.core.filter(c => !c.found) ?? [];
  return (
    <div className="rig">
      <div className="stage rig-stage">
        <canvas ref={canvas} />
        {!facts && !error && <div className="empty muted">{t.loading}</div>}
        {error && <div className="empty bad">{error}</div>}
      </div>
      {facts && (
        <div className="rig-controls">
          <div className="row tight">
            {clips.map(([id, label]) => (
              <button key={id} type="button" className={clip === id ? 'primary' : ''} aria-pressed={clip === id} onClick={() => play(id)}>{label}</button>
            ))}
            <label className="small muted amp">{t.strength}
              <input id="rig-amp" type="range" min={0} max={1.5} step={0.05} value={amp} onChange={e => setAmp(Number(e.target.value))} />
            </label>
          </div>
          <details>
            <summary>{t.pose}</summary>
            <div className="row tight">
              {poses.map(name => (
                <button key={name} type="button" className={pose === name ? 'primary' : ''} aria-pressed={pose === name} onClick={() => hold(name)}>{t.poses[name]}</button>
              ))}
            </div>
            <form className="row tight" onSubmit={e => { e.preventDefault(); ask(); }}>
              <input id="rig-describe" className="grow" value={describe} maxLength={500} placeholder={t.describe} aria-label={t.describe}
                onChange={e => setDescribe(e.target.value)} />
              <button type="submit" disabled={asking || !describe.trim()}>{asking ? t.asking : t.ask}</button>
            </form>
            {note && <div className="small muted">{note}</div>}
            {poseError && <div className="error">{poseError}</div>}
            <div className="small muted">{t.poseHint}</div>
          </details>
          <details>
            <summary>{t.byHand}</summary>
            <div className="bone-grid">
              <select id="rig-bone" value={bone} onChange={e => pick(Number(e.target.value))} aria-label={t.bone}>
                {stage.current?.bones.map((b, i) => <option key={i} value={i}>{b.name || `${t.bone} ${i}`}</option>)}
              </select>
              {(['X', 'Y', 'Z'] as const).map((axis, i) => (
                <label key={axis} className="axis">{axis}
                  <input id={`rig-${axis}`} type="range" min={-120} max={120} value={angles[i]} onChange={e => turn(i, Number(e.target.value))}
                    onPointerUp={() => stage.current?.ground()} onKeyUp={() => stage.current?.ground()} />
                  <output>{angles[i]}°</output>
                </label>
              ))}
            </div>
          </details>
          <div className="row tight">
            <button type="button" onClick={reset}>{t.restPose}</button>
            <button type="button" className={skeleton ? 'primary' : ''} aria-pressed={skeleton} onClick={() => setSkeleton(!skeleton)}>{t.skeleton}</button>
            <button type="button" className={wire ? 'primary' : ''} aria-pressed={wire} onClick={() => setWire(!wire)}>{t.wireframe}</button>
            <span className="small muted">
              {t.facts(facts.bones, facts.vertices.toLocaleString(locale), `${facts.core.length - missing.length}/${facts.core.length}`)}
            </span>
          </div>
          <div className="row tight">
            <button type="button" onClick={download} disabled={exporting}>{exporting ? t.packing : t.download}</button>
            <span className="small muted">{t.downloadHint}</span>
          </div>
          {exportError && <div className="error">{exportError}</div>}
          <PrintPanel jobId={jobId} pose={() => stage.current?.printPose()} />
          {missing.length > 0 && <div className="small warn">{t.missing(missing.map(m => m.name).join(', '))}</div>}
        </div>
      )}
    </div>
  );
}
